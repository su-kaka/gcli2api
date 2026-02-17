"""
Anthropic 到 Gemini 格式转换器

提供请求体、响应和流式转换的完整功能。
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import Response
from log import log
from src.converter.utils import merge_system_messages

from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature
)

DEFAULT_TEMPERATURE = 0.4
_DEBUG_TRUE = {"1", "true", "yes", "on"}

# ============================================================================
# Thinking 块验证和清理
# ============================================================================

# 最小有效签名长度
MIN_SIGNATURE_LENGTH = 10


def has_valid_thoughtsignature(block: Dict[str, Any]) -> bool:
    """
    检查 thinking 块是否有有效签名
    
    Args:
        block: content block 字典
        
    Returns:
        bool: 是否有有效签名
    """
    if not isinstance(block, dict):
        return True
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return True  # 非 thinking 块默认有效
    
    thinking = block.get("thinking", "")
    thoughtsignature = block.get("thoughtSignature")
    
    # 空 thinking + 任意 thoughtsignature = 有效 (trailing signature case)
    if not thinking and thoughtsignature is not None:
        return True
    
    # 有内容 + 足够长度的 thoughtsignature = 有效
    if thoughtsignature and isinstance(thoughtsignature, str) and len(thoughtsignature) >= MIN_SIGNATURE_LENGTH:
        return True
    
    return False


def sanitize_thinking_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    清理 thinking 块,只保留必要字段(移除 cache_control 等)
    
    Args:
        block: content block 字典
        
    Returns:
        清理后的 block 字典
    """
    if not isinstance(block, dict):
        return block
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return block
    
    # 重建块,移除额外字段
    sanitized: Dict[str, Any] = {
        "type": block_type,
        "thinking": block.get("thinking", "")
    }
    
    thoughtsignature = block.get("thoughtSignature")
    if thoughtsignature:
        sanitized["thoughtSignature"] = thoughtsignature
    
    return sanitized


def remove_trailing_unsigned_thinking(blocks: List[Dict[str, Any]]) -> None:
    """
    移除尾部的无签名 thinking 块
    
    Args:
        blocks: content blocks 列表 (会被修改)
    """
    if not blocks:
        return
    
    # 从后向前扫描
    end_index = len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        block = blocks[i]
        if not isinstance(block, dict):
            break
        
        block_type = block.get("type")
        if block_type in ("thinking", "redacted_thinking"):
            if not has_valid_thoughtsignature(block):
                end_index = i
            else:
                break  # 遇到有效签名的 thinking 块,停止
        else:
            break  # 遇到非 thinking 块,停止
    
    if end_index < len(blocks):
        removed = len(blocks) - end_index
        del blocks[end_index:]
        log.debug(f"Removed {removed} trailing unsigned thinking block(s)")


def filter_invalid_thinking_blocks(messages: List[Dict[str, Any]]) -> None:
    """
    过滤消息中的无效 thinking 块，并清理所有 thinking 块的额外字段（如 cache_control）

    Args:
        messages: Anthropic messages 列表 (会被修改)
    """
    total_filtered = 0

    for msg in messages:
        # 只处理 assistant 和 model 消息
        role = msg.get("role", "")
        if role not in ("assistant", "model"):
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        original_len = len(content)
        new_blocks: List[Dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue

            block_type = block.get("type")
            if block_type not in ("thinking", "redacted_thinking"):
                new_blocks.append(block)
                continue

            # 所有 thinking 块都需要清理（移除 cache_control 等额外字段）
            # 检查 thinking 块的有效性
            if has_valid_thoughtsignature(block):
                # 有效签名，清理后保留
                new_blocks.append(sanitize_thinking_block(block))
            else:
                # 无效签名，将内容转换为 text 块
                thinking_text = block.get("thinking", "")
                if thinking_text and str(thinking_text).strip():
                    log.info(
                        f"[Claude-Handler] Converting thinking block with invalid thoughtSignature to text. "
                        f"Content length: {len(thinking_text)} chars"
                    )
                    new_blocks.append({"type": "text", "text": thinking_text})
                else:
                    log.debug("[Claude-Handler] Dropping empty thinking block with invalid thoughtSignature")

        msg["content"] = new_blocks
        filtered_count = original_len - len(new_blocks)
        total_filtered += filtered_count

        # 如果过滤后为空,添加一个空文本块以保持消息有效
        if not new_blocks:
            msg["content"] = [{"type": "text", "text": ""}]

    if total_filtered > 0:
        log.debug(f"Filtered {total_filtered} invalid thinking block(s) from history")


# ============================================================================
# 请求验证和提取
# ============================================================================


def _anthropic_debug_enabled() -> bool:
    """检查是否启用 Anthropic 调试模式"""
    return str(os.getenv("ANTHROPIC_DEBUG", "true")).strip().lower() in _DEBUG_TRUE


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
# 2. JSON Schema 清理
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

    # 第一遍：构建 tool_use_id -> (name, thoughtsignature) 的映射
    # 注意：存储的是编码后的 ID（可能包含签名）
    tool_use_info: Dict[str, tuple[str, Optional[str]]] = {}
    for msg in messages:
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    encoded_tool_id = item.get("id")
                    tool_name = item.get("name")
                    if encoded_tool_id and tool_name:
                        # 解码获取原始ID和签名
                        original_id, thoughtsignature = decode_tool_id_and_signature(encoded_tool_id)
                        # 存储映射：编码ID -> (name, thoughtsignature)
                        tool_use_info[str(encoded_tool_id)] = (tool_name, thoughtsignature)

    for msg in messages:
        role = msg.get("role", "user")
        
        # system 消息已经由 merge_system_messages 处理，这里跳过
        if role == "system":
            continue
        
        # 支持 'assistant' 和 'model' 角色（Google history usage）
        gemini_role = "model" if role in ("assistant", "model") else "user"
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

                    thinking_text = item.get("thinking", "")
                    if thinking_text is None:
                        thinking_text = ""
                    
                    part: Dict[str, Any] = {
                        "text": str(thinking_text),
                        "thought": True,
                    }
                    
                    # 如果有 thoughtsignature 则添加
                    thoughtsignature = item.get("thoughtSignature")
                    if thoughtsignature:
                        part["thoughtSignature"] = thoughtsignature
                    
                    parts.append(part)
                elif item_type == "redacted_thinking":
                    if not include_thinking:
                        continue

                    thinking_text = item.get("thinking")
                    if thinking_text is None:
                        thinking_text = item.get("data", "")
                    
                    part_dict: Dict[str, Any] = {
                        "text": str(thinking_text or ""),
                        "thought": True,
                    }
                    
                    # 如果有 thoughtsignature 则添加
                    thoughtsignature = item.get("thoughtSignature")
                    if thoughtsignature:
                        part_dict["thoughtSignature"] = thoughtsignature
                    
                    parts.append(part_dict)
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
                    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "id": original_id,  # 使用原始ID，不带签名
                            "name": item.get("name"),
                            "args": item.get("input", {}) or {},
                        }
                    }

                    # 如果提取到签名则添加，否则使用占位符以满足 Gemini API 要求
                    if thoughtsignature:
                        fc_part["thoughtSignature"] = thoughtsignature
                    else:
                        fc_part["thoughtSignature"] = "skip_thought_signature_validator"

                    parts.append(fc_part)
                elif item_type == "tool_result":
                    output = _extract_tool_result_output(item.get("content"))
                    encoded_tool_use_id = item.get("tool_use_id") or ""
                    
                    # 解码获取原始ID（functionResponse不需要签名）
                    original_tool_use_id, _ = decode_tool_id_and_signature(encoded_tool_use_id)

                    # 从 tool_result 获取 name，如果没有则从映射中查找
                    func_name = item.get("name")
                    if not func_name and encoded_tool_use_id:
                        # 使用编码ID查找映射
                        tool_info = tool_use_info.get(str(encoded_tool_use_id))
                        if tool_info:
                            func_name = tool_info[0]  # 获取 name
                    if not func_name:
                        func_name = "unknown_function"
                    
                    parts.append(
                        {
                            "functionResponse": {
                                "id": original_tool_use_id,  # 使用解码后的原始ID以匹配functionCall
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
# 7. Tool Choice 转换
# ============================================================================

def convert_tool_choice_to_tool_config(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """
    将 Anthropic tool_choice 转换为 Gemini toolConfig

    Args:
        tool_choice: Anthropic 格式的 tool_choice
            - {"type": "auto"}: 模型自动决定是否使用工具
            - {"type": "any"}: 模型必须使用工具
            - {"type": "tool", "name": "tool_name"}: 模型必须使用指定工具

    Returns:
        Gemini 格式的 toolConfig，如果无效则返回 None
    """
    if not tool_choice:
        return None
    
    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        
        if choice_type == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif choice_type == "any":
            return {"functionCallingConfig": {"mode": "ANY"}}
        elif choice_type == "tool":
            tool_name = tool_choice.get("name")
            if tool_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [tool_name],
                    }
                }
    
    # 无效或不支持的 tool_choice，返回 None
    return None


# ============================================================================
# 8. Generation Config 构建
# ============================================================================

def build_generation_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 Anthropic Messages 请求构造下游 generationConfig。

    Returns:
        generation_config: 生成配置字典
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

    # 处理 extended thinking 参数 (plan mode)
    thinking = payload.get("thinking")
    is_plan_mode = False
    if thinking and isinstance(thinking, dict):
        thinking_type = thinking.get("type")
        budget_tokens = thinking.get("budget_tokens")
        
        # 如果启用了 extended thinking，设置 thinkingConfig
        if thinking_type == "enabled":
            is_plan_mode = True
            thinking_config: Dict[str, Any] = {}
            
            # 设置思考预算，默认使用较大的值以支持计划模式
            if budget_tokens is not None:
                thinking_config["thinkingBudget"] = budget_tokens
            else:
                # 默认给一个较大的思考预算以支持完整的计划生成
                thinking_config["thinkingBudget"] = 48000
            
            # 始终包含思考内容，这样才能看到计划
            thinking_config["includeThoughts"] = True
            
            config["thinkingConfig"] = thinking_config
            log.info(f"[ANTHROPIC2GEMINI] Extended thinking enabled with budget: {thinking_config['thinkingBudget']}")
        elif thinking_type == "disabled":
            # 明确禁用思考模式
            config["thinkingConfig"] = {
                "includeThoughts": False
            }
            log.info("[ANTHROPIC2GEMINI] Extended thinking explicitly disabled")

    stop_sequences = payload.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        config["stopSequences"] = config["stopSequences"] + [str(s) for s in stop_sequences]
    elif is_plan_mode:
        # Plan mode 时清空默认 stop sequences，避免过早停止
        # 默认的 stop sequences 可能会导致模型在生成计划时过早停止
        config["stopSequences"] = []
        log.info("[ANTHROPIC2GEMINI] Plan mode: cleared default stop sequences to prevent premature stopping")
    
    # 如果不是 plan mode 且没有自定义 stop_sequences，保持默认值
    # (默认值已经在 config 初始化时设置)

    return config


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
        - toolConfig: 工具调用配置 (如果有 tool_choice)
    """
    # 处理连续的system消息（兼容性模式）
    payload = await merge_system_messages(payload)

    # 提取和转换基础信息
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    
    # [CRITICAL FIX] 过滤并修复 Thinking 块签名
    # 在转换前先过滤无效的 thinking 块
    filter_invalid_thinking_blocks(messages)

    # 构建生成配置
    generation_config = build_generation_config(payload)

    # 转换消息内容（始终包含thinking块，由响应端处理）
    contents = convert_messages_to_contents(messages, include_thinking=True)
    
    # [CRITICAL FIX] 移除尾部无签名的 thinking 块
    # 对真实请求应用额外的清理
    for content in contents:
        role = content.get("role", "")
        if role == "model":  # 只处理 model/assistant 消息
            parts = content.get("parts", [])
            if isinstance(parts, list):
                remove_trailing_unsigned_thinking(parts)
    
    contents = reorganize_tool_messages(contents)

    # 转换工具
    tools = convert_tools(payload.get("tools"))
    
    # 转换 tool_choice
    tool_config = convert_tool_choice_to_tool_config(payload.get("tool_choice"))

    # 构建基础请求数据
    gemini_request = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    
    # 如果 merge_system_messages 已经添加了 systemInstruction，使用它
    if "systemInstruction" in payload:
        gemini_request["systemInstruction"] = payload["systemInstruction"]
    
    if tools:
        gemini_request["tools"] = tools
    
    # 添加 toolConfig（如果有 tool_choice）
    if tool_config:
        gemini_request["toolConfig"] = tool_config

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
            thinking_text = part.get("text", "")
            if thinking_text is None:
                thinking_text = ""
            
            block: Dict[str, Any] = {"type": "thinking", "thinking": str(thinking_text)}
            
            # 如果有 thoughtsignature 则添加
            thoughtsignature = part.get("thoughtSignature")
            if thoughtsignature:
                block["thoughtSignature"] = thoughtsignature
            
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
            thoughtsignature = part.get("thoughtSignature")
            
            # 对工具调用ID进行签名编码
            encoded_id = encode_tool_id_with_signature(original_id, thoughtsignature)
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
    
    # 只有在正常停止（STOP）且有工具调用时才设为 tool_use
    # 避免在 SAFETY、MAX_TOKENS 等情况下仍然返回 tool_use 导致循环
    if has_tool_use and finish_reason == "STOP":
        stop_reason = "tool_use"
    elif finish_reason == "MAX_TOKENS":
        stop_reason = "max_tokens"
    else:
        # 其他情况（SAFETY、RECITATION 等）默认为 end_turn
        stop_reason = "end_turn"

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
            # 检查是否是 Response 对象（错误情况）
            if isinstance(chunk, Response):
                log.warning(f"[GEMINI_TO_ANTHROPIC] 收到 Response 对象，状态码: {chunk.status_code}，直接转发错误")
                # 直接转发错误响应内容，不做格式转换
                error_content = chunk.body if isinstance(chunk.body, bytes) else chunk.body.encode('utf-8')
                yield error_content
                return

            # 记录接收到的原始chunk
            log.debug(f"[GEMINI_TO_ANTHROPIC] Raw chunk: {chunk[:200] if chunk else b''}")

            # 解析 Gemini 流式块
            if not chunk or not chunk.startswith(b"data: "):
                log.debug(f"[GEMINI_TO_ANTHROPIC] Skipping chunk (not SSE format or empty)")
                continue

            raw = chunk[6:].strip()
            if raw == b"[DONE]":
                log.debug(f"[GEMINI_TO_ANTHROPIC] Received [DONE] marker")
                break

            log.debug(f"[GEMINI_TO_ANTHROPIC] Parsing JSON: {raw[:200]}")

            try:
                data = json.loads(raw.decode('utf-8', errors='ignore'))
                log.debug(f"[GEMINI_TO_ANTHROPIC] Parsed data: {json.dumps(data, ensure_ascii=False)[:300]}")
            except Exception as e:
                log.warning(f"[GEMINI_TO_ANTHROPIC] JSON parse error: {e}")
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
                            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                        },
                    },
                )

            # 处理各种 parts
            for part in parts:
                if not isinstance(part, dict):
                    continue

                # 处理 thinking 块
                if part.get("thought") is True:
                    thinking_text = part.get("text", "")
                    thoughtsignature = part.get("thoughtSignature")
                    
                    # 检查是否需要关闭上一个块并开启新的 thinking 块
                    if current_block_type != "thinking":
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt

                        current_block_index += 1
                        current_block_type = "thinking"
                        current_thinking_signature = thoughtsignature

                        block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
                        if thoughtsignature:
                            block["thoughtSignature"] = thoughtsignature
                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": block,
                            },
                        )
                    elif thoughtsignature and thoughtsignature != current_thinking_signature:
                        # 签名变化，需要开启新的 thinking 块
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt
                        
                        current_block_index += 1
                        current_block_type = "thinking"
                        current_thinking_signature = thoughtsignature
                        
                        block_new: Dict[str, Any] = {"type": "thinking", "thinking": ""}
                        if thoughtsignature:
                            block_new["thoughtSignature"] = thoughtsignature
                        
                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": block_new,
                            },
                        )

                    # 发送 thinking 文本增量
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
                    thoughtsignature = part.get("thoughtSignature")
                    tool_id = encode_tool_id_with_signature(original_id, thoughtsignature)
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    if _anthropic_debug_enabled():
                        log.info(
                            f"[ANTHROPIC][tool_use] 处理工具调用: name={tool_name}, "
                            f"id={tool_id}, has_signature={thoughtsignature is not None}"
                        )

                    current_block_index += 1
                    # 注意：工具调用不设置 current_block_type，因为它是独立完整的块

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
                    # 工具调用块已完全关闭，current_block_type 保持为 None
                    
                    if _anthropic_debug_enabled():
                        log.info(f"[ANTHROPIC][tool_use] 工具调用块已关闭: index={current_block_index}")
                    
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
        # 只有在正常停止（STOP）且有工具调用时才设为 tool_use
        # 避免在 SAFETY、MAX_TOKENS 等情况下仍然返回 tool_use 导致循环
        if has_tool_use and finish_reason == "STOP":
            stop_reason = "tool_use"
        elif finish_reason == "MAX_TOKENS":
            stop_reason = "max_tokens"
        else:
            # 其他情况（SAFETY、RECITATION 等）默认为 end_turn
            stop_reason = "end_turn"

        if _anthropic_debug_enabled():
            log.info(
                f"[ANTHROPIC][stream_end] 流式结束: stop_reason={stop_reason}, "
                f"has_tool_use={has_tool_use}, finish_reason={finish_reason}, "
                f"input_tokens={input_tokens}, output_tokens={output_tokens}"
            )

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
                        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                    },
                },
            )
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )