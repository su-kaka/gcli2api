"""
Antigravity Router - Handles OpenAI and Gemini format requests and converts to Antigravity API
处理 OpenAI 和 Gemini 格式请求并转换为 Antigravity API 格式
"""

import json
import time
import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

from config import get_anti_truncation_max_attempts
from log import log
from .utils import is_anti_truncation_model, authenticate_bearer, authenticate_gemini_flexible, get_base_model_from_feature_model
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
)
from src.converter.anti_truncation import (
    apply_anti_truncation_to_stream,
)
from src.converter.openai2gemini import (
    convert_openai_tools_to_gemini,
    extract_tool_calls_from_parts,
    openai_messages_to_gemini_contents,
    gemini_stream_chunk_to_openai,
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


def gemini_contents_to_antigravity_contents(gemini_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 Gemini 原生 contents 格式转换为 Antigravity contents 格式
    Gemini 和 Antigravity 的 contents 格式完全一致，直接返回
    """
    return gemini_contents


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


def prepare_image_request(request_body: Dict[str, Any], model: str) -> Dict[str, Any]:
    """图像生成模型请求体后处理"""
    model_lower = model.lower()
    
    # 解析分辨率
    image_size = "4K" if "-4k" in model_lower else "2K" if "-2k" in model_lower else None
    
    # 解析比例
    aspect_ratio = None
    for suffix, ratio in [("-21x9", "21:9"), ("-16x9", "16:9"), ("-9x16", "9:16"), ("-4x3", "4:3"), ("-3x4", "3:4"), ("-1x1", "1:1")]:
        if suffix in model_lower:
            aspect_ratio = ratio
            break
    
    # 构建 imageConfig
    image_config = {}
    if aspect_ratio:
        image_config["aspectRatio"] = aspect_ratio
    if image_size:
        image_config["imageSize"] = image_size

    request_body["requestType"] = "image_gen"
    request_body["model"] = "gemini-3-pro-image"  # 统一使用基础模型名
    request_body["request"]["generationConfig"] = {"candidateCount": 1, "imageConfig": image_config}
    for key in ("systemInstruction", "tools", "toolConfig"):
        request_body["request"].pop(key, None)
    return request_body


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
    使用 openai2gemini 模块的 gemini_stream_chunk_to_openai 函数

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
                    await credential_manager.record_api_call_result(
                        credential_name, True, mode="antigravity"
                    )
                success_recorded = True

            # 解析 SSE 数据
            try:
                data = json.loads(line[6:])  # 去掉 "data: " 前缀
            except:
                continue

            # Antigravity 响应格式: {"response": {...}}
            # 提取内层的 Gemini 格式数据
            gemini_chunk = data.get("response", data)

            # 使用 openai2gemini 模块的函数转换为 OpenAI 格式
            openai_chunk = gemini_stream_chunk_to_openai(gemini_chunk, model, request_id)

            # 发送 OpenAI 格式的 chunk
            yield f"data: {json.dumps(openai_chunk)}\n\n"

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

    # 使用 openai2gemini 模块函数提取工具调用和文本内容
    tool_calls_list, text_content = extract_tool_calls_from_parts(parts, is_streaming=False)
    
    thinking_content = ""
    content = text_content  # 使用提取的文本内容作为基础

    for part in parts:
        # 处理思考内容（extract_tool_calls_from_parts 不处理思考内容）
        if part.get("thought") is True:
            thinking_content += part.get("text", "")

        # 处理图片数据 (inlineData)
        elif "inlineData" in part:
            inline_data = part["inlineData"]
            mime_type = inline_data.get("mimeType", "image/png")
            base64_data = inline_data.get("data", "")
            # 转换为 Markdown 格式的图片（需要额外添加到 content，因为 extract_tool_calls_from_parts 不处理图片）
            content += f"\n\n![生成的图片](data:{mime_type};base64,{base64_data})\n\n"

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
                    await credential_manager.record_api_call_result(credential_name, True, mode="antigravity")
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

    # 转换消息格式（使用 openai2gemini 模块的通用函数）
    try:
        contents, system_instructions = openai_messages_to_gemini_contents(
            messages, compatibility_mode=False
        )
    except Exception as e:
        log.error(f"Failed to convert messages: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 转换工具定义
    antigravity_tools = convert_openai_tools_to_gemini(tools)

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
    cred_result = await cred_mgr.get_valid_credential(mode="antigravity")
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

    # 图像生成模型特殊处理
    if "-image" in model:
        request_body = prepare_image_request(request_body, model)

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
    cred_result = await cred_mgr.get_valid_credential(mode="antigravity")
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

    # 图像生成模型特殊处理
    if "-image" in model:
        request_body = prepare_image_request(request_body, model)

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
    cred_result = await cred_mgr.get_valid_credential(mode="antigravity")
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

    # 图像生成模型特殊处理
    if "-image" in model:
        request_body = prepare_image_request(request_body, model)

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

