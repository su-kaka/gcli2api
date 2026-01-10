"""
Anthropic 到 Gemini 格式转换器

提供请求体、响应和流式转换的完整功能。
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from log import log
from src.converter.gemini_fix import build_system_instruction_from_list
from src.converter.utils import merge_system_messages

from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature
)

DEFAULT_THINKING_BUDGET = 1024
DEFAULT_TEMPERATURE = 0.4
_DEBUG_TRUE = {"1", "true", "yes", "on"}


# ============================================================================
# 请求验证和提取
# ============================================================================


def _anthropic_debug_enabled() -> bool:
    """检查是否启用 Anthropic 调试模式"""
    return str(os.getenv("ANTHROPIC_DEBUG", "")).strip().lower() in _DEBUG_TRUE


def _is_non_whitespace_text(value: Any) -> bool:
    """
    判断文本是否包含"非空白"内容。

    说明：下游（Antigravity/Claude 兼容层）会对纯 text 内容块做校验：
    - text 不能为空字符串
    - text 不能仅由空白字符（空格/换行/制表等）组成
    """
    if value is None:
        return False
    try:
        return bool(str(value).strip())
    except Exception:
        return False


def _remove_nulls_for_tool_input(value: Any) -> Any:
    """
    递归移除 dict/list 中值为 null/None 的字段/元素。

    背景：Roo/Kilo 在 Anthropic native tool 路径下，若收到 tool_use.input 中包含 null，
    可能会把 null 当作真实入参执行（例如"在 null 中搜索"）。
    """
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            cleaned[k] = _remove_nulls_for_tool_input(v)
        return cleaned

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            if item is None:
                continue
            cleaned_list.append(_remove_nulls_for_tool_input(item))
        return cleaned_list

    return value

# ============================================================================
# 2. Thinking 配置
# ============================================================================

def get_thinking_config(thinking: Optional[Union[bool, Dict[str, Any]]]) -> Dict[str, Any]:
    """
    根据 Anthropic/Claude 请求的 thinking 参数生成下游 thinkingConfig。
    """
    if thinking is None:
        return {"includeThoughts": True, "thinkingBudget": DEFAULT_THINKING_BUDGET}

    if isinstance(thinking, bool):
        if thinking:
            return {"includeThoughts": True, "thinkingBudget": DEFAULT_THINKING_BUDGET}
        return {"includeThoughts": False}

    if isinstance(thinking, dict):
        thinking_type = thinking.get("type", "enabled")
        is_enabled = thinking_type == "enabled"
        if not is_enabled:
            return {"includeThoughts": False}

        budget = thinking.get("budget_tokens", DEFAULT_THINKING_BUDGET)
        return {"includeThoughts": True, "thinkingBudget": budget}

    return {"includeThoughts": True, "thinkingBudget": DEFAULT_THINKING_BUDGET}


# ============================================================================
# 3. JSON Schema 清理
# ============================================================================

def clean_json_schema(schema: Any) -> Any:
    """
    清理 JSON Schema，移除下游不支持的字段，并把验证要求追加到 description。
    """
    if not isinstance(schema, dict):
        return schema

    # 下游不支持的字段
    unsupported_keys = {
        "$schema", "$id", "$ref", "$defs", "definitions", "title",
        "example", "examples", "readOnly", "writeOnly", "default",
        "exclusiveMaximum", "exclusiveMinimum", "oneOf", "anyOf", "allOf",
        "const", "additionalItems", "contains", "patternProperties",
        "dependencies", "propertyNames", "if", "then", "else",
        "contentEncoding", "contentMediaType",
    }

    validation_fields = {
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minimum": "minimum",
        "maximum": "maximum",
        "minItems": "minItems",
        "maxItems": "maxItems",
    }
    fields_to_remove = {"additionalProperties"}

    validations: List[str] = []
    for field, label in validation_fields.items():
        if field in schema:
            validations.append(f"{label}: {schema[field]}")

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in unsupported_keys or key in fields_to_remove or key in validation_fields:
            continue

        if key == "type" and isinstance(value, list):
            # type: ["string", "null"] -> type: "string", nullable: true
            has_null = any(
                isinstance(t, str) and t.strip() and t.strip().lower() == "null" for t in value
            )
            non_null_types = [
                t.strip()
                for t in value
                if isinstance(t, str) and t.strip() and t.strip().lower() != "null"
            ]

            cleaned[key] = non_null_types[0] if non_null_types else "string"
            if has_null:
                cleaned["nullable"] = True
            continue

        if key == "description" and validations:
            cleaned[key] = f"{value} ({', '.join(validations)})"
        elif isinstance(value, dict):
            cleaned[key] = clean_json_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_json_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value

    if validations and "description" not in cleaned:
        cleaned["description"] = f"Validation: {', '.join(validations)}"

    # 如果有 properties 但没有显式 type，则补齐为 object
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    return cleaned


# ============================================================================
# 4. Tools 转换
# ============================================================================

def convert_tools(anthropic_tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 Anthropic tools[] 转换为下游 tools（functionDeclarations）结构。
    """
    if not anthropic_tools:
        return None

    gemini_tools: List[Dict[str, Any]] = []
    for tool in anthropic_tools:
        name = tool.get("name", "nameless_function")
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", {}) or {}
        parameters = clean_json_schema(input_schema)

        gemini_tools.append(
            {
                "functionDeclarations": [
                    {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    }
                ]
            }
        )

    return gemini_tools or None


# ============================================================================
# 5. Messages 转换
# ============================================================================

def _extract_tool_result_output(content: Any) -> str:
    """从 tool_result.content 中提取输出字符串"""
    if isinstance(content, list):
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            return str(first.get("text", ""))
        return str(first)
    if content is None:
        return ""
    return str(content)


def convert_messages_to_contents(
    messages: List[Dict[str, Any]],
    *,
    include_thinking: bool = True
) -> List[Dict[str, Any]]:
    """
    将 Anthropic messages[] 转换为下游 contents[]（role: user/model, parts: []）。

    Args:
        messages: Anthropic 格式的消息列表
        include_thinking: 是否包含 thinking 块
    """
    contents: List[Dict[str, Any]] = []

    # 第一遍：构建 tool_use_id -> name 的映射
    tool_use_names: Dict[str, str] = {}
    for msg in messages:
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    tool_id = item.get("id")
                    tool_name = item.get("name")
                    if tool_id and tool_name:
                        tool_use_names[str(tool_id)] = tool_name

    for msg in messages:
        role = msg.get("role", "user")
        gemini_role = "model" if role == "assistant" else "user"
        raw_content = msg.get("content", "")

        parts: List[Dict[str, Any]] = []
        if isinstance(raw_content, str):
            if _is_non_whitespace_text(raw_content):
                parts = [{"text": str(raw_content)}]
        elif isinstance(raw_content, list):
            for item in raw_content:
                if not isinstance(item, dict):
                    if _is_non_whitespace_text(item):
                        parts.append({"text": str(item)})
                    continue

                item_type = item.get("type")
                if item_type == "thinking":
                    if not include_thinking:
                        continue

                    signature = item.get("signature")
                    if not signature:
                        continue

                    thinking_text = item.get("thinking", "")
                    if thinking_text is None:
                        thinking_text = ""
                    part: Dict[str, Any] = {
                        "text": str(thinking_text),
                        "thought": True,
                        "thoughtSignature": signature,
                    }
                    parts.append(part)
                elif item_type == "redacted_thinking":
                    if not include_thinking:
                        continue

                    signature = item.get("signature")
                    if not signature:
                        continue

                    thinking_text = item.get("thinking")
                    if thinking_text is None:
                        thinking_text = item.get("data", "")
                    parts.append(
                        {
                            "text": str(thinking_text or ""),
                            "thought": True,
                            "thoughtSignature": signature,
                        }
                    )
                elif item_type == "text":
                    text = item.get("text", "")
                    if _is_non_whitespace_text(text):
                        parts.append({"text": str(text)})
                elif item_type == "image":
                    source = item.get("source", {}) or {}
                    if source.get("type") == "base64":
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "data": source.get("data", ""),
                                }
                            }
                        )
                elif item_type == "tool_use":
                    encoded_id = item.get("id") or ""
                    original_id, signature = decode_tool_id_and_signature(encoded_id)

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "id": original_id,
                            "name": item.get("name"),
                            "args": item.get("input", {}) or {},
                        }
                    }

                    # 如果提取到签名则添加
                    if signature:
                        fc_part["thoughtSignature"] = signature

                    parts.append(fc_part)
                elif item_type == "tool_result":
                    output = _extract_tool_result_output(item.get("content"))
                    encoded_tool_use_id = item.get("tool_use_id") or ""
                    # 解码获取原始ID（functionResponse不需要签名）
                    original_tool_use_id, _ = decode_tool_id_and_signature(encoded_tool_use_id)

                    # 从 tool_result 获取 name，如果没有则从映射中查找
                    func_name = item.get("name")
                    if not func_name and encoded_tool_use_id:
                        # 使用编码ID查找，因为映射中存储的是编码ID
                        func_name = tool_use_names.get(str(encoded_tool_use_id))
                    if not func_name:
                        func_name = "unknown_function"
                    parts.append(
                        {
                            "functionResponse": {
                                "id": original_tool_use_id,  # 使用解码后的ID以匹配functionCall
                                "name": func_name,
                                "response": {"output": output},
                            }
                        }
                    )
                else:
                    parts.append({"text": json.dumps(item, ensure_ascii=False)})
        else:
            if _is_non_whitespace_text(raw_content):
                parts = [{"text": str(raw_content)}]

        if not parts:
            continue

        contents.append({"role": gemini_role, "parts": parts})

    return contents


def reorganize_tool_messages(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    重新组织消息，满足 tool_use/tool_result 约束。
    """
    tool_results: Dict[str, Dict[str, Any]] = {}

    for msg in contents:
        for part in msg.get("parts", []) or []:
            if isinstance(part, dict) and "functionResponse" in part:
                tool_id = (part.get("functionResponse") or {}).get("id")
                if tool_id:
                    tool_results[str(tool_id)] = part

    flattened: List[Dict[str, Any]] = []
    for msg in contents:
        role = msg.get("role")
        for part in msg.get("parts", []) or []:
            flattened.append({"role": role, "parts": [part]})

    new_contents: List[Dict[str, Any]] = []
    i = 0
    while i < len(flattened):
        msg = flattened[i]
        part = msg["parts"][0]

        if isinstance(part, dict) and "functionResponse" in part:
            i += 1
            continue

        if isinstance(part, dict) and "functionCall" in part:
            tool_id = (part.get("functionCall") or {}).get("id")
            new_contents.append({"role": "model", "parts": [part]})

            if tool_id is not None and str(tool_id) in tool_results:
                new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})

            i += 1
            continue

        new_contents.append(msg)
        i += 1

    return new_contents


# ============================================================================
# 6. System Instruction 构建
# ============================================================================

def build_system_instruction(system: Any) -> Optional[Dict[str, Any]]:
    """
    将 Anthropic system 字段转换为下游 systemInstruction

    统一使用 gemini_fix.build_system_instruction_from_list 来处理
    """
    if not system:
        return None

    system_instructions: List[str] = []

    if isinstance(system, str):
        if _is_non_whitespace_text(system):
            system_instructions.append(str(system))
    elif isinstance(system, list):
        for item in system:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "")
                if _is_non_whitespace_text(text):
                    system_instructions.append(str(text))
    else:
        if _is_non_whitespace_text(system):
            system_instructions.append(str(system))

    # 使用统一的函数构建 systemInstruction
    return build_system_instruction_from_list(system_instructions)


# ============================================================================
# 7. Generation Config 构建
# ============================================================================

def build_generation_config(payload: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """
    根据 Anthropic Messages 请求构造下游 generationConfig。

    Returns:
        (generation_config, should_include_thinking): 元组
    """
    config: Dict[str, Any] = {
        "topP": 1,
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>",
        ],
    }

    temperature = payload.get("temperature", None)
    config["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature

    top_p = payload.get("top_p", None)
    if top_p is not None:
        config["topP"] = top_p

    top_k = payload.get("top_k", None)
    if top_k is not None:
        config["topK"] = top_k

    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens

    stop_sequences = payload.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        config["stopSequences"] = config["stopSequences"] + [str(s) for s in stop_sequences]

    # Thinking 配置处理
    should_include_thinking = False
    if "thinking" in payload:
        thinking_value = payload.get("thinking")
        if thinking_value is not None:
            thinking_config = get_thinking_config(thinking_value)
            include_thoughts = bool(thinking_config.get("includeThoughts", False))

            # 检查最后一条 assistant 消息的首个块类型
            last_assistant_first_block_type = None
            for msg in reversed(payload.get("messages") or []):
                if not isinstance(msg, dict):
                    continue
                if msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if not isinstance(content, list) or not content:
                    continue
                first_block = content[0]
                if isinstance(first_block, dict):
                    last_assistant_first_block_type = first_block.get("type")
                else:
                    last_assistant_first_block_type = None
                break

            if include_thoughts and last_assistant_first_block_type not in {
                None, "thinking", "redacted_thinking",
            }:
                if _anthropic_debug_enabled():
                    log.info(
                        "[ANTHROPIC][thinking] 请求显式启用 thinking，但历史 messages 未回放 "
                        "满足约束的 assistant thinking/redacted_thinking 起始块，已跳过下发 thinkingConfig"
                    )
                return config, False

            # 处理 thinkingBudget 与 max_tokens 的关系
            if include_thoughts and isinstance(max_tokens, int):
                budget = thinking_config.get("thinkingBudget")
                if isinstance(budget, int) and budget >= max_tokens:
                    adjusted_budget = max(0, max_tokens - 1)
                    if adjusted_budget <= 0:
                        if _anthropic_debug_enabled():
                            log.info(
                                "[ANTHROPIC][thinking] thinkingBudget>=max_tokens 且无法下调到正数，"
                                "已跳过下发 thinkingConfig"
                            )
                        return config, False
                    if _anthropic_debug_enabled():
                        log.info(
                            f"[ANTHROPIC][thinking] thinkingBudget>=max_tokens，自动下调 budget: "
                            f"{budget} -> {adjusted_budget}（max_tokens={max_tokens}）"
                        )
                    thinking_config["thinkingBudget"] = adjusted_budget

            config["thinkingConfig"] = thinking_config
            should_include_thinking = include_thoughts
            if _anthropic_debug_enabled():
                log.info(
                    f"[ANTHROPIC][thinking] 已下发 thinkingConfig: includeThoughts="
                    f"{thinking_config.get('includeThoughts')}, thinkingBudget="
                    f"{thinking_config.get('thinkingBudget')}"
                )
        else:
            if _anthropic_debug_enabled():
                log.info("[ANTHROPIC][thinking] thinking=null，视为未启用 thinking")

    return config, should_include_thinking


# ============================================================================
# 8. 主要转换函数
# ============================================================================

async def anthropic_to_gemini_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Anthropic 格式请求体转换为 Gemini 格式请求体

    注意: 此函数只负责基础转换，不包含 normalize_gemini_request 中的处理
    (如 thinking config 自动设置、search tools、参数范围限制等)

    Args:
        payload: Anthropic 格式的请求体字典

    Returns:
        Gemini 格式的请求体字典，包含:
        - contents: 转换后的消息内容
        - generationConfig: 生成配置
        - systemInstruction: 系统指令 (如果有)
        - tools: 工具定义 (如果有)
    """
    # 处理连续的system消息（兼容性模式）
    payload = await merge_system_messages(payload)

    # 提取和转换基础信息
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    # 构建生成配置（包含thinking配置）
    generation_config, should_include_thinking = build_generation_config(payload)

    # 转换消息内容
    contents = convert_messages_to_contents(messages, include_thinking=should_include_thinking)
    contents = reorganize_tool_messages(contents)

    # 转换系统指令
    system_instruction = build_system_instruction(payload.get("system"))

    # 如果merge_system_messages已经添加了systemInstruction，优先使用它
    if "systemInstruction" in payload and not system_instruction:
        system_instruction = payload["systemInstruction"]

    # 转换工具
    tools = convert_tools(payload.get("tools"))

    # 构建基础请求数据
    gemini_request = {
        "contents": contents,
        "generationConfig": generation_config,
    }

    if system_instruction:
        gemini_request["systemInstruction"] = system_instruction

    if tools:
        gemini_request["tools"] = tools

    return gemini_request


def gemini_to_anthropic_response(
    gemini_response: Dict[str, Any],
    model: str,
    status_code: int = 200
) -> Dict[str, Any]:
    """
    将 Gemini 格式非流式响应转换为 Anthropic 格式非流式响应

    注意: 如果收到的不是 200 开头的响应体，不做任何处理，直接转发

    Args:
        gemini_response: Gemini 格式的响应体字典
        model: 模型名称
        status_code: HTTP 状态码 (默认 200)

    Returns:
        Anthropic 格式的响应体字典，或原始响应 (如果状态码不是 2xx)
    """
    # 非 2xx 状态码直接返回原始响应
    if not (200 <= status_code < 300):
        return gemini_response

    # 处理 GeminiCLI 的 response 包装格式
    if "response" in gemini_response:
        response_data = gemini_response["response"]
    else:
        response_data = gemini_response

    # 提取候选结果
    candidate = response_data.get("candidates", [{}])[0] or {}
    parts = candidate.get("content", {}).get("parts", []) or []

    # 获取 usage metadata
    usage_metadata = {}
    if "usageMetadata" in response_data:
        usage_metadata = response_data["usageMetadata"]
    elif "usageMetadata" in candidate:
        usage_metadata = candidate["usageMetadata"]

    # 转换内容块
    content = []
    has_tool_use = False

    for part in parts:
        if not isinstance(part, dict):
            continue

        # 处理 thinking 块
        if part.get("thought") is True:
            block: Dict[str, Any] = {"type": "thinking", "thinking": part.get("text", "")}
            signature = part.get("thoughtSignature")
            if signature:
                block["signature"] = signature
            content.append(block)
            continue

        # 处理文本块
        if "text" in part:
            content.append({"type": "text", "text": part.get("text", "")})
            continue

        # 处理工具调用
        if "functionCall" in part:
            has_tool_use = True
            fc = part.get("functionCall", {}) or {}
            original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
            signature = part.get("thoughtSignature")
            encoded_id = encode_tool_id_with_signature(original_id, signature)
            content.append(
                {
                    "type": "tool_use",
                    "id": encoded_id,
                    "name": fc.get("name") or "",
                    "input": _remove_nulls_for_tool_input(fc.get("args", {}) or {}),
                }
            )
            continue

        # 处理图片
        if "inlineData" in part:
            inline = part.get("inlineData", {}) or {}
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": inline.get("mimeType", "image/png"),
                        "data": inline.get("data", ""),
                    },
                }
            )
            continue

    # 确定停止原因
    finish_reason = candidate.get("finishReason")
    stop_reason = "tool_use" if has_tool_use else "end_turn"
    if finish_reason == "MAX_TOKENS" and not has_tool_use:
        stop_reason = "max_tokens"

    # 提取 token 使用情况
    input_tokens = usage_metadata.get("promptTokenCount", 0) if isinstance(usage_metadata, dict) else 0
    output_tokens = usage_metadata.get("candidatesTokenCount", 0) if isinstance(usage_metadata, dict) else 0

    # 构建 Anthropic 响应
    message_id = f"msg_{uuid.uuid4().hex}"

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
        },
    }


async def gemini_stream_to_anthropic_stream(
    gemini_stream: AsyncIterator[bytes],
    model: str,
    status_code: int = 200
) -> AsyncIterator[bytes]:
    """
    将 Gemini 格式流式响应转换为 Anthropic SSE 格式流式响应

    注意: 如果收到的不是 200 开头的响应体，不做任何处理，直接转发

    Args:
        gemini_stream: Gemini 格式的流式响应 (bytes 迭代器)
        model: 模型名称
        status_code: HTTP 状态码 (默认 200)

    Yields:
        Anthropic SSE 格式的响应块 (bytes)
    """
    # 非 2xx 状态码直接转发原始流
    if not (200 <= status_code < 300):
        async for chunk in gemini_stream:
            yield chunk
        return

    # 初始化状态
    message_id = f"msg_{uuid.uuid4().hex}"
    message_start_sent = False
    current_block_type: Optional[str] = None
    current_block_index = -1
    current_thinking_signature: Optional[str] = None
    has_tool_use = False
    input_tokens = 0
    output_tokens = 0
    finish_reason: Optional[str] = None

    def _sse_event(event: str, data: Dict[str, Any]) -> bytes:
        """生成 SSE 事件"""
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def _close_block() -> Optional[bytes]:
        """关闭当前内容块"""
        nonlocal current_block_type
        if current_block_type is None:
            return None
        event = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": current_block_index},
        )
        current_block_type = None
        return event

    # 处理流式数据
    try:
        async for chunk in gemini_stream:
            # 解析 Gemini 流式块
            if not chunk or not chunk.startswith(b"data: "):
                continue

            raw = chunk[6:].strip()
            if raw == b"[DONE]":
                break

            try:
                data = json.loads(raw.decode('utf-8', errors='ignore'))
            except Exception:
                continue

            # 处理 GeminiCLI 的 response 包装格式
            if "response" in data:
                response = data["response"]
            else:
                response = data

            candidate = (response.get("candidates", []) or [{}])[0] or {}
            parts = (candidate.get("content", {}) or {}).get("parts", []) or []

            # 更新 usage metadata
            if "usageMetadata" in response:
                usage = response["usageMetadata"]
                if isinstance(usage, dict):
                    if "promptTokenCount" in usage:
                        input_tokens = int(usage.get("promptTokenCount", 0) or 0)
                    if "candidatesTokenCount" in usage:
                        output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)

            # 发送 message_start（仅一次）
            if not message_start_sent:
                message_start_sent = True
                yield _sse_event(
                    "message_start",
                    {
                        "type": "message_start",
                        "message": {
                            "id": message_id,
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0},
                        },
                    },
                )

            # 处理各种 parts
            for part in parts:
                if not isinstance(part, dict):
                    continue

                # 处理 thinking 块
                if part.get("thought") is True:
                    if current_block_type != "thinking":
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt

                        current_block_index += 1
                        current_block_type = "thinking"
                        signature = part.get("thoughtSignature")
                        current_thinking_signature = signature

                        block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
                        if signature:
                            block["signature"] = signature

                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": block,
                            },
                        )

                    thinking_text = part.get("text", "")
                    if thinking_text:
                        yield _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": current_block_index,
                                "delta": {"type": "thinking_delta", "thinking": thinking_text},
                            },
                        )
                    continue

                # 处理文本块
                if "text" in part:
                    text = part.get("text", "")
                    if isinstance(text, str) and not text.strip():
                        continue

                    if current_block_type != "text":
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt

                        current_block_index += 1
                        current_block_type = "text"

                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": {"type": "text", "text": ""},
                            },
                        )

                    if text:
                        yield _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": current_block_index,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                    continue

                # 处理工具调用
                if "functionCall" in part:
                    close_evt = _close_block()
                    if close_evt:
                        yield close_evt

                    has_tool_use = True
                    fc = part.get("functionCall", {}) or {}
                    original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
                    signature = part.get("thoughtSignature")
                    tool_id = encode_tool_id_with_signature(original_id, signature)
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    current_block_index += 1

                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": current_block_index,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": {},
                            },
                        },
                    )

                    input_json = json.dumps(tool_args, ensure_ascii=False, separators=(",", ":"))
                    yield _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": current_block_index,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )

                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": current_block_index},
                    )
                    continue

            # 检查是否结束
            if candidate.get("finishReason"):
                finish_reason = candidate.get("finishReason")
                break

        # 关闭最后的内容块
        close_evt = _close_block()
        if close_evt:
            yield close_evt

        # 确定停止原因
        stop_reason = "tool_use" if has_tool_use else "end_turn"
        if finish_reason == "MAX_TOKENS" and not has_tool_use:
            stop_reason = "max_tokens"

        # 发送 message_delta 和 message_stop
        yield _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {
                    "output_tokens": output_tokens,
                },
            },
        )

        yield _sse_event("message_stop", {"type": "message_stop"})

    except Exception as e:
        log.error(f"[ANTHROPIC] 流式转换失败: {e}")
        # 发送错误事件
        if not message_start_sent:
            yield _sse_event(
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": message_id,
                        "type": "message",
                        "role": "assistant",
                        "model": model,
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 0, "output_tokens": 0},
                    },
                },
            )
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )