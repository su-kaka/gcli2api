"""
OpenAI Router - Handles OpenAI format API requests via Antigravity
通过Antigravity处理OpenAI格式请求的路由模块
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 标准库
import json

# 第三方库
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    get_base_model_from_feature_model,
    is_anti_truncation_model,
    authenticate_bearer,
)

# 本地模块 - 基础路由工具
from src.router.hi_check import is_health_check_request, create_health_check_response
from src.router.stream_passthrough import (
    build_streaming_response_or_error,
    prepend_async_item,
    read_first_async_item,
)

# 本地模块 - 数据模型
from src.models import OpenAIChatCompletionRequest, model_to_dict


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.post("/v1/chat/completions")
async def chat_completions(
    openai_request: OpenAIChatCompletionRequest,
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    处理OpenAI格式的聊天完成请求（流式和非流式）

    Args:
        openai_request: OpenAI格式的请求体
        token: Bearer认证令牌
    """
    log.debug(f"[ANTIGRAVITY-OPENAI] Request for model: {openai_request.model}")

    # 转换为字典
    normalized_dict = model_to_dict(openai_request)

    # 健康检查
    if is_health_check_request(normalized_dict, format="openai"):
        response = create_health_check_response(format="openai")
        return JSONResponse(content=response)

    # 处理模型名称和功能检测
    use_anti_truncation = is_anti_truncation_model(openai_request.model)
    real_model = get_base_model_from_feature_model(openai_request.model)

    # 获取流式标志
    is_streaming = openai_request.stream

    # 对于抗截断模型的非流式请求，给出警告
    if use_anti_truncation and not is_streaming:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")

    # 更新模型名为真实模型名
    normalized_dict["model"] = real_model

    # 转换为 Gemini 格式 (使用 converter)
    from src.converter.openai2gemini import convert_openai_to_gemini_request
    gemini_dict = await convert_openai_to_gemini_request(normalized_dict)

    # convert_openai_to_gemini_request 不包含 model 字段，需要手动添加
    gemini_dict["model"] = real_model

    # 规范化 Gemini 请求 (使用 antigravity 模式)
    from src.converter.antigravity_fix import normalize_antigravity_request
    gemini_dict = await normalize_antigravity_request(gemini_dict)

    # 准备API请求格式 - 提取model并将其他字段放入request中
    api_request = {
        "model": gemini_dict.pop("model"),
        "request": gemini_dict
    }

    # 请求日志上下文（api_format + 客户端 IP + 客户端原始模型名）
    log_ctx = {
        "api_format": "openai",
        "client_ip": request.client.host if request.client else "",
        "client_model": openai_request.model,
    }

    # ========== 非流式请求 ==========
    if not is_streaming:
        # 调用 API 层的非流式请求
        from src.api.antigravity import non_stream_request
        response = await non_stream_request(body=api_request, log_ctx=log_ctx)

        # 检查响应状态码
        status_code = getattr(response, "status_code", 200)

        # 提取响应体
        if hasattr(response, "body"):
            response_body = response.body.decode() if isinstance(response.body, bytes) else response.body
        elif hasattr(response, "content"):
            response_body = response.content.decode() if isinstance(response.content, bytes) else response.content
        else:
            response_body = str(response)

        try:
            gemini_response = json.loads(response_body)
        except Exception as e:
            log.error(f"Failed to parse Gemini response: {e}")
            raise HTTPException(status_code=500, detail="Response parsing failed")

        # 转换为 OpenAI 格式
        from src.converter.openai2gemini import convert_gemini_to_openai_response
        openai_response = convert_gemini_to_openai_response(
            gemini_response,
            real_model,
            status_code
        )

        return JSONResponse(content=openai_response, status_code=status_code)

    # ========== 流式请求 ==========

    # ========== 流式抗截断生成器 ==========
    async def anti_truncation_generator():
        from src.converter.anti_truncation import AntiTruncationStreamProcessor
        from src.api.antigravity import stream_request
        from src.converter.anti_truncation import apply_anti_truncation
        from fastapi import Response

        max_attempts = await get_anti_truncation_max_attempts()

        # 首先对payload应用反截断指令
        anti_truncation_payload = apply_anti_truncation(api_request)

        first_attempt_stream = stream_request(body=anti_truncation_payload, native=False, log_ctx=log_ctx)
        try:
            first_chunk = await read_first_async_item(first_attempt_stream)
        except StopAsyncIteration:
            return

        if isinstance(first_chunk, Response):
            yield first_chunk
            return

        first_attempt_pending = True

        async def stream_request_wrapper(payload):
            nonlocal first_attempt_pending

            if first_attempt_pending:
                first_attempt_pending = False
                stream_gen = prepend_async_item(first_chunk, first_attempt_stream)
            else:
                stream_gen = stream_request(body=payload, native=False, log_ctx=log_ctx)

            return StreamingResponse(stream_gen, media_type="text/event-stream")

        # 创建反截断处理器
        processor = AntiTruncationStreamProcessor(
            stream_request_wrapper,
            anti_truncation_payload,
            max_attempts,
        )

        # 转换为 OpenAI 格式
        import uuid
        response_id = str(uuid.uuid4())

        # 直接迭代 process_stream() 生成器，并转换为 OpenAI 格式
        async for chunk in processor.process_stream():
            if not chunk:
                continue

            # 解析 Gemini SSE 格式
            chunk_str = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk

            # 跳过空行
            if not chunk_str.strip():
                continue

            # 处理 [DONE] 标记
            if chunk_str.strip() == "data: [DONE]":
                yield "data: [DONE]\n\n".encode('utf-8')
                return

            # 解析 "data: {...}" 格式
            if chunk_str.startswith("data: "):
                try:
                    # 转换为 OpenAI 格式
                    from src.converter.openai2gemini import convert_gemini_to_openai_stream
                    openai_chunk_str = convert_gemini_to_openai_stream(
                        chunk_str,
                        real_model,
                        response_id
                    )

                    if openai_chunk_str:
                        yield openai_chunk_str.encode('utf-8')

                except Exception as e:
                    log.error(f"Failed to convert chunk: {e}")
                    continue

        # 发送结束标记
        yield "data: [DONE]\n\n".encode('utf-8')

    # ========== 普通流式生成器 ==========
    async def normal_stream_generator():
        from src.api.antigravity import stream_request
        from fastapi import Response
        import uuid

        # 调用 API 层的流式请求（不使用 native 模式）
        stream_gen = stream_request(body=api_request, native=False, log_ctx=log_ctx)
        try:
            first_chunk = await read_first_async_item(stream_gen)
        except StopAsyncIteration:
            return

        if isinstance(first_chunk, Response):
            yield first_chunk
            return

        response_id = str(uuid.uuid4())

        # yield所有数据,处理可能的错误Response
        async for chunk in prepend_async_item(first_chunk, stream_gen):
            # 检查是否是Response对象（错误情况）
            if isinstance(chunk, Response):
                # 将Response转换为SSE格式的错误消息
                try:
                    error_content = chunk.body if isinstance(chunk.body, bytes) else (chunk.body or b'').encode('utf-8')
                    gemini_error = json.loads(error_content.decode('utf-8'))
                    # 转换为 OpenAI 格式错误
                    from src.converter.openai2gemini import convert_gemini_to_openai_response
                    openai_error = convert_gemini_to_openai_response(
                        gemini_error,
                        real_model,
                        chunk.status_code
                    )
                    yield f"data: {json.dumps(openai_error)}\n\n".encode('utf-8')
                except Exception:
                    yield f"data: {json.dumps({'error': 'Stream error'})}\n\n".encode('utf-8')
                yield b"data: [DONE]\n\n"
                return
            else:
                # 正常的bytes数据，转换为 OpenAI 格式
                chunk_str = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk

                # 跳过空行
                if not chunk_str.strip():
                    continue

                # 处理 [DONE] 标记
                if chunk_str.strip() == "data: [DONE]":
                    yield "data: [DONE]\n\n".encode('utf-8')
                    return

                # 解析并转换 Gemini chunk 为 OpenAI 格式
                if chunk_str.startswith("data: "):
                    try:
                        # 转换为 OpenAI 格式
                        from src.converter.openai2gemini import convert_gemini_to_openai_stream
                        openai_chunk_str = convert_gemini_to_openai_stream(
                            chunk_str,
                            real_model,
                            response_id
                        )

                        if openai_chunk_str:
                            yield openai_chunk_str.encode('utf-8')

                    except Exception as e:
                        log.error(f"Failed to convert chunk: {e}")
                        continue

        # 发送结束标记
        yield "data: [DONE]\n\n".encode('utf-8')

    # ========== 根据模式选择生成器 ==========
    if use_anti_truncation:
        log.info("启用流式抗截断功能")
        return await build_streaming_response_or_error(anti_truncation_generator())
    else:
        return await build_streaming_response_or_error(normal_stream_generator())