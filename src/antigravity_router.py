"""
Antigravity Router - Handles OpenAI format requests and converts to Antigravity API
处理 OpenAI 格式请求并转换为 Antigravity API 格式
"""

import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from log import log

from .antigravity_api import (
    build_antigravity_request_body,
    send_antigravity_request_no_stream,
    send_antigravity_request_stream,
)
from .credential_manager import CredentialManager
from .models import ChatCompletionRequest, Model, ModelList

# 创建路由器
router = APIRouter()
security = HTTPBearer()

# 全局凭证管理器实例
credential_manager = None


@asynccontextmanager
async def get_credential_manager():
    """获取全局凭证管理器实例"""
    global credential_manager
    if not credential_manager:
        credential_manager = CredentialManager()
        await credential_manager.initialize()
    yield credential_manager


async def authenticate(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """验证用户密码"""
    from config import get_api_password

    password = await get_api_password()
    token = credentials.credentials
    if token != password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="密码错误")
    return token


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
    thinking_models = [
        "gemini-2.5-pro",
        "gemini-3-pro",
        "claude-sonnet-4-5-thinking",
        "claude-opus-4-5",  # 会被映射为 claude-opus-4-5-thinking
    ]

    # 检查是否包含 -thinking 后缀
    if "-thinking" in model_name:
        return True

    # 检查是否匹配特定模型
    for thinking_model in thinking_models:
        if model_name.startswith(thinking_model):
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
                if hasattr(func_params, "dict"):
                    func_params = func_params.dict()
                elif hasattr(func_params, "model_dump"):
                    func_params = func_params.model_dump()

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
    生成 Antigravity generationConfig
    """
    config = {
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>"
        ]
    }

    # 添加温度参数
    if "temperature" in parameters:
        config["temperature"] = parameters["temperature"]

    # 添加 topP
    if "top_p" in parameters:
        config["topP"] = parameters["top_p"]

    # 添加 topK
    if "top_k" in parameters:
        config["topK"] = parameters["top_k"]
    else:
        config["topK"] = 50  # 默认值

    # 添加 maxOutputTokens
    if "max_tokens" in parameters:
        config["maxOutputTokens"] = parameters["max_tokens"]

    # 思考模型配置
    if enable_thinking:
        config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": 1024
        }

        # Claude 思考模型：删除 topP 参数
        if "claude" in model_name.lower():
            config.pop("topP", None)

    return config


def convert_to_openai_tool_call(function_call: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Antigravity functionCall 转换为 OpenAI tool_call
    """
    return {
        "id": function_call.get("id", f"call_{uuid.uuid4().hex[:24]}"),
        "type": "function",
        "function": {
            "name": function_call.get("name", ""),
            "arguments": json.dumps(function_call.get("args", {}))
        }
    }


async def convert_antigravity_stream_to_openai(
    response: Any,
    stream_ctx: Any,
    client: Any,
    model: str,
    request_id: str,
    credential_manager: Any,
    credential_name: str
):
    """
    将 Antigravity 流式响应转换为 OpenAI 格式的 SSE 流

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

        async for line in response.aiter_lines():
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
    tool_calls = []

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
            tool_calls.append(convert_to_openai_tool_call(part["functionCall"]))

    # 拼接思考内容
    if thinking_content:
        content = f"<think>\n{thinking_content}\n</think>\n{content}"

    # 构建 OpenAI 响应
    message = {
        "role": "assistant",
        "content": content
    }

    if tool_calls:
        message["tool_calls"] = tool_calls

    # 确定 finish_reason
    finish_reason = "stop"
    if tool_calls:
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

    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason
        }],
        "usage": usage
    }


@router.get("/antigravity/v1/models", response_model=ModelList)
async def list_models():
    """返回 OpenAI 格式的模型列表 - 动态从 Antigravity API 获取"""
    from src.credential_manager import get_credential_manager
    from .antigravity_api import fetch_available_models

    try:
        # 获取凭证管理器
        cred_mgr = await get_credential_manager()

        # 从 Antigravity API 获取模型列表（返回 OpenAI 格式的字典列表）
        models = await fetch_available_models(cred_mgr)

        if not models:
            # 如果获取失败，直接返回空列表
            log.warning("[ANTIGRAVITY] Failed to fetch models from API, returning empty list")
            return ModelList(data=[])

        # models 已经是 OpenAI 格式的字典列表，直接转换为 Model 对象
        return ModelList(data=[Model(**m) for m in models])

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Error fetching models: {e}")
        # 返回空列表
        return ModelList(data=[])


@router.post("/antigravity/v1/chat/completions")
async def chat_completions(request: Request, token: str = Depends(authenticate)):
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

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    log.info(f"[ANTIGRAVITY] Request: model={model} -> {actual_model}, stream={stream}, thinking={enable_thinking}")

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

    # 获取凭证信息（用于 projectId 和 sessionId）
    cred_result = await cred_mgr.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("projectId", "default-project")
    session_id = credential_data.get("sessionId", f"session-{uuid.uuid4().hex}")

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
            # 流式请求
            resources, cred_name, cred_data = await send_antigravity_request_stream(
                request_body, cred_mgr
            )
            # resources 是一个元组: (response, stream_ctx, client)
            response, stream_ctx, client = resources

            # 转换并返回流式响应,传递资源管理对象
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
