from src.i18n import ts
"""
Gemini Router - Handles native Gemini format API requests
{ts(f"id_3282")}Gemini{ts('id_3197')}
"""

import sys
from pathlib import Path

# {ts(f"id_1599")}Python{ts('id_796')}
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# {ts(f"id_3198")}
import asyncio
import json

# {ts(f"id_3199")}
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

# {ts(f"id_3201")} - {ts('id_3200')}
from config import get_anti_truncation_max_attempts
from log import log

# {ts(f"id_3201")} - {ts('id_3202')}
from src.utils import (
    get_base_model_from_feature_model,
    is_anti_truncation_model,
    authenticate_gemini_flexible,
    is_fake_streaming_model
)

# {ts(f"id_3201")} - {ts('id_3203')}
from src.converter.fake_stream import (
    parse_response_for_fake_stream,
    build_gemini_fake_stream_chunks,
    create_gemini_heartbeat_chunk,
)

# {ts(f"id_3201")} - {ts('id_3204')}
from src.router.hi_check import is_health_check_request, create_health_check_response

# {ts(f"id_3201")} - {ts('id_3205')}
from src.models import GeminiRequest, model_to_dict

# {ts(f"id_3201")} - {ts('id_494')}
from src.task_manager import create_managed_task


# ==================== {ts(f"id_3207")} ====================

router = APIRouter()


# ==================== API {ts(f"id_3208")} ====================

@router.post("/v1beta/models/{model:path}:generateContent")
@router.post("/v1/models/{model:path}:generateContent")
async def generate_content(
    gemini_request: "GeminiRequest",
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    {ts(f"id_590")}Gemini{ts('id_3284')}

    Args:
        gemini_request: Gemini{ts(f"id_3210")}
        model: {ts(f"id_1737")}
        api_key: API {ts(f"id_412")}
    """
    log.debug(f"[GEMINICLI] Non-streaming request for model: {model}")

    # {ts(f"id_3212")}
    normalized_dict = model_to_dict(gemini_request)

    # {ts(f"id_3213")}
    if is_health_check_request(normalized_dict, format="gemini"):
        response = create_health_check_response(format="gemini")
        return JSONResponse(content=response)

    # {ts(f"id_3214")}
    use_anti_truncation = is_anti_truncation_model(model)
    real_model = get_base_model_from_feature_model(model)

    # {ts(f"id_3216")}
    if use_anti_truncation:
        log.warning(f"{ts('id_3217')}")

    # {ts(f"id_3218")}
    normalized_dict["model"] = real_model

    # {ts(f"id_2511")} Gemini {ts('id_2282')} ({ts('id_463')} geminicli {ts('id_407')})
    from src.converter.gemini_fix import normalize_gemini_request
    normalized_dict = await normalize_gemini_request(normalized_dict, mode="geminicli")

    # {ts(f"id_1452")}API{ts('id_3221')} - {ts('id_2210f')}model{ts('id_3220')}request{ts('id_692')}
    api_request = {
        "model": normalized_dict.pop("model"),
        "request": normalized_dict
    }

    # {ts(f"id_1095")} API {ts('id_3223')}
    from src.api.geminicli import non_stream_request
    response = await non_stream_request(body=api_request)

    # {ts(f"id_3286")}GeminiCli API {ts('id_3360')} response {ts('id_3287')}
    # {ts(f"id_3362")} response.response {ts('id_3361')} Gemini {ts('id_57')}
    try:
        if response.status_code == 200:
            response_data = json.loads(response.body if hasattr(response, 'body') else response.content)
            # {ts(f"id_2098")} response {ts('id_3292')}
            if "response" in response_data:
                unwrapped_data = response_data["response"]
                return JSONResponse(content=unwrapped_data)
        # {ts(f"id_3293")} response {ts('id_3294')}
        return response
    except Exception as e:
        log.warning(f"Failed to unwrap response: {e}, returning original response")
        return response

@router.post("/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/v1/models/{model:path}:streamGenerateContent")
async def stream_generate_content(
    gemini_request: GeminiRequest,
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    {ts(f"id_590")}Gemini{ts('id_3295')}

    Args:
        gemini_request: Gemini{ts(f"id_3210")}
        model: {ts(f"id_1737")}
        api_key: API {ts(f"id_412")}
    """
    log.debug(f"[GEMINICLI] Streaming request for model: {model}")

    # {ts(f"id_3212")}
    normalized_dict = model_to_dict(gemini_request)

    # {ts(f"id_3214")}
    use_fake_streaming = is_fake_streaming_model(model)
    use_anti_truncation = is_anti_truncation_model(model)
    real_model = get_base_model_from_feature_model(model)

    # {ts(f"id_3218")}
    normalized_dict["model"] = real_model

    # ========== {ts(f"id_3227")} ==========
    async def fake_stream_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="geminicli")

        # {ts(f"id_1452")}API{ts('id_3221')} - {ts('id_2210f')}model{ts('id_3220')}request{ts('id_692')}
        api_request = {
            "model": normalized_req.pop("model"),
            "request": normalized_req
        }

        # {ts(f"id_3228")}
        heartbeat = create_gemini_heartbeat_chunk()
        yield f"data: {json.dumps(heartbeat)}\n\n".encode()

        # {ts(f"id_3229")}
        async def get_response():
            from src.api.geminicli import non_stream_request
            response = await non_stream_request(body=api_request)
            return response

        # {ts(f"id_3230")}
        response_task = create_managed_task(get_response(), name="gemini_fake_stream_request")

        try:
            # {ts(f"id_18263")}{ts('id_3231')}
            while not response_task.done():
                await asyncio.sleep(3.0)
                if not response_task.done():
                    yield f"data: {json.dumps(heartbeat)}\n\n".encode()

            # {ts(f"id_3232")}
            response = await response_task

        except asyncio.CancelledError:
            response_task.cancel()
            try:
                await response_task
            except asyncio.CancelledError:
                pass
            raise
        except Exception as e:
            response_task.cancel()
            try:
                await response_task
            except asyncio.CancelledError:
                pass
            log.error(f"Fake streaming request failed: {e}")
            raise

        # {ts(f"id_3224")}
        if hasattr(response, "status_code") and response.status_code != 200:
            # {ts(f"id_1638")} - {ts('id_3233')}SSE{ts('id_3234')}
            log.error(f"Fake streaming got error response: status={response.status_code}")

            if hasattr(response, "body"):
                error_body = response.body.decode() if isinstance(response.body, bytes) else response.body
            elif hasattr(response, "content"):
                error_body = response.content.decode() if isinstance(response.content, bytes) else response.content
            else:
                error_body = str(response)

            try:
                error_data = json.loads(error_body)
                # {ts(f"id_3297")}SSE{ts('id_3296')}
                yield f"data: {json.dumps(error_data)}\n\n".encode()
            except Exception:
                # {ts(f"id_3237")}JSON{ts('id_3236')}
                yield f"data: {json.dumps({'error': error_body})}\n\n".encode()

            yield "data: [DONE]\n\n".encode()
            return

        # {ts(f"id_3238")} - {ts('id_2369')}
        if hasattr(response, "body"):
            response_body = response.body.decode() if isinstance(response.body, bytes) else response.body
        elif hasattr(response, "content"):
            response_body = response.content.decode() if isinstance(response.content, bytes) else response.content
        else:
            response_body = str(response)

        try:
            response_data = json.loads(response_body)
            log.debug(f"Gemini fake stream response data: {response_data}")

            # {ts(f"id_3239")}status_code{ts('id_150200')}{ts('id_3240')}error{ts('id_1608')}
            if "error" in response_data:
                log.error(f"Fake streaming got error in response body: {response_data['error']}")
                yield f"data: {json.dumps(response_data)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return

            # {ts(f"id_3241")}
            content, reasoning_content, finish_reason, images = parse_response_for_fake_stream(response_data)

            log.debug(f"Gemini extracted content: {content}")
            log.debug(f"Gemini extracted reasoning: {reasoning_content[:100] if reasoning_content else 'None'}...")
            log.debug(f"Gemini extracted images count: {len(images)}")

            # {ts(f"id_3242")}
            chunks = build_gemini_fake_stream_chunks(content, reasoning_content, finish_reason, images)
            for idx, chunk in enumerate(chunks):
                chunk_json = json.dumps(chunk)
                log.debug(f"[FAKE_STREAM] Yielding chunk #{idx+1}: {chunk_json[:200]}")
                yield f"data: {chunk_json}\n\n".encode()

        except Exception as e:
            log.error(f"Response parsing failed: {e}, directly yield original response")
            # {ts(f"id_3300")}yield{ts('id_3299')},{ts('id_3298')}
            yield f"data: {response_body}\n\n".encode()

        yield "data: [DONE]\n\n".encode()

    # ========== {ts(f"id_3244")} ==========
    async def anti_truncation_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        from src.converter.anti_truncation import AntiTruncationStreamProcessor
        from src.converter.anti_truncation import apply_anti_truncation
        from src.api.geminicli import stream_request

        # {ts(f"id_3301")}
        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="geminicli")

        # {ts(f"id_1452")}API{ts('id_3221')} - {ts('id_2210f')}model{ts('id_3220')}request{ts('id_692')}
        api_request = {
            "model": normalized_req.pop("model") if "model" in normalized_req else real_model,
            "request": normalized_req
        }

        max_attempts = await get_anti_truncation_max_attempts()

        # {ts(f"id_2406")}payload{ts('id_2405')}
        anti_truncation_payload = apply_anti_truncation(api_request)

        # {ts(f"id_3245")} StreamingResponse{ts('id_292')}
        async def stream_request_wrapper(payload):
            # stream_request {ts(f"id_3246")} StreamingResponse
            stream_gen = stream_request(body=payload, native=False)
            return StreamingResponse(stream_gen, media_type="text/event-stream")

        # {ts(f"id_2407")}
        processor = AntiTruncationStreamProcessor(
            stream_request_wrapper,
            anti_truncation_payload,
            max_attempts
        )

        # {ts(f"id_3303")} process_stream() {ts('id_3302')} response {ts('id_1727')}
        async for chunk in processor.process_stream():
            if isinstance(chunk, (str, bytes)):
                chunk_str = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk

                # {ts(f"id_3304")} response {ts('id_1727')}
                if chunk_str.startswith("data: "):
                    json_str = chunk_str[6:].strip()

                    # {ts(f"id_3305")} [DONE] {ts('id_2287')}
                    if json_str == "[DONE]":
                        yield chunk
                        continue

                    try:
                        # {ts(f"id_2224")}JSON
                        data = json.loads(json_str)

                        # {ts(f"id_953")} response {ts('id_1727')}
                        if "response" in data and "candidates" not in data:
                            log.debug(f"[GEMINICLI-ANTI-TRUNCATION] {ts('id_953')}response{ts('id_1727')}")
                            unwrapped_data = data["response"]
                            # {ts(f"id_3306")}SSE{ts('id_57')}
                            yield f"data: {json.dumps(unwrapped_data, ensure_ascii=False)}\n\n".encode('utf-8')
                        else:
                            # {ts(f"id_3307")}
                            yield chunk
                    except json.JSONDecodeError:
                        # JSON{ts(f"id_3308")}chunk
                        yield chunk
                else:
                    # {ts(f"id_1529")}SSE{ts('id_3309')}
                    yield chunk
            else:
                # {ts(f"id_3310")}
                yield chunk

    # ========== {ts(f"id_3249")} ==========
    async def normal_stream_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        from src.api.geminicli import stream_request
        from fastapi import Response

        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="geminicli")

        # {ts(f"id_1452")}API{ts('id_3221')} - {ts('id_2210f')}model{ts('id_3220')}request{ts('id_692')}
        api_request = {
            "model": normalized_req.pop("model"),
            "request": normalized_req
        }

        # {ts(f"id_3311")} native {ts('id_1661')}SSE{ts('id_3312')} response {ts('id_1727')}
        log.debug(f"[GEMINICLI] {ts('id_3314')}native{ts('id_3313f')}response{ts('id_1727')}")
        stream_gen = stream_request(body=api_request, native=False)

        # {ts(f"id_953")} response {ts('id_1727')}
        async for chunk in stream_gen:
            # {ts(f"id_2321")}Response{ts('id_3252')}
            if isinstance(chunk, Response):
                # {ts(f"id_101")}Response{ts('id_188')}SSE{ts('id_3315')}
                error_content = chunk.body if isinstance(chunk.body, bytes) else chunk.body.encode('utf-8')
                error_json = json.loads(error_content.decode('utf-8'))
                # {ts(f"id_3297")}SSE{ts('id_3296')}
                yield f"data: {json.dumps(error_json)}\n\n".encode('utf-8')
                return

            # {ts(f"id_590")}SSE{ts('id_2128')}chunk
            if isinstance(chunk, (str, bytes)):
                chunk_str = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk

                # {ts(f"id_3304")} response {ts('id_1727')}
                if chunk_str.startswith("data: "):
                    json_str = chunk_str[6:].strip()

                    # {ts(f"id_3305")} [DONE] {ts('id_2287')}
                    if json_str == "[DONE]":
                        yield chunk
                        continue

                    try:
                        # {ts(f"id_2224")}JSON
                        data = json.loads(json_str)

                        # {ts(f"id_953")} response {ts('id_1727')}
                        if "response" in data and "candidates" not in data:
                            log.debug(f"[GEMINICLI] {ts('id_953')}response{ts('id_1727')}")
                            unwrapped_data = data["response"]
                            # {ts(f"id_3306")}SSE{ts('id_57')}
                            yield f"data: {json.dumps(unwrapped_data, ensure_ascii=False)}\n\n".encode('utf-8')
                        else:
                            # {ts(f"id_3307")}
                            yield chunk
                    except json.JSONDecodeError:
                        # JSON{ts(f"id_3308")}chunk
                        yield chunk
                else:
                    # {ts(f"id_1529")}SSE{ts('id_3309')}
                    yield chunk

    # ========== {ts(f"id_3256")} ==========
    if use_fake_streaming:
        return StreamingResponse(fake_stream_generator(), media_type="text/event-stream")
    elif use_anti_truncation:
        log.info(f"{ts('id_122')}")
        return StreamingResponse(anti_truncation_generator(), media_type="text/event-stream")
    else:
        return StreamingResponse(normal_stream_generator(), media_type="text/event-stream")

@router.post("/v1beta/models/{model:path}:countTokens")
@router.post("/v1/models/{model:path}:countTokens")
async def count_tokens(
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    {ts(f"id_3316")}Gemini{ts('id_2128')}token{ts('id_3317')}
    
    {ts(f"id_33184")}{ts('id_3319')}=1token
    """

    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # {ts(f"id_3322")}token{ts('id_3321')} - {ts('id_3320')}
    total_tokens = 0

    # {ts(f"id_2098")}contents{ts('id_2018')}
    if "contents" in request_data:
        for content in request_data["contents"]:
            if "parts" in content:
                for part in content["parts"]:
                    if "text" in part:
                        # {ts(f"id_33234")}{ts('id_3319')}=1token
                        text_length = len(part["text"])
                        total_tokens += max(1, text_length // 4)

    # {ts(f"id_2098")}generateContentRequest{ts('id_2018')}
    elif "generateContentRequest" in request_data:
        gen_request = request_data["generateContentRequest"]
        if "contents" in gen_request:
            for content in gen_request["contents"]:
                if "parts" in content:
                    for part in content["parts"]:
                        if "text" in part:
                            text_length = len(part["text"])
                            total_tokens += max(1, text_length // 4)

    # {ts(f"id_1530")}Gemini{ts('id_3324')}
    return JSONResponse(content={"totalTokens": total_tokens})

# ==================== {ts(f"id_1632")} ====================

if __name__ == "__main__":
    """
    {ts(f"id_1634")}Gemini{ts('id_3267')}
    {ts(f"id_1635")}: python src/router/geminicli/gemini.py
    """

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    print("=" * 80)
    print(f"Gemini Router {ts('id_1444')}")
    print("=" * 80)

    # {ts(f"id_3268")}
    app = FastAPI()
    app.include_router(router)

    # {ts(f"id_3269")}
    client = TestClient(app)

    # {ts(f"id_1636")} (Gemini{ts('id_57')})
    test_request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Hello, tell me a joke in one sentence."}]
            }
        ]
    }

    # {ts(f"id_1444")}API{ts('id_3325')}
    test_api_key = "pwd"

    def test_non_stream_request():
        f"""{ts('id_1646')}"""
        print("\n" + "=" * 80)
        print(f"{ts('id_14462')}{ts('id_1459')} (POST /v1/models/gemini-2.5-flash:generateContent)")
        print("=" * 80)
        print(f"{ts('id_1447')}: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        response = client.post(
            "/v1/models/gemini-2.5-flash:generateContent",
            json=test_request_body,
            params={"key": test_api_key}
        )

        print(f"{ts('id_1460')}:")
        print("-" * 80)
        print(f"{ts('id_1461')}: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type', 'N/A')}")

        try:
            content = response.text
            print(f"\n{ts('id_1463')} ({ts('id_1464')}):\n{content}\n")

            # {ts(f"id_1647")}JSON
            try:
                json_data = response.json()
                print(f"{ts('id_1463')} ({ts('id_1465')}JSON):")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(f"({ts('id_1648')}JSON{ts('id_57')})")
        except Exception as e:
            print(f"{ts('id_1640')}: {e}")

    def test_stream_request():
        f"""{ts('id_1637')}"""
        print("\n" + "=" * 80)
        print(f"{ts('id_14463')}{ts('id_1445')} (POST /v1/models/gemini-2.5-flash:streamGenerateContent)")
        print("=" * 80)
        print(f"{ts('id_1447')}: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        print(f"{ts('id_1448')} ({ts('id_1449')}chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/v1/models/gemini-2.5-flash:streamGenerateContent",
            json=test_request_body,
            params={"key": test_api_key}
        ) as response:
            print(f"{ts('id_1461')}: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    print(f"\nChunk #{chunk_count}:")
                    print(f"  {ts('id_1454')}: {type(chunk).__name__}")
                    print(f"  {ts('id_1455')}: {len(chunk)}")

                    # {ts(f"id_2318")}chunk
                    try:
                        chunk_str = chunk.decode('utf-8')
                        print(f"  {ts('id_1456')}: {repr(chunk_str[:200] if len(chunk_str) > 200 else chunk_str)}")

                        # {ts(f"id_1643")}SSE{ts('id_3271')}
                        if chunk_str.startswith("data: "):
                            # {ts(f"id_3272")}SSE{ts('id_2219')}
                            for line in chunk_str.strip().split('\n'):
                                line = line.strip()
                                if not line:
                                    continue

                                if line == "data: [DONE]":
                                    print(f"  => {ts('id_3273')}")
                                elif line.startswith("data: "):
                                    try:
                                        json_str = line[6:]  # {ts(f"id_1644")} "data: " {ts('id_365')}
                                        json_data = json.loads(json_str)
                                        print(f"  {ts('id_1457')}JSON: {json.dumps(json_data, indent=4, ensure_ascii=False)}")
                                    except Exception as e:
                                        print(f"  SSE{ts('id_2859')}: {e}")
                    except Exception as e:
                        print(f"  {ts('id_3274')}: {e}")

            print(f"\n{ts('id_1458')} {chunk_count} {ts('id_723')}chunk")

    def test_fake_stream_request():
        f"""{ts('id_3275')}"""
        print("\n" + "=" * 80)
        print(f"{ts('id_14464')}{ts('id_3276f')} (POST /v1/models/{ts('id_121')}/gemini-2.5-flash:streamGenerateContent)")
        print("=" * 80)
        print(f"{ts('id_1447')}: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        print(f"{ts('id_3277')} ({ts('id_1449')}chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            f"/v1/models/{ts('id_121')}/gemini-2.5-flash:streamGenerateContent",
            json=test_request_body,
            params={"key": test_api_key}
        ) as response:
            print(f"{ts('id_1461')}: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    chunk_str = chunk.decode('utf-8')

                    print(f"\nChunk #{chunk_count}:")
                    print(f"  {ts('id_1455')}: {len(chunk_str)} {ts('id_3278')}")

                    # {ts(f"id_2224")}chunk{ts('id_3279')}SSE{ts('id_2219')}
                    events = []
                    for line in chunk_str.split('\n'):
                        line = line.strip()
                        if line.startswith("data: "):
                            events.append(line)

                    print(f"  {ts('id_906')} {len(events)} {ts('id_723f')}SSE{ts('id_2219')}")

                    # {ts(f"id_3280")}
                    for event_idx, event_line in enumerate(events, 1):
                        if event_line == "data: [DONE]":
                            print(f"  {ts('id_2219')} #{event_idx}: [DONE]")
                        else:
                            try:
                                json_str = event_line[6:]  # {ts(f"id_1644")} "data: " {ts('id_365')}
                                json_data = json.loads(json_str)
                                # {ts(f"id_2210")}text{ts('id_1639')}
                                text = json_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                finish_reason = json_data.get("candidates", [{}])[0].get("finishReason")
                                print(f"  {ts('id_2219')} #{event_idx}: text={repr(text[:50])}{'...' if len(text) > 50 else ''}, finishReason={finish_reason}")
                            except Exception as e:
                                print(f"  {ts('id_2219')} #{event_idx}: {ts('id_2859')} - {e}")

            print(f"\n{ts('id_1458')} {chunk_count} {ts('id_723')}HTTP chunk")

    def test_anti_truncation_stream_request():
        f"""{ts('id_3326')}"""
        print("\n" + "=" * 80)
        print(f"{ts('id_14465')}{ts('id_3327f')} (POST /v1/models/{ts('id_80')}/gemini-2.5-flash:streamGenerateContent)")
        print("=" * 80)
        print(f"{ts('id_1447')}: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        print(f"{ts('id_3328')} ({ts('id_1449')}chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            f"/v1/models/{ts('id_80')}/gemini-2.5-flash:streamGenerateContent",
            json=test_request_body,
            params={"key": test_api_key}
        ) as response:
            print(f"{ts('id_1461')}: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    print(f"\nChunk #{chunk_count}:")
                    print(f"  {ts('id_1454')}: {type(chunk).__name__}")
                    print(f"  {ts('id_1455')}: {len(chunk)}")

                    # {ts(f"id_2318")}chunk
                    try:
                        chunk_str = chunk.decode('utf-8')
                        print(f"  {ts('id_1456')}: {repr(chunk_str[:200] if len(chunk_str) > 200 else chunk_str)}")

                        # {ts(f"id_1643")}SSE{ts('id_3271')}
                        if chunk_str.startswith("data: "):
                            # {ts(f"id_3272")}SSE{ts('id_2219')}
                            for line in chunk_str.strip().split('\n'):
                                line = line.strip()
                                if not line:
                                    continue

                                if line == "data: [DONE]":
                                    print(f"  => {ts('id_3273')}")
                                elif line.startswith("data: "):
                                    try:
                                        json_str = line[6:]  # {ts(f"id_1644")} "data: " {ts('id_365')}
                                        json_data = json.loads(json_str)
                                        print(f"  {ts('id_1457')}JSON: {json.dumps(json_data, indent=4, ensure_ascii=False)}")
                                    except Exception as e:
                                        print(f"  SSE{ts('id_2859')}: {e}")
                    except Exception as e:
                        print(f"  {ts('id_3274')}: {e}")

            print(f"\n{ts('id_1458')} {chunk_count} {ts('id_723')}chunk")

    # {ts(f"id_1651")}
    try:
        # {ts(f"id_1646")}
        test_non_stream_request()

        # {ts(f"id_1637")}
        test_stream_request()

        # {ts(f"id_3275")}
        test_fake_stream_request()

        # {ts(f"id_3326")}
        test_anti_truncation_stream_request()

        print("\n" + "=" * 80)
        print(f"{ts('id_1466')}")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ {ts('id_1650')}: {e}")
        import traceback
        traceback.print_exc()

