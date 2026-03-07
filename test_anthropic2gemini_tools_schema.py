import asyncio
from fastapi import Response

from src.converter.anthropic2gemini import (
    anthropic_to_gemini_request,
    _can_use_text_only_fast_path,
    gemini_stream_to_anthropic_stream,
)


def test_anthropic_tools_schema_shorthand_object_is_normalized():
    payload = {
        "model": "gemini-3-flash-preview-high-search",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "save_config",
                "description": "Save key-value config",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": "object",
                    },
                    "required": ["key", "value"],
                },
            }
        ],
    }

    gemini_request = asyncio.run(anthropic_to_gemini_request(payload))
    params = gemini_request["tools"][0]["functionDeclarations"][0]["parameters"]

    assert params["type"] == "object"
    assert params["properties"]["key"]["type"] == "string"
    assert params["properties"]["value"]["type"] == "object"


def test_fast_path_guard_accepts_simple_text_messages():
    payload = {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ],
        "tools": [],
    }

    assert _can_use_text_only_fast_path(payload) is True


def test_fast_path_guard_rejects_tool_image_and_thinking_paths():
    tool_payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"name": "search", "input_schema": {"type": "object"}}],
    }
    image_payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "AAAA",
                        },
                    }
                ],
            }
        ]
    }
    thinking_payload = {
        "messages": [{"role": "user", "content": "hello"}],
        "thinking": {"type": "enabled", "budget_tokens": 256},
    }

    assert _can_use_text_only_fast_path(tool_payload) is False
    assert _can_use_text_only_fast_path(image_payload) is False
    assert _can_use_text_only_fast_path(thinking_payload) is False


def test_fast_path_text_conversion_keeps_expected_request_shape():
    payload = {
        "model": "gemini-2.5-pro",
        "max_tokens": 128,
        "messages": [
            {"role": "system", "content": "be concise"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
        ],
        "tools": [],
    }

    gemini_request = asyncio.run(anthropic_to_gemini_request(payload))

    assert gemini_request["contents"] == [
        {"role": "user", "parts": [{"text": "hello"}]},
        {"role": "model", "parts": [{"text": "hi"}]},
    ]
    assert "tools" not in gemini_request
    assert "toolConfig" not in gemini_request
    assert gemini_request["systemInstruction"]["parts"][0]["text"] == "be concise"


def test_non_fast_path_still_preserves_tool_and_thinking_structure():
    payload = {
        "model": "gemini-2.5-pro",
        "max_tokens": 256,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "plan",
                        "thoughtSignature": "abcdefghijk",
                    }
                ],
            },
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "fetch_weather",
                        "input": {"city": "shenzhen"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "name": "fetch_weather",
                        "content": [{"type": "text", "text": "sunny"}],
                    }
                ],
            },
        ],
        "tools": [
            {
                "name": "fetch_weather",
                "description": "Fetch city weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            }
        ],
        "tool_choice": {"type": "tool", "name": "fetch_weather"},
        "thinking": {"type": "enabled", "budget_tokens": 128},
    }

    gemini_request = asyncio.run(anthropic_to_gemini_request(payload))

    assert "tools" in gemini_request
    assert gemini_request["toolConfig"]["functionCallingConfig"][
        "allowedFunctionNames"
    ] == ["fetch_weather"]
    assert gemini_request["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 128

    all_parts = [
        part
        for content in gemini_request["contents"]
        for part in content.get("parts", [])
        if isinstance(part, dict)
    ]
    assert any(part.get("thought") is True for part in all_parts)
    assert any("functionCall" in part for part in all_parts)
    assert any("functionResponse" in part for part in all_parts)


def test_gemini_stream_interrupted_without_finish_reason_emits_error_event():
    async def fake_stream():
        yield b'data: {"response": {"candidates": [{"content": {"parts": [{"text": "partial answer"}]}}]}}\n\n'

    async def collect_stream_output():
        chunks = []
        async for chunk in gemini_stream_to_anthropic_stream(
            fake_stream(), "gemini-3-flash-preview", 200
        ):
            chunks.append(chunk.decode("utf-8"))
        return "".join(chunks)

    output = asyncio.run(collect_stream_output())

    assert "event: error" in output
    assert "upstream stream ended before finishReason" in output
    assert "event: message_delta" not in output


def test_gemini_stream_response_error_chunk_emits_error_event():
    async def fake_stream():
        yield Response(
            content=b'{"error":{"message":"stream broken"}}',
            status_code=502,
            media_type="application/json",
        )

    async def collect_stream_output():
        chunks = []
        async for chunk in gemini_stream_to_anthropic_stream(
            fake_stream(), "gemini-3-flash-preview", 200
        ):
            chunks.append(chunk.decode("utf-8"))
        return "".join(chunks)

    output = asyncio.run(collect_stream_output())

    assert "event: message_start" in output
    assert "event: error" in output
    assert "stream broken" in output
    assert "event: message_delta" not in output
