import asyncio

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
