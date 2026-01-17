"""
OpenAI Transfer Module - Handles conversion between OpenAI and Gemini API formats
被openai-router调用，负责OpenAI格式与Gemini格式的双向转换
"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from pypinyin import Style, lazy_pinyin

from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature,
)
from src.converter.utils import merge_system_messages

from log import log

def _convert_usage_metadata(usage_metadata: Dict[str, Any]) -> Dict[str, int]:
    """
    将Gemini的usageMetadata转换为OpenAI格式的usage字段

    Args:
        usage_metadata: Gemini API的usageMetadata字段

    Returns:
        OpenAI格式的usage字典，如果没有usage数据则返回None
    """
    if not usage_metadata:
        return None

    return {
        "prompt_tokens": usage_metadata.get("promptTokenCount", 0),
        "completion_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0),
    }


def _build_message_with_reasoning(role: str, content: str, reasoning_content: str) -> dict:
    """构建包含可选推理内容的消息对象"""
    message = {"role": role, "content": content}

    # 如果有thinking tokens，添加reasoning_content
    if reasoning_content:
        message["reasoning_content"] = reasoning_content

    return message


def _map_finish_reason(gemini_reason: str) -> str:
    """
    将Gemini结束原因映射到OpenAI结束原因

    Args:
        gemini_reason: 来自Gemini API的结束原因

    Returns:
        OpenAI兼容的结束原因
    """
    if gemini_reason == "STOP":
        return "stop"
    elif gemini_reason == "MAX_TOKENS":
        return "length"
    elif gemini_reason in ["SAFETY", "RECITATION"]:
        return "content_filter"
    else:
        # 对于 None 或未知的 finishReason，返回 "stop" 作为默认值
        # 避免返回 None 导致 MCP 客户端误判为响应未完成而循环调用
        return "stop"


# ==================== Tool Conversion Functions ====================


def _normalize_function_name(name: str) -> str:
    """
    规范化函数名以符合 Gemini API 要求

    规则：
    - 必须以字母或下划线开头
    - 只能包含 a-z, A-Z, 0-9, 下划线, 英文句点, 英文短划线
    - 最大长度 64 个字符

    转换策略：
    1. 中文字符转换为拼音
    2. 将非法字符替换为下划线
    3. 如果以非字母/下划线开头，添加下划线前缀
    4. 截断到 64 个字符

    Args:
        name: 原始函数名

    Returns:
        规范化后的函数名
    """
    import re

    if not name:
        return "_unnamed_function"

    # 步骤1：转换中文字符为拼音
    if re.search(r"[\u4e00-\u9fff]", name):
        try:
            parts = []
            for char in name:
                if "\u4e00" <= char <= "\u9fff":
                    # 中文字符转换为拼音
                    pinyin = lazy_pinyin(char, style=Style.NORMAL)
                    parts.append("".join(pinyin))
                else:
                    parts.append(char)
            normalized = "".join(parts)
        except ImportError:
            log.warning("pypinyin not installed, cannot convert Chinese characters to pinyin")
            normalized = name
    else:
        normalized = name

    # 步骤2：将非法字符替换为下划线
    # 合法字符：a-z, A-Z, 0-9, _, ., -
    normalized = re.sub(r"[^a-zA-Z0-9_.\-]", "_", normalized)

    # 步骤3：确保以字母或下划线开头
    if normalized and not (normalized[0].isalpha() or normalized[0] == "_"):
        # 以数字、点或短横线开头，添加下划线前缀
        normalized = "_" + normalized

    # 步骤4：截断到 64 个字符
    if len(normalized) > 64:
        normalized = normalized[:64]

    # 步骤5：确保不为空
    if not normalized:
        normalized = "_unnamed_function"

    return normalized


def _resolve_ref(ref: str, root_schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    解析 $ref 引用
    
    Args:
        ref: 引用路径，如 "#/definitions/MyType"
        root_schema: 根 schema 对象
        
    Returns:
        解析后的 schema，如果失败返回 None
    """
    if not ref.startswith('#/'):
        return None
    
    path = ref[2:].split('/')
    current = root_schema
    
    for segment in path:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    
    return current if isinstance(current, dict) else None


def _clean_schema_for_claude(schema: Any, root_schema: Optional[Dict[str, Any]] = None, visited: Optional[set] = None) -> Any:
    """
    清理 JSON Schema，转换为 Claude API 支持的格式（符合 JSON Schema draft 2020-12）

    处理逻辑：
    1. 解析 $ref 引用
    2. 合并 allOf 中的 schema
    3. 转换 anyOf 为更兼容的格式
    4. 保持标准 JSON Schema 类型（不转换为大写）
    5. 处理 array 的 items
    6. 清理 Claude 不支持的字段

    Args:
        schema: JSON Schema 对象
        root_schema: 根 schema（用于解析 $ref）
        visited: 已访问的对象集合（防止循环引用）

    Returns:
        清理后的 schema
    """
    # 非字典类型直接返回
    if not isinstance(schema, dict):
        return schema

    # 初始化
    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()

    # 防止循环引用
    schema_id = id(schema)
    if schema_id in visited:
        return schema
    visited.add(schema_id)

    # 创建副本避免修改原对象
    result = {}

    # 1. 处理 $ref
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], root_schema)
        if resolved:
            import copy
            result = copy.deepcopy(resolved)
            for key, value in schema.items():
                if key != "$ref":
                    result[key] = value
            schema = result
            result = {}

    # 2. 处理 allOf（合并所有 schema）
    if "allOf" in schema:
        all_of_schemas = schema["allOf"]
        for item in all_of_schemas:
            cleaned_item = _clean_schema_for_claude(item, root_schema, visited)

            if "properties" in cleaned_item:
                if "properties" not in result:
                    result["properties"] = {}
                result["properties"].update(cleaned_item["properties"])

            if "required" in cleaned_item:
                if "required" not in result:
                    result["required"] = []
                result["required"].extend(cleaned_item["required"])

            for key, value in cleaned_item.items():
                if key not in ["properties", "required"]:
                    result[key] = value

        for key, value in schema.items():
            if key not in ["allOf", "properties", "required"]:
                result[key] = value
            elif key in ["properties", "required"] and key not in result:
                result[key] = value
    else:
        result = dict(schema)

    # 3. 处理 type 数组（如 ["string", "null"]）
    if "type" in result:
        type_value = result["type"]
        if isinstance(type_value, list):
            # Claude 支持 type 数组，保持不变
            pass

    # 4. 处理 array 的 items
    if result.get("type") == "array":
        if "items" not in result:
            result["items"] = {}
        elif isinstance(result["items"], list):
            # Tuple 定义，检查是否所有元素类型相同
            tuple_items = result["items"]
            first_type = tuple_items[0].get("type") if tuple_items else None
            is_homogeneous = all(item.get("type") == first_type for item in tuple_items)

            if is_homogeneous and first_type:
                result["items"] = _clean_schema_for_claude(tuple_items[0], root_schema, visited)
            else:
                # 异质元组，使用 anyOf 表示
                result["items"] = {
                    "anyOf": [_clean_schema_for_claude(item, root_schema, visited) for item in tuple_items]
                }
        else:
            result["items"] = _clean_schema_for_claude(result["items"], root_schema, visited)

    # 5. 处理 anyOf（保持 anyOf，递归清理）
    if "anyOf" in result:
        result["anyOf"] = [_clean_schema_for_claude(item, root_schema, visited) for item in result["anyOf"]]

    # 6. 清理 Claude 不支持的字段（根据 JSON Schema 2020-12）
    # Claude API 对某些字段比较严格，移除可能导致问题的字段
    unsupported_keys = {
        "title", "$schema", "strict",
        "additionalItems",  # 废弃字段，使用 items 替代
        "exclusiveMaximum", "exclusiveMinimum",  # 在 2020-12 中这些应该是数值而非布尔值
        "$defs", "definitions",  # 移除 definitions 相关字段避免冲突
        "example", "examples", "readOnly", "writeOnly",
        "const",  # const 可能导致问题
        "contentEncoding", "contentMediaType",
        "oneOf",  # oneOf 可能导致问题，用 anyOf 替代
    }

    for key in list(result.keys()):
        if key in unsupported_keys:
            del result[key]

    # 递归处理 additionalProperties（如果存在）
    if "additionalProperties" in result and isinstance(result["additionalProperties"], dict):
        result["additionalProperties"] = _clean_schema_for_claude(result["additionalProperties"], root_schema, visited)

    # 7. 递归处理 properties
    if "properties" in result:
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            cleaned_props[prop_name] = _clean_schema_for_claude(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props

    # 8. 确保有 type 字段（如果有 properties 但没有 type）
    if "properties" in result and "type" not in result:
        result["type"] = "object"

    # 9. 去重 required 数组
    if "required" in result and isinstance(result["required"], list):
        result["required"] = list(dict.fromkeys(result["required"]))

    return result


def _clean_schema_for_gemini(schema: Any, root_schema: Optional[Dict[str, Any]] = None, visited: Optional[set] = None) -> Any:
    """
    清理 JSON Schema，转换为 Gemini 支持的格式

    参考 worker.mjs 的 transformOpenApiSchemaToGemini 实现

    处理逻辑：
    1. 解析 $ref 引用
    2. 合并 allOf 中的 schema
    3. 转换 anyOf 为 enum（如果可能）
    4. 类型映射（string -> STRING）
    5. 处理 ARRAY 的 items（包括 Tuple）
    6. 将 default 值移到 description
    7. 清理不支持的字段

    Args:
        schema: JSON Schema 对象
        root_schema: 根 schema（用于解析 $ref）
        visited: 已访问的对象集合（防止循环引用）

    Returns:
        清理后的 schema
    """
    # 非字典类型直接返回
    if not isinstance(schema, dict):
        return schema
    
    # 初始化
    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()
    
    # 防止循环引用
    schema_id = id(schema)
    if schema_id in visited:
        return schema
    visited.add(schema_id)
    
    # 创建副本避免修改原对象
    result = {}
    
    # 1. 处理 $ref
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], root_schema)
        if resolved:
            # 合并解析后的 schema 和当前 schema
            import copy
            result = copy.deepcopy(resolved)
            # 当前 schema 的其他字段会覆盖解析后的字段
            for key, value in schema.items():
                if key != "$ref":
                    result[key] = value
            schema = result
            result = {}
    
    # 2. 处理 allOf（合并所有 schema）
    if "allOf" in schema:
        all_of_schemas = schema["allOf"]
        for item in all_of_schemas:
            cleaned_item = _clean_schema_for_gemini(item, root_schema, visited)
            
            # 合并 properties
            if "properties" in cleaned_item:
                if "properties" not in result:
                    result["properties"] = {}
                result["properties"].update(cleaned_item["properties"])
            
            # 合并 required
            if "required" in cleaned_item:
                if "required" not in result:
                    result["required"] = []
                result["required"].extend(cleaned_item["required"])
            
            # 合并其他字段（简单覆盖）
            for key, value in cleaned_item.items():
                if key not in ["properties", "required"]:
                    result[key] = value
        
        # 复制其他字段
        for key, value in schema.items():
            if key not in ["allOf", "properties", "required"]:
                result[key] = value
            elif key in ["properties", "required"] and key not in result:
                result[key] = value
    else:
        # 复制所有字段
        result = dict(schema)
    
    # 3. 类型映射（转换为大写）
    if "type" in result:
        type_value = result["type"]
        
        # 处理 type: ["string", "null"] 的情况
        if isinstance(type_value, list):
            primary_type = next((t for t in type_value if t != "null"), None)
            if primary_type:
                type_value = primary_type
        
        # 类型映射
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }
        
        if isinstance(type_value, str) and type_value.lower() in type_map:
            result["type"] = type_map[type_value.lower()]
    
    # 4. 处理 ARRAY 的 items
    if result.get("type") == "ARRAY":
        if "items" not in result:
            # 没有 items，默认允许任意类型
            result["items"] = {}
        elif isinstance(result["items"], list):
            # Tuple 定义（items 是数组）
            tuple_items = result["items"]
            
            # 提取类型信息用于 description
            tuple_types = [item.get("type", "any") for item in tuple_items]
            tuple_desc = f"(Tuple: [{', '.join(tuple_types)}])"
            
            original_desc = result.get("description", "")
            result["description"] = f"{original_desc} {tuple_desc}".strip()
            
            # 检查是否所有元素类型相同
            first_type = tuple_items[0].get("type") if tuple_items else None
            is_homogeneous = all(item.get("type") == first_type for item in tuple_items)
            
            if is_homogeneous and first_type:
                # 同质元组，转换为 List<Type>
                result["items"] = _clean_schema_for_gemini(tuple_items[0], root_schema, visited)
            else:
                # 异质元组，Gemini 不支持，设为 {}
                result["items"] = {}
        else:
            # 递归处理 items
            result["items"] = _clean_schema_for_gemini(result["items"], root_schema, visited)
    
    # 5. 处理 anyOf（尝试转换为 enum）
    if "anyOf" in result:
        any_of_schemas = result["anyOf"]
        
        # 递归处理每个 schema
        cleaned_any_of = [_clean_schema_for_gemini(item, root_schema, visited) for item in any_of_schemas]
        
        # 尝试提取 enum
        if all("const" in item for item in cleaned_any_of):
            enum_values = [
                str(item["const"]) 
                for item in cleaned_any_of 
                if item.get("const") not in ["", None]
            ]
            if enum_values:
                result["type"] = "STRING"
                result["enum"] = enum_values
        elif "type" not in result:
            # 如果不是 enum，尝试取第一个有效的类型定义
            first_valid = next((item for item in cleaned_any_of if item.get("type") or item.get("enum")), None)
            if first_valid:
                result.update(first_valid)
        
        # 删除 anyOf
        del result["anyOf"]
    
    # 6. 将 default 值移到 description
    if "default" in result:
        default_value = result["default"]
        original_desc = result.get("description", "")
        result["description"] = f"{original_desc} (Default: {json.dumps(default_value)})".strip()
        del result["default"]
    
    # 7. 清理不支持的字段
    unsupported_keys = {
        "title", "$schema", "$ref", "strict", "exclusiveMaximum",
        "exclusiveMinimum", "additionalProperties", "oneOf", "allOf",
        "$defs", "definitions", "example", "examples", "readOnly",
        "writeOnly", "const", "additionalItems", "contains",
        "patternProperties", "dependencies", "propertyNames",
        "if", "then", "else", "contentEncoding", "contentMediaType"
    }
    
    for key in list(result.keys()):
        if key in unsupported_keys:
            del result[key]
    
    # 8. 递归处理 properties
    if "properties" in result:
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            cleaned_props[prop_name] = _clean_schema_for_gemini(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props
    
    # 9. 确保有 type 字段（如果有 properties 但没有 type）
    if "properties" in result and "type" not in result:
        result["type"] = "OBJECT"
    
    # 10. 去重 required 数组
    if "required" in result and isinstance(result["required"], list):
        result["required"] = list(dict.fromkeys(result["required"]))  # 保持顺序去重
    
    return result


def fix_tool_call_args_types(
    args: Dict[str, Any],
    parameters_schema: Dict[str, Any]
) -> Dict[str, Any]:
    """
    根据工具的参数 schema 修正函数调用参数的类型
    
    例如：将字符串 "5" 转换为数字 5，根据 schema 中的 type 定义
    
    Args:
        args: 函数调用的参数字典
        parameters_schema: 工具定义中的 parameters schema
        
    Returns:
        类型修正后的参数字典
    """
    if not args or not parameters_schema:
        return args
    
    properties = parameters_schema.get("properties", {})
    if not properties:
        return args
    
    fixed_args = {}
    for key, value in args.items():
        if key not in properties:
            # 参数不在 schema 中，保持原样
            fixed_args[key] = value
            continue
        
        param_schema = properties[key]
        param_type = param_schema.get("type")
        
        # 根据 schema 中的类型修正参数值
        if param_type == "number" or param_type == "integer":
            # 如果值是字符串，尝试转换为数字
            if isinstance(value, str):
                try:
                    if param_type == "integer":
                        fixed_args[key] = int(value)
                    else:
                        # 尝试转换为 float，如果是整数则保持为 int
                        num_value = float(value)
                        fixed_args[key] = int(num_value) if num_value.is_integer() else num_value
                    log.debug(f"[OPENAI2GEMINI] 修正参数类型: {key} '{value}' -> {fixed_args[key]} ({param_type})")
                except (ValueError, AttributeError):
                    # 转换失败，保持原样
                    fixed_args[key] = value
                    log.warning(f"[OPENAI2GEMINI] 无法将参数 {key} 的值 '{value}' 转换为 {param_type}")
            else:
                fixed_args[key] = value
        elif param_type == "boolean":
            # 如果值是字符串，转换为布尔值
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes"):
                    fixed_args[key] = True
                elif value.lower() in ("false", "0", "no"):
                    fixed_args[key] = False
                else:
                    fixed_args[key] = value
                if fixed_args[key] != value:
                    log.debug(f"[OPENAI2GEMINI] 修正参数类型: {key} '{value}' -> {fixed_args[key]} (boolean)")
            else:
                fixed_args[key] = value
        elif param_type == "string":
            # 如果值不是字符串，转换为字符串
            if not isinstance(value, str):
                fixed_args[key] = str(value)
                log.debug(f"[OPENAI2GEMINI] 修正参数类型: {key} {value} -> '{fixed_args[key]}' (string)")
            else:
                fixed_args[key] = value
        else:
            # 其他类型（array, object 等）保持原样
            fixed_args[key] = value
    
    return fixed_args


def convert_openai_tools_to_gemini(openai_tools: List, model: str = "") -> List[Dict[str, Any]]:
    """
    将 OpenAI tools 格式转换为 Gemini functionDeclarations 格式

    Args:
        openai_tools: OpenAI 格式的工具列表（可能是字典或 Pydantic 模型）
        model: 模型名称（用于判断是否为 Claude 模型）

    Returns:
        Gemini 格式的工具列表
    """
    if not openai_tools:
        return []

    # 判断是否为 Claude 模型
    is_claude_model = "claude" in model.lower()

    function_declarations = []

    for tool in openai_tools:
        if tool.get("type") != "function":
            log.warning(f"Skipping non-function tool type: {tool.get('type')}")
            continue

        function = tool.get("function")
        if not function:
            log.warning("Tool missing 'function' field")
            continue

        # 获取并规范化函数名
        original_name = function.get("name")
        if not original_name:
            log.warning("Tool missing 'name' field, using default")
            original_name = "_unnamed_function"

        normalized_name = _normalize_function_name(original_name)

        # 如果名称被修改了，记录日志
        if normalized_name != original_name:
            log.debug(f"Function name normalized: '{original_name}' -> '{normalized_name}'")

        # 构建 Gemini function declaration
        declaration = {
            "name": normalized_name,
            "description": function.get("description", ""),
        }

        # 添加参数（如果有）- 根据模型选择不同的清理函数
        if "parameters" in function:
            if is_claude_model:
                cleaned_params = _clean_schema_for_claude(function["parameters"])
                log.debug(f"[OPENAI2GEMINI] Using Claude schema cleaning for tool: {normalized_name}")
            else:
                cleaned_params = _clean_schema_for_gemini(function["parameters"])

            if cleaned_params:
                declaration["parameters"] = cleaned_params

        function_declarations.append(declaration)

    if not function_declarations:
        return []

    # Gemini 格式：工具数组中包含 functionDeclarations
    return [{"functionDeclarations": function_declarations}]


def convert_tool_choice_to_tool_config(tool_choice: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 OpenAI tool_choice 转换为 Gemini toolConfig

    Args:
        tool_choice: OpenAI 格式的 tool_choice

    Returns:
        Gemini 格式的 toolConfig
    """
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        elif tool_choice == "required":
            return {"functionCallingConfig": {"mode": "ANY"}}
    elif isinstance(tool_choice, dict):
        # {"type": "function", "function": {"name": "my_function"}}
        if tool_choice.get("type") == "function":
            function_name = tool_choice.get("function", {}).get("name")
            if function_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [function_name],
                    }
                }

    # 默认返回 AUTO 模式
    return {"functionCallingConfig": {"mode": "AUTO"}}


def convert_tool_message_to_function_response(message, all_messages: List = None) -> Dict[str, Any]:
    """
    将 OpenAI 的 tool role 消息转换为 Gemini functionResponse

    Args:
        message: OpenAI 格式的工具消息
        all_messages: 所有消息的列表，用于查找 tool_call_id 对应的函数名

    Returns:
        Gemini 格式的 functionResponse part
    """
    # 获取 name 字段
    name = getattr(message, "name", None)
    encoded_tool_call_id = getattr(message, "tool_call_id", None) or ""

    # 解码获取原始ID（functionResponse不需要签名）
    original_tool_call_id, _ = decode_tool_id_and_signature(encoded_tool_call_id)

    # 如果没有 name，尝试从 all_messages 中查找对应的 tool_call_id
    # 注意：使用编码ID查找，因为存储的是编码ID
    if not name and encoded_tool_call_id and all_messages:
        for msg in all_messages:
            if getattr(msg, "role", None) == "assistant" and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    if getattr(tool_call, "id", None) == encoded_tool_call_id:
                        func = getattr(tool_call, "function", None)
                        if func:
                            name = getattr(func, "name", None)
                            break
                if name:
                    break

    # 最终兜底：如果仍然没有 name，使用默认值
    if not name:
        name = "unknown_function"
        log.warning(f"Tool message missing function name, using default: {name}")

    try:
        # 尝试将 content 解析为 JSON
        response_data = (
            json.loads(message.content) if isinstance(message.content, str) else message.content
        )
    except (json.JSONDecodeError, TypeError):
        # 如果不是有效的 JSON，包装为对象
        response_data = {"result": str(message.content)}

    # 确保 response_data 是字典类型（Gemini API 要求 response 必须是对象）
    if not isinstance(response_data, dict):
        response_data = {"result": response_data}

    return {"functionResponse": {"id": original_tool_call_id, "name": name, "response": response_data}}


def _reverse_transform_value(value: Any) -> Any:
    """
    将值转换回原始类型（Gemini 可能将所有值转为字符串）
    
    参考 worker.mjs 的 reverseTransformValue
    
    Args:
        value: 要转换的值
        
    Returns:
        转换后的值
    """
    if not isinstance(value, str):
        return value
    
    # 布尔值
    if value == 'true':
        return True
    if value == 'false':
        return False
    
    # null
    if value == 'null':
        return None
    
    # 数字（确保字符串确实是纯数字）
    if value.strip() and not value.startswith('0') and value.replace('.', '', 1).replace('-', '', 1).replace('+', '', 1).isdigit():
        try:
            # 尝试转换为数字
            num_value = float(value)
            # 如果是整数，返回 int
            if num_value == int(num_value):
                return int(num_value)
            return num_value
        except ValueError:
            pass
    
    # 其他情况保持字符串
    return value


def _reverse_transform_args(args: Any) -> Any:
    """
    递归转换函数参数，将字符串转回原始类型
    
    参考 worker.mjs 的 reverseTransformArgs
    
    Args:
        args: 函数参数（可能是字典、列表或其他类型）
        
    Returns:
        转换后的参数
    """
    if not isinstance(args, (dict, list)):
        return args
    
    if isinstance(args, list):
        return [_reverse_transform_args(item) for item in args]
    
    # 处理字典
    result = {}
    for key, value in args.items():
        if isinstance(value, (dict, list)):
            result[key] = _reverse_transform_args(value)
        else:
            result[key] = _reverse_transform_value(value)
    
    return result


def extract_tool_calls_from_parts(
    parts: List[Dict[str, Any]], is_streaming: bool = False
) -> Tuple[List[Dict[str, Any]], str]:
    """
    从 Gemini response parts 中提取工具调用和文本内容

    Args:
        parts: Gemini response 的 parts 数组
        is_streaming: 是否为流式响应（流式响应需要添加 index 字段）

    Returns:
        (tool_calls, text_content) 元组
    """
    tool_calls = []
    text_content = ""

    for idx, part in enumerate(parts):
        # 检查是否是函数调用
        if "functionCall" in part:
            function_call = part["functionCall"]
            # 获取原始ID或生成新ID
            original_id = function_call.get("id") or f"call_{uuid.uuid4().hex[:24]}"
            # 将thoughtSignature编码到ID中以便往返保留
            signature = part.get("thoughtSignature")
            encoded_id = encode_tool_id_with_signature(original_id, signature)

            # 获取参数并转换类型
            args = function_call.get("args", {})
            # 将字符串类型的值转回原始类型
            args = _reverse_transform_args(args)

            tool_call = {
                "id": encoded_id,
                "type": "function",
                "function": {
                    "name": function_call.get("name", "nameless_function"),
                    "arguments": json.dumps(args),
                },
            }
            # 流式响应需要 index 字段
            if is_streaming:
                tool_call["index"] = idx
            tool_calls.append(tool_call)

        # 提取文本内容（排除 thinking tokens）
        elif "text" in part and not part.get("thought", False):
            text_content += part["text"]

    return tool_calls, text_content


def extract_images_from_content(content: Any) -> Dict[str, Any]:
    """
    从 OpenAI content 中提取文本和图片
    
    Args:
        content: OpenAI 消息的 content 字段（可能是字符串或列表）
    
    Returns:
        包含 text 和 images 的字典
    """
    result = {"text": "", "images": []}

    if isinstance(content, str):
        result["text"] = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    result["text"] += item.get("text", "")
                elif item.get("type") == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    # 解析 data:image/png;base64,xxx 格式
                    if image_url.startswith("data:image/"):
                        import re
                        match = re.match(r"^data:image/(\w+);base64,(.+)$", image_url)
                        if match:
                            mime_type = match.group(1)
                            base64_data = match.group(2)
                            result["images"].append({
                                "inlineData": {
                                    "mimeType": f"image/{mime_type}",
                                    "data": base64_data
                                }
                            })

    return result

async def convert_openai_to_gemini_request(openai_request: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 OpenAI 格式请求体转换为 Gemini 格式请求体

    注意: 此函数只负责基础转换,不包含 normalize_gemini_request 中的处理
    (如 thinking config, search tools, 参数范围限制等)

    Args:
        openai_request: OpenAI 格式的请求体字典,包含:
            - messages: 消息列表
            - temperature, top_p, max_tokens, stop 等生成参数
            - tools, tool_choice (可选)
            - response_format (可选)

    Returns:
        Gemini 格式的请求体字典,包含:
            - contents: 转换后的消息内容
            - generationConfig: 生成配置
            - systemInstruction: 系统指令 (如果有)
            - tools, toolConfig (如果有)
    """
    # 处理连续的system消息（兼容性模式）
    openai_request = await merge_system_messages(openai_request)

    contents = []

    # 提取消息列表
    messages = openai_request.get("messages", [])
    
    # 构建 tool_call_id -> (name, original_id, signature) 的映射
    tool_call_mapping = {}
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                encoded_id = tc.get("id", "")
                func_name = tc.get("function", {}).get("name") or ""
                if encoded_id:
                    # 解码获取原始ID和签名
                    original_id, signature = decode_tool_id_and_signature(encoded_id)
                    tool_call_mapping[encoded_id] = (func_name, original_id, signature)
    
    # 构建工具名称到参数 schema 的映射（用于类型修正）
    tool_schemas = {}
    if "tools" in openai_request and openai_request["tools"]:
        for tool in openai_request["tools"]:
            if tool.get("type") == "function":
                function = tool.get("function", {})
                func_name = function.get("name")
                if func_name:
                    tool_schemas[func_name] = function.get("parameters", {})

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        # 处理工具消息（tool role）
        if role == "tool":
            tool_call_id = message.get("tool_call_id", "")
            func_name = message.get("name")

            # 使用映射表查找
            if tool_call_id in tool_call_mapping:
                func_name, original_id, _ = tool_call_mapping[tool_call_id]
            else:
                # 如果没有name,尝试从消息列表中查找
                if not func_name and tool_call_id:
                    for msg in messages:
                        if msg.get("role") == "assistant" and msg.get("tool_calls"):
                            for tc in msg["tool_calls"]:
                                if tc.get("id") == tool_call_id:
                                    func_name = tc.get("function", {}).get("name")
                                    break
                            if func_name:
                                break

                # 解码 tool_call_id 获取原始 ID
                original_id, _ = decode_tool_id_and_signature(tool_call_id)

            # 最终兜底：确保 func_name 不为空
            if not func_name:
                func_name = "unknown_function"
                log.warning(f"Tool message missing function name for tool_call_id={tool_call_id}, using default: {func_name}")

            # 解析响应数据
            try:
                response_data = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                response_data = {"result": str(content)}

            # 确保 response_data 是字典类型（Gemini API 要求 response 必须是对象）
            if not isinstance(response_data, dict):
                response_data = {"result": response_data}

            # 使用原始 ID（不带签名）
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "id": original_id,
                        "name": func_name,
                        "response": response_data
                    }
                }]
            })
            continue

        # system 消息已经由 merge_system_messages 处理，这里跳过
        if role == "system":
            continue

        # 将OpenAI角色映射到Gemini角色
        if role == "assistant":
            role = "model"

        # 检查是否有tool_calls
        tool_calls = message.get("tool_calls")
        if tool_calls:
            parts = []

            # 如果有文本内容,先添加文本
            if content:
                parts.append({"text": content})

            # 添加每个工具调用
            for tool_call in tool_calls:
                try:
                    args = (
                        json.loads(tool_call["function"]["arguments"])
                        if isinstance(tool_call["function"]["arguments"], str)
                        else tool_call["function"]["arguments"]
                    )
                    
                    # 根据工具的 schema 修正参数类型
                    func_name = tool_call["function"]["name"]
                    if func_name in tool_schemas:
                        args = fix_tool_call_args_types(args, tool_schemas[func_name])

                    # 解码工具ID和thoughtSignature
                    encoded_id = tool_call.get("id", "")
                    original_id, signature = decode_tool_id_and_signature(encoded_id)

                    # 构建functionCall part
                    function_call_part = {
                        "functionCall": {
                            "id": original_id,
                            "name": func_name,
                            "args": args
                        }
                    }

                    # 如果有thoughtSignature则添加，否则使用占位符以满足 Gemini API 要求
                    if signature:
                        function_call_part["thoughtSignature"] = signature
                    else:
                        function_call_part["thoughtSignature"] = "skip_thought_signature_validator"

                    parts.append(function_call_part)
                except (json.JSONDecodeError, KeyError) as e:
                    log.error(f"Failed to parse tool call: {e}")
                    continue

            if parts:
                contents.append({"role": role, "parts": parts})
            continue

        # 处理普通内容
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append({"text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url")
                    if image_url:
                        try:
                            mime_type, base64_data = image_url.split(";")
                            _, mime_type = mime_type.split(":")
                            _, base64_data = base64_data.split(",")
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_data,
                                }
                            })
                        except ValueError:
                            continue
            if parts:
                contents.append({"role": role, "parts": parts})
        elif content:
            contents.append({"role": role, "parts": [{"text": content}]})

    # 构建生成配置
    generation_config = {}
    model = openai_request.get("model", "")
    
    # 基础参数映射
    if "temperature" in openai_request:
        generation_config["temperature"] = openai_request["temperature"]
    if "top_p" in openai_request:
        generation_config["topP"] = openai_request["top_p"]
    if "top_k" in openai_request:
        generation_config["topK"] = openai_request["top_k"]
    if "max_tokens" in openai_request or "max_completion_tokens" in openai_request:
        # max_completion_tokens 优先于 max_tokens
        max_tokens = openai_request.get("max_completion_tokens") or openai_request.get("max_tokens")
        generation_config["maxOutputTokens"] = max_tokens
    if "stop" in openai_request:
        stop = openai_request["stop"]
        generation_config["stopSequences"] = [stop] if isinstance(stop, str) else stop
    if "frequency_penalty" in openai_request:
        generation_config["frequencyPenalty"] = openai_request["frequency_penalty"]
    if "presence_penalty" in openai_request:
        generation_config["presencePenalty"] = openai_request["presence_penalty"]
    if "n" in openai_request:
        generation_config["candidateCount"] = openai_request["n"]
    if "seed" in openai_request:
        generation_config["seed"] = openai_request["seed"]
    
    # 处理 response_format
    if "response_format" in openai_request and openai_request["response_format"]:
        response_format = openai_request["response_format"]
        format_type = response_format.get("type")
        
        if format_type == "json_schema":
            # JSON Schema 模式
            if "json_schema" in response_format and "schema" in response_format["json_schema"]:
                schema = response_format["json_schema"]["schema"]
                # 清理 schema
                generation_config["responseSchema"] = _clean_schema_for_gemini(schema)
                generation_config["responseMimeType"] = "application/json"
        elif format_type == "json_object":
            # JSON Object 模式
            generation_config["responseMimeType"] = "application/json"
        elif format_type == "text":
            # Text 模式
            generation_config["responseMimeType"] = "text/plain"
            
    # 如果contents为空,添加默认用户消息
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "请根据系统指令回答。"}]})

    # 构建基础请求
    gemini_request = {
        "contents": contents,
        "generationConfig": generation_config
    }

    # 如果 merge_system_messages 已经添加了 systemInstruction，使用它
    if "systemInstruction" in openai_request:
        gemini_request["systemInstruction"] = openai_request["systemInstruction"]

    # 处理工具 - 传递 model 参数以便根据模型类型选择清理策略
    model = openai_request.get("model", "")
    if "tools" in openai_request and openai_request["tools"]:
        gemini_request["tools"] = convert_openai_tools_to_gemini(openai_request["tools"], model)

    # 处理tool_choice
    if "tool_choice" in openai_request and openai_request["tool_choice"]:
        gemini_request["toolConfig"] = convert_tool_choice_to_tool_config(openai_request["tool_choice"])

    return gemini_request


def convert_gemini_to_openai_response(
    gemini_response: Union[Dict[str, Any], Any],
    model: str,
    status_code: int = 200
) -> Dict[str, Any]:
    """
    将 Gemini 格式非流式响应转换为 OpenAI 格式非流式响应

    注意: 如果收到的不是 200 开头的响应,不做任何处理,直接转发原始响应

    Args:
        gemini_response: Gemini 格式的响应体 (字典或响应对象)
        model: 模型名称
        status_code: HTTP 状态码 (默认 200)

    Returns:
        OpenAI 格式的响应体字典,或原始响应 (如果状态码不是 2xx)
    """
    # 非 2xx 状态码直接返回原始响应
    if not (200 <= status_code < 300):
        if isinstance(gemini_response, dict):
            return gemini_response
        else:
            # 如果是响应对象,尝试解析为字典
            try:
                if hasattr(gemini_response, "json"):
                    return gemini_response.json()
                elif hasattr(gemini_response, "body"):
                    body = gemini_response.body
                    if isinstance(body, bytes):
                        return json.loads(body.decode())
                    return json.loads(str(body))
                else:
                    return {"error": str(gemini_response)}
            except:
                return {"error": str(gemini_response)}

    # 确保是字典格式
    if not isinstance(gemini_response, dict):
        try:
            if hasattr(gemini_response, "json"):
                gemini_response = gemini_response.json()
            elif hasattr(gemini_response, "body"):
                body = gemini_response.body
                if isinstance(body, bytes):
                    gemini_response = json.loads(body.decode())
                else:
                    gemini_response = json.loads(str(body))
            else:
                gemini_response = json.loads(str(gemini_response))
        except:
            return {"error": "Invalid response format"}

    # 处理 GeminiCLI 的 response 包装格式
    if "response" in gemini_response:
        gemini_response = gemini_response["response"]

    # 转换为 OpenAI 格式
    choices = []

    for candidate in gemini_response.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")

        # 将Gemini角色映射回OpenAI角色
        if role == "model":
            role = "assistant"

        # 提取并分离thinking tokens和常规内容
        parts = candidate.get("content", {}).get("parts", [])

        # 提取工具调用和文本内容
        tool_calls, text_content = extract_tool_calls_from_parts(parts)

        # 提取多种类型的内容
        content_parts = []
        reasoning_parts = []
        
        for part in parts:
            # 处理 executableCode（代码生成）
            if "executableCode" in part:
                exec_code = part["executableCode"]
                lang = exec_code.get("language", "python").lower()
                code = exec_code.get("code", "")
                # 添加代码块（前后加换行符确保 Markdown 渲染正确）
                content_parts.append(f"\n```{lang}\n{code}\n```\n")
            
            # 处理 codeExecutionResult（代码执行结果）
            elif "codeExecutionResult" in part:
                result = part["codeExecutionResult"]
                outcome = result.get("outcome")
                output = result.get("output", "")
                
                if output:
                    label = "output" if outcome == "OUTCOME_OK" else "error"
                    content_parts.append(f"\n```{label}\n{output}\n```\n")
            
            # 处理 thought（思考内容）
            elif part.get("thought", False) and "text" in part:
                reasoning_parts.append(part["text"])
            
            # 处理普通文本（非思考内容）
            elif "text" in part and not part.get("thought", False):
                # 这部分已经在 extract_tool_calls_from_parts 中处理
                pass
            
            # 处理 inlineData（图片）
            elif "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                # 使用 Markdown 格式
                content_parts.append(f"![gemini-generated-content](data:{mime_type};base64,{base64_data})")
        
        # 合并所有内容部分
        if content_parts:
            # 使用双换行符连接各部分，确保块之间有间距
            additional_content = "\n\n".join(content_parts)
            if text_content:
                text_content = text_content + "\n\n" + additional_content
            else:
                text_content = additional_content
        
        # 合并 reasoning content
        reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else ""

        # 构建消息对象
        message = {"role": role}

        # 获取 Gemini 的 finishReason
        gemini_finish_reason = candidate.get("finishReason")
        
        # 如果有工具调用
        if tool_calls:
            message["tool_calls"] = tool_calls
            message["content"] = text_content if text_content else None
            # 只有在正常停止（STOP）时才设为 tool_calls，其他情况保持原始 finish_reason
            # 这样可以避免在 SAFETY、MAX_TOKENS 等情况下仍然返回 tool_calls 导致循环
            if gemini_finish_reason == "STOP":
                finish_reason = "tool_calls"
            else:
                finish_reason = _map_finish_reason(gemini_finish_reason)
        else:
            message["content"] = text_content
            finish_reason = _map_finish_reason(gemini_finish_reason)

        # 添加 reasoning content (如果有)
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        choices.append({
            "index": candidate.get("index", 0),
            "message": message,
            "finish_reason": finish_reason,
        })

    # 转换 usageMetadata
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

    response_data = {
        "id": str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

    if usage:
        response_data["usage"] = usage

    return response_data


def convert_gemini_to_openai_stream(
    gemini_stream_chunk: str,
    model: str,
    response_id: str,
    status_code: int = 200
) -> Optional[str]:
    """
    将 Gemini 格式流式响应块转换为 OpenAI SSE 格式流式响应

    注意: 如果收到的不是 200 开头的响应,不做任何处理,直接转发原始内容

    Args:
        gemini_stream_chunk: Gemini 格式的流式响应块 (字符串,通常是 "data: {json}" 格式)
        model: 模型名称
        response_id: 此流式响应的一致ID
        status_code: HTTP 状态码 (默认 200)

    Returns:
        OpenAI SSE 格式的响应字符串 (如 "data: {json}\n\n"),
        或原始内容 (如果状态码不是 2xx),
        或 None (如果解析失败)
    """
    # 非 2xx 状态码直接返回原始内容
    if not (200 <= status_code < 300):
        return gemini_stream_chunk

    # 解析 Gemini 流式块
    try:
        # 去除 "data: " 前缀
        if isinstance(gemini_stream_chunk, bytes):
            if gemini_stream_chunk.startswith(b"data: "):
                payload_str = gemini_stream_chunk[len(b"data: "):].strip().decode("utf-8")
            else:
                payload_str = gemini_stream_chunk.strip().decode("utf-8")
        else:
            if gemini_stream_chunk.startswith("data: "):
                payload_str = gemini_stream_chunk[len("data: "):].strip()
            else:
                payload_str = gemini_stream_chunk.strip()

        # 跳过空块
        if not payload_str:
            return None

        # 解析 JSON
        gemini_chunk = json.loads(payload_str)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 解析失败,跳过此块
        return None

    # 处理 GeminiCLI 的 response 包装格式
    if "response" in gemini_chunk:
        gemini_response = gemini_chunk["response"]
    else:
        gemini_response = gemini_chunk

    # 转换为 OpenAI 流式格式
    choices = []

    for candidate in gemini_response.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")

        # 将Gemini角色映射回OpenAI角色
        if role == "model":
            role = "assistant"

        # 提取并分离thinking tokens和常规内容
        parts = candidate.get("content", {}).get("parts", [])

        # 提取工具调用和文本内容 (流式需要 index)
        tool_calls, text_content = extract_tool_calls_from_parts(parts, is_streaming=True)

        # 提取多种类型的内容
        content_parts = []
        reasoning_parts = []
        
        for part in parts:
            # 处理 executableCode（代码生成）
            if "executableCode" in part:
                exec_code = part["executableCode"]
                lang = exec_code.get("language", "python").lower()
                code = exec_code.get("code", "")
                content_parts.append(f"\n```{lang}\n{code}\n```\n")
            
            # 处理 codeExecutionResult（代码执行结果）
            elif "codeExecutionResult" in part:
                result = part["codeExecutionResult"]
                outcome = result.get("outcome")
                output = result.get("output", "")
                
                if output:
                    label = "output" if outcome == "OUTCOME_OK" else "error"
                    content_parts.append(f"\n```{label}\n{output}\n```\n")
            
            # 处理 thought（思考内容）
            elif part.get("thought", False) and "text" in part:
                reasoning_parts.append(part["text"])
            
            # 处理普通文本（非思考内容）
            elif "text" in part and not part.get("thought", False):
                # 这部分已经在 extract_tool_calls_from_parts 中处理
                pass
            
            # 处理 inlineData（图片）
            elif "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                content_parts.append(f"![gemini-generated-content](data:{mime_type};base64,{base64_data})")
        
        # 合并所有内容部分
        if content_parts:
            additional_content = "\n\n".join(content_parts)
            if text_content:
                text_content = text_content + "\n\n" + additional_content
            else:
                text_content = additional_content
        
        # 合并 reasoning content
        reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else ""

        # 构建 delta 对象
        delta = {}

        if tool_calls:
            delta["tool_calls"] = tool_calls
            if text_content:
                delta["content"] = text_content
        elif text_content:
            delta["content"] = text_content

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content

        # 获取 Gemini 的 finishReason
        gemini_finish_reason = candidate.get("finishReason")
        finish_reason = _map_finish_reason(gemini_finish_reason)
        
        # 只有在正常停止（STOP）且有工具调用时才设为 tool_calls
        # 避免在 SAFETY、MAX_TOKENS 等情况下仍然返回 tool_calls 导致循环
        if tool_calls and gemini_finish_reason == "STOP":
            finish_reason = "tool_calls"

        choices.append({
            "index": candidate.get("index", 0),
            "delta": delta,
            "finish_reason": finish_reason,
        })

    # 转换 usageMetadata (只在流结束时存在)
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

    # 构建 OpenAI 流式响应
    response_data = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

    # 只在有 usage 数据且有 finish_reason 时添加 usage
    if usage:
        has_finish_reason = any(choice.get("finish_reason") for choice in choices)
        if has_finish_reason:
            response_data["usage"] = usage

    # 转换为 SSE 格式: "data: {json}\n\n"
    return f"data: {json.dumps(response_data)}\n\n"
