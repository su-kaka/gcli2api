import asyncio

from src.converter.anthropic2gemini import anthropic_to_gemini_request
from src.converter.gemini_fix import normalize_gemini_request


def _convert_then_normalize(payload: dict) -> dict:
    gemini_request = asyncio.run(anthropic_to_gemini_request(payload))
    return asyncio.run(normalize_gemini_request(gemini_request, mode="geminicli"))


def _assert_model_user_function_parity(contents: list[dict]) -> None:
    """Every model turn with functionCall must be followed by user with same number of functionResponse."""
    assert isinstance(contents, list)
    assert contents, "contents must not be empty"

    for i, turn in enumerate(contents):
        if not isinstance(turn, dict) or turn.get("role") != "model":
            continue

        parts = turn.get("parts") or []
        call_parts = [
            p
            for p in parts
            if isinstance(p, dict) and isinstance(p.get("functionCall"), dict)
        ]
        if not call_parts:
            continue

        assert i + 1 < len(contents), (
            f"model tool-call turn at index {i} has no next turn"
        )
        next_turn = contents[i + 1]
        assert next_turn.get("role") == "user", (
            f"model tool-call turn at index {i} must be followed by user turn"
        )

        response_parts = [
            p
            for p in (next_turn.get("parts") or [])
            if isinstance(p, dict) and isinstance(p.get("functionResponse"), dict)
        ]
        assert len(response_parts) == len(call_parts), (
            f"call/response count mismatch at index {i}: "
            f"{len(call_parts)} calls vs {len(response_parts)} responses"
        )

        for call_part, response_part in zip(call_parts, response_parts):
            call = call_part["functionCall"]
            response = response_part["functionResponse"]

            assert response.get("id") == call.get("id")
            assert response.get("name") == call.get("name")
            assert "response" in response


def test_e2e_parallel_tool_use_tool_result_keeps_parity():
    payload = {
        "model": "gemini-2.5-flash",
        "max_tokens": 128,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_parallel_1",
                        "name": "get_weather",
                        "input": {"city": "shenzhen"},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_parallel_2",
                        "name": "get_news",
                        "input": {"topic": "ai"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_parallel_1",
                        "name": "get_weather",
                        "content": [{"type": "text", "text": "sunny"}],
                    },
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_parallel_2",
                        "name": "get_news",
                        "content": [{"type": "text", "text": "headline"}],
                    },
                ],
            },
        ],
    }

    normalized = _convert_then_normalize(payload)
    contents = normalized["contents"]

    _assert_model_user_function_parity(contents)
    assert len(contents) == 2


def test_e2e_missing_response_synthesizes_no_response_and_restores_parity():
    payload = {
        "model": "gemini-2.5-flash",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_missing_1",
                        "name": "lookup_a",
                        "input": {},
                    },
                    {
                        "type": "tool_use",
                        "id": "toolu_missing_2",
                        "name": "lookup_b",
                        "input": {},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_missing_1",
                        "name": "lookup_a",
                        "content": [{"type": "text", "text": "ok"}],
                    }
                ],
            },
        ],
    }

    normalized = _convert_then_normalize(payload)
    contents = normalized["contents"]

    _assert_model_user_function_parity(contents)

    response_parts = [
        p
        for p in contents[1]["parts"]
        if isinstance(p, dict) and "functionResponse" in p
    ]
    assert len(response_parts) == 2
    assert response_parts[1]["functionResponse"] == {
        "id": "toolu_missing_2",
        "name": "lookup_b",
        "response": {"result": "no response"},
    }


def test_e2e_empty_response_object_is_preserved_and_parity_holds():
    payload = {
        "model": "gemini-2.5-flash",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_empty_1",
                        "name": "store_data",
                        "input": {},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_empty_1",
                        "name": "store_data",
                        "content": [{"type": "text", "text": ""}],
                    }
                ],
            },
        ],
    }

    gemini_request = asyncio.run(anthropic_to_gemini_request(payload))
    # Simulate empty-response edge case entering normalize stage.
    gemini_request["contents"][1]["parts"][0]["functionResponse"]["response"] = {}

    normalized = asyncio.run(normalize_gemini_request(gemini_request, mode="geminicli"))
    contents = normalized["contents"]

    _assert_model_user_function_parity(contents)

    response = contents[1]["parts"][0]["functionResponse"]["response"]
    assert response == {}
