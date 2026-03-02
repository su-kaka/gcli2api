import asyncio
from typing import Any

import httpx
from fastapi import Response

from src.api import geminicli
from src.models import GeminiRequest
from src.router.geminicli import gemini as gemini_router


class _DummyCredentialManager:
    async def get_valid_credential(self, mode=None, model_name=None):
        return "dummy.json", {"token": "token", "project_id": "project"}

    async def update_credential_state(self, *args, **kwargs):
        return None

    async def record_api_call_result(self, *args, **kwargs):
        return None

    async def set_cred_disabled(self, *args, **kwargs):
        return None


async def _collect(gen):
    items = []
    async for item in gen:
        items.append(item)
    return items


def test_stream_request_handles_disable_code_without_unbound_state(monkeypatch):
    called = {"record_error": 0, "handle_retry": 0}

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 0, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return [403]

    async def fake_get_proxy_config():
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        called["record_error"] += 1

    async def fake_handle_error_with_retry(*args, **kwargs):
        called["handle_retry"] += 1
        return False

    async def fake_stream_post_async(*args, **kwargs):
        yield Response(
            content=b'{"error":"forbidden"}',
            status_code=403,
            media_type="application/json",
        )

    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(geminicli, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(
        geminicli, "handle_error_with_retry", fake_handle_error_with_retry
    )
    monkeypatch.setattr(geminicli, "stream_post_async", fake_stream_post_async)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    chunks = asyncio.run(_collect(geminicli.stream_request(body=body, native=False)))

    assert len(chunks) == 1
    assert isinstance(chunks[0], Response)
    assert chunks[0].status_code == 403
    assert called["record_error"] == 1
    assert called["handle_retry"] == 1


def test_stream_request_uses_structured_stream_timeout(monkeypatch):
    captured = {"timeout": None}

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 0, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return [403]

    async def fake_get_proxy_config():
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_stream_post_async(*args, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        yield "data: test"

    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(geminicli, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(
        geminicli, "record_api_call_success", fake_record_api_call_success
    )
    monkeypatch.setattr(geminicli, "stream_post_async", fake_stream_post_async)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    chunks = asyncio.run(_collect(geminicli.stream_request(body=body, native=False)))

    assert chunks == ["data: test"]
    assert isinstance(captured["timeout"], httpx.Timeout)
    assert captured["timeout"].connect == 30.0
    assert captured["timeout"].write == 30.0
    assert captured["timeout"].pool == 30.0
    assert captured["timeout"].read is None


def test_stream_router_injects_thought_signature_for_function_call(monkeypatch):
    captured: dict[str, Any] = {"body": None, "native": None}

    async def fake_stream_request(*, body, native=False):
        captured["body"] = body
        captured["native"] = native
        yield b"data: [DONE]\n\n"

    monkeypatch.setattr(geminicli, "stream_request", fake_stream_request)

    request = GeminiRequest.model_validate(
        {
            "contents": [
                {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "id": "call_read_3",
                                "name": "read",
                                "args": {"file": "README.md"},
                            }
                        }
                    ],
                }
            ]
        }
    )

    response = asyncio.run(
        gemini_router.stream_generate_content(
            gemini_request=request,
            model="gemini-3-flash-preview-high-search",
            api_key="test-key",
        )
    )
    chunks = asyncio.run(_collect(response.body_iterator))

    assert chunks == [b"data: [DONE]\n\n"]
    assert captured["native"] is False
    assert captured["body"] is not None
    part = captured["body"]["request"]["contents"][0]["parts"][0]
    assert part["functionCall"]["name"] == "read"
    assert part["thoughtSignature"] == "skip_thought_signature_validator"
