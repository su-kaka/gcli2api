from src.i18n import ts
"""
Anthropic Router - Handles Anthropic/Claude format API requests via Antigravity
{ts(f"id_935")}Antigravity{ts("id_590")}Anthropic/Claude{ts("id_3197")}
"""

import sys
from pathlib import Path

# {ts("id_1599")}Python{ts("id_796")}
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# {ts("id_3198")}
import asyncio
import json

# {ts("id_3199")}
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# {ts("id_3201")} - {ts("id_3200")}
from config import get_anti_truncation_max_attempts, get_api_password
from log import log

# {ts("id_3201")} - {ts("id_3202")}
from src.utils import (
    get_base_model_from_feature_model,
    is_anti_truncation_model,
    is_fake_streaming_model,
    authenticate_bearer,
)

# {ts("id_3201")} - {ts("id_3203")}
from src.converter.fake_stream import (
    parse_response_for_fake_stream,
    build_anthropic_fake_stream_chunks,
    create_anthropic_heartbeat_chunk,
)

# {ts("id_3201")} - {ts("id_3204")}
from src.router.hi_check import is_health_check_request, create_health_check_response

# {ts("id_3201")} - {ts("id_3205")}
from src.models import ClaudeRequest, model_to_dict

# {ts("id_3201")} - {ts("id_494")}
from src.task_manager import create_managed_task

# {ts("id_3201")} - Token{ts("id_3206")}
from src.token_estimator import estimate_input_tokens


# ==================== {ts("id_3207")} ====================

router = APIRouter()


# ==================== API {ts("id_3208")} ====================

@router.post("/antigravity/v1/messages")
async def messages(
    claude_request: ClaudeRequest,
    _token: str = Depends(authenticate_bearer)
):
    """
    {ts("id_590")}Anthropic/Claude{ts("id_3209")}

    Args:
        claude_request: Anthropic/Claude{ts("id_3210")}
        token: Bearer{ts("id_3211")}
    """
    log.debug(f"[ANTIGRAVITY-ANTHROPIC] Request for model: {claude_request.model}")

    # {ts("id_3212")}
    normalized_dict = model_to_dict(claude_request)

    # {ts("id_3213")}
    if is_health_check_request(normalized_dict, format="anthropic"):
        response = create_health_check_response(format="anthropic")
        return JSONResponse(content=response)

    # {ts("id_3214")}
    use_fake_streaming = is_fake_streaming_model(claude_request.model)
    use_anti_truncation = is_anti_truncation_model(claude_request.model)
    real_model = get_base_model_from_feature_model(claude_request.model)

    # {ts("id_3215")}
    is_streaming = claude_request.stream

    # {ts("id_3216")}
    if use_anti_truncation and not is_streaming:
        log.warning(f"{ts("id_3217")}")

    # {ts("id_3218")}
    normalized_dict["model"] = real_model

    # {ts(f"id_188")} Gemini {ts("id_57")} ({ts("id_463")} converter)
    from src.converter.anthropic2gemini import anthropic_to_gemini_request
    gemini_dict = await anthropic_to_gemini_request(normalized_dict)

    # anthropic_to_gemini_request {ts("id_2784")} model {ts("id_3219")}
    gemini_dict["model"] = real_model

    # {ts(f"id_2511")} Gemini {ts("id_2282")} ({ts("id_463")} antigravity {ts("id_407")})
    from src.converter.gemini_fix import normalize_gemini_request
    gemini_dict = await normalize_gemini_request(gemini_dict, mode="antigravity")

    # {ts(f"id_1452")}API{ts("id_3221")} - {ts("id_2210f")}model{ts("id_3220")}request{ts("id_692")}
    api_request = {
        "model": gemini_dict.pop("model"),
        "request": gemini_dict
    }

    # ========== {ts("id_3222")} ==========
    if not is_streaming:
        # {ts("id_1095")} API {ts("id_3223")}
        from src.api.antigravity import non_stream_request
        response = await non_stream_request(body=api_request)

        # {ts("id_3224")}
        status_code = getattr(response, "status_code", 200)

        # {ts("id_3225")}
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

        # {ts("id_188")} Anthropic {ts("id_57")}
        from src.converter.anthropic2gemini import gemini_to_anthropic_response
        anthropic_response = gemini_to_anthropic_response(
            gemini_response,
            real_model,
            status_code
        )

        return JSONResponse(content=anthropic_response, status_code=status_code)

    # ========== {ts("id_3226")} ==========

    # ========== {ts("id_3227")} ==========
    async def fake_stream_generator():
        # {ts("id_3228")}
        heartbeat = create_anthropic_heartbeat_chunk()
        yield f"data: {json.dumps(heartbeat)}\n\n".encode()

        # {ts("id_3229")}
        async def get_response():
            from src.api.antigravity import non_stream_request
            response = await non_stream_request(body=api_request)
            return response

        # {ts("id_3230")}
        response_task = create_managed_task(get_response(), name="anthropic_fake_stream_request")

        try:
            # {ts("id_18263")}{ts("id_3231")}
            while not response_task.done():
                await asyncio.sleep(3.0)
                if not response_task.done():
                    yield f"data: {json.dumps(heartbeat)}\n\n".encode()

            # {ts("id_3232")}
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

        # {ts("id_3224")}
        if hasattr(response, "status_code") and response.status_code != 200:
            # {ts(f"id_1638")} - {ts("id_3233")}SSE{ts("id_3234")}
            log.error(f"Fake streaming got error response: status={response.status_code}")

            if hasattr(response, "body"):
                error_body = response.body.decode() if isinstance(response.body, bytes) else response.body
            elif hasattr(response, "content"):
                error_body = response.content.decode() if isinstance(response.content, bytes) else response.content
            else:
                error_body = str(response)

            try:
                error_data = json.loads(error_body)
                # {ts("id_3235")} Anthropic {ts("id_57")}
                from src.converter.anthropic2gemini import gemini_to_anthropic_response
                anthropic_error = gemini_to_anthropic_response(
                    error_data,
                    real_model,
                    response.status_code
                )
                yield f"data: {json.dumps(anthropic_error)}\n\n".encode()
            except Exception:
                # {ts("id_3237")}JSON{ts("id_3236")}
                yield f"data: {json.dumps({'error': error_body})}\n\n".encode()

            yield "data: [DONE]\n\n".encode()
            return

        # {ts("id_3238")} - {ts("id_2369")}
        if hasattr(response, "body"):
            response_body = response.body.decode() if isinstance(response.body, bytes) else response.body
        elif hasattr(response, "content"):
            response_body = response.content.decode() if isinstance(response.content, bytes) else response.content
        else:
            response_body = str(response)

        try:
            gemini_response = json.loads(response_body)
            log.debug(f"Anthropic fake stream Gemini response: {gemini_response}")

            # {ts(f"id_3239")}status_code{ts("id_150200")}{ts("id_3240")}error{ts("id_1608")}
            if "error" in gemini_response:
                log.error(f"Fake streaming got error in response body: {gemini_response['error']}")
                # {ts("id_3235")} Anthropic {ts("id_57")}
                from src.converter.anthropic2gemini import gemini_to_anthropic_response
                anthropic_error = gemini_to_anthropic_response(
                    gemini_response,
                    real_model,
                    200
                )
                yield f"data: {json.dumps(anthropic_error)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return

            # {ts("id_3241")}
            content, reasoning_content, finish_reason, images = parse_response_for_fake_stream(gemini_response)

            log.debug(f"Anthropic extracted content: {content}")
            log.debug(f"Anthropic extracted reasoning: {reasoning_content[:100] if reasoning_content else 'None'}...")
            log.debug(f"Anthropic extracted images count: {len(images)}")

            # {ts("id_3242")}
            chunks = build_anthropic_fake_stream_chunks(content, reasoning_content, finish_reason, real_model, images)
            for idx, chunk in enumerate(chunks):
                chunk_json = json.dumps(chunk)
                log.debug(f"[FAKE_STREAM] Yielding chunk #{idx+1}: {chunk_json[:200]}")
                yield f"data: {chunk_json}\n\n".encode()

        except Exception as e:
            log.error(f"Response parsing failed: {e}, directly yield error")
            # {ts("id_3243")}
            error_chunk = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": str(e)
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n".encode()

        yield "data: [DONE]\n\n".encode()

    # ========== {ts("id_3244")} ==========
    async def anti_truncation_generator():
        from src.converter.anti_truncation import AntiTruncationStreamProcessor
        from src.api.antigravity import stream_request
        from src.converter.anti_truncation import apply_anti_truncation
        from src.converter.anthropic2gemini import gemini_stream_to_anthropic_stream

        max_attempts = await get_anti_truncation_max_attempts()

        # {ts("id_2406")}payload{ts("id_2405")}
        anti_truncation_payload = apply_anti_truncation(api_request)

        # {ts("id_3245")} StreamingResponse{ts("id_292")}
        async def stream_request_wrapper(payload):
            # stream_request {ts("id_3246")} StreamingResponse
            stream_gen = stream_request(body=payload, native=False)
            return StreamingResponse(stream_gen, media_type="text/event-stream")

        # {ts("id_2407")}
        processor = AntiTruncationStreamProcessor(
            stream_request_wrapper,
            anti_truncation_payload,
            max_attempts
        )

        # {ts("id_3247")}bytes{ts("id_1486")}
        async def bytes_wrapper():
            async for chunk in processor.process_stream():
                if isinstance(chunk, str):
                    yield chunk.encode('utf-8')
                else:
                    yield chunk

        # {ts("id_3248")}
        async for anthropic_chunk in gemini_stream_to_anthropic_stream(
            bytes_wrapper(),
            real_model,
            200
        ):
            if anthropic_chunk:
                yield anthropic_chunk

    # ========== {ts("id_3249")} ==========
    async def normal_stream_generator():
        from src.api.antigravity import stream_request
        from fastapi import Response
        from src.converter.anthropic2gemini import gemini_stream_to_anthropic_stream

        # {ts(f"id_1095")} API {ts("id_3250")} native {ts("id_543")}
        stream_gen = stream_request(body=api_request, native=False)

        # {ts("id_3251")}
        async def gemini_chunk_wrapper():
            async for chunk in stream_gen:
                # {ts("id_2321")}Response{ts("id_3252")}
                if isinstance(chunk, Response):
                    # {ts("id_3253")}
                    error_content = chunk.body if isinstance(chunk.body, bytes) else chunk.body.encode('utf-8')
                    try:
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
                    return
                else:
                    # {ts("id_3254")}bytes{ts("id_1454")}
                    if isinstance(chunk, str):
                        yield chunk.encode('utf-8')
                    else:
                        yield chunk

        # {ts("id_3255")}
        async for anthropic_chunk in gemini_stream_to_anthropic_stream(
            gemini_chunk_wrapper(),
            real_model,
            200
        ):
            if anthropic_chunk:
                yield anthropic_chunk

    # ========== {ts("id_3256")} ==========
    if use_fake_streaming:
        return StreamingResponse(fake_stream_generator(), media_type="text/event-stream")
    elif use_anti_truncation:
        log.info(f"{ts("id_122")}")
        return StreamingResponse(anti_truncation_generator(), media_type="text/event-stream")
    else:
        return StreamingResponse(normal_stream_generator(), media_type="text/event-stream")


@router.post("/antigravity/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    _token: str = Depends(authenticate_bearer)
):
    """
    {ts(f"id_590")}Anthropic{ts("id_2128")}token{ts("id_3257")}
    
    Args:
        request: FastAPI{ts("id_3258")}
        _token: Bearer{ts("id_3259")}Depends{ts("id_3260")}
    
    Returns:
        JSONResponse: {ts("id_906")}input_tokens{ts("id_3261")}
    """
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={f"type": "error", "error": {"type": "invalid_request_error", "message": f"JSON {ts("id_2859")}: {str(e)}"}}
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={f"type": "error", "error": {"type": "invalid_request_error", "message": "{ts("id_3262")} JSON object"}}
        )

    if not payload.get("model") or not isinstance(payload.get("messages"), list):
        return JSONResponse(
            status_code=400,
            content={f"type": "error", "error": {"type": "invalid_request_error", "message": "{ts("id_3263")}model / messages"}}
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
        ff"[ANTIGRAVITY-ANTHROPIC] /messages/count_tokens {ts("id_3264")}: client={client_host}:{client_port}, "
        f"model={payload.get('model')}, messages={len(payload.get('messages') or [])}, "
        f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
    )

    # {ts("id_3265")}
    input_tokens = 0
    try:
        input_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.error(ff"[ANTIGRAVITY-ANTHROPIC] token {ts("id_3266")}: {e}")

    return JSONResponse(content={"input_tokens": input_tokens})


# ==================== {ts("id_1632")} ====================

if __name__ == "__main__":
    """
    {ts("id_1634")}Anthropic{ts("id_3267")}
    {ts("id_1635")}: python src/router/antigravity/anthropic.py
    """

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    print("=" * 80)
    print(f"Anthropic Router {ts("id_1444")}")
    print("=" * 80)

    # {ts("id_3268")}
    app = FastAPI()
    app.include_router(router)

    # {ts("id_3269")}
    client = TestClient(app)

    # {ts("id_1636")} (Anthropic{ts("id_57")})
    test_request_body = {
        "model": "gemini-2.5-flash",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello, tell me a joke in one sentence."}
        ]
    }

    # {ts("id_1444")}Bearer{ts("id_3270")}
    test_token = "Bearer pwd"

    def test_non_stream_request():
        f"""{ts("id_1646")}"""
        print("\n" + "=" * 80)
        print(f"{ts("id_14461")}{ts("id_1459")} (POST /antigravity/v1/messages)")
        print("=" * 80)
        print(ff"{ts("id_1447")}: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        response = client.post(
            "/antigravity/v1/messages",
            json=test_request_body,
            headers={"Authorization": test_token}
        )

        print(f"{ts("id_1460")}:")
        print("-" * 80)
        print(ff"{ts("id_1461")}: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type', 'N/A')}")

        try:
            content = response.text
            print(ff"\n{ts("id_1463")} ({ts("id_1464")}):\n{content}\n")

            # {ts("id_1647")}JSON
            try:
                json_data = response.json()
                print(ff"{ts("id_1463")} ({ts("id_1465")}JSON):")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print(f"({ts("id_1648")}JSON{ts("id_57")})")
        except Exception as e:
            print(ff"{ts("id_1640")}: {e}")

    def test_stream_request():
        f"""{ts("id_1637")}"""
        print("\n" + "=" * 80)
        print(f"{ts("id_14462")}{ts("id_1445")} (POST /antigravity/v1/messages)")
        print("=" * 80)

        stream_request_body = test_request_body.copy()
        stream_request_body["stream"] = True

        print(ff"{ts("id_1447")}: {json.dumps(stream_request_body, indent=2, ensure_ascii=False)}\n")

        print(f"{ts("id_1448")} ({ts("id_1449")}chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/antigravity/v1/messages",
            json=stream_request_body,
            headers={"Authorization": test_token}
        ) as response:
            print(ff"{ts("id_1461")}: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    print(f"\nChunk #{chunk_count}:")
                    print(ff"  {ts("id_1454")}: {type(chunk).__name__}")
                    print(ff"  {ts("id_1455")}: {len(chunk)}")

                    # {ts("id_2318")}chunk
                    try:
                        chunk_str = chunk.decode('utf-8')
                        print(ff"  {ts("id_1456")}: {repr(chunk_str[:200] if len(chunk_str) > 200 else chunk_str)}")

                        # {ts("id_1643")}SSE{ts("id_3271")}
                        if chunk_str.startswith("event: ") or chunk_str.startswith("data: "):
                            # {ts("id_3272")}SSE{ts("id_2219")}
                            for line in chunk_str.strip().split('\n'):
                                line = line.strip()
                                if not line:
                                    continue

                                if line == "data: [DONE]":
                                    print(ff"  => {ts("id_3273")}")
                                elif line.startswith("data: "):
                                    try:
                                        json_str = line[6:]  # {ts("id_1644")} "data: " {ts("id_365")}
                                        json_data = json.loads(json_str)
                                        print(ff"  {ts("id_1457")}JSON: {json.dumps(json_data, indent=4, ensure_ascii=False)}")
                                    except Exception as e:
                                        print(ff"  SSE{ts("id_2859")}: {e}")
                    except Exception as e:
                        print(ff"  {ts("id_3274")}: {e}")

            print(ff"\n{ts("id_1458")} {chunk_count} {ts("id_723")}chunk")

    def test_fake_stream_request():
        f"""{ts("id_3275")}"""
        print("\n" + "=" * 80)
        print(f"{ts("id_14463")}{ts("id_3276f")} (POST /antigravity/v1/messages with {ts("id_121")} prefix)")
        print("=" * 80)

        fake_stream_request_body = test_request_body.copy()
        fake_stream_request_body[f"model"] = "{ts("id_121")}/gemini-2.5-flash"
        fake_stream_request_body["stream"] = True

        print(ff"{ts("id_1447")}: {json.dumps(fake_stream_request_body, indent=2, ensure_ascii=False)}\n")

        print(f"{ts("id_3277")} ({ts("id_1449")}chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/antigravity/v1/messages",
            json=fake_stream_request_body,
            headers={"Authorization": test_token}
        ) as response:
            print(ff"{ts("id_1461")}: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    chunk_str = chunk.decode('utf-8')

                    print(f"\nChunk #{chunk_count}:")
                    print(ff"  {ts("id_1455")}: {len(chunk_str)} {ts("id_3278")}")

                    # {ts(f"id_2224")}chunk{ts("id_3279")}SSE{ts("id_2219")}
                    events = []
                    for line in chunk_str.split('\n'):
                        line = line.strip()
                        if line.startswith("data: ") or line.startswith("event: "):
                            events.append(line)

                    print(ff"  {ts("id_906")} {len(events)} {ts("id_723f")}SSE{ts("id_2219")}")

                    # {ts("id_3280")}
                    for event_idx, event_line in enumerate(events, 1):
                        if event_line == "data: [DONE]":
                            print(ff"  {ts("id_2219")} #{event_idx}: [DONE]")
                        elif event_line.startswith("data: "):
                            try:
                                json_str = event_line[6:]  # {ts("id_1644")} "data: " {ts("id_365")}
                                json_data = json.loads(json_str)
                                event_type = json_data.get("type", "unknown")
                                print(ff"  {ts("id_2219")} #{event_idx}: type={event_type}")
                            except Exception as e:
                                print(ff"  {ts("id_2219")} #{event_idx}: {ts("id_2859")} - {e}")

            print(ff"\n{ts("id_1458")} {chunk_count} {ts("id_723")}HTTP chunk")

    # {ts("id_1651")}
    try:
        # {ts("id_1646")}
        test_non_stream_request()

        # {ts("id_1637")}
        test_stream_request()

        # {ts("id_3275")}
        test_fake_stream_request()

        print("\n" + "=" * 80)
        print(f"{ts("id_1466")}")
        print("=" * 80)

    except Exception as e:
        print(ff"\n❌ {ts("id_1650")}: {e}")
        import traceback
        traceback.print_exc()
