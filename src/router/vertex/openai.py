"""
Vertex AI OpenAI-compatible Router - Handles OpenAI format requests via anonymous Vertex AI endpoint
通过匿名 Vertex AI 端点处理 OpenAI 格式请求
"""

import json
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from log import log
from src.utils import authenticate_bearer, get_base_model_from_feature_model
from src.models import OpenAIChatCompletionRequest, model_to_dict
from src.router.hi_check import is_health_check_request, create_health_check_response
from src.router.stream_passthrough import (
    build_streaming_response_or_error,
    prepend_async_item,
    read_first_async_item,
)

router = APIRouter()


@router.post("/vertex/v1/chat/completions")
async def chat_completions(
    openai_request: OpenAIChatCompletionRequest,
    token: str = Depends(authenticate_bearer),
):
    """处理 OpenAI 格式的聊天完成请求（流式和非流式），底层通过匿名 Vertex AI 端点。"""
    log.debug(f"[VERTEX-OPENAI] Request for model: {openai_request.model}")

    normalized_dict = model_to_dict(openai_request)

    if is_health_check_request(normalized_dict, format="openai"):
        return JSONResponse(content=create_health_check_response(format="openai"))

    real_model = get_base_model_from_feature_model(openai_request.model)
    is_streaming = openai_request.stream

    normalized_dict["model"] = real_model

    from src.converter.openai2gemini import convert_openai_to_gemini_request
    gemini_dict = await convert_openai_to_gemini_request(normalized_dict)
    gemini_dict["model"] = real_model

    from src.converter.gemini_fix import normalize_gemini_request
    gemini_dict = await normalize_gemini_request(gemini_dict, mode="vertex")

    api_request = {
        "model": gemini_dict.pop("model"),
        "request": gemini_dict,
    }

    # ========== 非流式请求 ==========
    if not is_streaming:
        from src.api.vertex import non_stream_request
        response = await non_stream_request(body=api_request)

        status_code = getattr(response, "status_code", 200)

        if hasattr(response, "body"):
            response_body = response.body.decode() if isinstance(response.body, bytes) else response.body
        elif hasattr(response, "content"):
            response_body = response.content.decode() if isinstance(response.content, bytes) else response.content
        else:
            response_body = str(response)

        try:
            gemini_response = json.loads(response_body)
        except Exception as e:
            log.error(f"[VERTEX-OPENAI] Failed to parse response: {e}")
            return JSONResponse(content={"error": "Response parsing failed"}, status_code=500)

        from src.converter.openai2gemini import convert_gemini_to_openai_response
        openai_response = convert_gemini_to_openai_response(gemini_response, real_model, status_code)
        return JSONResponse(content=openai_response, status_code=status_code)

    # ========== 流式请求 ==========
    async def stream_generator():
        from src.api.vertex import stream_request
        from fastapi import Response

        stream_gen = stream_request(body=api_request, native=False)
        try:
            first_chunk = await read_first_async_item(stream_gen)
        except StopAsyncIteration:
            return

        if isinstance(first_chunk, Response):
            yield first_chunk
            return

        response_id = str(uuid.uuid4())

        async for chunk in prepend_async_item(first_chunk, stream_gen):
            if isinstance(chunk, Response):
                try:
                    error_content = chunk.body if isinstance(chunk.body, bytes) else (chunk.body or b"").encode()
                    gemini_error = json.loads(error_content.decode())
                    from src.converter.openai2gemini import convert_gemini_to_openai_response
                    openai_error = convert_gemini_to_openai_response(gemini_error, real_model, chunk.status_code)
                    yield f"data: {json.dumps(openai_error)}\n\n".encode()
                except Exception:
                    yield f"data: {json.dumps({'error': 'Stream error'})}\n\n".encode()
                yield b"data: [DONE]\n\n"
                return

            chunk_str = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk

            if not chunk_str.strip():
                continue

            if chunk_str.strip() == "data: [DONE]":
                yield "data: [DONE]\n\n".encode("utf-8")
                return

            if chunk_str.startswith("data: "):
                try:
                    from src.converter.openai2gemini import convert_gemini_to_openai_stream
                    openai_chunk_str = convert_gemini_to_openai_stream(chunk_str, real_model, response_id)
                    if openai_chunk_str:
                        yield openai_chunk_str.encode("utf-8")
                except Exception as e:
                    log.error(f"[VERTEX-OPENAI] Failed to convert chunk: {e}")
                    continue

        yield "data: [DONE]\n\n".encode("utf-8")

    return await build_streaming_response_or_error(stream_generator())
