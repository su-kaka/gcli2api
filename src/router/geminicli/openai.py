"""
GeminiCLI OpenAI Router - OpenAI格式API路由
通过GeminiCLI处理OpenAI格式的聊天完成请求
"""

# 标准库
import asyncio
import json
import uuid

# 第三方库
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    get_available_models,
    get_base_model_from_feature_model,
    is_anti_truncation_model,
    is_fake_streaming_model,
    authenticate_bearer,
)

# 本地模块 - 模型和API客户端
from src.models import ChatCompletionRequest, Model, ModelList
from src.api.geminicli import (
    send_geminicli_request_stream,
    send_geminicli_request_no_stream,
)

# 本地模块 - 转换器
from src.converter.anti_truncation import apply_anti_truncation_to_stream
from src.converter.openai2gemini import (
    create_openai_heartbeat_chunk,
    create_openai_stream_chunk,
    extract_fake_stream_content,
    gemini_response_to_openai,
    gemini_stream_chunk_to_openai,
    normalize_openai_request,
    openai_request_to_gemini_payload,
    parse_gemini_stream_chunk,
)

# 本地模块 - 基础路由工具
from src.router.base_router import wrap_stream_with_cleanup
from src.router.hi_check import is_health_check_request, create_health_check_response

# 本地模块 - 任务管理
from src.task_manager import create_managed_task


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.get("/v1/models", response_model=ModelList)
async def list_models(token: str = Depends(authenticate_bearer)):
    """
    返回OpenAI格式的模型列表
    
    Returns:
        ModelList: 可用模型列表
    """
    models = get_available_models("openai")
    return ModelList(data=[Model(id=m) for m in models])


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    处理OpenAI格式的聊天完成请求
    
    支持功能：
    - 健康检查
    - 假流式响应
    - 流式抗截断
    - 标准流式/非流式响应
    
    Args:
        request: FastAPI请求对象
        token: Bearer认证令牌
        
    Returns:
        JSONResponse或StreamingResponse
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
    if is_health_check_request(request_data.model_dump()):
        return JSONResponse(content=create_health_check_response())

    # 标准化请求数据（限制max_tokens、设置top_k、过滤空消息）
    request_data = normalize_openai_request(request_data)

    # 处理模型名称和功能检测
    model = request_data.model
    use_fake_streaming = is_fake_streaming_model(model)
    use_anti_truncation = is_anti_truncation_model(model)

    # 获取基础模型名
    real_model = get_base_model_from_feature_model(model)
    request_data.model = real_model

    # 获取有效凭证（API层自己管理）

    # 转换为Gemini API payload格式
    try:
        api_payload = await openai_request_to_gemini_payload(request_data)
    except Exception as e:
        log.error(f"OpenAI to Gemini conversion failed: {e}")
        raise HTTPException(status_code=500, detail="Request conversion failed")

    # 处理假流式
    if use_fake_streaming and getattr(request_data, "stream", False):
        request_data.stream = False
        return await fake_stream_response(api_payload)

    # 处理抗截断 (仅流式传输时有效)
    is_streaming = getattr(request_data, "stream", False)
    if use_anti_truncation and is_streaming:
        log.info("启用流式抗截断功能")
        max_attempts = await get_anti_truncation_max_attempts()

        # 使用流式抗截断处理器
        async def stream_request(payload):
            resources, _, _ = await send_geminicli_request_stream(payload)
            filtered_lines, stream_ctx, client = resources
            return StreamingResponse(
                wrap_stream_with_cleanup(filtered_lines, stream_ctx, client),
                media_type="text/event-stream"
            )
        
        gemini_response = await apply_anti_truncation_to_stream(
            stream_request,
            api_payload,
            max_attempts,
        )

        return await convert_streaming_response(gemini_response, model)
    elif use_anti_truncation and not is_streaming:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")

    # 发送请求（429重试已在google_api_client中处理）
    is_streaming = getattr(request_data, "stream", False)
    log.debug(f"Sending request: streaming={is_streaming}, model={real_model}")
    
    if is_streaming:
        # 流式请求
        resources, _, _ = await send_geminicli_request_stream(api_payload)
        filtered_lines, stream_ctx, client = resources
        response = StreamingResponse(
            wrap_stream_with_cleanup(filtered_lines, stream_ctx, client),
            media_type="text/event-stream"
        )
        return await convert_streaming_response(response, model)
    
    # 非流式请求
    response_data, _, _ = await send_geminicli_request_no_stream(api_payload)
    from fastapi import Response
    response = Response(
        content=json.dumps(response_data),
        media_type="application/json"
    )

    # 转换非流式响应
    try:
        if hasattr(response, "body"):
            response_data = json.loads(
                response.body.decode() if isinstance(response.body, bytes) else response.body
            )
        else:
            response_data = json.loads(
                response.content.decode()
                if isinstance(response.content, bytes)
                else response.content
            )

        openai_response = gemini_response_to_openai(response_data, model)
        return JSONResponse(content=openai_response)

    except Exception as e:
        log.error(f"Response conversion failed: {e}")
        log.error(f"Response object: {response}")
        raise HTTPException(status_code=500, detail="Response conversion failed")


# ==================== 辅助函数 ====================

async def fake_stream_response(api_payload: dict) -> StreamingResponse:
    """
    处理假流式响应
    
    通过定期发送心跳保持连接，实际请求完成后一次性返回结果
    
    Args:
        api_payload: Gemini API请求负载
        
    Returns:
        StreamingResponse: SSE格式的流式响应
    """
    async def stream_generator():
        try:
            # 发送心跳
            heartbeat = create_openai_heartbeat_chunk()
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
            response_task = create_managed_task(get_response(), name="openai_fake_stream_request")

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

            # 从响应中提取内容（使用 converter 函数）
            content, reasoning_content, usage = extract_fake_stream_content(response)

            # 创建响应块（使用 converter 函数）
            content_chunk = create_openai_stream_chunk(
                content=content,
                reasoning_content=reasoning_content,
                usage=usage,
                model="gcli2api-streaming",
                finish_reason="stop"
            )
            yield f"data: {json.dumps(content_chunk)}\n\n".encode()
            yield "data: [DONE]\n\n".encode()

        except Exception as e:
            log.error(f"Fake streaming error: {e}")
            error_chunk = create_openai_stream_chunk(
                content=f"Error: {str(e)}",
                model="gcli2api-streaming",
                finish_reason="stop"
            )
            yield f"data: {json.dumps(error_chunk)}\n\n".encode()
            yield "data: [DONE]\n\n".encode()

    return StreamingResponse(stream_generator(), media_type="text/event-stream")


async def convert_streaming_response(gemini_response, model: str) -> StreamingResponse:
    """
    转换Gemini流式响应为OpenAI格式
    
    Args:
        gemini_response: Gemini API流式响应对象
        model: 模型名称
        
    Returns:
        StreamingResponse: OpenAI格式的SSE流式响应
    """
    response_id = str(uuid.uuid4())

    async def openai_stream_generator():
        try:
            # 处理不同类型的响应对象
            if hasattr(gemini_response, "body_iterator"):
                # FastAPI StreamingResponse
                async for chunk in gemini_response.body_iterator:
                    if not chunk:
                        continue

                    # 使用 converter 中的解析函数
                    gemini_chunk = parse_gemini_stream_chunk(chunk)
                    if gemini_chunk is None:
                        continue

                    # 转换为 OpenAI 格式
                    openai_chunk = gemini_stream_chunk_to_openai(
                        gemini_chunk, model, response_id
                    )
                    yield f"data: {json.dumps(openai_chunk, separators=(',', ':'))}\n\n".encode()
                    await asyncio.sleep(0)  # 让出执行权，立即推送数据
            else:
                # 其他类型的响应，尝试直接处理
                log.warning(f"Unexpected response type: {type(gemini_response)}")
                error_chunk = create_openai_stream_chunk(
                    content="Response type error",
                    model=model,
                    finish_reason="stop"
                )
                yield f"data: {json.dumps(error_chunk)}\n\n".encode()

            # 发送结束标记
            yield "data: [DONE]\n\n".encode()

        except Exception as e:
            log.error(f"Stream conversion error: {e}")
            error_chunk = create_openai_stream_chunk(
                content=f"Stream error: {str(e)}",
                model=model,
                finish_reason="stop"
            )
            yield f"data: {json.dumps(error_chunk)}\n\n".encode()
            yield "data: [DONE]\n\n".encode()

    return StreamingResponse(openai_stream_generator(), media_type="text/event-stream")
