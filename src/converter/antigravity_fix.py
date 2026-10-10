"""
Antigravity Format Utilities - 独立的 Antigravity 请求处理和转换工具

设计要点:
  * 每个模型家族在 resolve_profile 里有独立的一行配置 (后端模型 ID / 思考方式 / 是否禁预填充)
  * 不修改调用方传入的 request (generationConfig / thinkingConfig / contents 均先复制)
────────────────────────────────────────────────────────────────
"""
import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from src.converter.thoughtSignature_fix import SKIP_THOUGHT_SIGNATURE_VALIDATOR

# ==================== 常量配置 ====================

# 图片生成基础模型名（上游变更时仅需改这里）
IMAGE_BASE_MODEL = "gemini-3.1-flash-image"

# 图片生成支持的宽高比
SUPPORTED_ASPECT_RATIOS = [
    (1, 1), (2, 3), (3, 2), (3, 4), (4, 3),
    (4, 5), (5, 4), (9, 16), (16, 9), (21, 9),
]

# ==================== Gemini API 安全配置 ====================

DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_IMAGE_HATE", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_IMAGE_HARASSMENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_JAILBREAK", "threshold": "OFF"},
]

MAX_OUTPUT_TOKENS = 64000
TOP_K = 64
_CLAUDE_THINKING_SIGNATURE = "skip_thought_signature_validator"  # 官方文档推荐的虚拟签名


def _append_schema_hint(schema: Dict[str, Any], hint: str) -> None:
    """Move fragile validation details into description instead of sending them raw."""
    if not hint:
        return
    desc = schema.get("description")
    schema["description"] = f"{desc} ({hint})" if desc else hint


def _resolve_schema_ref(ref: str, root_schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return None

    node: Any = root_schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]

    return node if isinstance(node, dict) else None


def _clean_parameters_json_schema(
    schema: Any,
    root_schema: Optional[Dict[str, Any]] = None,
    visited: Optional[set] = None,
) -> Any:
    """Clean a tool schema for Code Assist's parametersJsonSchema field."""
    if isinstance(schema, list):
        return [_clean_parameters_json_schema(item, root_schema, visited) for item in schema]
    if not isinstance(schema, dict):
        return schema

    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()

    schema_id = id(schema)
    if schema_id in visited:
        return {"type": "object", "description": "circular reference"}
    visited.add(schema_id)

    ref_key = "$ref" if "$ref" in schema else ("ref" if "ref" in schema else None)
    if ref_key:
        resolved = _resolve_schema_ref(schema[ref_key], root_schema)
        if resolved:
            merged = dict(resolved)
            for key in ("description", "default"):
                if key in schema:
                    merged[key] = schema[key]
            schema = merged

    if "allOf" in schema:
        result: Dict[str, Any] = {}
        for item in schema.get("allOf") or []:
            cleaned_item = _clean_parameters_json_schema(item, root_schema, visited)
            if not isinstance(cleaned_item, dict):
                continue
            if "properties" in cleaned_item:
                result.setdefault("properties", {}).update(cleaned_item["properties"])
            if "required" in cleaned_item:
                result.setdefault("required", []).extend(cleaned_item["required"])
            for key, value in cleaned_item.items():
                if key not in ("properties", "required"):
                    result[key] = value
        for key, value in schema.items():
            if key not in ("allOf", "properties", "required"):
                result[key] = value
            elif key in ("properties", "required") and key not in result:
                result[key] = value
    else:
        result = dict(schema)

    if result.get("nullable") is True:
        _append_schema_hint(result, "nullable")

    if "type" in result:
        type_value = result["type"]
        if isinstance(type_value, list):
            non_null_types = [
                str(t).lower()
                for t in type_value
                if isinstance(t, str) and t.lower() != "null"
            ]
            if non_null_types:
                result["type"] = non_null_types[0]
                if any(str(t).lower() == "null" for t in type_value):
                    _append_schema_hint(result, "nullable")
            else:
                result["type"] = "string"
        elif isinstance(type_value, str):
            lower_type = type_value.lower()
            if lower_type in {"string", "number", "integer", "boolean", "array", "object"}:
                result["type"] = lower_type
            elif lower_type == "null":
                result["type"] = "string"
                _append_schema_hint(result, "nullable")
            else:
                result.pop("type", None)

    if "anyOf" in result or "oneOf" in result:
        union_key = "anyOf" if "anyOf" in result else "oneOf"
        union_items = result.get(union_key) or []
        cleaned_items = [
            item for item in (
                _clean_parameters_json_schema(item, root_schema, visited)
                for item in union_items
            )
            if isinstance(item, dict)
        ]
        enum_values = [
            item.get("const")
            for item in union_items
            if isinstance(item, dict) and item.get("const") not in ("", None)
        ]
        if enum_values and len(enum_values) == len(union_items):
            result["type"] = "string"
            result["enum"] = [str(v) for v in enum_values]
        else:
            preferred = next(
                (
                    item for item in cleaned_items
                    if item.get("type") in ("object", "array") or item.get("properties")
                ),
                None,
            )
            if preferred is None:
                preferred = next((item for item in cleaned_items if item.get("type") or item.get("enum")), None)
            if preferred:
                original_description = result.get("description")
                result.update(preferred)
                if original_description:
                    _append_schema_hint(result, original_description)
        result.pop("anyOf", None)
        result.pop("oneOf", None)

    if result.get("type") == "array":
        items = result.get("items")
        if isinstance(items, list):
            if items:
                result["items"] = _clean_parameters_json_schema(items[0], root_schema, visited)
                _append_schema_hint(result, "tuple schema simplified")
            else:
                result.pop("items", None)
        elif isinstance(items, dict):
            result["items"] = _clean_parameters_json_schema(items, root_schema, visited)

    validation_keys = {
        "default", "minLength", "maxLength", "minimum", "maximum",
        "minItems", "maxItems", "pattern", "format", "uniqueItems",
    }
    for key in list(result.keys()):
        if key in validation_keys:
            value = result.pop(key)
            if value not in (None, "", {}, []):
                _append_schema_hint(result, f"{key}: {json.dumps(value, ensure_ascii=False)}")

    unsupported_keys = {
        "title", "$schema", "$id", "$ref", "ref", "strict", "nullable",
        "exclusiveMaximum", "exclusiveMinimum", "additionalProperties",
        "allOf", "anyOf", "oneOf", "$defs", "definitions", "example",
        "examples", "readOnly", "writeOnly", "const", "additionalItems",
        "contains", "patternProperties", "dependencies", "propertyNames",
        "if", "then", "else", "contentEncoding", "contentMediaType",
    }
    for key in list(result.keys()):
        if key in unsupported_keys or key.startswith("x-"):
            del result[key]

    nullable_props = set()
    if isinstance(result.get("properties"), dict):
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            if isinstance(prop_schema, dict):
                prop_type = prop_schema.get("type")
                if (
                    prop_schema.get("nullable") is True
                    or (
                        isinstance(prop_type, list)
                        and any(str(t).lower() == "null" for t in prop_type)
                    )
                ):
                    nullable_props.add(prop_name)
            cleaned_props[prop_name] = _clean_parameters_json_schema(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props

    if "properties" in result and "type" not in result:
        result["type"] = "object"

    if isinstance(result.get("required"), list):
        prop_names = set(result.get("properties", {}).keys()) if isinstance(result.get("properties"), dict) else None
        required = []
        for item in result["required"]:
            if not isinstance(item, str):
                continue
            if prop_names is not None and item not in prop_names:
                continue
            if item in nullable_props:
                continue
            if item not in required:
                required.append(item)
        if required:
            result["required"] = required
        else:
            result.pop("required", None)

    return result


def _normalize_tools_for_internal_api(tools: Any) -> Any:
    if not isinstance(tools, list):
        return tools

    normalized_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            normalized_tools.append(tool)
            continue

        normalized_tool = tool.copy()
        declarations = normalized_tool.get("functionDeclarations")
        if declarations is None:
            declarations = normalized_tool.get("function_declarations")
        if isinstance(declarations, list):
            normalized_declarations = []
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    normalized_declarations.append(declaration)
                    continue

                normalized_declaration = declaration.copy()
                if "parametersJsonSchema" in normalized_declaration:
                    schema = normalized_declaration["parametersJsonSchema"]
                elif "parameters_json_schema" in normalized_declaration:
                    schema = normalized_declaration.pop("parameters_json_schema", None)
                else:
                    schema = normalized_declaration.pop("parameters", None)

                normalized_declaration.pop("parameters", None)
                normalized_declaration.pop("parameters_json_schema", None)
                if schema not in (None, {}, []):
                    normalized_declaration["parametersJsonSchema"] = _clean_parameters_json_schema(schema)
                else:
                    normalized_declaration.pop("parametersJsonSchema", None)

                normalized_declarations.append(normalized_declaration)

            normalized_tool.pop("function_declarations", None)
            normalized_tool["functionDeclarations"] = normalized_declarations

        normalized_tools.append(normalized_tool)

    return normalized_tools


def _ensure_empty_tool_schema_for_claude(tools: Any, model_name: str, mode: str = "antigravity") -> Any:
    if not isinstance(tools, list):
        return tools

    is_claude = "claude" in (model_name or "").lower()

    if is_claude:
        normalized_tools = []
        for tool in tools:
            if not isinstance(tool, dict):
                normalized_tools.append(tool)
                continue

            normalized_tool = tool.copy()

            schema = {"type": "object", "properties": {}}
            name = ""
            description = ""

            # Extract schema from either format
            custom_tool = normalized_tool.get("custom")
            if isinstance(custom_tool, dict):
                schema = custom_tool.get("input_schema") or custom_tool.get("inputSchema") or schema
                name = custom_tool.get("name", "")
                description = custom_tool.get("description", "")
            else:
                declarations = normalized_tool.get("functionDeclarations") or normalized_tool.get("function_declarations")
                if isinstance(declarations, list) and declarations and isinstance(declarations[0], dict):
                    decl = declarations[0]
                    schema = (
                        decl.get("parametersJsonSchema") or
                        decl.get("parameters_json_schema") or
                        decl.get("parameters") or schema
                    )
                    name = decl.get("name", "")
                    description = decl.get("description", "")

            # For ALL Claude models, try outputting functionDeclarations with parameters!
            # If Google's backend expects parameters to translate to input_schema, this will fix the Field required error.
            normalized_tools.append({
                "functionDeclarations": [{
                    "name": name,
                    "description": description,
                    "parameters": schema
                }]
            })

        return normalized_tools

    # 对于 Gemini 模型：
    # 后端需要标准的 functionDeclarations 格式。
    # 并且，必须只能使用 "parameters" 字段，如果使用了 "parametersJsonSchema"，
    # 会报 "parameters_json_schema must not be set when parameters is set" 等冲突错误
    normalized_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            normalized_tools.append(tool)
            continue

        normalized_tool = tool.copy()

        # 1. 如果包含 Anthropic 原生的 "custom" 工具格式，将其转换为 Gemini 的 functionDeclarations 格式
        custom_tool = normalized_tool.get("custom")
        if isinstance(custom_tool, dict):
            schema = custom_tool.get("input_schema") or custom_tool.get("inputSchema")
            if schema in (None, {}, []):
                schema = {"type": "object", "properties": {}}
            declaration = {
                "name": custom_tool.get("name", ""),
                "description": custom_tool.get("description", ""),
                "parameters": schema
            }
            normalized_tools.append({
                "functionDeclarations": [declaration]
            })
            continue

        # 2. 如果包含标准的 functionDeclarations 格式，确保参数不为空且只使用 parameters 字段
        declarations = normalized_tool.get("functionDeclarations") or normalized_tool.get("function_declarations")
        if isinstance(declarations, list):
            normalized_declarations = []
            for declaration in declarations:
                if not isinstance(declaration, dict):
                    normalized_declarations.append(declaration)
                    continue

                normalized_declaration = declaration.copy()
                # 兼容不同字段格式并归一化到 parameters
                schema = (
                    normalized_declaration.get("parameters")
                    or normalized_declaration.get("parametersJsonSchema")
                    or normalized_declaration.get("parameters_json_schema")
                )

                if schema in (None, {}, []):
                    schema = {"type": "object", "properties": {}}

                # 只保留 parameters 字段，防止与 parametersJsonSchema 冲突
                normalized_declaration["parameters"] = schema
                normalized_declaration.pop("parametersJsonSchema", None)
                normalized_declaration.pop("parameters_json_schema", None)

                normalized_declarations.append(normalized_declaration)

            normalized_tool.pop("function_declarations", None)
            normalized_tool["functionDeclarations"] = normalized_declarations

        normalized_tools.append(normalized_tool)

    return normalized_tools


def _should_skip_thought_signature(part: Dict[str, Any], model_name: str) -> bool:
    if "claude" in (model_name or "").lower():
        return False

    return (
        "functionCall" in part
        or "function_call" in part
        or part.get("thought") is True
        or "thoughtSignature" in part
        or "thought_signature" in part
    )


def _normalize_part_thought_signature(part: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    normalized = part.copy()
    if _should_skip_thought_signature(normalized, model_name):
        normalized.pop("thought_signature", None)
        normalized["thoughtSignature"] = SKIP_THOUGHT_SIGNATURE_VALIDATOR
    return normalized


def _ensure_tool_call_ids(contents: Any, model_name: str) -> Any:
    """
    确保 functionCall/functionResponse 携带 id 字段 (仅 Claude)。

    Antigravity 后端在目标模型为 Claude 时，会将 Gemini 的
    functionCall/functionResponse 内部转换为 Anthropic 的
    tool_use/tool_result，而后者的 id 是必填字段。原生 Gemini 请求
    可能不带 id，因此这里按 name 补全缺失的 id，保证同一次调用的
    functionCall 与 functionResponse 使用相同 id。不修改入参。
    """
    if "claude" not in (model_name or "").lower():
        return contents
    if not isinstance(contents, list):
        return contents

    pending_ids_by_name: Dict[str, list] = {}
    result = []

    for content in contents:
        if not isinstance(content, dict) or not isinstance(content.get("parts"), list):
            result.append(content)
            continue

        new_parts = []
        for part in content["parts"]:
            if isinstance(part, dict):
                fc = part.get("functionCall")
                fr = part.get("functionResponse")
                if isinstance(fc, dict):
                    if not fc.get("id"):
                        fc = {**fc, "id": f"toolu_{uuid.uuid4().hex}"}
                        part = {**part, "functionCall": fc}
                    pending_ids_by_name.setdefault(fc.get("name"), []).append(fc["id"])
                elif isinstance(fr, dict):
                    queue = pending_ids_by_name.get(fr.get("name")) or []
                    if fr.get("id"):
                        if fr["id"] in queue:
                            queue.remove(fr["id"])
                    else:
                        new_id = queue.pop(0) if queue else f"toolu_{uuid.uuid4().hex}"
                        part = {**part, "functionResponse": {**fr, "id": new_id}}
            new_parts.append(part)

        result.append({**content, "parts": new_parts})

    return result


def _parse_size_to_image_config(size_str: str) -> Dict[str, str]:
    """
    解析用户传入的 size 参数为 Gemini imageConfig 参数

    支持格式: "1024x1536", "1024*1536", "1024X1536"

    Returns:
        包含 aspectRatio 和/或 imageSize 的字典
    """
    import re

    config = {}
    size_str = size_str.strip()

    match = re.match(r"^(\d+)\s*[xX*×]\s*(\d+)$", size_str)
    if not match:
        return config

    width, height = int(match.group(1)), int(match.group(2))

    if width <= 0 or height <= 0:
        return config

    # 计算最接近的支持宽高比
    target_ratio = width / height
    best_ratio = None
    best_diff = float("inf")
    for w, h in SUPPORTED_ASPECT_RATIOS:
        diff = abs(target_ratio - w / h)
        if diff < best_diff:
            best_diff = diff
            best_ratio = f"{w}:{h}"
    if best_ratio:
        config["aspectRatio"] = best_ratio

    # 根据最大边长确定 imageSize（使用最接近的档位）
    max_dim = max(width, height)
    if max_dim <= 1280:
        config["imageSize"] = "1K"
    elif max_dim <= 2560:
        config["imageSize"] = "2K"
    else:
        config["imageSize"] = "4K"

    return config


def prepare_image_generation_request(
    request_body: Dict[str, Any],
    model: str
) -> Dict[str, Any]:
    """
    图像生成模型请求体后处理

    支持三种方式指定图片参数（优先级从高到低）:
    1. size 参数: 如 "1024x1536"，自动计算 aspectRatio 和 imageSize
    2. 模型名后缀: 如 -4k, -2k, -16x9, -1x1
    3. 默认值: 不设置额外参数
    """
    request_body = request_body.copy()
    model_lower = model.lower()

    # 优先使用 size 参数
    size_str = request_body.pop("size", None)
    if size_str:
        image_config = _parse_size_to_image_config(size_str)
    else:
        # 从模型名后缀解析
        image_size = "4K" if "-4k" in model_lower else "2K" if "-2k" in model_lower else None

        aspect_ratio = None
        for suffix, ratio in [
            ("-21x9", "21:9"), ("-16x9", "16:9"), ("-9x16", "9:16"),
            ("-4x3", "4:3"), ("-3x4", "3:4"), ("-1x1", "1:1")
        ]:
            if suffix in model_lower:
                aspect_ratio = ratio
                break

        image_config = {}
        if aspect_ratio:
            image_config["aspectRatio"] = aspect_ratio
        if image_size:
            image_config["imageSize"] = image_size

    request_body["model"] = IMAGE_BASE_MODEL  # 统一使用基础模型名
    request_body["generationConfig"] = {
        "candidateCount": 1,
        "imageConfig": image_config
    }

    # 移除不需要的字段
    for key in ("systemInstruction", "tools", "toolConfig"):
        request_body.pop(key, None)

    return request_body


# ==================== 模型配置 (关键词匹配) ====================

@dataclass(frozen=True)
class ModelProfile:
    family: str                     # "gemini3" | "gemini25" | "claude" | "image" | "other"
    upstream: str                   # 实际发给 Antigravity 后端的模型 ID
    thinking: str = "none"          # "route" | "budget" | "none"，见 _apply_thinking
    thinking_budget: int = 1024     # 仅 thinking == "budget" 时使用
    no_prefill: bool = False        # True: 请求不能以 model 消息结尾


# 精确别名: 客户端模型名 -> 后端真实 ID
_UPSTREAM_ALIASES = {
    "gemini-3.1-pro-high": "gemini-pro-agent",
}

# 不支持预填充的 Gemini 3.x Flash (3.5 / 3.6 / 3.7 / 3.8)
_NO_PREFILL_GEMINI3 = re.compile(r"gemini-3\.[5-8]-flash")


def resolve_profile(model: str) -> ModelProfile:
    """
    按关键词匹配模型，自上而下第一个命中的规则生效:

      image 图片生成，走独立路径
      claude-opus-4-6   映射到 claude-opus-4-6-thinking
      claude-sonnet-4-6-thinking    映射到 claude-sonnet-4-6；不支持预填充
      claude (其他)  原样透传；不支持预填充
      gemini-3* / *-agent   思考深度由模型 ID (-low/-medium/-high/-tiered/-extra-low) 决定
      gemini (其他, 2.5 等) 名字含 think 才发 thinkingBudget
      其他  原样透传
    """
    model = model or ""
    lower = model.lower()
    think = "budget" if "think" in lower else "none"

    if "image" in lower:
        return ModelProfile("image", model)

    if "claude" in lower:
        upstream = model
        if "claude-opus-4-6" in lower:
            upstream = "claude-opus-4-6-thinking"
        elif "claude-sonnet-4-6-thinking" in lower:
            upstream = "claude-sonnet-4-6"
        return ModelProfile("claude", upstream, think, no_prefill=True)

    if "gemini-3" in lower or lower.endswith("-agent"):
        return ModelProfile(
            "gemini3",
            _UPSTREAM_ALIASES.get(lower, model),
            "route",
            no_prefill=bool(_NO_PREFILL_GEMINI3.search(lower)),
        )

    if "gemini" in lower:
        return ModelProfile("gemini25", model, think)

    return ModelProfile("other", model, think)


# ==================== 模型特性辅助函数 ====================

def is_thinking_model(model_name: str) -> bool:
    """检查是否为思考模型 (模型名包含 think，仅为兼容保留)"""
    return "think" in model_name.lower()


def _apply_thinking(profile: ModelProfile, generation_config: Dict[str, Any], return_thoughts: bool) -> None:
    """按模型 profile 写入 thinkingConfig (就地修改 generation_config，thinkingConfig 先复制)"""
    if profile.thinking == "none":
        return

    thinking_config = dict(generation_config.get("thinkingConfig") or {})
    thinking_config.pop("thinkingLevel", None)
    if profile.thinking == "route":
        # 思考深度由模型 ID 决定，level/budget 会与之冲突
        thinking_config.pop("thinkingBudget", None)
    else:  # "budget"
        thinking_config["thinkingBudget"] = profile.thinking_budget
    thinking_config["includeThoughts"] = return_thoughts
    generation_config["thinkingConfig"] = thinking_config


# ==================== contents 处理 ====================

def _clean_contents(contents: List[Any], model_name: str) -> List[Any]:
    """过滤空 part、规范 thoughtSignature、修正 text 字段类型、丢弃没有有效 part 的 content"""
    cleaned_contents = []
    for content in contents:
        if not (isinstance(content, dict) and "parts" in content):
            cleaned_contents.append(content)
            continue

        valid_parts = []
        for part in content["parts"]:
            if not isinstance(part, dict):
                continue

            # 必须先归一化 text（rstrip 可能把 "\n" 之类削成 ""）再做有效性校验，
            # 否则纯空白 text 先通过校验、后变空串，发给上游会转成缺少 text 字段的
            # text 块（Claude 报 messages.N.content.M.text.text: Field required）
            if "text" in part:
                part = part.copy()  # copy-on-write，避免原地修改调用方的 contents
                text_value = part["text"]
                if isinstance(text_value, list):
                    # 元素可能是 {"type":"text","text":"..."}，不能直接 str(dict)，否则会污染 model 历史
                    print(f"[ANTIGRAVITY_FIX] text 字段是列表，自动合并: {text_value}", flush=True)
                    text_parts = []
                    for t in text_value:
                        if isinstance(t, dict) and "text" in t:
                            text_parts.append(str(t["text"]))
                        elif isinstance(t, str):
                            text_parts.append(t)
                        elif t is not None:
                            text_parts.append(str(t))
                    part["text"] = " ".join(text_parts)
                elif isinstance(text_value, str):
                    part["text"] = text_value.rstrip()
                else:
                    print(f"[ANTIGRAVITY_FIX] text 字段类型异常 ({type(text_value)}), 转为字符串: {text_value}", flush=True)
                    part["text"] = str(text_value)

                if part["text"] == "":
                    # part 还有其他有效字段（如 functionCall）时仅去掉 text 字段
                    part.pop("text", None)

            # thought 字段可以为空，其余字段至少要有一个非空值
            has_valid_value = any(
                value not in (None, "", {}, [])
                for key, value in part.items()
                if key != "thought"
            )
            if not has_valid_value:
                print(f"[ANTIGRAVITY_FIX] 移除空的或无效的 part: {part}", flush=True)
                continue

            part = _normalize_part_thought_signature(part, model_name)

            valid_parts.append(part)

        if valid_parts:
            cleaned_content = content.copy()
            cleaned_content["parts"] = valid_parts
            cleaned_contents.append(cleaned_content)
        else:
            print(f"[ANTIGRAVITY_FIX] 跳过没有有效 parts 的 content: {content.get('role')}", flush=True)
    return cleaned_contents


def _strip_trailing_model_turns(contents: List[Any]) -> Tuple[List[Any], int]:
    """循环移除末尾的 model 消息，保证以用户消息结尾"""
    contents = list(contents)
    removed = 0
    while contents and isinstance(contents[-1], dict) and contents[-1].get("role") == "model":
        contents.pop()
        removed += 1
    return contents, removed


def _prepare_claude_history(contents: List[Any], generation_config: Dict[str, Any]) -> List[Any]:
    """
    Claude 专属历史处理:
    - 含工具调用 (MCP 场景): 移除 thinkingConfig，避免失效
    - 否则: 给最后一条 model 消息补一个 thinking 块 (虚拟签名跳过验证)
    """
    has_tool_calls = any(
        isinstance(content, dict)
        and any(
            isinstance(part, dict) and ("functionCall" in part or "function_call" in part)
            for part in content.get("parts", []) or []
        )
        for content in contents
    )
    if has_tool_calls:
        print("[ANTIGRAVITY] 检测到工具调用（MCP场景），移除 thinkingConfig 避免失效", flush=True)
        generation_config.pop("thinkingConfig", None)
        return contents

    contents = list(contents)
    for i in range(len(contents) - 1, -1, -1):
        content = contents[i]
        if isinstance(content, dict) and content.get("role") == "model":
            parts = content.get("parts", []) or []
            first = parts[0] if parts else None
            if not (isinstance(first, dict) and ("thought" in first or "thoughtSignature" in first)):
                thinking_part = {"text": "...", "thoughtSignature": _CLAUDE_THINKING_SIGNATURE}
                contents[i] = {**content, "parts": [thinking_part] + list(parts)}
            break
    return contents


# ==================== 统一的 Antigravity 请求后处理 ====================

async def normalize_antigravity_request(
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """
    规范化 Antigravity 请求

    处理逻辑:
    1. 按模型名取 profile (关键词匹配)
    2. 图片模型走独立路径
    3. 模型映射 + thinkingConfig (按 profile.thinking)
    4. contents 清理 -> 预填充处理 -> Claude 专属处理 -> 工具调用 id
    5. 工具 schema 规范化
    6. 公共参数 (safetySettings / maxOutputTokens / topK)

    Args:
        request: 原始请求字典 (不会被修改)

    Returns:
        规范化后的请求
    """
    result = request.copy()
    model = result.get("model", "")
    generation_config = (result.get("generationConfig") or {}).copy()

    # 项目固定为 True（原版从 config 读取 return_thoughts，本项目无此配置项）
    return_thoughts = True

    profile = resolve_profile(model)

    # 图片模型走独立的图片生成处理路径
    if profile.family == "image":
        return prepare_image_generation_request(result, model)

    upstream = profile.upstream
    result["model"] = upstream

    # ========== 1. 思考设置 ==========
    _apply_thinking(profile, generation_config, return_thoughts)

    # ========== 2. contents ==========
    if "contents" in result:
        contents = _clean_contents(result["contents"], upstream)

        if profile.no_prefill:
            contents, removed = _strip_trailing_model_turns(contents)
            if removed:
                print(f"[ANTIGRAVITY] {upstream} 不支持预填充，移除了 {removed} 条末尾 model 消息", flush=True)

        if profile.family == "claude":
            contents = _prepare_claude_history(contents, generation_config)

        result["contents"] = _ensure_tool_call_ids(contents, upstream)

    # ========== 3. 工具 ==========
    if "tools" in result:
        tools = _normalize_tools_for_internal_api(result.get("tools"))
        # Claude: functionDeclarations + parameters；Gemini: 只保留 parameters，避免与 parametersJsonSchema 冲突
        result["tools"] = _ensure_empty_tool_schema_for_claude(tools, upstream, "antigravity")

    # ========== 公共处理 ==========
    generation_config.pop("presencePenalty", None)
    generation_config.pop("frequencyPenalty", None)
    generation_config.pop("stopSequences", None)

    result["safetySettings"] = DEFAULT_SAFETY_SETTINGS

    if generation_config:
        generation_config["maxOutputTokens"] = MAX_OUTPUT_TOKENS
        generation_config["topK"] = TOP_K
        result["generationConfig"] = generation_config

    return result


# ==================== 请求构建 & Antigravity payload 准备 ====================


def build_gemini_request(
    model: str,
    contents: Any,
    generation_config: Optional[Dict[str, Any]] = None,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[list] = None,
    tool_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建 Gemini 请求格式（按需添加可选字段）。

    采用“存在才添加”而非“先全量再排除”的策略，避免把 None/空字段
    透传给上游导致不必要的校验问题。
    """
    gemini_request: Dict[str, Any] = {
        "model": model,
        "contents": contents,
        "generationConfig": generation_config or {},
    }
    if system_instruction:
        gemini_request["systemInstruction"] = system_instruction
    if tools:
        gemini_request["tools"] = tools
    if tool_config:
        gemini_request["toolConfig"] = tool_config
    return gemini_request


def _extract_first_user_text(request_payload: Dict[str, Any]) -> str:
    contents = request_payload.get("contents", [])
    if not isinstance(contents, list):
        return ""
    for content in contents:
        if not isinstance(content, dict) or content.get("role") != "user":
            continue
        parts = content.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if isinstance(part, dict) and part.get("text"):
                return str(part["text"])
    return ""


def _generate_stable_session_id(request_payload: Dict[str, Any]) -> str:
    first_user_text = _extract_first_user_text(request_payload)
    if first_user_text:
        digest = hashlib.sha256(first_user_text.encode("utf-8")).digest()
        value = int.from_bytes(digest[:8], "big") & 0x7FFFFFFFFFFFFFFF
        return f"-{value}"

    value = uuid.uuid4().int % 9_000_000_000_000_000_000
    return f"-{value}"


def _ensure_antigravity_session_id(payload: Dict[str, Any], model_name: str) -> None:
    if "image" in (model_name or "").lower():
        return

    request_payload = payload.get("request")
    if not isinstance(request_payload, dict):
        return

    if request_payload.get("sessionId"):
        return

    request_payload["sessionId"] = _generate_stable_session_id(request_payload)


def _build_labels(model_name: str, trajectory_id: str, step: int) -> Dict[str, str]:
    used_claude = "claude" in (model_name or "").lower()
    return {
        "last_step_index": str(step),
        "model_enum": model_name or "",
        "trajectory_id": trajectory_id,
        "used_claude": str(used_claude).lower(),
        "used_claude_conservative": str(used_claude).lower(),
    }


def prepare_antigravity_payload(payload: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    """
    对 Antigravity 上游 payload 做最终的格式准备：
    - 设置 userAgent / requestType / requestId
    - 确保 sessionId / labels
    - 移除 safetySettings（由 normalize 阶段统一覆盖）
    - 规范化 toolConfig
    """
    payload = copy.deepcopy(payload)
    if "image" in (model_name or "").lower():
        payload["requestType"] = "image_gen"
        payload.setdefault(
            "requestId",
            f"image_gen/{int(datetime.now(timezone.utc).timestamp() * 1000)}/{uuid.uuid4()}/12",
        )
    else:
        payload["requestType"] = "agent"
        trajectory_id = str(uuid.uuid4())
        step = 1
        payload.setdefault(
            "requestId",
            f"agent/{uuid.uuid4()}/{int(datetime.now(timezone.utc).timestamp() * 1000)}/{trajectory_id}/{step}",
        )

    request_payload = payload.get("request")
    if not isinstance(request_payload, dict):
        return payload

    _ensure_antigravity_session_id(payload, model_name)
    request_payload.pop("safetySettings", None)

    if "image" not in (model_name or "").lower() and not request_payload.get("labels"):
        request_payload["labels"] = _build_labels(
            model_name,
            str(request_payload.get("sessionId") or trajectory_id),
            step,
        )

    # tools 已在 normalize_antigravity_request 阶段统一规范化，此处不再重复处理

    if "image" not in (model_name or "").lower():
        tool_config = request_payload.get("toolConfig")
        if not isinstance(tool_config, dict):
            tool_config = {}
            request_payload["toolConfig"] = tool_config

        function_config = tool_config.get("functionCallingConfig")
        if not isinstance(function_config, dict):
            function_config = {}
            tool_config["functionCallingConfig"] = function_config

        function_config["mode"] = "VALIDATED"

    return payload


async def build_antigravity_payload(
    model: str,
    contents: Any,
    generation_config: Optional[Dict[str, Any]] = None,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[list] = None,
    tool_config: Optional[Dict[str, Any]] = None,
    *,
    project_id: str = "",
    enable_credit: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """
    一站式构建 Antigravity 上游 payload。

    串联三个步骤：
      build_gemini_request → normalize_antigravity_request → prepare_antigravity_payload
    并合并 client 侧 build_request_payload 的 project / enableCreditTypes 逻辑。

    Returns:
        (final_model, payload) — 最终模型名（经过映射）与可直接发送的上游 payload。
    """
    gemini_request = build_gemini_request(
        model, contents, generation_config, system_instruction, tools, tool_config
    )
    normalized = await normalize_antigravity_request(gemini_request)
    final_model = normalized.pop("model")

    payload: Dict[str, Any] = {
        "model": final_model,
        "project": project_id,
        "request": normalized,
    }
    if enable_credit:
        payload["enabledCreditTypes"] = ["GOOGLE_ONE_AI"]

    return final_model, prepare_antigravity_payload(payload, final_model)
