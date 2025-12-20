from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Optional
import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from log import log

from .antigravity_api import (
    build_antigravity_request_body,
    send_antigravity_request_no_stream,
    send_antigravity_request_stream,
)
from .anthropic_converter import convert_anthropic_request_to_antigravity_components
from .anthropic_streaming import antigravity_sse_to_anthropic_sse
from .token_estimator import estimate_input_tokens

router = APIRouter()
security = HTTPBearer(auto_error=False)

_DEBUG_TRUE = {"1", "true", "yes", "on"}
_REDACTED = "<REDACTED>"
_SENSITIVE_KEYS = {
    "authorization",
    "x-api-key",
    "api_key",
    "apikey",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "secret",
}

def _remove_nulls_for_tool_input(value: Any) -> Any:
    """
    递归移除 dict/list 中值为 null/None 的字段/元素。

    背景：Roo/Kilo 在 Anthropic native tool 路径下，若收到 tool_use.input 中包含 null，
    可能会把 null 当作真实入参执行（例如“在 null 中搜索”）。因此在返回 tool_use.input 前做兜底清理。
    """
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            cleaned[k] = _remove_nulls_for_tool_input(v)
        return cleaned

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            if item is None:
                continue
            cleaned_list.append(_remove_nulls_for_tool_input(item))
        return cleaned_list

    return value


def _anthropic_debug_max_chars() -> int:
    """
    调试日志中单个字符串字段的最大输出长度（避免把 base64 图片/超长 schema 打爆日志）。
    """
    raw = str(os.getenv("ANTHROPIC_DEBUG_MAX_CHARS", "")).strip()
    if not raw:
        return 2000
    try:
        return max(200, int(raw))
    except Exception:
        return 2000


def _anthropic_debug_enabled() -> bool:
    return str(os.getenv("ANTHROPIC_DEBUG", "")).strip().lower() in _DEBUG_TRUE


def _anthropic_debug_body_enabled() -> bool:
    """
    是否打印请求体/下游请求体等“高体积”调试日志。

    说明：`ANTHROPIC_DEBUG=1` 仅开启 token 对比等精简日志；为避免刷屏，入参/下游 body 必须显式开启。
    """
    return str(os.getenv("ANTHROPIC_DEBUG_BODY", "")).strip().lower() in _DEBUG_TRUE


def _redact_for_log(value: Any, *, key_hint: str | None = None, max_chars: int) -> Any:
    """
    递归脱敏/截断用于日志打印的 JSON。

    目标：
    - 让用户能看到“实际入参结构”（system/messages/tools 等）
    - 默认避免泄露凭证/令牌
    - 避免把图片 base64 或超长字段直接写入日志文件
    """
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for k, v in value.items():
            k_str = str(k)
            k_lower = k_str.strip().lower()
            if k_lower in _SENSITIVE_KEYS:
                redacted[k_str] = _REDACTED
                continue
            redacted[k_str] = _redact_for_log(v, key_hint=k_lower, max_chars=max_chars)
        return redacted

    if isinstance(value, list):
        return [_redact_for_log(v, key_hint=key_hint, max_chars=max_chars) for v in value]

    if isinstance(value, str):
        if (key_hint or "").lower() == "data" and len(value) > 64:
            return f"<base64 len={len(value)}>"
        if len(value) > max_chars:
            head = value[: max_chars // 2]
            tail = value[-max_chars // 2 :]
            return f"{head}<...省略 {len(value) - len(head) - len(tail)} 字符...>{tail}"
        return value

    return value


def _json_dumps_for_log(data: Any) -> str:
    try:
        return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    except Exception:
        return str(data)


def _debug_log_request_payload(request: Request, payload: Dict[str, Any]) -> None:
    """
    在开启 `ANTHROPIC_DEBUG` 时打印入参（已脱敏/截断）。
    """
    if not _anthropic_debug_enabled() or not _anthropic_debug_body_enabled():
        return

    max_chars = _anthropic_debug_max_chars()
    safe_payload = _redact_for_log(payload, max_chars=max_chars)

    headers_of_interest = {
        "content-type": request.headers.get("content-type"),
        "content-length": request.headers.get("content-length"),
        "anthropic-version": request.headers.get("anthropic-version"),
        "user-agent": request.headers.get("user-agent"),
    }
    safe_headers = _redact_for_log(headers_of_interest, max_chars=max_chars)
    log.info(f"[ANTHROPIC][DEBUG] headers={_json_dumps_for_log(safe_headers)}")
    log.info(f"[ANTHROPIC][DEBUG] payload={_json_dumps_for_log(safe_payload)}")


def _debug_log_downstream_request_body(request_body: Dict[str, Any]) -> None:
    """
    在开启 `ANTHROPIC_DEBUG` 时打印最终转发到下游的请求体（已截断）。
    """
    if not _anthropic_debug_enabled() or not _anthropic_debug_body_enabled():
        return

    max_chars = _anthropic_debug_max_chars()
    safe_body = _redact_for_log(request_body, max_chars=max_chars)
    log.info(f"[ANTHROPIC][DEBUG] downstream_request_body={_json_dumps_for_log(safe_body)}")


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
    这里同时兼容两种方式，便于“无感接入”。
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


def _infer_project_and_session(credential_data: Dict[str, Any]) -> tuple[str, str]:
    project_id = credential_data.get("project_id")
    session_id = f"session-{uuid.uuid4().hex}"   
    return str(project_id), str(session_id)

def _pick_usage_metadata_from_antigravity_response(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    兼容下游 usageMetadata 的多种落点：
    - response.usageMetadata
    - response.candidates[0].usageMetadata

    如两者同时存在，优先选择“字段更完整”的一侧。
    """
    response = response_data.get("response", {}) or {}
    if not isinstance(response, dict):
        return {}

    response_usage = response.get("usageMetadata", {}) or {}
    if not isinstance(response_usage, dict):
        response_usage = {}

    candidate = (response.get("candidates", []) or [{}])[0] or {}
    if not isinstance(candidate, dict):
        candidate = {}
    candidate_usage = candidate.get("usageMetadata", {}) or {}
    if not isinstance(candidate_usage, dict):
        candidate_usage = {}

    fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")

    def score(d: Dict[str, Any]) -> int:
        s = 0
        for f in fields:
            if f in d and d.get(f) is not None:
                s += 1
        return s

    if score(candidate_usage) > score(response_usage):
        return candidate_usage
    return response_usage


def _convert_antigravity_response_to_anthropic_message(
    response_data: Dict[str, Any],
    *,
    model: str,
    message_id: str,
    fallback_input_tokens: int = 0,
) -> Dict[str, Any]:
    candidate = response_data.get("response", {}).get("candidates", [{}])[0] or {}
    parts = candidate.get("content", {}).get("parts", []) or []
    usage_metadata = _pick_usage_metadata_from_antigravity_response(response_data)

    content = []
    has_tool_use = False

    for part in parts:
        if not isinstance(part, dict):
            continue

        if part.get("thought") is True:
            block: Dict[str, Any] = {"type": "thinking", "thinking": part.get("text", "")}
            signature = part.get("thoughtSignature")
            if signature:
                block["signature"] = signature
            content.append(block)
            continue

        if "text" in part:
            content.append({"type": "text", "text": part.get("text", "")})
            continue

        if "functionCall" in part:
            has_tool_use = True
            fc = part.get("functionCall", {}) or {}
            content.append(
                {
                    "type": "tool_use",
                    "id": fc.get("id") or f"toolu_{uuid.uuid4().hex}",
                    "name": fc.get("name") or "",
                    "input": _remove_nulls_for_tool_input(fc.get("args", {}) or {}),
                }
            )
            continue

        if "inlineData" in part:
            inline = part.get("inlineData", {}) or {}
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": inline.get("mimeType", "image/png"),
                        "data": inline.get("data", ""),
                    },
                }
            )
            continue

    finish_reason = candidate.get("finishReason")
    stop_reason = "tool_use" if has_tool_use else "end_turn"
    if finish_reason == "MAX_TOKENS" and not has_tool_use:
        stop_reason = "max_tokens"

    input_tokens_present = isinstance(usage_metadata, dict) and "promptTokenCount" in usage_metadata
    output_tokens_present = isinstance(usage_metadata, dict) and "candidatesTokenCount" in usage_metadata

    input_tokens = usage_metadata.get("promptTokenCount", 0) if isinstance(usage_metadata, dict) else 0
    output_tokens = usage_metadata.get("candidatesTokenCount", 0) if isinstance(usage_metadata, dict) else 0

    if not input_tokens_present:
        input_tokens = max(0, int(fallback_input_tokens or 0))
    if not output_tokens_present:
        output_tokens = 0

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
        },
    }


@router.post("/antigravity/v1/messages")
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

    _debug_log_request_payload(request, payload)

    model = payload.get("model")
    max_tokens = payload.get("max_tokens")
    messages = payload.get("messages")
    stream = bool(payload.get("stream", False))
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

    if not model or max_tokens is None or not isinstance(messages, list):
        return _anthropic_error(
            status_code=400,
            message="缺少必填字段：model / max_tokens / messages",
            error_type="invalid_request_error",
        )

    try:
        client_host = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else "unknown"
    except Exception:
        client_host = "unknown"
        client_port = "unknown"

    user_agent = request.headers.get("user-agent", "")
    log.info(
        f"[ANTHROPIC] /messages 收到请求: client={client_host}:{client_port}, model={model}, "
        f"stream={stream}, messages={len(messages)}, thinking_present={thinking_present}, "
        f"thinking={thinking_summary}, ua={user_agent}"
    )

    if len(messages) == 1 and messages[0].get("role") == "user" and messages[0].get("content") == "Hi":
        return JSONResponse(
            content={
                "id": f"msg_{uuid.uuid4().hex}",
                "type": "message",
                "role": "assistant",
                "model": str(model),
                "content": [{"type": "text", "text": "antigravity Anthropic Messages 正常工作中"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 0, "output_tokens": 0},
            }
        )

    from src.credential_manager import get_credential_manager

    cred_mgr = await get_credential_manager()
    cred_result = await cred_mgr.get_valid_credential(is_antigravity=True)
    if not cred_result:
        return _anthropic_error(status_code=500, message="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id, session_id = _infer_project_and_session(credential_data)

    try:
        components = convert_anthropic_request_to_antigravity_components(payload)
    except Exception as e:
        log.error(f"[ANTHROPIC] 请求转换失败: {e}")
        return _anthropic_error(
            status_code=400, message="请求转换失败", error_type="invalid_request_error"
        )

    log.info(f"[ANTHROPIC] /messages 模型映射: upstream={model} -> downstream={components['model']}")

    # 下游要求每条 text 内容块必须包含“非空白”文本；上游客户端偶尔会追加空白 text block（例如图片后跟一个空字符串），
    # 经过转换过滤后可能导致 contents 为空，此时应在本地直接返回 400，避免把无效请求打到下游。
    if not (components.get("contents") or []):
        return _anthropic_error(
            status_code=400,
            message="messages 不能为空；text 内容块必须包含非空白文本",
            error_type="invalid_request_error",
        )

    # 简单估算 token
    estimated_tokens = 0
    try:
        estimated_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.debug(f"[ANTHROPIC] token 估算失败: {e}")

    request_body = build_antigravity_request_body(
        contents=components["contents"],
        model=components["model"],
        project_id=project_id,
        session_id=session_id,
        system_instruction=components["system_instruction"],
        tools=components["tools"],
        generation_config=components["generation_config"],
    )
    _debug_log_downstream_request_body(request_body)

    if stream:
        message_id = f"msg_{uuid.uuid4().hex}"

        try:
            resources, cred_name, _ = await send_antigravity_request_stream(request_body, cred_mgr)
            response, stream_ctx, client = resources
        except Exception as e:
            log.error(f"[ANTHROPIC] 下游流式请求失败: {e}")
            return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")

        async def stream_generator():
            try:
                # response 现在是 filtered_lines 生成器，直接使用
                async for chunk in antigravity_sse_to_anthropic_sse(
                    response,
                    model=str(model),
                    message_id=message_id,
                    initial_input_tokens=estimated_tokens,
                    credential_manager=cred_mgr,
                    credential_name=cred_name,
                ):
                    yield chunk
            finally:
                try:
                    await stream_ctx.__aexit__(None, None, None)
                except Exception as e:
                    log.debug(f"[ANTHROPIC] 关闭 stream_ctx 失败: {e}")
                try:
                    await client.aclose()
                except Exception as e:
                    log.debug(f"[ANTHROPIC] 关闭 client 失败: {e}")

        return StreamingResponse(stream_generator(), media_type="text/event-stream")

    request_id = f"msg_{int(time.time() * 1000)}"
    try:
        response_data, _, _ = await send_antigravity_request_no_stream(request_body, cred_mgr)
    except Exception as e:
        log.error(f"[ANTHROPIC] 下游非流式请求失败: {e}")
        return _anthropic_error(status_code=500, message="下游请求失败", error_type="api_error")

    anthropic_response = _convert_antigravity_response_to_anthropic_message(
        response_data,
        model=str(model),
        message_id=request_id,
        fallback_input_tokens=estimated_tokens,
    )
    return JSONResponse(content=anthropic_response)


@router.post("/antigravity/v1/messages/count_tokens")
async def anthropic_messages_count_tokens(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
):
    """
    Anthropic Messages API 兼容的 token 计数端点（用于 claude-cli 等客户端预检）。

    返回结构尽量贴近 Anthropic：`{"input_tokens": <int>}`。
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

    _debug_log_request_payload(request, payload)

    if not payload.get("model") or not isinstance(payload.get("messages"), list):
        return _anthropic_error(
            status_code=400,
            message="缺少必填字段：model / messages",
            error_type="invalid_request_error",
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
        f"[ANTHROPIC] /messages/count_tokens 收到请求: client={client_host}:{client_port}, "
        f"model={payload.get('model')}, messages={len(payload.get('messages') or [])}, "
        f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
    )

    # 简单估算
    input_tokens = 0
    try:
        input_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.error(f"[ANTHROPIC] token 估算失败: {e}")

    return JSONResponse(content={"input_tokens": input_tokens})
