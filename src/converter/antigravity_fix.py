"""
Antigravity Format Utilities - 独立的 Antigravity 请求处理和转换工具
从 gemini_fix.py 中拆分出来，专供 src/router/antigravity 使用
────────────────────────────────────────────────────────────────
"""
import json
import uuid
from typing import Any, Dict, Optional

from log import log
from src.converter.thoughtSignature_fix import SKIP_THOUGHT_SIGNATURE_VALIDATOR

# ==================== Gemini API 配置 ====================

DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_HATE", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_JAILBREAK", "threshold": "BLOCK_NONE"},
]

LITE_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
]


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
    确保 functionCall/functionResponse 携带 id 字段。

    Antigravity 后端在目标模型为 Claude 时，会将 Gemini 的
    functionCall/functionResponse 内部转换为 Anthropic 的
    tool_use/tool_result，而后者的 id 是必填字段。原生 Gemini 请求
    可能不带 id（Gemini API 本身不要求），因此这里按 name 补全缺失的 id，
    保证同一次调用的 functionCall 与 functionResponse 使用相同 id。
    """
    if "claude" not in (model_name or "").lower():
        return contents
    if not isinstance(contents, list):
        return contents

    pending_ids_by_name: Dict[str, list] = {}

    for content in contents:
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []) or []:
            if not isinstance(part, dict):
                continue

            fc = part.get("functionCall")
            if isinstance(fc, dict) and not fc.get("id"):
                new_id = f"toolu_{uuid.uuid4().hex}"
                fc["id"] = new_id
                pending_ids_by_name.setdefault(fc.get("name"), []).append(new_id)
                continue

            fr = part.get("functionResponse")
            if isinstance(fr, dict) and not fr.get("id"):
                name = fr.get("name")
                queue = pending_ids_by_name.get(name)
                if queue:
                    fr["id"] = queue.pop(0)
                else:
                    fr["id"] = f"toolu_{uuid.uuid4().hex}"

    return contents


SUPPORTED_ASPECT_RATIOS = [
    (1, 1), (2, 3), (3, 2), (3, 4), (4, 3),
    (4, 5), (5, 4), (9, 16), (16, 9), (21, 9),
]


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

    Args:
        request_body: 原始请求体
        model: 模型名称

    Returns:
        处理后的请求体
    """
    request_body = request_body.copy()
    model_lower = model.lower()

    # 优先使用 size 参数
    size_str = request_body.pop("size", None)
    if size_str:
        image_config = _parse_size_to_image_config(size_str)
        log.debug(f"[IMAGE] 从 size 参数 '{size_str}' 解析: {image_config}")
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

    request_body["model"] = "gemini-3.1-flash-image"  # 统一使用基础模型名
    request_body["generationConfig"] = {
        "candidateCount": 1,
        "imageConfig": image_config
    }

    # 移除不需要的字段
    for key in ("systemInstruction", "tools", "toolConfig"):
        request_body.pop(key, None)

    return request_body


# ==================== 模型特性辅助函数 ====================

def is_thinking_model(model_name: str) -> bool:
    """检查是否为思考模型 (模型名包含 think)"""
    return "think" in model_name.lower()


def _normalize_antigravity_request(
    result: Dict[str, Any],
    model: str,
    generation_config: Dict[str, Any],
    return_thoughts: bool,
) -> str:
    """antigravity 模式专属处理，返回处理后的模型名"""
    # 1. 思考模型处理：antigravity 模型名不带 -high/-low/-search 等后缀，
    # 仅通过 "think" 是否出现在模型名中判断，命中则使用默认思考预算。
    thinking = is_thinking_model(model)

    # 针对 Gemini 模型：根据思考设置映射至真实的 Antigravity 后端模型 ID
    if "gemini" in model.lower():

        # 既然 Antigravity 后端是通过模型名来确定思考深度的，
        # 对于 Gemini 3/3.5 模型必须移除 thinkingConfig 以防止 API 返回参数冲突错误。
        if "gemini-3" in model:
            generation_config.pop("thinkingConfig", None)
        else:
            # 对于 Gemini 2.5 系列，保留 thinkingConfig
            if thinking:
                if "thinkingConfig" not in generation_config:
                    generation_config["thinkingConfig"] = {}
                thinking_config = generation_config["thinkingConfig"]
                thinking_config["thinkingBudget"] = 1024
                thinking_config.pop("thinkingLevel", None)
                thinking_config["includeThoughts"] = return_thoughts
    else:
        # 针对非 Gemini 模型（如 Claude）
        if thinking:
            # 直接设置 thinkingConfig，默认思考预算
            if "thinkingConfig" not in generation_config:
                generation_config["thinkingConfig"] = {}

            thinking_config = generation_config["thinkingConfig"]
            thinking_config["thinkingBudget"] = 1024
            thinking_config.pop("thinkingLevel", None)
            thinking_config["includeThoughts"] = return_thoughts

        # 检查最后一个 assistant 消息是否以 thinking 块开始
        contents = result.get("contents", [])

        if "claude" in model.lower():
            # 检测是否有工具调用（MCP场景）
            has_tool_calls = any(
                isinstance(content, dict) and
                any(
                    isinstance(part, dict) and ("functionCall" in part or "function_call" in part)
                    for part in content.get("parts", [])
                )
                for content in contents
            )

            if has_tool_calls:
                # MCP 场景：检测到工具调用，移除 thinkingConfig
                log.warning(f"[ANTIGRAVITY] 检测到工具调用（MCP场景），移除 thinkingConfig 避免失效")
                generation_config.pop("thinkingConfig", None)
            else:
                # 非 MCP 场景：填充思考块
                # 找到最后一个 model 角色的 content
                for i in range(len(contents) - 1, -1, -1):
                    content = contents[i]
                    if isinstance(content, dict) and content.get("role") == "model":
                        # 在 parts 开头插入思考块（使用官方跳过验证的虚拟签名）
                        parts = content.get("parts", [])
                        thinking_part = {
                            "text": "...",
                            "thoughtSignature": "skip_thought_signature_validator"  # 官方文档推荐的虚拟签名
                        }
                        # 如果第一个 part 不是 thinking，则插入
                        if not parts or not (isinstance(parts[0], dict) and ("thought" in parts[0] or "thoughtSignature" in parts[0])):
                            content["parts"] = [thinking_part] + parts
                            log.debug(f"[ANTIGRAVITY] 已在最后一个 assistant 消息开头插入思考块（含跳过验证签名）")
                        break

    if "claude" in model.lower():
        # 2. Claude 模型关键词映射
        # 使用关键词匹配而不是精确匹配，更灵活地处理各种变体
        original_model = model
        if "opus" in model.lower():
            model = "claude-opus-4-6-thinking"
        elif "sonnet" in model.lower():
            model = "claude-sonnet-4-6"
        elif "haiku" in model.lower():
            model = "gemini-2.5-flash"
        elif "claude" in model.lower():
            # Claude 模型兜底：如果包含 claude 但不是 opus/sonnet/haiku
            model = "claude-sonnet-4-6"

        if original_model != model:
            log.debug(f"[ANTIGRAVITY] 映射模型: {original_model} -> {model}")

    return model


async def normalize_antigravity_request(
    request: Dict[str, Any],
) -> Dict[str, Any]:
    """
    规范化 Antigravity 请求

    处理逻辑:
    1. 模型特性处理 (thinking config)
    2. 图片生成请求处理
    3. 参数范围限制 (maxOutputTokens, topK)
    4. 工具清理

    Args:
        request: 原始请求字典

    Returns:
        规范化后的请求
    """
    # 导入配置函数
    from config import get_return_thoughts_to_frontend

    result = request.copy()
    model = result.get("model", "")
    generation_config = (result.get("generationConfig") or {}).copy()  # 创建副本避免修改原对象
    tools = result.get("tools")
    system_instruction = result.get("systemInstruction") or result.get("system_instructions")

    # 记录原始请求
    log.debug(f"[ANTIGRAVITY_FIX] 原始请求 - 模型: {model}, generationConfig: {generation_config}")

    # 获取配置值
    return_thoughts = await get_return_thoughts_to_frontend()

    # 图片模型走独立的图片生成处理路径
    if "image" in model.lower():
        return prepare_image_generation_request(result, model)

    model = _normalize_antigravity_request(result, model, generation_config, return_thoughts)
    result["model"] = model

    # 该模型不支持预填充：循环移除末尾的 model 消息，保证以用户消息结尾
    if "claude-opus-4-6-thinking" in model.lower() or "claude-sonnet-4-6" in model.lower():
        contents = result.get("contents", [])
        removed_count = 0
        while contents and isinstance(contents[-1], dict) and contents[-1].get("role") == "model":
            contents.pop()
            removed_count += 1
        if removed_count > 0:
            log.warning(f"[ANTIGRAVITY] {model} 不支持预填充，移除了 {removed_count} 条末尾 model 消息")
            result["contents"] = contents

    # 移除 antigravity 模式不支持的字段
    generation_config.pop("presencePenalty", None)
    generation_config.pop("frequencyPenalty", None)
    generation_config.pop("stopSequences", None)

    # ========== 公共处理 ==========

    # 1. 安全设置覆盖
    if "tools" in result:
        result["tools"] = _normalize_tools_for_internal_api(result.get("tools"))
        # 对于 Claude 模型：antigravity/Vertex AI 通道需要标准 functionDeclarations/parametersJsonSchema 格式
        # 对于 Gemini 模型：统一转换为 functionDeclarations 并确保只使用 parameters 字段（移除 parametersJsonSchema 以防报错）
        result["tools"] = _ensure_empty_tool_schema_for_claude(result.get("tools"), model, "antigravity")

    if "gemini-2.5-flash-lite" in model.lower():
        result["safetySettings"] = LITE_SAFETY_SETTINGS
    else:
        result["safetySettings"] = DEFAULT_SAFETY_SETTINGS

    # 2. 参数范围限制
    if generation_config:
        # 强制设置 maxOutputTokens 为 64000
        generation_config["maxOutputTokens"] = 64000
        # 强制设置 topK 为 64
        generation_config["topK"] = 64

    if "contents" in result:
        result["contents"] = _ensure_tool_call_ids(result["contents"], model)

        cleaned_contents = []
        for content in result["contents"]:
            if isinstance(content, dict) and "parts" in content:
                # 过滤掉空的或无效的 parts
                valid_parts = []
                for part in content["parts"]:
                    if not isinstance(part, dict):
                        continue

                    # 检查 part 是否有有效的非空值
                    # 过滤掉空字典或所有值都为空的 part
                    has_valid_value = any(
                        value not in (None, "", {}, [])
                        for key, value in part.items()
                        if key != "thought"  # thought 字段可以为空
                    )

                    if has_valid_value:
                        part = _normalize_part_thought_signature(part, model)

                        # 修复 text 字段：确保是字符串而不是列表
                        if "text" in part:
                            text_value = part["text"]
                            if isinstance(text_value, list):
                                # 如果是列表，合并为字符串
                                # 注意: list 中的元素可能是 dict（如 {"type":"text","text":"..."}），不能直接 str(dict)
                                # 否则会产生 Python repr 字符串 "{'type': 'text', 'text': '...'}"，污染 model 历史
                                log.warning(f"[ANTIGRAVITY_FIX] text 字段是列表，自动合并: {text_value}")
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
                                # 清理尾随空格
                                part["text"] = text_value.rstrip()
                            else:
                                # 其他类型转为字符串
                                log.warning(f"[ANTIGRAVITY_FIX] text 字段类型异常 ({type(text_value)}), 转为字符串: {text_value}")
                                part["text"] = str(text_value)

                        valid_parts.append(part)
                    else:
                        log.warning(f"[ANTIGRAVITY_FIX] 移除空的或无效的 part: {part}")

                # 只添加有有效 parts 的 content
                if valid_parts:
                    cleaned_content = content.copy()
                    cleaned_content["parts"] = valid_parts
                    cleaned_contents.append(cleaned_content)
                else:
                    log.warning(f"[ANTIGRAVITY_FIX] 跳过没有有效 parts 的 content: {content.get('role')}")
            else:
                cleaned_contents.append(content)

        result["contents"] = cleaned_contents

    if generation_config:
        result["generationConfig"] = generation_config

    return result
