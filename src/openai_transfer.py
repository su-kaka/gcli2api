"""
OpenAI Transfer Module - Handles conversion between OpenAI and Gemini API formats
被openai-router调用，负责OpenAI格式与Gemini格式的双向转换
"""

import json
import time
import uuid
from typing import Any, Dict, List, Tuple, Union

from pypinyin import Style, lazy_pinyin

from config import (
    DEFAULT_SAFETY_SETTINGS,
    get_base_model_name,
    get_compatibility_mode_enabled,
    get_thinking_budget,
    is_search_model,
    should_include_thoughts,
)
from log import log

from .models import ChatCompletionRequest


async def openai_request_to_gemini_payload(
    openai_request: ChatCompletionRequest,
) -> Dict[str, Any]:
    """
    将OpenAI聊天完成请求直接转换为完整的Gemini API payload格式

    Args:
        openai_request: OpenAI格式请求对象

    Returns:
        完整的Gemini API payload，包含model和request字段
    """
    contents = []
    system_instructions = []

    # 检查是否启用兼容性模式
    compatibility_mode = await get_compatibility_mode_enabled()

    # 处理对话中的每条消息
    # 第一阶段：收集连续的system消息到system_instruction中（除非在兼容性模式下）
    collecting_system = True if not compatibility_mode else False

    for message in openai_request.messages:
        role = message.role

        # 处理工具消息（tool role）
        if role == "tool":
            # 转换工具结果消息为 functionResponse
            # 传递所有消息以便在需要时查找 function name
            function_response = convert_tool_message_to_function_response(
                message, all_messages=openai_request.messages
            )
            contents.append(
                {"role": "user", "parts": [function_response]}  # Gemini 中工具响应作为 user 消息
            )
            continue

        # 处理系统消息
        if role == "system":
            if compatibility_mode:
                # 兼容性模式：所有system消息转换为user消息
                role = "user"
            elif collecting_system:
                # 正常模式：仍在收集连续的system消息
                if isinstance(message.content, str):
                    system_instructions.append(message.content)
                elif isinstance(message.content, list):
                    # 处理列表格式的系统消息
                    for part in message.content:
                        if part.get("type") == "text" and part.get("text"):
                            system_instructions.append(part["text"])
                continue
            else:
                # 正常模式：后续的system消息转换为user消息
                role = "user"
        else:
            # 遇到非system消息，停止收集system消息
            collecting_system = False

        # 将OpenAI角色映射到Gemini角色
        if role == "assistant":
            role = "model"

        # 检查是否有 tool_calls（assistant 消息中的工具调用）
        has_tool_calls = hasattr(message, "tool_calls") and message.tool_calls

        if has_tool_calls:
            # 构建包含 functionCall 的 parts
            parts = []
            parsed_count = 0

            # 如果有文本内容，先添加文本
            if message.content:
                parts.append({"text": message.content})

            # 添加每个工具调用
            for tool_call in message.tool_calls:
                try:
                    # 解析 arguments（OpenAI 格式是 JSON 字符串）
                    args = (
                        json.loads(tool_call.function.arguments)
                        if isinstance(tool_call.function.arguments, str)
                        else tool_call.function.arguments
                    )
                    parts.append({"functionCall": {"name": tool_call.function.name, "args": args}})
                    parsed_count += 1
                except (json.JSONDecodeError, AttributeError) as e:
                    log.error(
                        f"Failed to parse tool call '{getattr(tool_call.function, 'name', 'unknown')}': {e}"
                    )
                    continue

            # 检查是否至少解析了一个工具调用
            if parsed_count == 0 and message.tool_calls:
                log.error(f"All {len(message.tool_calls)} tool calls failed to parse")
                # 如果没有文本内容且所有工具调用都失败，这是一个严重错误
                if not message.content:
                    raise ValueError(
                        f"All {len(message.tool_calls)} tool calls failed to parse and no content available"
                    )

            if parts:
                contents.append({"role": role, "parts": parts})
            continue

        # 处理普通内容
        if isinstance(message.content, list):
            parts = []
            for part in message.content:
                if part.get("type") == "text":
                    parts.append({"text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url")
                    if image_url:
                        # 解析数据URI: "data:image/jpeg;base64,{base64_image}"
                        try:
                            mime_type, base64_data = image_url.split(";")
                            _, mime_type = mime_type.split(":")
                            _, base64_data = base64_data.split(",")
                            parts.append(
                                {
                                    "inlineData": {
                                        "mimeType": mime_type,
                                        "data": base64_data,
                                    }
                                }
                            )
                        except ValueError:
                            continue
            contents.append({"role": role, "parts": parts})
            # log.debug(f"Added message to contents: role={role}, parts={parts}")
        elif message.content:
            # 简单文本内容
            contents.append({"role": role, "parts": [{"text": message.content}]})
            # log.debug(f"Added message to contents: role={role}, content={message.content}")

    # 将OpenAI生成参数映射到Gemini格式
    generation_config = {}
    if openai_request.temperature is not None:
        generation_config["temperature"] = openai_request.temperature
    if openai_request.top_p is not None:
        generation_config["topP"] = openai_request.top_p
    if openai_request.max_tokens is not None:
        generation_config["maxOutputTokens"] = openai_request.max_tokens
    if openai_request.stop is not None:
        # Gemini支持停止序列
        if isinstance(openai_request.stop, str):
            generation_config["stopSequences"] = [openai_request.stop]
        elif isinstance(openai_request.stop, list):
            generation_config["stopSequences"] = openai_request.stop
    if openai_request.frequency_penalty is not None:
        generation_config["frequencyPenalty"] = openai_request.frequency_penalty
    if openai_request.presence_penalty is not None:
        generation_config["presencePenalty"] = openai_request.presence_penalty
    if openai_request.n is not None:
        generation_config["candidateCount"] = openai_request.n
    if openai_request.seed is not None:
        generation_config["seed"] = openai_request.seed
    if openai_request.response_format is not None:
        # 处理JSON模式
        if openai_request.response_format.get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"

    # 如果contents为空（只有系统消息的情况），添加一个默认的用户消息以满足Gemini API要求
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "请根据系统指令回答。"}]})

    # 构建请求数据
    request_data = {
        "contents": contents,
        "generationConfig": generation_config,
        "safetySettings": DEFAULT_SAFETY_SETTINGS,
    }

    # 如果有系统消息且未启用兼容性模式，添加systemInstruction
    if system_instructions and not compatibility_mode:
        combined_system_instruction = "\n\n".join(system_instructions)
        request_data["systemInstruction"] = {"parts": [{"text": combined_system_instruction}]}

    log.debug(
        f"Request prepared: {len(contents)} messages, compatibility_mode: {compatibility_mode}"
    )

    # 为thinking模型添加thinking配置
    thinking_budget = get_thinking_budget(openai_request.model)
    if thinking_budget is not None:
        request_data["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": thinking_budget,
            "includeThoughts": should_include_thoughts(openai_request.model),
        }

    # 处理工具定义和配置
    # 首先检查是否有自定义工具
    if hasattr(openai_request, "tools") and openai_request.tools:
        gemini_tools = convert_openai_tools_to_gemini(openai_request.tools)
        if gemini_tools:
            request_data["tools"] = gemini_tools

    # 为搜索模型添加Google Search工具（如果还没有tools）
    if is_search_model(openai_request.model):
        if "tools" not in request_data:
            request_data["tools"] = [{"googleSearch": {}}]
        else:
            # 如果已有工具，检查是否需要添加 Google Search
            has_google_search = any(
                tool.get("googleSearch") for tool in request_data.get("tools", [])
            )
            if not has_google_search:
                request_data["tools"].append({"googleSearch": {}})

    # 处理 tool_choice
    if hasattr(openai_request, "tool_choice") and openai_request.tool_choice:
        request_data["toolConfig"] = convert_tool_choice_to_tool_config(openai_request.tool_choice)

    # 移除None值
    request_data = {k: v for k, v in request_data.items() if v is not None}

    # 返回完整的Gemini API payload格式
    return {"model": get_base_model_name(openai_request.model), "request": request_data}


def _extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容和推理内容"""
    content = ""
    reasoning_content = ""

    for part in parts:
        # 处理文本内容
        if part.get("text"):
            # 检查这个部件是否包含thinking tokens
            if part.get("thought", False):
                reasoning_content += part.get("text", "")
            else:
                content += part.get("text", "")

    return content, reasoning_content


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


def gemini_response_to_openai(gemini_response: Dict[str, Any], model: str) -> Dict[str, Any]:
    """
    将Gemini API响应转换为OpenAI聊天完成格式

    Args:
        gemini_response: 来自Gemini API的响应
        model: 要在响应中包含的模型名称

    Returns:
        OpenAI聊天完成格式的字典
    """

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

        # 提取 reasoning content (thinking tokens)
        reasoning_content = ""
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]

        # 构建消息对象
        message = {"role": role}

        # 如果有工具调用
        if tool_calls:
            message["tool_calls"] = tool_calls
            # content 可以是 None 或包含文本
            message["content"] = text_content if text_content else None
            finish_reason = "tool_calls"
        else:
            message["content"] = text_content
            finish_reason = _map_finish_reason(candidate.get("finishReason"))

        # 添加 reasoning content（如果有）
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        choices.append(
            {
                "index": candidate.get("index", 0),
                "message": message,
                "finish_reason": finish_reason,
            }
        )

    # 转换usageMetadata为OpenAI格式
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

    response_data = {
        "id": str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

    # 只有在有usage数据时才添加usage字段
    if usage:
        response_data["usage"] = usage

    return response_data


def gemini_stream_chunk_to_openai(
    gemini_chunk: Dict[str, Any], model: str, response_id: str
) -> Dict[str, Any]:
    """
    将Gemini流式响应块转换为OpenAI流式格式

    Args:
        gemini_chunk: 来自Gemini流式响应的单个块
        model: 要在响应中包含的模型名称
        response_id: 此流式响应的一致ID

    Returns:
        OpenAI流式格式的字典
    """
    choices = []

    for candidate in gemini_chunk.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")

        # 将Gemini角色映射回OpenAI角色
        if role == "model":
            role = "assistant"

        # 提取并分离thinking tokens和常规内容
        parts = candidate.get("content", {}).get("parts", [])

        # 提取工具调用和文本内容（流式响应需要 index 字段）
        tool_calls, text_content = extract_tool_calls_from_parts(parts, is_streaming=True)

        # 提取 reasoning content
        reasoning_content = ""
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]

        # 构建delta对象
        delta = {}

        if tool_calls:
            # 流式响应中的工具调用
            delta["tool_calls"] = tool_calls
            if text_content:
                delta["content"] = text_content
        elif text_content:
            delta["content"] = text_content

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content

        finish_reason = _map_finish_reason(candidate.get("finishReason"))
        # 如果有工具调用且结束了，finish_reason 应该是 tool_calls
        if finish_reason and tool_calls:
            finish_reason = "tool_calls"

        choices.append(
            {
                "index": candidate.get("index", 0),
                "delta": delta,
                "finish_reason": finish_reason,
            }
        )

    # 转换usageMetadata为OpenAI格式（只在流结束时存在）
    usage = _convert_usage_metadata(gemini_chunk.get("usageMetadata"))

    # 构建基础响应数据（确保所有必需字段都存在）
    response_data = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

    # 只有在有usage数据且这是最后一个chunk时才添加usage字段
    # 这确保了codex-server能正确识别和记录用量
    if usage:
        has_finish_reason = any(choice.get("finish_reason") for choice in choices)
        if has_finish_reason:
            response_data["usage"] = usage

    return response_data


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


def validate_openai_request(request_data: Dict[str, Any]) -> ChatCompletionRequest:
    """
    验证并标准化OpenAI请求数据

    Args:
        request_data: 原始请求数据字典

    Returns:
        验证后的ChatCompletionRequest对象

    Raises:
        ValueError: 当请求数据无效时
    """
    try:
        return ChatCompletionRequest(**request_data)
    except Exception as e:
        raise ValueError(f"Invalid OpenAI request format: {str(e)}")


def normalize_openai_request(
    request_data: ChatCompletionRequest,
) -> ChatCompletionRequest:
    """
    标准化OpenAI请求数据，应用默认值和限制

    Args:
        request_data: 原始请求对象

    Returns:
        标准化后的请求对象
    """
    # 限制max_tokens
    if getattr(request_data, "max_tokens", None) is not None and request_data.max_tokens > 65535:
        request_data.max_tokens = 65535

    # 覆写 top_k 为 64
    setattr(request_data, "top_k", 64)

    # 过滤空消息
    filtered_messages = []
    for m in request_data.messages:
        content = getattr(m, "content", None)
        if content:
            if isinstance(content, str) and content.strip():
                filtered_messages.append(m)
            elif isinstance(content, list) and len(content) > 0:
                has_valid_content = False
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text" and part.get("text", "").strip():
                            has_valid_content = True
                            break
                        elif part.get("type") == "image_url" and part.get("image_url", {}).get(
                            "url"
                        ):
                            has_valid_content = True
                            break
                if has_valid_content:
                    filtered_messages.append(m)

    request_data.messages = filtered_messages

    return request_data


def is_health_check_request(request_data: ChatCompletionRequest) -> bool:
    """
    检查是否为健康检查请求

    Args:
        request_data: 请求对象

    Returns:
        是否为健康检查请求
    """
    return (
        len(request_data.messages) == 1
        and getattr(request_data.messages[0], "role", None) == "user"
        and getattr(request_data.messages[0], "content", None) == "Hi"
    )


def create_health_check_response() -> Dict[str, Any]:
    """
    创建健康检查响应

    Returns:
        健康检查响应字典
    """
    return {"choices": [{"message": {"role": "assistant", "content": "gcli2api正常工作中"}}]}


def extract_model_settings(model: str) -> Dict[str, Any]:
    """
    从模型名称中提取设置信息

    Args:
        model: 模型名称

    Returns:
        包含模型设置的字典
    """
    return {
        "base_model": get_base_model_name(model),
        "use_fake_streaming": model.endswith("-假流式"),
        "thinking_budget": get_thinking_budget(model),
        "include_thoughts": should_include_thoughts(model),
    }


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

    # Gemini 不支持的字段（官方文档 + GitHub Issues 确认）
    # 参考: github.com/googleapis/python-genai/issues/699, #388, #460, #1122, #264, #4551
    # example (OpenAPI 3.0) 和 examples (JSON Schema) 都不支持
    unsupported_keys = {
        '$schema', '$id', '$ref', '$defs', 'definitions',
        'title', 'example', 'examples', 'readOnly', 'writeOnly',
        'default',
        'exclusiveMaximum', 'exclusiveMinimum',
        'oneOf', 'anyOf', 'allOf', 'const',
        'additionalItems', 'contains', 'patternProperties',
        'dependencies', 'propertyNames', 'if', 'then', 'else',
        'contentEncoding', 'contentMediaType',
    }

    cleaned = {}
    for key, value in schema.items():
        if key in unsupported_keys:
            continue
        if isinstance(value, dict):
            cleaned[key] = _clean_schema_for_gemini(value)
        elif isinstance(value, list):
            cleaned[key] = [
                _clean_schema_for_gemini(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            cleaned[key] = value

    # 确保有 type 字段（如果有 properties 但没有 type）
    if 'properties' in cleaned and 'type' not in cleaned:
        cleaned['type'] = 'object'

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
        # 处理 Pydantic 模型
        if hasattr(tool, "model_dump"):
            tool_dict = tool.model_dump()
        elif hasattr(tool, "dict"):
            tool_dict = tool.dict()
        else:
            tool_dict = tool

        if tool_dict.get("type") != "function":
            log.warning(f"Skipping non-function tool type: {tool_dict.get('type')}")
            continue

        function = tool_dict.get("function")
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
        all_messages: 所有消息的列表，用于在缺少 name 时查找对应的 tool_call

    Returns:
        Gemini 格式的 functionResponse part

    Raises:
        ValueError: 如果 tool 消息缺少必需的 name 字段且无法从历史中推断
    """
    # 获取 name 字段
    name = None
    if hasattr(message, "name") and message.name:
        name = message.name
    else:
        # 尝试从历史消息中查找对应的 tool_call
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id and all_messages:
            # 遍历历史消息，查找包含此 tool_call_id 的 assistant 消息
            for hist_msg in all_messages:
                if (
                    hasattr(hist_msg, "role")
                    and hist_msg.role == "assistant"
                    and hasattr(hist_msg, "tool_calls")
                    and hist_msg.tool_calls
                ):
                    # 在 tool_calls 中查找匹配的 id
                    for tool_call in hist_msg.tool_calls:
                        if tool_call.id == tool_call_id:
                            name = tool_call.function.name
                            log.info(
                                f"Tool message missing 'name' field, "
                                f"inferred from history: {name} (tool_call_id={tool_call_id})"
                            )
                            break
                    if name:
                        break

        # 如果仍然没有找到 name
        if not name:
            content_preview = (
                str(message.content)[:100] if hasattr(message, "content") else "no content"
            )
            error_msg = (
                f"Tool message must have a 'name' field. "
                f"The 'name' field is required to match the tool call with its response in Gemini API. "
                f"tool_call_id={tool_call_id or 'missing'}, content preview: {content_preview}... "
                f"Please ensure your client sends tool messages with the 'name' field set to the function name."
            )
            log.error(error_msg)
            raise ValueError(error_msg)

    try:
        # 尝试将 content 解析为 JSON
        response_data = (
            json.loads(message.content) if isinstance(message.content, str) else message.content
        )
    except (json.JSONDecodeError, TypeError):
        # 如果不是有效的 JSON，包装为对象
        response_data = {"result": str(message.content)}

    return {"functionResponse": {"name": name, "response": response_data}}


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
            tool_call = {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": function_call.get("name"),
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
