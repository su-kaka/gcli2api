"""
Gemini Router - Handles native Gemini format API requests
处理原生Gemini格式请求的路由模块
"""

# 标准库
import asyncio
import json

# 第三方库
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    get_available_models,
    get_base_model_from_feature_model,
    get_base_model_name,
    is_anti_truncation_model,
    is_fake_streaming_model,
    authenticate_gemini_flexible,
)

# 本地模块 - API客户端
from src.api.geminicli import (
    build_gemini_payload_from_native,
    send_geminicli_request_stream,
    send_geminicli_request_no_stream,
)

# 本地模块 - 转换器
from src.converter.anti_truncation import apply_anti_truncation_to_stream
from src.converter.gemini_fix import (
    process_generation_config,
    parse_response_for_fake_stream,
    build_gemini_fake_stream_chunks,
    create_gemini_heartbeat_chunk,
    create_gemini_error_chunk,
)

# 本地模块 - 基础路由工具
from src.router.base_router import (
    create_gemini_model_list,
    extract_base_model_name,
    wrap_stream_with_cleanup,
)
from src.router.hi_check import is_health_check_request, create_health_check_response

# 本地模块 - 任务管理
from src.task_manager import create_managed_task

# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.get("/v1beta/models")
@router.get("/v1/models")
async def list_gemini_models(token: str = Depends(authenticate_gemini_flexible)):
    """
    返回Gemini格式的模型列表
    
    使用 create_gemini_model_list 工具函数创建标准格式
    """
    models = get_available_models("gemini")
    return JSONResponse(content=create_gemini_model_list(
        models, 
        base_name_extractor=get_base_model_from_feature_model
    ))

@router.post("/v1beta/models/{model:path}:generateContent")
@router.post("/v1/models/{model:path}:generateContent")
async def generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    处理Gemini格式的内容生成请求（非流式）
    
    Args:
        model: 模型名称
        request: FastAPI 请求对象
        api_key: API 密钥
    """
    log.debug(f"[GEMINICLI] Non-streaming request for model: {model}")

    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")

    # 请求预处理：使用统一的配置处理函数
    request_data["generationConfig"] = process_generation_config(
        request_data.get("generationConfig")
    )

    # 处理模型名称和功能检测
    use_anti_truncation = is_anti_truncation_model(model)

    # 获取基础模型名
    real_model = get_base_model_from_feature_model(model)

    # 对于假流式模型，如果是流式端点才返回假流式响应
    # 注意：这是generateContent端点，不应该触发假流式

    # 对于抗截断模型的非流式请求，给出警告
    if use_anti_truncation:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")

    # 健康检查
    if is_health_check_request(request_data, format="gemini"):
        response = create_health_check_response(format="gemini")
        return JSONResponse(content=response)

    # 构建Google API payload（API层自己管理凭证）
    try:
        api_payload = build_gemini_payload_from_native(request_data, real_model)
    except Exception as e:
        log.error(f"Gemini payload build failed: {e}")
        raise HTTPException(status_code=500, detail="Request processing failed")

    # 发送请求（API层自己管理凭证）
    response_data, _, _ = await send_geminicli_request_no_stream(api_payload)

    return JSONResponse(content=response_data)

@router.post("/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/v1/models/{model:path}:streamGenerateContent")
async def stream_generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    处理Gemini格式的流式内容生成请求
    
    Args:
        model: 模型名称
        request: FastAPI 请求对象
        api_key: API 密钥
    """
    log.debug(f"[GEMINICLI] Streaming request for model: {model}")

    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 请求预处理：使用统一的配置处理函数
    request_data["generationConfig"] = process_generation_config(
        request_data.get("generationConfig")
    )

    # 处理模型名称和功能检测
    use_fake_streaming = is_fake_streaming_model(model)
    use_anti_truncation = is_anti_truncation_model(model)

    # 获取基础模型名
    real_model = get_base_model_from_feature_model(model)

    # 对于假流式模型，返回假流式响应
    if use_fake_streaming:
        return await fake_stream_response_gemini(request_data, real_model)
    
    # 构建Google API payload（API层自己管理凭证）
    try:
        api_payload = build_gemini_payload_from_native(request_data, real_model)
    except Exception as e:
        log.error(f"Gemini payload build failed: {e}")
        raise HTTPException(status_code=500, detail="Request processing failed")

    # 处理抗截断功能（仅流式传输时有效）
    if use_anti_truncation:
        log.info("启用流式抗截断功能")
        # 使用流式抗截断处理器
        max_attempts = await get_anti_truncation_max_attempts()
        async def stream_request(payload):
            resources, _, _ = await send_geminicli_request_stream(payload)
            filtered_lines, stream_ctx, client = resources
            return StreamingResponse(
                wrap_stream_with_cleanup(filtered_lines, stream_ctx, client),
                media_type="text/event-stream"
            )
        return await apply_anti_truncation_to_stream(
            stream_request, api_payload, max_attempts
        )

    # 常规流式请求（API层自己管理凭证）
    resources, _, _ = await send_geminicli_request_stream(api_payload)
    filtered_lines, stream_ctx, client = resources
    return StreamingResponse(
        wrap_stream_with_cleanup(filtered_lines, stream_ctx, client),
        media_type="text/event-stream"
    )

@router.post("/v1beta/models/{model:path}:countTokens")
@router.post("/v1/models/{model:path}:countTokens")
async def count_tokens(
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    模拟Gemini格式的token计数
    
    使用简单的启发式方法：大约4字符=1token
    """

    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 简单的token计数模拟 - 基于文本长度估算
    total_tokens = 0

    # 如果有contents字段
    if "contents" in request_data:
        for content in request_data["contents"]:
            if "parts" in content:
                for part in content["parts"]:
                    if "text" in part:
                        # 简单估算：大约4字符=1token
                        text_length = len(part["text"])
                        total_tokens += max(1, text_length // 4)

    # 如果有generateContentRequest字段
    elif "generateContentRequest" in request_data:
        gen_request = request_data["generateContentRequest"]
        if "contents" in gen_request:
            for content in gen_request["contents"]:
                if "parts" in content:
                    for part in content["parts"]:
                        if "text" in part:
                            text_length = len(part["text"])
                            total_tokens += max(1, text_length // 4)

    # 返回Gemini格式的响应
    return JSONResponse(content={"totalTokens": total_tokens})

@router.get("/v1beta/models/{model:path}")
@router.get("/v1/models/{model:path}")
async def get_model_info(
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    获取特定模型的信息
    
    Args:
        model: 模型名称
        api_key: API 密钥
    """

    # 获取基础模型名称
    base_model = get_base_model_name(model)

    # 模拟模型信息
    model_info = {
        "name": f"models/{base_model}",
        "baseModelId": base_model,
        "version": "001",
        "displayName": base_model,
        "description": f"Gemini {base_model} model",
        "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
    }

    return JSONResponse(content=model_info)


# ==================== 辅助函数 ====================

async def fake_stream_response_gemini(request_data: dict, model: str):
    """
    处理Gemini格式的假流式响应
    
    Args:
        request_data: 请求数据
        model: 模型名称
        
    Returns:
        StreamingResponse: SSE 流式响应
    """

    async def gemini_stream_generator():
        """
        Gemini 流式数据生成器
        
        生成 SSE 格式的流式数据，包括心跳和实际响应
        """
        try:
            # 构建Google API payload（API层自己管理凭证）
            try:
                api_payload = build_gemini_payload_from_native(request_data, model)
            except Exception as e:
                log.error(f"Gemini payload build failed: {e}")
                error_chunk = create_gemini_error_chunk(
                    f"Request processing failed: {str(e)}",
                    "api_error",
                    500
                )
                yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return

            # 发送心跳
            heartbeat = create_gemini_heartbeat_chunk()
            yield f"data: {json.dumps(heartbeat)}\n\n".encode()

            # 异步发送实际请求
            async def get_response():
                response_data, _, _ = await send_geminicli_request_no_stream(api_payload)
                from fastapi import Response
                return Response(
                    content=json.dumps(response_data),
                    media_type="application/json"
                )

            # 创建请求任务
            response_task = create_managed_task(get_response(), name="gemini_fake_stream_request")

            try:
                # 每3秒发送一次心跳，直到收到响应
                while not response_task.done():
                    await asyncio.sleep(3.0)
                    if not response_task.done():
                        yield f"data: {json.dumps(heartbeat)}\n\n".encode()

                # 获取响应结果
                response = await response_task

            except asyncio.CancelledError:
                # 取消任务并传播取消
                response_task.cancel()
                try:
                    await response_task
                except asyncio.CancelledError:
                    pass
                raise
            except Exception as e:
                # 取消任务并处理其他异常
                response_task.cancel()
                try:
                    await response_task
                except asyncio.CancelledError:
                    pass
                log.error(f"Fake streaming request failed: {e}")
                raise

            # 处理结果
            try:
                if hasattr(response, "body"):
                    response_data = json.loads(
                        response.body.decode()
                        if isinstance(response.body, bytes)
                        else response.body
                    )
                elif hasattr(response, "content"):
                    response_data = json.loads(
                        response.content.decode()
                        if isinstance(response.content, bytes)
                        else response.content
                    )
                else:
                    response_data = json.loads(str(response))

                log.debug(f"Gemini fake stream response data: {response_data}")

                # 使用统一的解析函数
                content, reasoning_content, finish_reason = parse_response_for_fake_stream(response_data)
                
                log.debug(f"Gemini extracted content: {content}")
                log.debug(
                    f"Gemini extracted reasoning: {reasoning_content[:100] if reasoning_content else 'None'}..."
                )

                # 构建响应块
                chunks = build_gemini_fake_stream_chunks(content, reasoning_content, finish_reason)
                for chunk in chunks:
                    yield f"data: {json.dumps(chunk)}\n\n".encode()

            except Exception as e:
                log.error(f"Response parsing failed: {e}")
                error_chunk = create_gemini_error_chunk(
                    f"Response parsing error: {str(e)}",
                    "api_error",
                    500
                )
                error_chunk["candidates"] = [{
                    "content": {
                        "parts": [{"text": f"Response parsing error: {str(e)}"}],
                        "role": "model",
                    },
                    "finishReason": "ERROR",
                    "index": 0,
                }]
                yield f"data: {json.dumps(error_chunk)}\n\n".encode()

            yield "data: [DONE]\n\n".encode()

        except Exception as e:
            log.error(f"Fake streaming error: {e}")
            error_chunk = create_gemini_error_chunk(
                f"Fake streaming error: {str(e)}",
                "api_error",
                500
            )
            yield f"data: {json.dumps(error_chunk)}\n\n".encode()
            yield "data: [DONE]\n\n".encode()

    return StreamingResponse(gemini_stream_generator(), media_type="text/event-stream")
