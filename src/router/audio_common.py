"""
Audio Router Factory - 共用的 OpenAI 音频转录路由

各后端 (geminicli / antigravity) 通过 build_audio_router 生成
互相独立但行为一致的 /v1/audio/transcriptions 路由。

音频以 inlineData 的形式随 generateContent 一起发送，
凭证轮换、重试和错误处理全部复用各后端已有的 non_stream_request。
"""

import importlib
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from config import get_audio_transcription_model
from log import log
from src.converter.audio import (
    build_transcription_request,
    detect_audio_mime_type,
    encode_to_base64,
    exceeds_size_limit,
    extract_transcription_text,
    MAX_INLINE_AUDIO_BYTES,
)
from src.utils import authenticate_bearer, get_base_model_from_feature_model

# 支持的后端及其 API 模块
BACKEND_API_MODULES = {
    "geminicli": "src.api.geminicli",
    "antigravity": "src.api.antigravity",
}

# 客户端常用的 OpenAI 转录模型名，收到这些名字时改用配置中的 Gemini 模型，
# 这样未经改造的 OpenAI SDK 也能直接指向本服务。
OPENAI_TRANSCRIPTION_MODELS = frozenset(
    {
        "whisper",
        "whisper-1",
        "whisper-large-v3",
        "gpt-4o-transcribe",
        "gpt-4o-mini-transcribe",
    }
)

# 需要时间戳的响应格式，当前实现不返回分段信息
UNSUPPORTED_RESPONSE_FORMATS = frozenset({"verbose_json", "srt", "vtt"})


def _error_response(status_code: int, message: str, error_type: str) -> JSONResponse:
    """构建 OpenAI 风格的错误响应"""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type, "code": None}},
    )


async def _resolve_model(requested_model: Optional[str]) -> str:
    """将客户端请求的模型名解析为实际调用的 Gemini 模型名"""
    base_model = get_base_model_from_feature_model((requested_model or "").strip())

    if not base_model or base_model.lower() in OPENAI_TRANSCRIPTION_MODELS:
        return await get_audio_transcription_model()

    return base_model


def _parse_response_body(response: Response) -> Dict[str, Any]:
    """从后端返回的 Response 对象中解析 JSON 响应体"""
    body = getattr(response, "body", None)
    if body is None:
        body = getattr(response, "content", b"")
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    return json.loads(body)


def build_audio_router(path: str, backend: str) -> APIRouter:
    """
    构建某个后端的音频转录路由

    Args:
        path: 路由路径，例如 "/v1/audio/transcriptions"
        backend: 后端名称，必须是 BACKEND_API_MODULES 中的键

    Returns:
        已注册转录路由的 APIRouter
    """
    if backend not in BACKEND_API_MODULES:
        raise ValueError(f"Unknown audio backend: {backend}")

    router = APIRouter()
    log_prefix = f"[{backend.upper()}-AUDIO]"

    @router.post(path)
    async def create_transcription(
        file: UploadFile = File(...),
        model: str = Form(default=""),
        prompt: str = Form(default=""),
        language: Optional[str] = Form(default=None),
        response_format: str = Form(default="json"),
        temperature: Optional[float] = Form(default=None),
        token: str = Depends(authenticate_bearer),
    ):
        """处理 OpenAI 格式的音频转录请求 (multipart/form-data)"""
        response_format = (response_format or "json").strip().lower()
        if response_format in UNSUPPORTED_RESPONSE_FORMATS:
            return _error_response(
                400,
                f"response_format '{response_format}' is not supported; "
                f"use 'json' or 'text'.",
                "invalid_request_error",
            )
        if response_format not in ("json", "text"):
            return _error_response(
                400,
                f"Unknown response_format '{response_format}'; use 'json' or 'text'.",
                "invalid_request_error",
            )

        # 1. 解析音频格式
        try:
            mime_type = detect_audio_mime_type(file.filename, file.content_type)
        except ValueError as e:
            return _error_response(400, str(e), "invalid_request_error")

        # 2. 读取并校验大小
        audio_bytes = await file.read()
        if not audio_bytes:
            return _error_response(
                400, "Uploaded audio file is empty.", "invalid_request_error"
            )

        if exceeds_size_limit(len(audio_bytes)):
            limit_mb = MAX_INLINE_AUDIO_BYTES // (1024 * 1024)
            return _error_response(
                413,
                f"Audio file too large ({len(audio_bytes)} bytes). "
                f"The inline upload limit is {limit_mb} MB "
                f"(roughly 16 minutes of MP3); compress or split the file.",
                "invalid_request_error",
            )

        # 3. 构建 Gemini 请求
        real_model = await _resolve_model(model)
        log.info(
            f"{log_prefix} transcription: file={file.filename}, "
            f"size={len(audio_bytes)} bytes, mime={mime_type}, model={real_model}"
        )

        api_request = {
            "model": real_model,
            "request": build_transcription_request(
                audio_base64=encode_to_base64(audio_bytes),
                mime_type=mime_type,
                prompt=prompt,
                language=language,
                temperature=temperature,
            ),
        }

        # 4. 调用后端 (凭证轮换和重试由 non_stream_request 负责)
        module = importlib.import_module(BACKEND_API_MODULES[backend])
        response = await module.non_stream_request(body=api_request)

        # 5. 解析响应
        # 重建响应而不是直接转发后端的 Response，避免把上游的
        # Content-Encoding / Content-Length 等响应头带到已解码的响应体上
        status_code = getattr(response, "status_code", 200)
        upstream_failed = not 200 <= status_code < 300

        try:
            payload = _parse_response_body(response)
        except Exception as e:
            log.error(f"{log_prefix} failed to parse upstream response: {e}")
            if upstream_failed:
                return _error_response(
                    status_code,
                    f"Upstream returned status {status_code}.",
                    "api_error",
                )
            return _error_response(
                502, f"Failed to parse upstream response: {e}", "api_error"
            )

        if upstream_failed or "error" in payload:
            log.error(
                f"{log_prefix} upstream error (status={status_code}): "
                f"{json.dumps(payload, ensure_ascii=False)[:500]}"
            )
            return JSONResponse(
                status_code=status_code if upstream_failed else 502, content=payload
            )

        # 6. 提取转录文本
        text = extract_transcription_text(payload)
        log.info(f"{log_prefix} transcription completed, {len(text)} characters")

        if response_format == "text":
            return PlainTextResponse(content=text)

        return JSONResponse(content={"text": text})

    return router
