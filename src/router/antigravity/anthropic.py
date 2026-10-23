"""
Anthropic Router - Handles Anthropic/Claude format API requests via Antigravity
通过Antigravity处理Anthropic/Claude格式请求的路由模块
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
from config import get_anti_truncation_max_attempts, get_api_password
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
from src.models import ClaudeRequest, model_to_dict

# 本地模块 - Token估算
from src.token_estimator import estimate_input_tokens


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.post("/v1/messages")
async def messages(
    claude_request: ClaudeRequest,
    request: Request,
    _token: str = Depends(authenticate_bearer)
):
    """
    处理Anthropic/Claude格式的消息请求（流式和非流式）

    Args:
        claude_request: Anthropic/Claude格式的请求体
        token: Bearer认证令牌
    """
    log.debug(f"[ANTIGRAVITY-ANTHROPIC] Request for model: {claude_request.model}")

    # 转换为字典
    normalized_dict = model_to_dict(claude_request)

    # 健康检查
    if is_health_check_request(normalized_dict, format="anthropic"):
        response = create_health_check_response(format="anthropic")
        return JSONResponse(content=response)

    # 处理模型名称和功能检测
    use_anti_truncation = is_anti_truncation_model(claude_request.model)
    real_model = get_base_model_from_feature_model(claude_request.model)

    # 获取流式标志
    is_streaming = claude_request.stream

    # 对于抗截断模型的非流式请求，给出警告
    if use_anti_truncation and not is_streaming:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")

    # 更新模型名为真实模型名
    normalized_dict["model"] = real_model

    # 转换为 Gemini 格式 (使用 converter)
    from src.converter.anthropic2gemini import anthropic_to_gemini_request
    gemini_dict = await anthropic_to_gemini_request(normalized_dict)

    # anthropic_to_gemini_request 不包含 model 字段，需要手动添加
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
        "api_format": "anthropic",
        "client_ip": request.client.host if request.client else "",
        "client_model": claude_request.model,
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

        # 转换为 Anthropic 格式
        from src.converter.anthropic2gemini import gemini_to_anthropic_response
        anthropic_response = gemini_to_anthropic_response(
            gemini_response,
            real_model,
            status_code
        )

        return JSONResponse(content=anthropic_response, status_code=status_code)

    # ========== 流式请求 ==========

    # ========== 流式抗截断生成器 ==========
    async def anti_truncation_generator():
        from src.converter.anti_truncation import AntiTruncationStreamProcessor
        from src.api.antigravity import stream_request
        from src.converter.anti_truncation import apply_anti_truncation
        from src.converter.anthropic2gemini import gemini_stream_to_anthropic_stream
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

        # 包装以确保是bytes流
        async def bytes_wrapper():
            async for chunk in processor.process_stream():
                if isinstance(chunk, str):
                    yield chunk.encode('utf-8')
                else:
                    yield chunk

        # 直接将整个流传递给转换器
        async for anthropic_chunk in gemini_stream_to_anthropic_stream(
            bytes_wrapper(),
            real_model,
            200
        ):
            if anthropic_chunk:
                yield anthropic_chunk

    # ========== 普通流式生成器 ==========
    async def normal_stream_generator():
        from src.api.antigravity import stream_request
        from fastapi import Response
        from src.converter.anthropic2gemini import gemini_stream_to_anthropic_stream

        # 调用 API 层的流式请求（不使用 native 模式）
        stream_gen = stream_request(body=api_request, native=False, log_ctx=log_ctx)
        try:
            first_chunk = await read_first_async_item(stream_gen)
        except StopAsyncIteration:
            return

        if isinstance(first_chunk, Response):
            yield first_chunk
            return

        # 包装流式生成器以处理错误响应
        async def gemini_chunk_wrapper():
            async for chunk in prepend_async_item(first_chunk, stream_gen):
                # 检查是否是Response对象（错误情况）
                if isinstance(chunk, Response):
                    # 错误响应，不进行转换，直接传递
                    try:
                        error_content = chunk.body if isinstance(chunk.body, bytes) else (chunk.body or b'').encode('utf-8')
                        gemini_error = json.loads(error_content.decode('utf-8'))
                        from src.converter.anthropic2gemini import gemini_to_anthropic_response
                        anthropic_error = gemini_to_anthropic_response(
                            gemini_error,
                            real_model,
                            chunk.status_code
                        )
                        yield f"data: {json.dumps(anthropic_error)}\n\n".encode('utf-8')
                    except Exception:
                        yield f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': 'Stream error'}})}\n\n".encode('utf-8')
                    yield b"data: [DONE]\n\n"
                    return
                else:
                    # 确保是bytes类型
                    if isinstance(chunk, str):
                        yield chunk.encode('utf-8')
                    else:
                        yield chunk

        # 使用转换器处理整个流
        async for anthropic_chunk in gemini_stream_to_anthropic_stream(
            gemini_chunk_wrapper(),
            real_model,
            200
        ):
            if anthropic_chunk:
                yield anthropic_chunk

    # ========== 根据模式选择生成器 ==========
    if use_anti_truncation:
        log.info("启用流式抗截断功能")
        return await build_streaming_response_or_error(anti_truncation_generator())
    else:
        return await build_streaming_response_or_error(normal_stream_generator())


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    _token: str = Depends(authenticate_bearer)
):
    """
    处理Anthropic格式的token计数请求
    
    Args:
        request: FastAPI请求对象
        _token: Bearer认证令牌（由Depends验证）
    
    Returns:
        JSONResponse: 包含input_tokens的响应
    """
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": f"JSON 解析失败: {str(e)}"}}
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "请求体必须为 JSON object"}}
        )

    if not payload.get("model") or not isinstance(payload.get("messages"), list):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "缺少必填字段：model / messages"}}
        )

    try:
        client_host = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else "unknown"
    except Exception:
        client_host = "unknown"
        client_port = "unknown"

    thinking_present = "thinking" in payload
    thinking_value = payload.get("thinking")
    thinking_summary = None
    if thinking_present:
        if isinstance(thinking_value, dict):
            thinking_summary = {
                "type": thinking_value.get("type"),
                "budget_tokens": thinking_value.get("budget_tokens"),
            }
        else:
            thinking_summary = thinking_value

    user_agent = request.headers.get("user-agent", "")
    log.info(
        f"[ANTIGRAVITY-ANTHROPIC] /messages/count_tokens 收到请求: client={client_host}:{client_port}, "
        f"model={payload.get('model')}, messages={len(payload.get('messages') or [])}, "
        f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
    )

    # 简单估算
    input_tokens = 0
    try:
        input_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.error(f"[ANTIGRAVITY-ANTHROPIC] token 估算失败: {e}")

    return JSONResponse(content={"input_tokens": input_tokens})