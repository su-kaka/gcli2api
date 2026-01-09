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
    generate_dummy_signature,
)

from config import (
    get_compatibility_mode_enabled,
)
from src.utils import (
    DEFAULT_SAFETY_SETTINGS,
    get_base_model_name,
    get_thinking_budget,
    is_search_model,
    should_include_thoughts,
)
from log import log

from src.models import ChatCompletionRequest, model_to_dict
from src.converter.gemini_fix import (
    normalize_gemini_request,
    build_system_instruction_from_list,
    prepare_image_generation_request,
)


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
                    # 解码工具调用ID以提取原始ID和签名
                    encoded_id = getattr(tool_call, "id", "") or ""
                    original_id, signature = decode_tool_id_and_signature(encoded_id)

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "id": original_id,
                            "name": tool_call.function.name,
                            "args": args
                        }
                    }

                    # 如果提取到签名则添加，否则为Gemini 3+生成占位签名
                    if signature:
                        fc_part["thoughtSignature"] = signature
                    else:
                        fc_part["thoughtSignature"] = generate_dummy_signature()

                    parts.append(fc_part)
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
                elif part.get("type") == "thinking":
                    # 处理 thinking 块（用于多轮对话中的历史回放）
                    thinking_text = part.get("thinking", "")
                    signature = part.get("signature")
                    if signature:  # 只有包含 signature 的 thinking 块才处理
                        thought_part = {
                            "text": thinking_text,
                            "thought": True,
                            "thoughtSignature": signature,
                        }
                        parts.append(thought_part)
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

    # 构建基础请求数据
    request_data = {
        "contents": contents,
        "generationConfig": generation_config,
    }

    log.debug(
        f"Request prepared: {len(contents)} messages, compatibility_mode: {compatibility_mode}"
    )

    # 从extra_body中取得thinking配置
    thinking_config_override = None
    try:
        thinking_override = (
            openai_request.extra_body.get("google", {}).get("thinking_config")
            if openai_request.extra_body
            else None
        )
        if thinking_override:  # 使用OPENAI的额外参数作为thinking参数
            thinking_config_override = {
                "thinkingBudget": thinking_override.get("thinking_budget"),
                "includeThoughts": thinking_override.get("include_thoughts", False),
            }
    except Exception:
        pass

    # 处理工具定义和配置
    gemini_tools = None
    if hasattr(openai_request, "tools") and openai_request.tools:
        gemini_tools = convert_openai_tools_to_gemini(openai_request.tools)

    # 处理 tool_choice
    if hasattr(openai_request, "tool_choice") and openai_request.tool_choice:
        request_data["toolConfig"] = convert_tool_choice_to_tool_config(openai_request.tool_choice)

    # 构建 system instruction
    system_instruction = build_system_instruction_from_list(system_instructions)

    # 使用统一的后处理函数
    request_data = normalize_gemini_request(
        request_data,
        model=openai_request.model,
        system_instruction=system_instruction,
        tools=gemini_tools,
        thinking_config_override=thinking_config_override,
        compatibility_mode=compatibility_mode,
        default_safety_settings=DEFAULT_SAFETY_SETTINGS,
        get_thinking_budget_func=get_thinking_budget,
        should_include_thoughts_func=should_include_thoughts,
        is_search_model_func=is_search_model,
    )

    # 构建基础 payload
    payload = {"model": get_base_model_name(openai_request.model), "request": request_data}

    # 图像生成模型特殊处理
    if "-image" in openai_request.model:
        log.debug(f"Detected image generation model: {openai_request.model}")
        payload = prepare_image_generation_request(payload, openai_request.model)

    # 返回完整的Gemini API payload格式
    return payload


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

    # 处理GeminiCLI的response包装格式
    if "response" in gemini_response:
        gemini_response = gemini_response["response"]

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
                # 转换为 OpenAI 的 data URI 格式
                images.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}"
                    }
                })

        # 提取 reasoning content 和 thinking 块
        reasoning_content = ""
        thinking_blocks = []
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]
                # 如果有 signature，构建 thinking 块
                signature = part.get("thoughtSignature")
                if signature:
                    thinking_blocks.append({
                        "type": "thinking",
                        "thinking": part["text"],
                        "signature": signature
                    })

        # 构建消息对象
        message = {"role": role}

        # 如果有工具调用
        if tool_calls:
            message["tool_calls"] = tool_calls
            # content 可以是 None 或包含文本
            message["content"] = text_content if text_content else None
            finish_reason = "tool_calls"
        # 如果有图片，构建包含图片的内容
        elif images:
            content_list = []
            # 如果有文本，先添加文本
            if text_content:
                content_list.append({"type": "text", "text": text_content})
            # 添加所有图片
            content_list.extend(images)
            message["content"] = content_list
            finish_reason = _map_finish_reason(candidate.get("finishReason"))
        else:
            message["content"] = text_content
            finish_reason = _map_finish_reason(candidate.get("finishReason"))

        # 添加 reasoning content（如果有）- 保持向后兼容
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        # 添加结构化的 thinking 块（如果有 signature）
        if thinking_blocks:
            message["thinking_blocks"] = thinking_blocks

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

    # 调试日志：查看原始 chunk 结构
    log.debug(f"[STREAM CONVERT] gemini_chunk keys: {gemini_chunk.keys()}")

    # GeminiCLI 返回的格式是 {"response": {...}, "traceId": "..."}
    # 需要先提取 response 字段
    if "response" in gemini_chunk:
        gemini_response = gemini_chunk["response"]
    else:
        gemini_response = gemini_chunk

    candidates = gemini_response.get("candidates", [])
    log.debug(f"[STREAM CONVERT] candidates count: {len(candidates)}")
    if candidates:
        log.debug(f"[STREAM CONVERT] first candidate: {candidates[0]}")

    for candidate in candidates:
        role = candidate.get("content", {}).get("role", "assistant")

        # 将Gemini角色映射回OpenAI角色
        if role == "model":
            role = "assistant"

        # 提取并分离thinking tokens和常规内容
        parts = candidate.get("content", {}).get("parts", [])
        log.debug(f"[STREAM CONVERT] parts: {parts}")

        # 提取工具调用和文本内容（流式响应需要 index 字段）
        tool_calls, text_content = extract_tool_calls_from_parts(parts, is_streaming=True)
        log.debug(f"[STREAM CONVERT] extracted - tool_calls: {len(tool_calls)}, text_content: '{text_content[:50] if text_content else ''}'...")

        # 提取图片数据
        images = []
        for part in parts:
            if "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                # 转换为 OpenAI 的 data URI 格式
                images.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{base64_data}"
                    }
                })

        # 提取 reasoning content 和 thinking 块
        reasoning_content = ""
        thinking_blocks = []
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]
                # 如果有 signature，构建 thinking 块
                signature = part.get("thoughtSignature")
                if signature:
                    thinking_blocks.append({
                        "type": "thinking",
                        "thinking": part["text"],
                        "signature": signature
                    })

        # 构建delta对象
        delta = {}

        if tool_calls:
            # 流式响应中的工具调用
            delta["tool_calls"] = tool_calls
            if text_content:
                delta["content"] = text_content
        elif images:
            # 流式响应中的图片：以 markdown 格式返回
            # 注意：OpenAI 流式响应的 delta.content 必须是字符串
            markdown_images = [f"![Generated Image]({img['image_url']['url']})" for img in images]
            if text_content:
                # 如果有文本，将图片 markdown 附加在文本后
                delta["content"] = text_content + "\n\n" + "\n\n".join(markdown_images)
            else:
                # 只有图片，返回图片 markdown
                delta["content"] = "\n\n".join(markdown_images)
        elif text_content:
            delta["content"] = text_content

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content

        # 添加结构化的 thinking 块（如果有 signature）
        if thinking_blocks:
            delta["thinking_blocks"] = thinking_blocks

        finish_reason = _map_finish_reason(candidate.get("finishReason"))
        # 如果有工具调用且结束了，finish_reason 应该是 tool_calls
        if finish_reason and tool_calls:
            finish_reason = "tool_calls"

        log.debug(f"[STREAM CONVERT] delta: {delta}, finish_reason: {finish_reason}")

        choices.append(
            {
                "index": candidate.get("index", 0),
                "delta": delta,
                "finish_reason": finish_reason,
            }
        )

    log.debug(f"[STREAM CONVERT] final choices count: {len(choices)}")

    # 转换usageMetadata为OpenAI格式（只在流结束时存在）
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

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

    注意: maxTokens 和 topK 的处理已移至统一的 normalize_gemini_request 函数中

    Args:
        request_data: 原始请求对象

    Returns:
        标准化后的请求对象
    """
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
        # 处理 Pydantic 模型
        if hasattr(tool, "model_dump") or hasattr(tool, "dict"):
            tool_dict = model_to_dict(tool)
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


def extract_fake_stream_content(response: Any) -> Tuple[str, str, Dict[str, int]]:
    """
    从 Gemini 非流式响应中提取内容，用于假流式处理
    
    Args:
        response: Gemini API 响应对象
    
    Returns:
        (content, reasoning_content, usage) 元组
    """
    from src.converter.gemini_fix import extract_content_and_reasoning
    
    # 解析响应体
    if hasattr(response, "body"):
        body_str = (
            response.body.decode()
            if isinstance(response.body, bytes)
            else str(response.body)
        )
    elif hasattr(response, "content"):
        body_str = (
            response.content.decode()
            if isinstance(response.content, bytes)
            else str(response.content)
        )
    else:
        body_str = str(response)

    try:
        response_data = json.loads(body_str)

        # GeminiCLI 返回的格式是 {"response": {...}, "traceId": "..."}
        # 需要先提取 response 字段
        if "response" in response_data:
            gemini_response = response_data["response"]
        else:
            gemini_response = response_data

        # 从Gemini响应中提取内容，使用思维链分离逻辑
        content = ""
        reasoning_content = ""
        if "candidates" in gemini_response and gemini_response["candidates"]:
            # Gemini格式响应 - 使用思维链分离
            candidate = gemini_response["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                parts = candidate["content"]["parts"]
                content, reasoning_content = extract_content_and_reasoning(parts)
        elif "choices" in gemini_response and gemini_response["choices"]:
            # OpenAI格式响应
            content = gemini_response["choices"][0].get("message", {}).get("content", "")

        # 如果没有正常内容但有思维内容，给出警告
        if not content and reasoning_content:
            log.warning("Fake stream response contains only thinking content")
            content = "[模型正在思考中，请稍后再试或重新提问]"
        
        # 如果完全没有内容，提供默认回复
        if not content:
            log.warning(f"No content found in response: {gemini_response}")
            content = "[响应为空，请重新尝试]"

        # 转换usageMetadata为OpenAI格式
        usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))
        
        return content, reasoning_content, usage

    except json.JSONDecodeError:
        # 如果不是JSON，直接返回原始文本
        return body_str, "", None


def create_openai_stream_chunk(
    content: str,
    reasoning_content: str = "",
    usage: Dict[str, int] = None,
    model: str = "gcli2api-streaming",
    finish_reason: str = "stop"
) -> Dict[str, Any]:
    """
    创建 OpenAI 格式的流式响应块
    
    Args:
        content: 主要内容
        reasoning_content: 推理内容（可选）
        usage: token使用情况（可选）
        model: 模型名称
        finish_reason: 结束原因
    
    Returns:
        OpenAI 格式的流式响应块字典
    """
    # 构建 delta
    delta = {"role": "assistant", "content": content}
    if reasoning_content:
        delta["reasoning_content"] = reasoning_content

    # 构建完整的OpenAI格式的流式响应块
    chunk = {
        "id": str(uuid.uuid4()),
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }

    # 只有在有usage数据时才添加usage字段（确保在最后一个chunk中）
    if usage:
        chunk["usage"] = usage

    return chunk


def create_openai_heartbeat_chunk() -> Dict[str, Any]:
    """
    创建 OpenAI 格式的心跳块（用于假流式）
    
    Returns:
        心跳响应块字典
    """
    return {
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ]
    }


def parse_gemini_stream_chunk(chunk: Union[bytes, str]) -> Dict[str, Any]:
    """
    解析 Gemini 流式响应的单个块

    Args:
        chunk: 原始响应块（bytes 或 str）

    Returns:
        解析后的 JSON 字典，如果解析失败返回 None
    """
    # 处理 bytes 类型
    if isinstance(chunk, bytes):
        if not chunk.startswith(b"data: "):
            return None
        # 解码 bytes 后再解析
        payload = chunk[len(b"data: "):].strip()
        try:
            return json.loads(payload.decode('utf-8', errors='ignore'))
        except json.JSONDecodeError:
            return None

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return None


def openai_messages_to_gemini_contents(
    messages: List[Any], 
    compatibility_mode: bool = False
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    将 OpenAI 消息列表转换为 Gemini/Antigravity contents 格式
    
    Args:
        messages: OpenAI 格式的消息列表
        compatibility_mode: 是否启用兼容性模式（将所有 system 消息转为 user 消息）
    
    Returns:
        (contents, system_instructions) 元组
    """
    contents = []
    system_instructions = []
    collecting_system = True if not compatibility_mode else False

    for msg in messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None)
        tool_call_id = getattr(msg, "tool_call_id", None)

        # 处理 system 消息
        if role == "system":
            if compatibility_mode:
                role = "user"
            elif collecting_system:
                # 收集连续的 system 消息到 system_instructions
                if isinstance(content, str):
                    system_instructions.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if part.get("type") == "text" and part.get("text"):
                            system_instructions.append(part["text"])
                continue
            else:
                # 后续的 system 消息转换为 user 消息
                role = "user"
        else:
            collecting_system = False

        # 处理 tool 消息
        if role == "tool":
            func_name = getattr(msg, "name", None)
            encoded_tool_call_id = tool_call_id or ""

            # 解码获取原始ID（functionResponse不需要签名）
            original_tool_call_id, _ = decode_tool_id_and_signature(encoded_tool_call_id)

            # 如果没有 name，尝试从之前的 assistant 消息中查找对应的 tool_call_id
            # 注意：使用编码ID查找，因为存储的是编码ID
            if not func_name and encoded_tool_call_id:
                for prev_msg in messages:
                    if getattr(prev_msg, "role", None) == "assistant":
                        prev_tool_calls = getattr(prev_msg, "tool_calls", None)
                        if prev_tool_calls:
                            for tc in prev_tool_calls:
                                if getattr(tc, "id", None) == encoded_tool_call_id:
                                    tc_func = getattr(tc, "function", None)
                                    if tc_func:
                                        func_name = getattr(tc_func, "name", None)
                                        break
                            if func_name:
                                break

            if not func_name:
                func_name = "unknown_function"

            # 尝试解析 JSON 响应
            try:
                response_data = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                response_data = {"output": str(content)}

            parts = [{
                "functionResponse": {
                    "id": original_tool_call_id,  # 使用解码后的ID以匹配functionCall
                    "name": func_name,
                    "response": response_data
                }
            }]
            contents.append({"role": "user", "parts": parts})
            continue

        # 将 OpenAI 角色映射到 Gemini 角色
        if role == "assistant":
            role = "model"

        # 处理 assistant 消息中的工具调用
        if tool_calls:
            parts = []
            
            # 如果有文本内容，先添加文本
            if content:
                extracted = extract_images_from_content(content)
                if extracted["text"]:
                    parts.append({"text": extracted["text"]})
                parts.extend(extracted["images"])
            
            # 添加工具调用
            for tool_call in tool_calls:
                encoded_tc_id = getattr(tool_call, "id", None) or ""
                tc_function = getattr(tool_call, "function", None)

                if tc_function:
                    func_name = getattr(tc_function, "name", "")
                    func_args = getattr(tc_function, "arguments", "{}")

                    # 解析 arguments（可能是字符串）
                    if isinstance(func_args, str):
                        try:
                            args_dict = json.loads(func_args)
                        except:
                            args_dict = {"query": func_args}
                    else:
                        args_dict = func_args

                    # 解码工具调用ID以提取原始ID和签名
                    original_id, signature = decode_tool_id_and_signature(encoded_tc_id)

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            "id": original_id,
                            "name": func_name,
                            "args": args_dict
                        }
                    }

                    # 如果提取到签名则添加，否则为Gemini 3+生成占位签名
                    if signature:
                        fc_part["thoughtSignature"] = signature
                    else:
                        fc_part["thoughtSignature"] = generate_dummy_signature()

                    parts.append(fc_part)
            
            if parts:
                contents.append({"role": role, "parts": parts})
            continue

        # 处理普通内容（user 或 assistant 消息）
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
            # 简单文本内容
            extracted = extract_images_from_content(content)
            parts = []
            if extracted["text"]:
                parts.append({"text": extracted["text"]})
            parts.extend(extracted["images"])
            
            if parts:
                contents.append({"role": role, "parts": parts})

    return contents, system_instructions
