"""
Vertex AI Router - Handles native Gemini format API requests via anonymous Vertex AI endpoint
通过匿名 Vertex AI 端点处理 Gemini 格式请求
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse

from log import log
from src.utils import authenticate_gemini_flexible
from src.models import GeminiRequest, model_to_dict
from src.router.hi_check import is_health_check_request, create_health_check_response
from src.router.stream_passthrough import build_streaming_response_or_error


router = APIRouter()


@router.post("/vertex/v1beta/models/{model:path}:generateContent")
@router.post("/vertex/v1/models/{model:path}:generateContent")
async def generate_content(
    gemini_request: GeminiRequest,
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """处理 Vertex 匿名通道的非流式内容生成请求。"""
    log.debug(f"[VERTEX ROUTER] Non-streaming request for model: {model}")

    normalized_dict = model_to_dict(gemini_request)

    if is_health_check_request(normalized_dict, format="gemini"):
        return JSONResponse(content=create_health_check_response(format="gemini"))

    normalized_dict["model"] = model

    from src.converter.gemini_fix import normalize_gemini_request
    normalized_dict = await normalize_gemini_request(normalized_dict, mode="vertex")

    api_request = {
        "model": normalized_dict.pop("model"),
        "request": normalized_dict,
    }

    from src.api.vertex import non_stream_request
    response = await non_stream_request(body=api_request)

    return response


@router.post("/vertex/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/vertex/v1/models/{model:path}:streamGenerateContent")
async def stream_generate_content(
    gemini_request: GeminiRequest,
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """处理 Vertex 匿名通道的流式内容生成请求。"""
    log.debug(f"[VERTEX ROUTER] Streaming request for model: {model}")

    normalized_dict = model_to_dict(gemini_request)
    normalized_dict["model"] = model

    async def stream_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        from src.api.vertex import stream_request
        from fastapi import Response

        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="vertex")

        api_request = {
            "model": normalized_req.pop("model"),
            "request": normalized_req,
        }

        async for chunk in stream_request(body=api_request, native=False):
            if isinstance(chunk, Response):
                yield chunk
                return
            if isinstance(chunk, (str, bytes)):
                yield chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

    return await build_streaming_response_or_error(stream_generator())


@router.post("/vertex/v1beta/models/{model:path}:countTokens")
@router.post("/vertex/v1/models/{model:path}:countTokens")
async def count_tokens(
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """模拟 token 计数（启发式估算）。"""
    try:
        request_data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    total_tokens = 0
    contents = None

    if "generateContentRequest" in request_data:
        contents = request_data["generateContentRequest"].get("contents", [])
    elif "contents" in request_data:
        contents = request_data["contents"]

    if contents:
        for content in contents:
            if isinstance(content, dict) and "parts" in content:
                for part in content["parts"]:
                    if isinstance(part, dict) and "text" in part:
                        total_tokens += max(1, len(part["text"]) // 4)

    return JSONResponse(content={"totalTokens": total_tokens})
