"""
Anthropic 到 Gemini 格式转换器

提供请求体、响应和流式转换的完整功能。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional, Union

from log import log
from src.converter.gemini_fix import normalize_gemini_request, build_system_instruction_from_list


from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature,
    generate_dummy_signature,
)

DEFAULT_THINKING_BUDGET = 1024
DEFAULT_TEMPERATURE = 0.4
_DEBUG_TRUE = {"1", "true", "yes", "on"}


# ============================================================================
# 请求验证和提取
# ============================================================================

class AnthropicRequestValidationError(Exception):
    """Anthropic 请求验证错误"""
    def __init__(self, message: str, error_type: str = "invalid_request_error"):
        self.message = message
        self.error_type = error_type
        super().__init__(message)


def validate_and_extract_anthropic_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证并提取 Anthropic 请求的必要字段。
    
    Args:
        payload: 原始请求体
        
    Returns:
        包含提取字段的字典：
        - model: 模型名
        - max_tokens: 最大 token 数
        - messages: 消息列表
        - stream: 是否流式
        - thinking_present: 是否包含 thinking 字段
        - thinking_value: thinking 字段的值
        - thinking_summary: thinking 的摘要（用于日志）
        
    Raises:
        AnthropicRequestValidationError: 验证失败时抛出
    """
    if not isinstance(payload, dict):
        raise AnthropicRequestValidationError("请求体必须为 JSON object")
    
    model = payload.get("model")
    max_tokens = payload.get("max_tokens")
    messages = payload.get("messages")
    stream = bool(payload.get("stream", False))
    
    # 验证必填字段
    if not model or max_tokens is None or not isinstance(messages, list):
        raise AnthropicRequestValidationError(
            "缺少必填字段：model / max_tokens / messages"
        )
    
    # 提取 thinking 相关信息
    thinking_present = "thinking" in payload
    thinking_value = payload.get("thinking")
    thinking_summary = None
    
    if thinking_present:
        if isinstance(thinking_value, dict):
            thinking_summary = {
                "type": thinking_value.get("type"),
                "budget_tokens": thinking_value.get("budget_tokens"),
            }
        else:
            thinking_summary = thinking_value
    
    return {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": stream,
        "thinking_present": thinking_present,
        "thinking_value": thinking_value,
        "thinking_summary": thinking_summary,
    }


def validate_anthropic_count_tokens_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证并提取 Anthropic count_tokens 请求的必要字段。
    
    Args:
        payload: 原始请求体
        
    Returns:
        包含提取字段的字典
        
    Raises:
        AnthropicRequestValidationError: 验证失败时抛出
    """
    if not isinstance(payload, dict):
        raise AnthropicRequestValidationError("请求体必须为 JSON object")
    
    model = payload.get("model")
    messages = payload.get("messages")
    
    if not model or not isinstance(messages, list):
        raise AnthropicRequestValidationError(
            "缺少必填字段：model / messages"
        )
    
    # 提取 thinking 相关信息
    thinking_present = "thinking" in payload
    thinking_value = payload.get("thinking")
    thinking_summary = None
    
    if thinking_present:
        if isinstance(thinking_value, dict):
            thinking_summary = {
                "type": thinking_value.get("type"),
                "budget_tokens": thinking_value.get("budget_tokens"),
            }
        else:
            thinking_summary = thinking_value
    
    return {
        "model": model,
        "messages": messages,
        "thinking_present": thinking_present,
        "thinking_value": thinking_value,
        "thinking_summary": thinking_summary,
    }


# ============================================================================
# 调试和辅助函数
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
# 1. 模型映射
# ============================================================================

def map_claude_model_to_gemini(claude_model: str) -> str:
    """
    将 Claude 模型名映射为下游 Gemini 模型名。
    非 Claude 模型直接透传。
    """
    claude_model = str(claude_model or "").strip()
    if not claude_model:
        return "claude-sonnet-4-5"

    # 版本化模型名规范化（例如 claude-opus-4-5-20251101 -> claude-opus-4-5）
    m = re.match(r"^(claude-(?:opus|sonnet|haiku)-4-5)-\d{8}$", claude_model)
    if m:
        claude_model = m.group(1)

    # Claude 模型映射
    model_mapping = {
        "claude-opus-4-5": "claude-opus-4-5-thinking",
        "claude-haiku-4-5": "gemini-2.5-flash",
        "claude-opus-4": "claude-opus-4-5-thinking",
        "claude-haiku-4": "gemini-2.5-flash",
    }

    # 如果在映射表中找到，返回映射值；否则直接透传
    return model_mapping.get(claude_model, claude_model)


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

                    # 如果提取到签名则添加，否则为Gemini 3+生成占位签名
                    if signature:
                        fc_part["thoughtSignature"] = signature
                    else:
                        fc_part["thoughtSignature"] = generate_dummy_signature()

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

    注意: topK 和 maxOutputTokens 的限制已移至统一的 normalize_gemini_request 函数中

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
# 8. 请求转换（主函数）
# ============================================================================

def convert_anthropic_request_to_gemini(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Anthropic Messages 请求转换为 Gemini 请求所需的组件。

    返回字段：
    - model: 下游模型名
    - contents: 下游 contents[]
    - system_instruction: 下游 systemInstruction（可选）
    - tools: 下游 tools（可选）
    - generation_config: 下游 generationConfig
    """
    model = map_claude_model_to_gemini(str(payload.get("model", "")))
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = []

    generation_config, should_include_thinking = build_generation_config(payload)
    contents = convert_messages_to_contents(messages, include_thinking=should_include_thinking)
    contents = reorganize_tool_messages(contents)
    system_instruction = build_system_instruction(payload.get("system"))
    tools = convert_tools(payload.get("tools"))

    # 构建基础请求数据
    request_data = {
        "contents": contents,
        "generationConfig": generation_config,
    }

    # 使用统一的后处理函数（注意: Anthropic 不需要 compatibility_mode）
    # 由于 Anthropic 的 thinking 已经在 generation_config 中处理,这里不传递 thinking_config_override
    # 由于 Anthropic 没有搜索模型,不传递 is_search_model_func
    request_data = normalize_gemini_request(
        request_data,
        model=model,
        system_instruction=system_instruction,
        tools=tools,
        thinking_config_override=None,  # Anthropic 的 thinking 已在 build_generation_config 中处理
        compatibility_mode=False,  # Anthropic 不使用兼容性模式
        default_safety_settings=[
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ],
        get_thinking_budget_func=None,  # 不使用自动 thinking 配置
        should_include_thoughts_func=None,  # 不使用自动 thinking 配置
        is_search_model_func=None,  # Anthropic 没有搜索模型
    )

    return {
        "model": model,
        "contents": request_data["contents"],
        "system_instruction": request_data.get("systemInstruction"),
        "tools": request_data.get("tools"),
        "generation_config": request_data["generationConfig"],
    }


# ============================================================================
# 9. 响应转换（非流式）
# ============================================================================

def _pick_usage_metadata(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """兼容下游 usageMetadata 的多种落点（response_data 已 unwrap，无 'response' 包装）"""
    response = response_data or {}
    if not isinstance(response, dict):
        return {}

    response_usage = response.get("usageMetadata", {}) or {}
    if not isinstance(response_usage, dict):
        response_usage = {}

    candidate = (response.get("candidates", []) or [{}])[0] or {}
    if not isinstance(candidate, dict):
        candidate = {}
    candidate_usage = candidate.get("usageMetadata", {}) or {}
    if not isinstance(candidate_usage, dict):
        candidate_usage = {}

    fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")

    def score(d: Dict[str, Any]) -> int:
        s = 0
        for f in fields:
            if f in d and d.get(f) is not None:
                s += 1
        return s

    if score(candidate_usage) > score(response_usage):
        return candidate_usage
    return response_usage


def convert_gemini_response_to_anthropic(
    response_data: Dict[str, Any],
    *,
    model: str,
    message_id: str,
    fallback_input_tokens: int = 0,
) -> Dict[str, Any]:
    """
    将 Gemini 响应转换为 Anthropic Message 格式（response_data 已 unwrap，无 'response' 包装）。
    """
    candidate = response_data.get("candidates", [{}])[0] or {}
    parts = candidate.get("content", {}).get("parts", []) or []
    usage_metadata = _pick_usage_metadata(response_data)

    content = []
    has_tool_use = False

    for part in parts:
        if not isinstance(part, dict):
            continue

        if part.get("thought") is True:
            block: Dict[str, Any] = {"type": "thinking", "thinking": part.get("text", "")}
            signature = part.get("thoughtSignature")
            if signature:
                block["signature"] = signature
            content.append(block)
            continue

        if "text" in part:
            content.append({"type": "text", "text": part.get("text", "")})
            continue

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

    finish_reason = candidate.get("finishReason")
    stop_reason = "tool_use" if has_tool_use else "end_turn"
    if finish_reason == "MAX_TOKENS" and not has_tool_use:
        stop_reason = "max_tokens"

    input_tokens_present = isinstance(usage_metadata, dict) and "promptTokenCount" in usage_metadata
    output_tokens_present = isinstance(usage_metadata, dict) and "candidatesTokenCount" in usage_metadata

    input_tokens = usage_metadata.get("promptTokenCount", 0) if isinstance(usage_metadata, dict) else 0
    output_tokens = usage_metadata.get("candidatesTokenCount", 0) if isinstance(usage_metadata, dict) else 0

    if not input_tokens_present:
        input_tokens = max(0, int(fallback_input_tokens or 0))
    if not output_tokens_present:
        output_tokens = 0

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


# ============================================================================
# 10. 流式转换
# ============================================================================

def _sse_event(event: str, data: Dict[str, Any]) -> bytes:
    """生成 SSE 事件"""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


class _StreamingState:
    """流式转换状态管理"""
    def __init__(self, message_id: str, model: str):
        self.message_id = message_id
        self.model = model

        self._current_block_type: Optional[str] = None
        self._current_block_index: int = -1
        self._current_thinking_signature: Optional[str] = None

        self.has_tool_use: bool = False
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.has_input_tokens: bool = False
        self.has_output_tokens: bool = False
        self.finish_reason: Optional[str] = None

    def _next_index(self) -> int:
        self._current_block_index += 1
        return self._current_block_index

    def close_block_if_open(self) -> Optional[bytes]:
        if self._current_block_type is None:
            return None
        event = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": self._current_block_index},
        )
        self._current_block_type = None
        self._current_thinking_signature = None
        return event

    def open_text_block(self) -> bytes:
        idx = self._next_index()
        self._current_block_type = "text"
        return _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            },
        )

    def open_thinking_block(self, signature: Optional[str]) -> bytes:
        idx = self._next_index()
        self._current_block_type = "thinking"
        self._current_thinking_signature = signature
        block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
        if signature:
            block["signature"] = signature
        return _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": block,
            },
        )


async def gemini_sse_to_anthropic_sse(
    lines: AsyncIterator[str],
    *,
    model: str,
    message_id: str,
    initial_input_tokens: int = 0,
) -> AsyncIterator[bytes]:
    """
    将 Gemini SSE 转换为 Anthropic Messages Streaming SSE。

    Args:
        mode: 凭证模式，用于记录 API 调用结果（antigravity 或 geminicli）
    """
    state = _StreamingState(message_id=message_id, model=model)
    message_start_sent = False
    pending_output: list[bytes] = []

    try:
        initial_input_tokens_int = max(0, int(initial_input_tokens or 0))
    except Exception:
        initial_input_tokens_int = 0

    def pick_usage_metadata(response: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        response_usage = response.get("usageMetadata", {}) or {}
        if not isinstance(response_usage, dict):
            response_usage = {}

        candidate_usage = candidate.get("usageMetadata", {}) or {}
        if not isinstance(candidate_usage, dict):
            candidate_usage = {}

        fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")

        def score(d: Dict[str, Any]) -> int:
            s = 0
            for f in fields:
                if f in d and d.get(f) is not None:
                    s += 1
            return s

        if score(candidate_usage) > score(response_usage):
            return candidate_usage
        return response_usage

    def enqueue(evt: bytes) -> None:
        pending_output.append(evt)

    def flush_pending_ready(ready: list[bytes]) -> None:
        if not pending_output:
            return
        ready.extend(pending_output)
        pending_output.clear()

    def send_message_start(ready: list[bytes], *, input_tokens: int) -> None:
        nonlocal message_start_sent
        if message_start_sent:
            return
        message_start_sent = True
        ready.append(
            _sse_event(
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
                        "usage": {"input_tokens": int(input_tokens or 0), "output_tokens": 0},
                    },
                },
            )
        )
        flush_pending_ready(ready)

    try:
        async for line in lines:
            ready_output: list[bytes] = []
            # 处理 bytes 类型的流式数据
            if not line or not line.startswith(b"data: "):
                continue

            raw = line[6:].strip()
            if raw == b"[DONE]":
                break

            try:
                # 解码 bytes 后再解析 JSON
                data = json.loads(raw.decode('utf-8', errors='ignore'))
            except Exception:
                continue

            # geminicli 的响应已经解包，直接使用
            response = data
            candidate = (response.get("candidates", []) or [{}])[0] or {}
            parts = (candidate.get("content", {}) or {}).get("parts", []) or []

            # 获取 usage metadata
            if isinstance(response, dict) and isinstance(candidate, dict):
                usage = pick_usage_metadata(response, candidate)
                if isinstance(usage, dict):
                    if "promptTokenCount" in usage:
                        state.input_tokens = int(usage.get("promptTokenCount", 0) or 0)
                        state.has_input_tokens = True
                    if "candidatesTokenCount" in usage:
                        state.output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
                        state.has_output_tokens = True

            # 发送 message_start
            if state.has_input_tokens and not message_start_sent:
                send_message_start(ready_output, input_tokens=state.input_tokens)

            # 处理各种 parts
            for part in parts:
                if not isinstance(part, dict):
                    continue

                # 处理 thoughtSignature
                if _anthropic_debug_enabled() and "thoughtSignature" in part:
                    try:
                        sig_val = part.get("thoughtSignature")
                        sig_len = len(str(sig_val)) if sig_val is not None else 0
                    except Exception:
                        sig_len = -1
                    log.info(
                        "[ANTHROPIC][thinking_signature] 收到 thoughtSignature 字段: "
                        f"current_block_type={state._current_block_type}, "
                        f"current_index={state._current_block_index}, len={sig_len}"
                    )

                signature = part.get("thoughtSignature")
                if (
                    signature
                    and state._current_block_type == "thinking"
                    and not state._current_thinking_signature
                ):
                    evt = _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state._current_block_index,
                            "delta": {"type": "signature_delta", "signature": signature},
                        },
                    )
                    state._current_thinking_signature = str(signature)
                    if message_start_sent:
                        ready_output.append(evt)
                    else:
                        enqueue(evt)

                # 处理 thinking 块
                if part.get("thought") is True:
                    if state._current_block_type != "thinking":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            if message_start_sent:
                                ready_output.append(stop_evt)
                            else:
                                enqueue(stop_evt)
                        signature = part.get("thoughtSignature")
                        evt = state.open_thinking_block(signature=signature)
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    thinking_text = part.get("text", "")
                    if thinking_text:
                        evt = _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "thinking_delta", "thinking": thinking_text},
                            },
                        )
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    continue

                # 处理 text 块
                if "text" in part:
                    text = part.get("text", "")
                    if isinstance(text, str) and not text.strip():
                        continue

                    if state._current_block_type != "text":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            if message_start_sent:
                                ready_output.append(stop_evt)
                            else:
                                enqueue(stop_evt)
                        evt = state.open_text_block()
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)

                    if text:
                        evt = _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    continue

                # 处理图片
                if "inlineData" in part:
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        if message_start_sent:
                            ready_output.append(stop_evt)
                        else:
                            enqueue(stop_evt)

                    inline = part.get("inlineData", {}) or {}
                    idx = state._next_index()
                    block = {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": inline.get("mimeType", "image/png"),
                            "data": inline.get("data", ""),
                        },
                    }
                    evt1 = _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": block,
                        },
                    )
                    evt2 = _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    if message_start_sent:
                        ready_output.extend([evt1, evt2])
                    else:
                        enqueue(evt1)
                        enqueue(evt2)
                    continue

                # 处理 function call
                if "functionCall" in part:
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        if message_start_sent:
                            ready_output.append(stop_evt)
                        else:
                            enqueue(stop_evt)

                    state.has_tool_use = True

                    fc = part.get("functionCall", {}) or {}
                    original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
                    signature = part.get("thoughtSignature")
                    tool_id = encode_tool_id_with_signature(original_id, signature)
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    idx = state._next_index()
                    evt_start = _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": {},
                            },
                        },
                    )

                    input_json = json.dumps(tool_args, ensure_ascii=False, separators=(",", ":"))
                    evt_delta = _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )
                    evt_stop = _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    if message_start_sent:
                        ready_output.extend([evt_start, evt_delta, evt_stop])
                    else:
                        enqueue(evt_start)
                        enqueue(evt_delta)
                        enqueue(evt_stop)
                    continue

            finish_reason = candidate.get("finishReason")

            if ready_output:
                for evt in ready_output:
                    yield evt

            if finish_reason:
                state.finish_reason = str(finish_reason)
                break

        # 关闭当前块
        stop_evt = state.close_block_if_open()
        if stop_evt:
            if message_start_sent:
                yield stop_evt
            else:
                enqueue(stop_evt)

        # 确保发送 message_start
        if not message_start_sent:
            ready_output = []
            send_message_start(ready_output, input_tokens=initial_input_tokens_int)
            for evt in ready_output:
                yield evt

        # 确定 stop_reason
        stop_reason = "tool_use" if state.has_tool_use else "end_turn"
        if state.finish_reason == "MAX_TOKENS" and not state.has_tool_use:
            stop_reason = "max_tokens"

        if _anthropic_debug_enabled():
            estimated_input = initial_input_tokens_int
            downstream_input = state.input_tokens if state.has_input_tokens else 0
            log.info(
                f"[ANTHROPIC][TOKEN] 流式 token: estimated={estimated_input}, "
                f"downstream={downstream_input}"
            )

        # 发送 message_delta 和 message_stop
        yield _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {
                    "input_tokens": state.input_tokens if state.has_input_tokens else initial_input_tokens_int,
                    "output_tokens": state.output_tokens if state.has_output_tokens else 0,
                },
            },
        )
        yield _sse_event("message_stop", {"type": "message_stop"})

    except Exception as e:
        log.error(f"[ANTHROPIC] 流式转换失败: {e}")
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
                        "usage": {"input_tokens": initial_input_tokens_int, "output_tokens": 0},
                    },
                },
            )
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )
