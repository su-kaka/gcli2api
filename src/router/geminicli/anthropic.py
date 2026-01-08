from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from log import log

from src.api.geminicli import (
    send_geminicli_request_no_stream,
    send_geminicli_request_stream,
)
from src.converter.anthropic2gemini import (
    convert_anthropic_request_to_gemini,
    convert_gemini_response_to_anthropic,
    gemini_sse_to_anthropic_sse,
    validate_and_extract_anthropic_request,
    validate_anthropic_count_tokens_request,
    AnthropicRequestValidationError,
)
from src.router.hi_check import is_health_check_message, create_health_check_response
from src.router.base_router import wrap_stream_with_processor
from src.api.geminicli import build_gemini_payload_from_native
from src.token_estimator import estimate_input_tokens

router = APIRouter()
security = HTTPBearer(auto_error=False)


def _anthropic_error(
    *,
    status_code: int,
    message: str,
    error_type: str = "api_error",
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": error_type, "message": message}},
    )


def _extract_api_token(
    request: Request, credentials: Optional[HTTPAuthorizationCredentials]
) -> Optional[str]:
    """
    Anthropic 生态客户端通常使用 `x-api-key`；现有项目其它路由使用 `Authorization: Bearer`。
    这里同时兼容两种方式，便于"无感接入"。
    """
    if credentials and credentials.credentials:
        return credentials.credentials

    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()

    x_api_key = request.headers.get("x-api-key")
    if x_api_key:
        return x_api_key.strip()

    return None


@router.post("/v1/messages")
async def anthropic_messages(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    from config import get_api_password

    password = await get_api_password()
    token = _extract_api_token(request, credentials)
    if token != password:
        return _anthropic_error(status_code=403, message="密码错误", error_type="authentication_error")

    try:
        payload = await request.json()
    except Exception as e:
        return _anthropic_error(
            status_code=400, message=f"JSON 解析失败: {str(e)}", error_type="invalid_request_error"
        )

    if not isinstance(payload, dict):
        return _anthropic_error(
            status_code=400, message="请求体必须为 JSON object", error_type="invalid_request_error"
        )

    # 验证并提取请求字段
    try:
        extracted = validate_and_extract_anthropic_request(payload)
    except AnthropicRequestValidationError as e:
        return _anthropic_error(
            status_code=400,
            message=e.message,
            error_type=e.error_type,
        )

    model = extracted["model"]
    messages = extracted["messages"]
    stream = extracted["stream"]

    log.info(f"[ANTHROPIC-GEMINICLI] /messages 收到请求: model={model}, stream={stream}, messages={len(messages)}")

    # 健康检查
    if is_health_check_message(messages):
        return JSONResponse(
            content=create_health_check_response(
                format="anthropic",
                model=model,
                message_id=f"msg_{uuid.uuid4().hex}"
            )
        )

    try:
        components = convert_anthropic_request_to_gemini(payload)
    except Exception as e:
        log.error(f"[ANTHROPIC-GEMINICLI] 请求转换失败: {e}")
        return _anthropic_error(
            status_code=400, message="请求转换失败", error_type="invalid_request_error"
        )

    log.info(f"[ANTHROPIC-GEMINICLI] 模型映射: upstream={model} -> downstream={components['model']}")

    # 下游要求每条 text 内容块必须包含"非空白"文本
    if not (components.get("contents") or []):
        return _anthropic_error(
            status_code=400,
            message="messages 不能为空；text 内容块必须包含非空白文本",
            error_type="invalid_request_error",
        )

    # 估算 token
    estimated_tokens = 0
    try:
        estimated_tokens = estimate_input_tokens(payload)
    except Exception:
        pass

    request_body = build_gemini_payload_from_native(
        {
            "contents": components["contents"],
            "systemInstruction": components["system_instruction"],
            "tools": components["tools"],
            "generationConfig": components["generation_config"],
        },
        components["model"]
    )

    if stream:
        message_id = f"msg_{uuid.uuid4().hex}"

        try:
            resources, _, _ = await send_geminicli_request_stream(request_body)
            response, stream_ctx, client = resources
        except Exception as e:
            log.error(f"[ANTHROPIC-GEMINICLI] 下游流式请求失败: {e}")
            return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")

        return StreamingResponse(
            await wrap_stream_with_processor(
                response, stream_ctx, client,
                lambda lines: gemini_sse_to_anthropic_sse(
                    lines,
                    model=str(model),
                    message_id=message_id,
                    initial_input_tokens=estimated_tokens
                )
            ),
            media_type="text/event-stream"
        )

    request_id = f"msg_{int(time.time() * 1000)}"
    try:
        response_data, _, _ = await send_geminicli_request_no_stream(request_body)
    except Exception as e:
        log.error(f"[ANTHROPIC-GEMINICLI] 下游非流式请求失败: {e}")
        return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")

    anthropic_response = convert_gemini_response_to_anthropic(
        response_data,
        model=str(model),
        message_id=request_id,
        fallback_input_tokens=estimated_tokens,
    )
    return JSONResponse(content=anthropic_response)


@router.post("/v1/messages/count_tokens")
async def anthropic_messages_count_tokens(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    Anthropic Messages API 兼容的 token 计数端点。
    """
    from config import get_api_password

    password = await get_api_password()
    token = _extract_api_token(request, credentials)
    if token != password:
        return _anthropic_error(status_code=403, message="密码错误", error_type="authentication_error")

    try:
        payload = await request.json()
    except Exception as e:
        return _anthropic_error(
            status_code=400, message=f"JSON 解析失败: {str(e)}", error_type="invalid_request_error"
        )

    if not isinstance(payload, dict):
        return _anthropic_error(
            status_code=400, message="请求体必须为 JSON object", error_type="invalid_request_error"
        )

    # 验证并提取请求字段
    try:
        extracted = validate_anthropic_count_tokens_request(payload)
    except AnthropicRequestValidationError as e:
        return _anthropic_error(
            status_code=400,
            message=e.message,
            error_type=e.error_type,
        )

    model = extracted["model"]
    messages = extracted["messages"]

    log.info(f"[ANTHROPIC-GEMINICLI] /messages/count_tokens 收到请求: model={model}, messages={len(messages)}")

    # 估算 token
    input_tokens = 0
    try:
        input_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.error(f"[ANTHROPIC-GEMINICLI] token 估算失败: {e}")

    return JSONResponse(content={"input_tokens": input_tokens})
