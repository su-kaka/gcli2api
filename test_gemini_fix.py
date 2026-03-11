import asyncio
from unittest.mock import patch

from src.converter.gemini_fix import normalize_gemini_request


def test_normalize_gemini_request_adds_thought_signature_for_function_call():
    request = {
        "model": "gemini-3-flash-preview-high-search",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "id": "call_read_1",
                            "name": "read",
                            "args": {"file": "README.md"},
                        }
                    }
                ],
            }
        ],
    }

    normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))
    parts = normalized["contents"][0]["parts"]

    assert parts[0]["functionCall"]["name"] == "read"
    assert parts[0]["thoughtSignature"] == "skip_thought_signature_validator"


def test_normalize_gemini_request_preserves_existing_signature_formats():
    request = {
        "model": "gemini-3-flash-preview-high-search",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {
                        "functionCall": {
                            "id": "call_read_2",
                            "name": "read",
                            "args": {"file": "README.md"},
                        },
                        "thought_signature": "sig_from_client",
                    }
                ],
            }
        ],
    }

    normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))
    part = normalized["contents"][0]["parts"][0]

    assert part["thoughtSignature"] == "sig_from_client"
    assert "thought_signature" not in part


def test_normalize_gemini_request_unifies_system_instruction_aliases():
    request = {
        "model": "gemini-2.5-flash",
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        "system_instruction": {"parts": [{"text": "base"}]},
        "system_instructions": {"parts": [{"text": "fallback"}]},
    }

    normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))

    assert "system_instruction" not in normalized
    assert "system_instructions" not in normalized
    assert normalized["systemInstruction"]["parts"][0]["text"] == "base"


def test_normalize_gemini_request_exemption_for_empty_function_parts():
    request = {
        "model": "gemini-2.5-flash",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {"functionResponse": {"name": "foo", "response": {}}},
                    {"functionCall": {"name": "bar", "args": {}}},
                    {"text": ""},  # Should be filtered
                    {"text": "valid"},
                ],
            }
        ],
    }

    normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))
    parts = normalized["contents"][0]["parts"]

    # Only 3 parts should remain: functionResponse, functionCall, and valid text
    assert len(parts) == 3

    assert "functionResponse" in parts[0]
    assert parts[0]["functionResponse"]["response"] == {}

    assert "functionCall" in parts[1]
    assert parts[1]["functionCall"]["args"] == {}
    # Check if thoughtSignature was added (from existing logic)
    assert parts[1]["thoughtSignature"] == "skip_thought_signature_validator"

    assert "text" in parts[2]
    assert parts[2]["text"] == "valid"


def test_validate_function_call_pairs_repairs_missing_response():
    request = {
        "model": "gemini-2.5-flash",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"id": "call-1", "name": "tool_a", "args": {}}},
                    {"functionCall": {"id": "call-2", "name": "tool_b", "args": {}}},
                    {"functionCall": {"id": "call-3", "name": "tool_c", "args": {}}},
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "id": "call-1",
                            "name": "tool_a",
                            "response": {"ok": True},
                        }
                    },
                    {
                        "functionResponse": {
                            "id": "call-2",
                            "name": "tool_b",
                            "response": {"ok": True},
                        }
                    },
                ],
            },
        ],
    }

    with patch("src.converter.gemini_fix.log.warning") as warning_mock:
        normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))

    response_parts = [
        part
        for part in normalized["contents"][1]["parts"]
        if "functionResponse" in part
    ]
    assert len(response_parts) == 3
    assert response_parts[2]["functionResponse"]["id"] == "call-3"
    assert response_parts[2]["functionResponse"]["name"] == "tool_c"
    assert response_parts[2]["functionResponse"]["response"] == {
        "result": "no response"
    }

    warning_texts = [str(call.args[0]) for call in warning_mock.call_args_list]
    assert any("已补齐 1 个 response" in text for text in warning_texts)


def test_validate_function_call_pairs_removes_extra_response():
    request = {
        "model": "gemini-2.5-flash",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"id": "call-1", "name": "tool_a", "args": {}}},
                    {"functionCall": {"id": "call-2", "name": "tool_b", "args": {}}},
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "id": "call-1",
                            "name": "tool_a",
                            "response": {"ok": True},
                        }
                    },
                    {
                        "functionResponse": {
                            "id": "call-2",
                            "name": "tool_b",
                            "response": {"ok": True},
                        }
                    },
                    {
                        "functionResponse": {
                            "id": "call-3",
                            "name": "tool_c",
                            "response": {"ok": True},
                        }
                    },
                ],
            },
        ],
    }

    with patch("src.converter.gemini_fix.log.warning") as warning_mock:
        normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))

    response_parts = [
        part
        for part in normalized["contents"][1]["parts"]
        if "functionResponse" in part
    ]
    assert len(response_parts) == 2
    assert [part["functionResponse"]["id"] for part in response_parts] == [
        "call-1",
        "call-2",
    ]

    warning_texts = [str(call.args[0]) for call in warning_mock.call_args_list]
    assert any("已移除 1 个多余 response" in text for text in warning_texts)


def test_validate_function_call_pairs_unchanged_when_counts_match():
    request = {
        "model": "gemini-2.5-flash",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"id": "call-1", "name": "tool_a", "args": {}}},
                    {"functionCall": {"id": "call-2", "name": "tool_b", "args": {}}},
                ],
            },
            {
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "id": "call-1",
                            "name": "tool_a",
                            "response": {"ok": True},
                        }
                    },
                    {
                        "functionResponse": {
                            "id": "call-2",
                            "name": "tool_b",
                            "response": {"ok": True},
                        }
                    },
                ],
            },
        ],
    }

    with patch("src.converter.gemini_fix.log.warning") as warning_mock:
        normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))

    response_parts = [
        part
        for part in normalized["contents"][1]["parts"]
        if "functionResponse" in part
    ]
    assert len(response_parts) == 2

    warning_texts = [str(call.args[0]) for call in warning_mock.call_args_list]
    assert not any("数量不匹配" in text for text in warning_texts)
    assert not any("已插入 user turn" in text for text in warning_texts)


def test_validate_function_call_pairs_inserts_user_turn_when_missing():
    request = {
        "model": "gemini-2.5-flash",
        "contents": [
            {
                "role": "model",
                "parts": [
                    {"functionCall": {"id": "call-1", "name": "tool_a", "args": {}}}
                ],
            },
            {"role": "model", "parts": [{"text": "next model turn"}]},
        ],
    }

    with patch("src.converter.gemini_fix.log.warning") as warning_mock:
        normalized = asyncio.run(normalize_gemini_request(request, mode="geminicli"))

    assert normalized["contents"][1]["role"] == "user"
    inserted_parts = normalized["contents"][1]["parts"]
    assert len(inserted_parts) == 1
    assert inserted_parts[0]["functionResponse"]["id"] == "call-1"
    assert inserted_parts[0]["functionResponse"]["name"] == "tool_a"
    assert inserted_parts[0]["functionResponse"]["response"] == {
        "result": "no response"
    }

    warning_texts = [str(call.args[0]) for call in warning_mock.call_args_list]
    assert any(
        "已插入 user turn 并补齐 1 个 response" in text for text in warning_texts
    )
