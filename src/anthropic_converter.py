from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Union


DEFAULT_THINKING_BUDGET = 1024
DEFAULT_TEMPERATURE = 0.4


def get_thinking_config(thinking: Optional[Union[bool, Dict[str, Any]]]) -> Dict[str, Any]:
    """
    根据 Anthropic/Claude 请求的 thinking 参数生成下游 thinkingConfig。

    该逻辑以根目录 `converter.py` 的语义为准：
    - thinking=None：默认启用 includeThoughts，并使用默认 budget
    - thinking=bool：True 启用 / False 禁用
    - thinking=dict：{'type':'enabled'|'disabled', 'budget_tokens': int}
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


def map_claude_model_to_gemini(claude_model: str) -> str:
    """
    将 Claude 模型名映射为下游模型名（含“支持列表透传”与固定映射）。

    该逻辑以根目录 `converter.py` 为准。
    """
    supported_models = {
        "gemini-2.5-flash",
        "gemini-2.5-flash-thinking",
        "gemini-2.5-pro",
        "gemini-3-pro-low",
        "gemini-3-pro-high",
        "gemini-2.5-flash-lite",
        "gemini-2.5-flash-image",
        "claude-sonnet-4-5",
        "claude-sonnet-4-5-thinking",
        "claude-opus-4-5-thinking",
        "gpt-oss-120b-medium",
    }

    if claude_model in supported_models:
        return claude_model

    model_mapping = {
        "claude-sonnet-4.5": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20241022": "claude-sonnet-4-5",
        "claude-3-5-sonnet-20240620": "claude-sonnet-4-5",
        "claude-opus-4": "gemini-3-pro-high",
        "claude-haiku-4": "claude-haiku-4.5",
        "claude-3-haiku-20240307": "gemini-2.5-flash",
    }

    return model_mapping.get(claude_model, "claude-sonnet-4-5")


def clean_json_schema(schema: Any) -> Any:
    """
    清理 JSON Schema，移除下游不支持的字段，并把验证要求追加到 description。

    该逻辑以根目录 `converter.py` 的语义为准。
    """
    if not isinstance(schema, dict):
        return schema

    validation_fields = {
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minimum": "minimum",
        "maximum": "maximum",
        "minItems": "minItems",
        "maxItems": "maxItems",
    }
    fields_to_remove = {"$schema", "additionalProperties"}

    validations: List[str] = []
    for field, label in validation_fields.items():
        if field in schema:
            validations.append(f"{label}: {schema[field]}")

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in fields_to_remove or key in validation_fields:
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

    return cleaned


def convert_tools(anthropic_tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 Anthropic tools[] 转换为下游 tools（functionDeclarations）结构。
    """
    if not anthropic_tools:
        return None

    gemini_tools: List[Dict[str, Any]] = []
    for tool in anthropic_tools:
        name = tool.get("name")
        if not name:
            continue
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


def _extract_tool_result_output(content: Any) -> str:
    """
    从 tool_result.content 中提取输出字符串（按 converter.py 的最小语义）。
    """
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


def convert_messages_to_contents(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 Anthropic messages[] 转换为下游 contents[]（role: user/model, parts: []）。
    """
    contents: List[Dict[str, Any]] = []

    for msg in messages:
        role = msg.get("role", "user")
        gemini_role = "model" if role == "assistant" else "user"
        raw_content = msg.get("content", "")

        parts: List[Dict[str, Any]] = []
        if isinstance(raw_content, str):
            parts = [{"text": raw_content}]
        elif isinstance(raw_content, list):
            for item in raw_content:
                if not isinstance(item, dict):
                    parts.append({"text": str(item)})
                    continue

                item_type = item.get("type")
                if item_type == "thinking":
                    part: Dict[str, Any] = {"text": item.get("thinking", ""), "thought": True}
                    if "signature" in item:
                        part["thoughtSignature"] = item.get("signature")
                    parts.append(part)
                elif item_type == "text":
                    parts.append({"text": item.get("text", "")})
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
                    parts.append(
                        {
                            "functionCall": {
                                "id": item.get("id"),
                                "name": item.get("name"),
                                "args": item.get("input", {}) or {},
                            }
                        }
                    )
                elif item_type == "tool_result":
                    output = _extract_tool_result_output(item.get("content"))
                    parts.append(
                        {
                            "functionResponse": {
                                "id": item.get("tool_use_id"),
                                "name": item.get("name", ""),
                                "response": {"output": output},
                            }
                        }
                    )
                else:
                    parts.append({"text": json.dumps(item, ensure_ascii=False)})
        else:
            parts = [{"text": str(raw_content)}]

        contents.append({"role": gemini_role, "parts": parts})

    return contents


def build_system_instruction(system: Any) -> Optional[Dict[str, Any]]:
    """
    将 Anthropic system 字段转换为下游 systemInstruction。
    """
    if not system:
        return None

    parts: List[Dict[str, Any]] = []
    if isinstance(system, str):
        parts.append({"text": system})
    elif isinstance(system, list):
        for item in system:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append({"text": item.get("text", "")})
    else:
        parts.append({"text": str(system)})

    if not parts:
        return None

    return {"role": "user", "parts": parts}


def build_generation_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据 Anthropic Messages 请求构造下游 generationConfig。

    默认值与 `converter.py` 保持一致，并在此基础上兼容 stop_sequences。
    """
    config: Dict[str, Any] = {
        "topP": 1,
        "topK": 40,
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

    config["thinkingConfig"] = get_thinking_config(payload.get("thinking"))
    return config


def convert_anthropic_request_to_antigravity_components(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Anthropic Messages 请求转换为构造下游请求所需的组件。

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

    contents = convert_messages_to_contents(messages)
    system_instruction = build_system_instruction(payload.get("system"))
    tools = convert_tools(payload.get("tools"))
    generation_config = build_generation_config(payload)

    return {
        "model": model,
        "contents": contents,
        "system_instruction": system_instruction,
        "tools": tools,
        "generation_config": generation_config,
    }

