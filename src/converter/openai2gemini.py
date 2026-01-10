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
        return None


# ==================== Tool Conversion Functions ====================


def _normalize_function_name(name: str) -> str:
    """
    规范化函数名以符合 Gemini API 要求

    规则：
    - 必须以字母或下划线开头
    - 只能包含 a-z, A-Z, 0-9, 下划线, 点, 短横线
    - 最大长度 64 个字符

    转换策略：
    - 中文字符转换为拼音
    - 如果以非字母/下划线开头，添加 "_" 前缀
    - 将非法字符（空格、@、#等）替换为下划线
    - 连续的下划线合并为一个
    - 如果超过 64 个字符，截断

    Args:
        name: 原始函数名

    Returns:
        规范化后的函数名
    """
    import re

    if not name:
        return "_unnamed_function"

    # 第零步：检测并转换中文字符为拼音
    # 检查是否包含中文字符
    if re.search(r"[\u4e00-\u9fff]", name):
        try:

            # 将中文转换为拼音，用下划线连接多音字
            parts = []
            for char in name:
                if "\u4e00" <= char <= "\u9fff":
                    # 中文字符，转换为拼音
                    pinyin = lazy_pinyin(char, style=Style.NORMAL)
                    parts.append("".join(pinyin))
                else:
                    # 非中文字符，保持不变
                    parts.append(char)
            normalized = "".join(parts)
        except ImportError:
            log.warning("pypinyin not installed, cannot convert Chinese characters to pinyin")
            normalized = name
    else:
        normalized = name

    # 第一步：将非法字符替换为下划线
    # 保留：a-z, A-Z, 0-9, 下划线, 点, 短横线
    normalized = re.sub(r"[^a-zA-Z0-9_.\-]", "_", normalized)

    # 第二步：如果以非字母/下划线开头，处理首字符
    prefix_added = False
    if normalized and not (normalized[0].isalpha() or normalized[0] == "_"):
        if normalized[0] in ".-":
            # 点和短横线在开头位置替换为下划线（它们在中间是合法的）
            normalized = "_" + normalized[1:]
        else:
            # 其他字符（如数字）添加下划线前缀
            normalized = "_" + normalized
        prefix_added = True

    # 第三步：合并连续的下划线
    normalized = re.sub(r"_+", "_", normalized)

    # 第四步：移除首尾的下划线
    # 如果原本就是下划线开头，或者我们添加了前缀，则保留开头的下划线
    if name.startswith("_") or prefix_added:
        # 只移除尾部的下划线
        normalized = normalized.rstrip("_")
    else:
        # 移除首尾的下划线
        normalized = normalized.strip("_")

    # 第五步：确保不为空
    if not normalized:
        normalized = "_unnamed_function"

    # 第六步：截断到 64 个字符
    if len(normalized) > 64:
        normalized = normalized[:64]

    return normalized


def _clean_schema_for_gemini(schema: Any) -> Any:
    """
    清理 JSON Schema，移除 Gemini 不支持的字段

    Gemini API 只支持有限的 OpenAPI 3.0 Schema 属性：
    - 支持: type, description, enum, items, properties, required, nullable, format
    - 不支持: $schema, $id, $ref, $defs, title, examples, default, readOnly,
              exclusiveMaximum, exclusiveMinimum, oneOf, anyOf, allOf, const 等

    Args:
        schema: JSON Schema 对象（字典、列表或其他值）

    Returns:
        清理后的 schema
    """
    if not isinstance(schema, dict):
        return schema

    # Gemini 不支持的字段
    unsupported_keys = {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "definitions",
        "example",
        "examples",
        "readOnly",
        "writeOnly",
        "default",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "oneOf",
        "anyOf",
        "allOf",
        "const",
        "additionalItems",
        "contains",
        "patternProperties",
        "dependencies",
        "propertyNames",
        "if",
        "then",
        "else",
        "contentEncoding",
        "contentMediaType",
    }

    cleaned = {}
    for key, value in schema.items():
        if key in unsupported_keys:
            continue
        if isinstance(value, dict):
            cleaned[key] = _clean_schema_for_gemini(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _clean_schema_for_gemini(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[key] = value

    # 确保有 type 字段（如果有 properties 但没有 type）
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    return cleaned


def convert_openai_tools_to_gemini(openai_tools: List) -> List[Dict[str, Any]]:
    """
    将 OpenAI tools 格式转换为 Gemini functionDeclarations 格式

    Args:
        openai_tools: OpenAI 格式的工具列表（可能是字典或 Pydantic 模型）

    Returns:
        Gemini 格式的工具列表
    """
    if not openai_tools:
        return []

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
            log.info(f"Function name normalized: '{original_name}' -> '{normalized_name}'")

        # 构建 Gemini function declaration
        declaration = {
            "name": normalized_name,
            "description": function.get("description", ""),
        }

        # 添加参数（如果有）- 清理不支持的 schema 字段
        if "parameters" in function:
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

    return {"functionResponse": {"id": original_tool_call_id, "name": name, "response": response_data}}


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

            tool_call = {
                "id": encoded_id,
                "type": "function",
                "function": {
                    "name": function_call.get("name", "nameless_function"),
                    "arguments": json.dumps(function_call.get("args", {})),
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
    system_instructions = []

    # 提取消息列表
    messages = openai_request.get("messages", [])

    # 第一阶段：收集连续的system消息
    collecting_system = True

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        # 处理工具消息（tool role）
        if role == "tool":
            tool_call_id = message.get("tool_call_id", "")
            func_name = message.get("name")

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

            if not func_name:
                func_name = "unknown_function"

            # 解析响应数据
            try:
                response_data = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                response_data = {"result": str(content)}

            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "id": tool_call_id,
                        "name": func_name,
                        "response": response_data
                    }
                }]
            })
            continue

        # 处理系统消息
        if role == "system":
            if collecting_system:
                if isinstance(content, str):
                    system_instructions.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if part.get("type") == "text" and part.get("text"):
                            system_instructions.append(part["text"])
                continue
            else:
                # 后续的system消息转换为user消息
                role = "user"
        else:
            collecting_system = False

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

                    parts.append({
                        "functionCall": {
                            "id": tool_call.get("id", ""),
                            "name": tool_call["function"]["name"],
                            "args": args
                        }
                    })
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
    if "temperature" in openai_request:
        generation_config["temperature"] = openai_request["temperature"]
    if "top_p" in openai_request:
        generation_config["topP"] = openai_request["top_p"]
    if "max_tokens" in openai_request:
        generation_config["maxOutputTokens"] = openai_request["max_tokens"]
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
    if "response_format" in openai_request:
        if openai_request["response_format"].get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"

    # 如果contents为空,添加默认用户消息
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "请根据系统指令回答。"}]})

    # 构建基础请求
    gemini_request = {
        "contents": contents,
        "generationConfig": generation_config
    }

    # 添加系统指令
    if system_instructions:
        gemini_request["systemInstruction"] = {
            "parts": [{"text": "\n\n".join(system_instructions)}]
        }

    # 处理工具
    if "tools" in openai_request and openai_request["tools"]:
        gemini_request["tools"] = convert_openai_tools_to_gemini(openai_request["tools"])

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

        # 提取图片数据
        images = []
        for part in parts:
            if "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                images.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}"
                    }
                })

        # 提取 reasoning content
        reasoning_content = ""
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]

        # 构建消息对象
        message = {"role": role}

        # 如果有工具调用
        if tool_calls:
            message["tool_calls"] = tool_calls
            message["content"] = text_content if text_content else None
            finish_reason = "tool_calls"
        # 如果有图片
        elif images:
            content_list = []
            if text_content:
                content_list.append({"type": "text", "text": text_content})
            content_list.extend(images)
            message["content"] = content_list
            finish_reason = _map_finish_reason(candidate.get("finishReason"))
        else:
            message["content"] = text_content
            finish_reason = _map_finish_reason(candidate.get("finishReason"))

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

        # 提取图片数据
        images = []
        for part in parts:
            if "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                images.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}"
                    }
                })

        # 提取 reasoning content
        reasoning_content = ""
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]

        # 构建 delta 对象
        delta = {}

        if tool_calls:
            delta["tool_calls"] = tool_calls
            if text_content:
                delta["content"] = text_content
        elif images:
            # 流式响应中的图片: 以 markdown 格式返回
            markdown_images = [f"![Generated Image]({img['image_url']['url']})" for img in images]
            if text_content:
                delta["content"] = text_content + "\n\n" + "\n\n".join(markdown_images)
            else:
                delta["content"] = "\n\n".join(markdown_images)
        elif text_content:
            delta["content"] = text_content

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content

        finish_reason = _map_finish_reason(candidate.get("finishReason"))
        if finish_reason and tool_calls:
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
