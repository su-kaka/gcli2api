"""
Antigravity Router - Handles OpenAI and Gemini format requests and converts to Antigravity API
处理 OpenAI 和 Gemini 格式请求并转换为 Antigravity API 格式
"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import get_anti_truncation_max_attempts
from log import log
from src.utils import is_anti_truncation_model, authenticate_bearer, authenticate_gemini_flexible, authenticate_sdwebui_flexible

from .antigravity_api import (
    build_antigravity_request_body,
    send_antigravity_request_no_stream,
    send_antigravity_request_stream,
    fetch_available_models,
)
from .credential_manager import CredentialManager
from .models import (
    ChatCompletionRequest,
    GeminiGenerationConfig,
    Model,
    ModelList,
    model_to_dict,
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionResponse,
    OpenAIChatMessage,
    OpenAIToolCall,
    OpenAIToolFunction,
)
from .anti_truncation import (
    apply_anti_truncation_to_stream,
)

# 创建路由器
router = APIRouter()

# 全局凭证管理器实例
credential_manager = None


async def get_credential_manager():
    """获取全局凭证管理器实例"""
    global credential_manager
    if not credential_manager:
        credential_manager = CredentialManager()
        await credential_manager.initialize()
    return credential_manager


# 模型名称映射
def model_mapping(model_name: str) -> str:
    """
    OpenAI 模型名映射到 Antigravity 实际模型名

    参考文档:
    - claude-sonnet-4-5-thinking -> claude-sonnet-4-5
    - claude-opus-4-5 -> claude-opus-4-5-thinking
    - gemini-2.5-flash-thinking -> gemini-2.5-flash
    """
    mapping = {
        "claude-sonnet-4-5-thinking": "claude-sonnet-4-5",
        "claude-opus-4-5": "claude-opus-4-5-thinking",
        "gemini-2.5-flash-thinking": "gemini-2.5-flash",
    }
    return mapping.get(model_name, model_name)


def is_thinking_model(model_name: str) -> bool:
    """检测是否是思考模型"""
    # 检查是否包含 -thinking 后缀
    if "-thinking" in model_name:
        return True

    # 检查是否包含 pro 关键词
    if "pro" in model_name.lower():
        return True

    return False


def extract_images_from_content(content: Any) -> Dict[str, Any]:
    """
    从 OpenAI content 中提取文本和图片
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


def openai_messages_to_antigravity_contents(messages: List[Any]) -> List[Dict[str, Any]]:
    """
    将 OpenAI 消息格式转换为 Antigravity contents 格式
    """
    contents = []
    system_messages = []

    for msg in messages:
        role = getattr(msg, "role", "user")
        content = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None)
        tool_call_id = getattr(msg, "tool_call_id", None)

        # 处理 system 消息 - 合并到第一条用户消息
        if role == "system":
            system_messages.append(content)
            continue

        # 处理 user 消息
        elif role == "user":
            parts = []

            # 如果有系统消息，添加到第一条用户消息
            if system_messages:
                for sys_msg in system_messages:
                    parts.append({"text": sys_msg})
                system_messages = []

            # 提取文本和图片
            extracted = extract_images_from_content(content)
            if extracted["text"]:
                parts.append({"text": extracted["text"]})
            parts.extend(extracted["images"])

            if parts:
                contents.append({"role": "user", "parts": parts})

        # 处理 assistant 消息
        elif role == "assistant":
            parts = []

            # 添加文本内容
            if content:
                extracted = extract_images_from_content(content)
                if extracted["text"]:
                    parts.append({"text": extracted["text"]})

            # 添加工具调用
            if tool_calls:
                for tool_call in tool_calls:
                    tc_id = getattr(tool_call, "id", None)
                    tc_type = getattr(tool_call, "type", "function")
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

                        parts.append({
                            "functionCall": {
                                "id": tc_id,
                                "name": func_name,
                                "args": args_dict
                            }
                        })

            if parts:
                contents.append({"role": "model", "parts": parts})

        # 处理 tool 消息
        elif role == "tool":
            parts = [{
                "functionResponse": {
                    "id": tool_call_id,
                    "name": getattr(msg, "name", "unknown"),
                    "response": {"output": content}
                }
            }]
            contents.append({"role": "user", "parts": parts})

    return contents


def gemini_contents_to_antigravity_contents(gemini_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 Gemini 原生 contents 格式转换为 Antigravity contents 格式
    Gemini 和 Antigravity 的 contents 格式基本一致，只需要做少量调整
    """
    contents = []

    for content in gemini_contents:
        role = content.get("role", "user")
        parts = content.get("parts", [])

        contents.append({
            "role": role,
            "parts": parts
        })

    return contents


def convert_openai_tools_to_antigravity(tools: Optional[List[Any]]) -> Optional[List[Dict[str, Any]]]:
    """
    将 OpenAI 工具定义转换为 Antigravity 格式
    """
    if not tools:
        return None

    # 需要排除的字段
    EXCLUDED_KEYS = {'$schema', 'additionalProperties', 'minLength', 'maxLength',
                     'minItems', 'maxItems', 'uniqueItems'}

    def clean_parameters(obj):
        """递归清理参数对象"""
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                if key in EXCLUDED_KEYS:
                    continue
                cleaned[key] = clean_parameters(value)
            return cleaned
        elif isinstance(obj, list):
            return [clean_parameters(item) for item in obj]
        else:
            return obj

    function_declarations = []

    for tool in tools:
        tool_type = getattr(tool, "type", "function")
        if tool_type == "function":
            function = getattr(tool, "function", None)
            if function:
                func_name = function.get("name")
                assert func_name is not None, "Function name is required"
                func_desc = function.get("description", "")
                func_params = function.get("parameters", {})

                # 转换为字典（如果是 Pydantic 模型）
                if hasattr(func_params, "dict") or hasattr(func_params, "model_dump"):
                    func_params = model_to_dict(func_params)

                # 清理参数
                cleaned_params = clean_parameters(func_params)

                function_declarations.append({
                    "name": func_name,
                    "description": func_desc,
                    "parameters": cleaned_params
                })

    if function_declarations:
        return [{"functionDeclarations": function_declarations}]

    return None


def generate_generation_config(
    parameters: Dict[str, Any],
    enable_thinking: bool,
    model_name: str
) -> Dict[str, Any]:
    """
    生成 Antigravity generationConfig，使用 GeminiGenerationConfig 模型
    """
    # 构建基础配置
    config_dict = {
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>"
        ],
        "topK": parameters.get("top_k", 50),  # 默认值 50
    }

    # 添加可选参数
    if "temperature" in parameters:
        config_dict["temperature"] = parameters["temperature"]

    if "top_p" in parameters:
        config_dict["topP"] = parameters["top_p"]

    if "max_tokens" in parameters:
        config_dict["maxOutputTokens"] = parameters["max_tokens"]

    # 图片生成相关参数
    if "response_modalities" in parameters:
        config_dict["response_modalities"] = parameters["response_modalities"]

    if "image_config" in parameters:
        config_dict["image_config"] = parameters["image_config"]

    # 思考模型配置
    if enable_thinking:
        config_dict["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": 1024
        }

        # Claude 思考模型：删除 topP 参数
        if "claude" in model_name.lower():
            config_dict.pop("topP", None)

    # 使用 GeminiGenerationConfig 模型进行验证
    try:
        config = GeminiGenerationConfig(**config_dict)
        return config.model_dump(exclude_none=True)
    except Exception as e:
        log.warning(f"[ANTIGRAVITY] Failed to validate generation config: {e}, using dict directly")
        return config_dict


def convert_to_openai_tool_call(function_call: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Antigravity functionCall 转换为 OpenAI tool_call，使用 OpenAIToolCall 模型
    """
    tool_call = OpenAIToolCall(
        id=function_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
        type="function",
        function=OpenAIToolFunction(
            name=function_call.get("name", ""),
            arguments=json.dumps(function_call.get("args", {}))
        )
    )
    return model_to_dict(tool_call)


async def convert_antigravity_stream_to_openai(
    lines_generator: Any,
    stream_ctx: Any,
    client: Any,
    model: str,
    request_id: str,
    credential_manager: Any,
    credential_name: str
):
    """
    将 Antigravity 流式响应转换为 OpenAI 格式的 SSE 流

    Args:
        lines_generator: 行生成器 (已经过滤的 SSE 行)
    """
    state = {
        "thinking_started": False,
        "tool_calls": [],
        "content_buffer": "",
        "thinking_buffer": "",
        "success_recorded": False
    }

    created = int(time.time())

    try:
        def build_content_chunk(content: str) -> str:
            chunk = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None
                }]
            }
            return f"data: {json.dumps(chunk)}\n\n"

        def flush_thinking_buffer() -> Optional[str]:
            if not state["thinking_started"]:
                return None
            state["thinking_buffer"] += "\n</think>\n"
            thinking_block = state["thinking_buffer"]
            state["content_buffer"] += thinking_block
            state["thinking_buffer"] = ""
            state["thinking_started"] = False
            return thinking_block

        async for line in lines_generator:
            if not line or not line.startswith("data: "):
                continue

            # 记录第一次成功响应
            if not state["success_recorded"]:
                if credential_name and credential_manager:
                    await credential_manager.record_api_call_result(credential_name, True, is_antigravity=True)
                state["success_recorded"] = True

            # 解析 SSE 数据
            try:
                data = json.loads(line[6:])  # 去掉 "data: " 前缀
            except:
                continue

            # 提取 parts
            parts = data.get("response", {}).get("candidates", [{}])[0].get("content", {}).get("parts", [])

            for part in parts:
                # 处理思考内容
                if part.get("thought") is True:
                    if not state["thinking_started"]:
                        state["thinking_buffer"] = "<think>\n"
                        state["thinking_started"] = True
                    state["thinking_buffer"] += part.get("text", "")

                # 处理图片数据 (inlineData)
                elif "inlineData" in part:
                    # 如果之前在思考，先结束思考
                    thinking_block = flush_thinking_buffer()
                    if thinking_block:
                        yield build_content_chunk(thinking_block)

                    # 提取图片数据
                    inline_data = part["inlineData"]
                    mime_type = inline_data.get("mimeType", "image/png")
                    base64_data = inline_data.get("data", "")

                    # 转换为 Markdown 格式的图片
                    image_markdown = f"\n\n![生成的图片](data:{mime_type};base64,{base64_data})\n\n"
                    state["content_buffer"] += image_markdown

                    # 发送图片块
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": image_markdown},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

                # 处理普通文本
                elif "text" in part:
                    # 如果之前在思考，先结束思考
                    thinking_block = flush_thinking_buffer()
                    if thinking_block:
                        yield build_content_chunk(thinking_block)

                    # 添加文本内容
                    text = part.get("text", "")
                    state["content_buffer"] += text

                    # 发送文本块
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": text},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

                # 处理工具调用
                elif "functionCall" in part:
                    tool_call = convert_to_openai_tool_call(part["functionCall"])
                    state["tool_calls"].append(tool_call)

            # 检查是否结束
            finish_reason = data.get("response", {}).get("candidates", [{}])[0].get("finishReason")
            if finish_reason:
                thinking_block = flush_thinking_buffer()
                if thinking_block:
                    yield build_content_chunk(thinking_block)

                # 发送工具调用
                if state["tool_calls"]:
                    chunk = {
                        "id": request_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"tool_calls": state["tool_calls"]},
                            "finish_reason": None
                        }]
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

                # 发送使用统计
                usage_metadata = data.get("response", {}).get("usageMetadata", {})
                usage = {
                    "prompt_tokens": usage_metadata.get("promptTokenCount", 0),
                    "completion_tokens": usage_metadata.get("candidatesTokenCount", 0),
                    "total_tokens": usage_metadata.get("totalTokenCount", 0)
                }

                # 确定 finish_reason
                openai_finish_reason = "stop"
                if state["tool_calls"]:
                    openai_finish_reason = "tool_calls"
                elif finish_reason == "MAX_TOKENS":
                    openai_finish_reason = "length"

                chunk = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {},
                        "finish_reason": openai_finish_reason
                    }],
                    "usage": usage
                }
                yield f"data: {json.dumps(chunk)}\n\n"

        # 发送结束标记
        yield "data: [DONE]\n\n"

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Streaming error: {e}")
        error_response = {
            "error": {
                "message": str(e),
                "type": "api_error",
                "code": 500
            }
        }
        yield f"data: {json.dumps(error_response)}\n\n"
    finally:
        # 确保清理所有资源
        try:
            await stream_ctx.__aexit__(None, None, None)
        except Exception as e:
            log.debug(f"[ANTIGRAVITY] Error closing stream context: {e}")
        try:
            await client.aclose()
        except Exception as e:
            log.debug(f"[ANTIGRAVITY] Error closing client: {e}")


def convert_antigravity_response_to_openai(
    response_data: Dict[str, Any],
    model: str,
    request_id: str
) -> Dict[str, Any]:
    """
    将 Antigravity 非流式响应转换为 OpenAI 格式
    """
    # 提取 parts
    parts = response_data.get("response", {}).get("candidates", [{}])[0].get("content", {}).get("parts", [])

    content = ""
    thinking_content = ""
    tool_calls_list = []

    for part in parts:
        # 处理思考内容
        if part.get("thought") is True:
            thinking_content += part.get("text", "")

        # 处理图片数据 (inlineData)
        elif "inlineData" in part:
            inline_data = part["inlineData"]
            mime_type = inline_data.get("mimeType", "image/png")
            base64_data = inline_data.get("data", "")
            # 转换为 Markdown 格式的图片
            content += f"\n\n![生成的图片](data:{mime_type};base64,{base64_data})\n\n"

        # 处理普通文本
        elif "text" in part:
            content += part.get("text", "")

        # 处理工具调用
        elif "functionCall" in part:
            tool_calls_list.append(convert_to_openai_tool_call(part["functionCall"]))

    # 拼接思考内容
    if thinking_content:
        content = f"<think>\n{thinking_content}\n</think>\n{content}"

    # 使用 OpenAIChatMessage 模型构建消息
    message = OpenAIChatMessage(
        role="assistant",
        content=content,
        tool_calls=tool_calls_list if tool_calls_list else None
    )

    # 确定 finish_reason
    finish_reason = "stop"
    if tool_calls_list:
        finish_reason = "tool_calls"

    finish_reason_raw = response_data.get("response", {}).get("candidates", [{}])[0].get("finishReason")
    if finish_reason_raw == "MAX_TOKENS":
        finish_reason = "length"

    # 提取使用统计
    usage_metadata = response_data.get("response", {}).get("usageMetadata", {})
    usage = {
        "prompt_tokens": usage_metadata.get("promptTokenCount", 0),
        "completion_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0)
    }

    # 使用 OpenAIChatCompletionChoice 模型
    choice = OpenAIChatCompletionChoice(
        index=0,
        message=message,
        finish_reason=finish_reason
    )

    # 使用 OpenAIChatCompletionResponse 模型
    response = OpenAIChatCompletionResponse(
        id=request_id,
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=usage
    )

    return model_to_dict(response)


def convert_antigravity_response_to_gemini(
    response_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    将 Antigravity 非流式响应转换为 Gemini 格式
    Antigravity 的响应格式与 Gemini 非常相似，只需要提取 response 字段
    """
    # Antigravity 响应格式: {"response": {...}}
    # Gemini 响应格式: {...}
    return response_data.get("response", response_data)


async def convert_antigravity_stream_to_gemini(
    lines_generator: Any,
    stream_ctx: Any,
    client: Any,
    credential_manager: Any,
    credential_name: str
):
    """
    将 Antigravity 流式响应转换为 Gemini 格式的 SSE 流

    Args:
        lines_generator: 行生成器 (已经过滤的 SSE 行)
    """
    success_recorded = False

    try:
        async for line in lines_generator:
            if not line or not line.startswith("data: "):
                continue

            # 记录第一次成功响应
            if not success_recorded:
                if credential_name and credential_manager:
                    await credential_manager.record_api_call_result(credential_name, True, is_antigravity=True)
                success_recorded = True

            # 解析 SSE 数据
            try:
                data = json.loads(line[6:])  # 去掉 "data: " 前缀
            except:
                continue

            # Antigravity 流式响应格式: {"response": {...}}
            # Gemini 流式响应格式: {...}
            gemini_data = data.get("response", data)

            # 发送 Gemini 格式的数据
            yield f"data: {json.dumps(gemini_data)}\n\n"

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Streaming error: {e}")
        error_response = {
            "error": {
                "message": str(e),
                "code": 500,
                "status": "INTERNAL"
            }
        }
        yield f"data: {json.dumps(error_response)}\n\n"
    finally:
        # 确保清理所有资源
        try:
            await stream_ctx.__aexit__(None, None, None)
        except Exception as e:
            log.debug(f"[ANTIGRAVITY GEMINI] Error closing stream context: {e}")
        try:
            await client.aclose()
        except Exception as e:
            log.debug(f"[ANTIGRAVITY GEMINI] Error closing client: {e}")


@router.get("/antigravity/v1/models", response_model=ModelList)
async def list_models():
    """返回 OpenAI 格式的模型列表 - 动态从 Antigravity API 获取"""

    try:
        # 获取凭证管理器
        cred_mgr = await get_credential_manager()

        # 从 Antigravity API 获取模型列表（返回 OpenAI 格式的字典列表）
        models = await fetch_available_models(cred_mgr)

        if not models:
            # 如果获取失败，直接返回空列表
            log.warning("[ANTIGRAVITY] Failed to fetch models from API, returning empty list")
            return ModelList(data=[])

        # models 已经是 OpenAI 格式的字典列表，扩展为包含抗截断版本
        expanded_models = []
        for model in models:
            # 添加原始模型
            expanded_models.append(Model(**model))

            # 添加流式抗截断版本
            anti_truncation_model = model.copy()
            anti_truncation_model["id"] = f"流式抗截断/{model['id']}"
            expanded_models.append(Model(**anti_truncation_model))

        return ModelList(data=expanded_models)

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Error fetching models: {e}")
        # 返回空列表
        return ModelList(data=[])


@router.post("/antigravity/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    处理 OpenAI 格式的聊天完成请求，转换为 Antigravity API
    """
    # 获取原始请求数据
    try:
        raw_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 创建请求对象
    try:
        request_data = ChatCompletionRequest(**raw_data)
    except Exception as e:
        log.error(f"Request validation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Request validation error: {str(e)}")

    # 健康检查
    if (
        len(request_data.messages) == 1
        and getattr(request_data.messages[0], "role", None) == "user"
        and getattr(request_data.messages[0], "content", None) == "Hi"
    ):
        return JSONResponse(
            content={
                "choices": [{"message": {"role": "assistant", "content": "antigravity API 正常工作中"}}]
            }
        )

    # 获取凭证管理器
    from src.credential_manager import get_credential_manager
    cred_mgr = await get_credential_manager()

    # 提取参数
    model = request_data.model
    messages = request_data.messages
    stream = getattr(request_data, "stream", False)
    tools = getattr(request_data, "tools", None)

    # 检测并处理抗截断模式
    use_anti_truncation = is_anti_truncation_model(model)
    if use_anti_truncation:
        # 去掉 "流式抗截断/" 前缀
        from src.utils import get_base_model_from_feature_model
        model = get_base_model_from_feature_model(model)

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    log.info(f"[ANTIGRAVITY] Request: model={model} -> {actual_model}, stream={stream}, thinking={enable_thinking}, anti_truncation={use_anti_truncation}")

    # 转换消息格式
    try:
        contents = openai_messages_to_antigravity_contents(messages)
    except Exception as e:
        log.error(f"Failed to convert messages: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 转换工具定义
    antigravity_tools = convert_openai_tools_to_antigravity(tools)

    # 生成配置参数
    parameters = {
        "temperature": getattr(request_data, "temperature", None),
        "top_p": getattr(request_data, "top_p", None),
        "max_tokens": getattr(request_data, "max_tokens", None),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    generation_config = generate_generation_config(parameters, enable_thinking, actual_model)

    # 获取凭证信息（用于 project_id 和 session_id）
    cred_result = await cred_mgr.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("project_id", "default-project")
    session_id = f"session-{uuid.uuid4().hex}"

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        project_id=project_id,
        session_id=session_id,
        tools=antigravity_tools,
        generation_config=generation_config,
    )

    # 生成请求 ID
    request_id = f"chatcmpl-{int(time.time() * 1000)}"

    # 发送请求
    try:
        if stream:
            # 处理抗截断功能（仅流式传输时有效）
            if use_anti_truncation:
                log.info("[ANTIGRAVITY] 启用流式抗截断功能")
                max_attempts = await get_anti_truncation_max_attempts()

                # 包装请求函数以适配抗截断处理器
                async def antigravity_request_func(payload):
                    resources, cred_name, cred_data = await send_antigravity_request_stream(
                        payload, cred_mgr
                    )
                    response, stream_ctx, client = resources
                    return StreamingResponse(
                        convert_antigravity_stream_to_openai(
                            response, stream_ctx, client, model, request_id, cred_mgr, cred_name
                        ),
                        media_type="text/event-stream"
                    )

                return await apply_anti_truncation_to_stream(
                    antigravity_request_func, request_body, max_attempts
                )

            # 流式请求（无抗截断）
            resources, cred_name, cred_data = await send_antigravity_request_stream(
                request_body, cred_mgr
            )
            # resources 是一个元组: (response, stream_ctx, client)
            response, stream_ctx, client = resources

            # 转换并返回流式响应,传递资源管理对象
            # response 现在是 filtered_lines 生成器
            return StreamingResponse(
                convert_antigravity_stream_to_openai(
                    response, stream_ctx, client, model, request_id, cred_mgr, cred_name
                ),
                media_type="text/event-stream"
            )
        else:
            # 非流式请求
            response_data, cred_name, cred_data = await send_antigravity_request_no_stream(
                request_body, cred_mgr
            )

            # 转换并返回响应
            openai_response = convert_antigravity_response_to_openai(response_data, model, request_id)
            return JSONResponse(content=openai_response)

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {str(e)}")


# ==================== Gemini 格式 API 端点 ====================

@router.get("/antigravity/v1beta/models")
@router.get("/antigravity/v1/models")
async def gemini_list_models(api_key: str = Depends(authenticate_gemini_flexible)):
    """返回 Gemini 格式的模型列表 - 动态从 Antigravity API 获取"""

    try:
        # 获取凭证管理器
        cred_mgr = await get_credential_manager()

        # 从 Antigravity API 获取模型列表（返回 OpenAI 格式的字典列表）
        models = await fetch_available_models(cred_mgr)

        if not models:
            # 如果获取失败，返回空列表
            log.warning("[ANTIGRAVITY GEMINI] Failed to fetch models from API, returning empty list")
            return JSONResponse(content={"models": []})

        # 将 OpenAI 格式转换为 Gemini 格式，同时添加抗截断版本
        gemini_models = []
        for model in models:
            model_id = model.get("id", "")

            # 添加原始模型
            gemini_models.append({
                "name": f"models/{model_id}",
                "version": "001",
                "displayName": model_id,
                "description": f"Antigravity API - {model_id}",
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })

            # 添加流式抗截断版本
            anti_truncation_id = f"流式抗截断/{model_id}"
            gemini_models.append({
                "name": f"models/{anti_truncation_id}",
                "version": "001",
                "displayName": anti_truncation_id,
                "description": f"Antigravity API - {anti_truncation_id} (带流式抗截断功能)",
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })

        return JSONResponse(content={"models": gemini_models})

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Error fetching models: {e}")
        # 返回空列表
        return JSONResponse(content={"models": []})


@router.post("/antigravity/v1beta/models/{model:path}:generateContent")
@router.post("/antigravity/v1/models/{model:path}:generateContent")
async def gemini_generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """处理 Gemini 格式的非流式内容生成请求（通过 Antigravity API）"""
    log.debug(f"[ANTIGRAVITY GEMINI] Non-streaming request for model: {model}")

    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")

    # 健康检查
    if (
        len(request_data["contents"]) == 1
        and request_data["contents"][0].get("role") == "user"
        and request_data["contents"][0].get("parts", [{}])[0].get("text") == "Hi"
    ):
        return JSONResponse(
            content={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "antigravity API 正常工作中"}], "role": "model"},
                        "finishReason": "STOP",
                        "index": 0,
                    }
                ]
            }
        )

    # 获取凭证管理器
    from src.credential_manager import get_credential_manager
    cred_mgr = await get_credential_manager()

    # 提取模型名称（移除 "models/" 前缀）
    if model.startswith("models/"):
        model = model[7:]

    # 检测并处理抗截断模式（虽然非流式不会使用，但要处理模型名）
    use_anti_truncation = is_anti_truncation_model(model)
    if use_anti_truncation:
        # 去掉 "流式抗截断/" 前缀
        from src.utils import get_base_model_from_feature_model
        model = get_base_model_from_feature_model(model)

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    log.info(f"[ANTIGRAVITY GEMINI] Request: model={model} -> {actual_model}, thinking={enable_thinking}")

    # 转换 Gemini contents 为 Antigravity contents
    try:
        contents = gemini_contents_to_antigravity_contents(request_data["contents"])
    except Exception as e:
        log.error(f"Failed to convert Gemini contents: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 提取 Gemini generationConfig
    gemini_config = request_data.get("generationConfig", {})

    # 转换为 Antigravity generation_config
    parameters = {
        "temperature": gemini_config.get("temperature"),
        "top_p": gemini_config.get("topP"),
        "top_k": gemini_config.get("topK"),
        "max_tokens": gemini_config.get("maxOutputTokens"),
        # 图片生成相关参数
        "response_modalities": gemini_config.get("response_modalities"),
        "image_config": gemini_config.get("image_config"),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    generation_config = generate_generation_config(parameters, enable_thinking, actual_model)

    # 获取凭证信息（用于 project_id 和 session_id）
    cred_result = await cred_mgr.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("project_id", "default-project")
    session_id = credential_data.get("session_id", f"session-{uuid.uuid4().hex}")

    # 处理 systemInstruction
    system_instruction = None
    if "systemInstruction" in request_data:
        system_instruction = request_data["systemInstruction"]

    # 处理 tools
    antigravity_tools = None
    if "tools" in request_data:
        # Gemini 和 Antigravity 的 tools 格式基本一致
        antigravity_tools = request_data["tools"]

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        project_id=project_id,
        session_id=session_id,
        system_instruction=system_instruction,
        tools=antigravity_tools,
        generation_config=generation_config,
    )

    # 发送非流式请求
    try:
        response_data, cred_name, cred_data = await send_antigravity_request_no_stream(
            request_body, cred_mgr
        )

        # 转换并返回 Gemini 格式响应
        gemini_response = convert_antigravity_response_to_gemini(response_data)
        return JSONResponse(content=gemini_response)

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {str(e)}")


@router.post("/antigravity/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/antigravity/v1/models/{model:path}:streamGenerateContent")
async def gemini_stream_generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """处理 Gemini 格式的流式内容生成请求（通过 Antigravity API）"""
    log.debug(f"[ANTIGRAVITY GEMINI] Streaming request for model: {model}")

    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")

    # 获取凭证管理器
    from src.credential_manager import get_credential_manager
    cred_mgr = await get_credential_manager()

    # 提取模型名称（移除 "models/" 前缀）
    if model.startswith("models/"):
        model = model[7:]

    # 检测并处理抗截断模式
    use_anti_truncation = is_anti_truncation_model(model)
    if use_anti_truncation:
        # 去掉 "流式抗截断/" 前缀
        from src.utils import get_base_model_from_feature_model
        model = get_base_model_from_feature_model(model)

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    log.info(f"[ANTIGRAVITY GEMINI] Stream request: model={model} -> {actual_model}, thinking={enable_thinking}, anti_truncation={use_anti_truncation}")

    # 转换 Gemini contents 为 Antigravity contents
    try:
        contents = gemini_contents_to_antigravity_contents(request_data["contents"])
    except Exception as e:
        log.error(f"Failed to convert Gemini contents: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 提取 Gemini generationConfig
    gemini_config = request_data.get("generationConfig", {})

    # 转换为 Antigravity generation_config
    parameters = {
        "temperature": gemini_config.get("temperature"),
        "top_p": gemini_config.get("topP"),
        "top_k": gemini_config.get("topK"),
        "max_tokens": gemini_config.get("maxOutputTokens"),
        # 图片生成相关参数
        "response_modalities": gemini_config.get("response_modalities"),
        "image_config": gemini_config.get("image_config"),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    generation_config = generate_generation_config(parameters, enable_thinking, actual_model)

    # 获取凭证信息（用于 project_id 和 session_id）
    cred_result = await cred_mgr.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("project_id", "default-project")
    session_id = credential_data.get("session_id", f"session-{uuid.uuid4().hex}")

    # 处理 systemInstruction
    system_instruction = None
    if "systemInstruction" in request_data:
        system_instruction = request_data["systemInstruction"]

    # 处理 tools
    antigravity_tools = None
    if "tools" in request_data:
        # Gemini 和 Antigravity 的 tools 格式基本一致
        antigravity_tools = request_data["tools"]

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        project_id=project_id,
        session_id=session_id,
        system_instruction=system_instruction,
        tools=antigravity_tools,
        generation_config=generation_config,
    )

    # 发送流式请求
    try:
        # 处理抗截断功能（仅流式传输时有效）
        if use_anti_truncation:
            log.info("[ANTIGRAVITY GEMINI] 启用流式抗截断功能")
            max_attempts = await get_anti_truncation_max_attempts()

            # 包装请求函数以适配抗截断处理器
            async def antigravity_gemini_request_func(payload):
                resources, cred_name, cred_data = await send_antigravity_request_stream(
                    payload, cred_mgr
                )
                response, stream_ctx, client = resources
                return StreamingResponse(
                    convert_antigravity_stream_to_gemini(
                        response, stream_ctx, client, cred_mgr, cred_name
                    ),
                    media_type="text/event-stream"
                )

            return await apply_anti_truncation_to_stream(
                antigravity_gemini_request_func, request_body, max_attempts
            )

        # 流式请求（无抗截断）
        resources, cred_name, cred_data = await send_antigravity_request_stream(
            request_body, cred_mgr
        )
        # resources 是一个元组: (response, stream_ctx, client)
        response, stream_ctx, client = resources

        # 转换并返回流式响应
        # response 现在是 filtered_lines 生成器
        return StreamingResponse(
            convert_antigravity_stream_to_gemini(
                response, stream_ctx, client, cred_mgr, cred_name
            ),
            media_type="text/event-stream"
        )

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Stream request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {str(e)}")


# ==================== SD-WebUI 格式 API 端点 ====================

@router.get("/sdapi/v1/options")
@router.get("/antigravity/sdapi/v1/options")
async def sdwebui_get_options(_: str = Depends(authenticate_sdwebui_flexible)):
    """返回 SD-WebUI 格式的配置选项"""
    log.info("[ANTIGRAVITY SD-WebUI] Received options request")
    # 返回基本的配置选项
    return {
        "sd_model_checkpoint": "gemini-3-pro-image",
        "sd_checkpoint_hash": None,
        "samples_save": True,
        "samples_format": "png",
        "save_images_add_number": True,
        "grid_save": True,
        "return_grid": True,
        "enable_pnginfo": True,
        "save_txt": False,
        "CLIP_stop_at_last_layers": 1,
    }


@router.get("/sdapi/v1/sd-models")
@router.get("/antigravity/sdapi/v1/sd-models")
async def sdwebui_list_models(_: str = Depends(authenticate_sdwebui_flexible)):
    """返回 SD-WebUI 格式的模型列表 - 只包含带 image 关键词的模型"""

    try:
        # 获取凭证管理器
        cred_mgr = await get_credential_manager()

        # 从 Antigravity API 获取模型列表
        models = await fetch_available_models(cred_mgr)

        if not models:
            log.warning("[ANTIGRAVITY SD-WebUI] Failed to fetch models from API, returning empty list")
            return []

        # 过滤只包含 "image" 关键词的模型
        image_models = []
        for model in models:
            model_id = model.get("id", "")
            if "image" in model_id.lower():
                # SD-WebUI 格式: {"title": "model_name", "model_name": "model_name", "hash": null}
                image_models.append({
                    "title": model_id,
                    "model_name": model_id,
                    "hash": None,
                    "sha256": None,
                    "filename": model_id,
                    "config": None
                })

        return image_models

    except Exception as e:
        log.error(f"[ANTIGRAVITY SD-WebUI] Error fetching models: {e}")
        return []


@router.post("/sdapi/v1/txt2img")
@router.post("/antigravity/sdapi/v1/txt2img")
async def sdwebui_txt2img(request: Request, _: str = Depends(authenticate_sdwebui_flexible)):
    """处理 SD-WebUI 格式的 txt2img 请求，转换为 Antigravity API"""
    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 提取基本参数
    prompt = request_data.get("prompt", "")
    if not prompt:
        raise HTTPException(status_code=400, detail="Missing required field: prompt")

    negative_prompt = request_data.get("negative_prompt", "")

    # 提取图片生成相关参数
    width = request_data.get("width", 1024)
    height = request_data.get("height", 1024)

    # 计算 aspect_ratio - 映射到支持的比例
    # 支持的比例: "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"
    def find_closest_aspect_ratio(w: int, h: int) -> str:
        ratio = w / h
        supported_ratios = {
            "1:1": 1.0,
            "2:3": 0.667,
            "3:2": 1.5,
            "3:4": 0.75,
            "4:3": 1.333,
            "4:5": 0.8,
            "5:4": 1.25,
            "9:16": 0.5625,
            "16:9": 1.778,
            "21:9": 2.333
        }
        closest = min(supported_ratios.items(), key=lambda x: abs(x[1] - ratio))
        return closest[0]

    aspect_ratio = find_closest_aspect_ratio(width, height)

    # 简化的尺寸映射到 image_size
    max_dimension = max(width, height)
    if max_dimension <= 1024:
        image_size = "1K"
    elif max_dimension <= 2048:
        image_size = "2K"
    else:
        image_size = "4K"

    # 提取模型（如果指定）
    model = request_data.get("override_settings", {}).get("sd_model_checkpoint", "gemini-3-pro-image")
    if not model or "image" not in model.lower():
        model = "gemini-3-pro-image"

    log.info(f"[ANTIGRAVITY SD-WebUI] txt2img request: model={model}, prompt={prompt[:50]}..., aspect_ratio={aspect_ratio}, image_size={image_size}")

    cred_mgr = await get_credential_manager()

    # 构建 Gemini 格式的 contents
    full_prompt = prompt
    if negative_prompt:
        full_prompt = f"{prompt}\n\nNegative prompt: {negative_prompt}"

    contents = [{
        "role": "user",
        "parts": [{"text": full_prompt}]
    }]

    # 构建 generation_config，包含图片生成参数
    parameters = {
        "response_modalities": ["TEXT", "IMAGE"],
        "image_config": {
            "aspect_ratio": aspect_ratio,
            "image_size": image_size
        }
    }

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    generation_config = generate_generation_config(parameters, enable_thinking, actual_model)

    # 获取凭证信息
    cred_result = await cred_mgr.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("project_id", "default-project")
    session_id = f"session-{uuid.uuid4().hex}"

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        project_id=project_id,
        session_id=session_id,
        tools=None,
        generation_config=generation_config,
    )

    # 发送非流式请求
    try:
        response_data, cred_name, cred_data = await send_antigravity_request_no_stream(
            request_body, cred_mgr
        )

        # 提取生成的图片
        parts = response_data.get("response", {}).get("candidates", [{}])[0].get("content", {}).get("parts", [])

        images = []
        info_text = ""

        for part in parts:
            if "inlineData" in part:
                inline_data = part["inlineData"]
                base64_data = inline_data.get("data", "")
                images.append(base64_data)
            elif "text" in part:
                info_text += part.get("text", "")

        if not images:
            raise HTTPException(status_code=500, detail="No images generated")

        # 构建 SD-WebUI 格式的响应
        sdwebui_response = {
            "images": images,
            "parameters": request_data,
            "info": info_text or f"Generated by {model}"
        }

        return JSONResponse(content=sdwebui_response)

    except Exception as e:
        log.error(f"[ANTIGRAVITY SD-WebUI] txt2img request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Image generation failed: {str(e)}")

