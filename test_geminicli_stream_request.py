import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import Response

from src.api import geminicli
from src.api import utils as api_utils
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


async def _retry_policy_v2_enabled():
    return True


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
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
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
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
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


def test_stream_request_does_not_retry_after_first_chunk(monkeypatch):
    called = {"stream_post": 0, "handle_retry": 0}

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_get_proxy_config():
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        called["handle_retry"] += 1
        return True

    async def fake_stream_post_async(*args, **kwargs):
        called["stream_post"] += 1
        yield (
            "data: "
            + json.dumps(
                {
                    "response": {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": "final output"}],
                                }
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            )
        )
        yield Response(
            content=b'{"error":"too many requests"}',
            status_code=429,
            media_type="application/json",
        )

    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
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

    assert called["stream_post"] == 1
    assert called["handle_retry"] == 0
    assert "final output" in chunks[0]
    assert isinstance(chunks[1], Response)
    assert chunks[1].status_code == 429


def test_stream_request_retries_when_exception_after_thinking_only(monkeypatch):
    called = {"stream_post": 0, "record_error": 0, "record_success": 0}

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_get_proxy_config():
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        called["record_success"] += 1

    async def fake_record_api_call_error(*args, **kwargs):
        called["record_error"] += 1

    async def fake_sleep_with_observability(*args, **kwargs):
        return None

    async def fake_stream_post_async(*args, **kwargs):
        called["stream_post"] += 1
        if called["stream_post"] == 1:
            yield (
                "data: "
                + json.dumps(
                    {
                        "response": {
                            "candidates": [
                                {
                                    "content": {
                                        "parts": [
                                            {"text": "thinking-only", "thought": True}
                                        ]
                                    }
                                }
                            ]
                        }
                    },
                    ensure_ascii=False,
                )
            )
            raise httpx.ReadError("stream interrupted")

        yield (
            "data: "
            + json.dumps(
                {
                    "response": {
                        "candidates": [
                            {
                                "content": {
                                    "parts": [{"text": "final answer"}],
                                },
                                "finishReason": "STOP",
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
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
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(geminicli, "stream_post_async", fake_stream_post_async)
    monkeypatch.setattr(
        geminicli, "_sleep_with_observability", fake_sleep_with_observability
    )

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    chunks = asyncio.run(_collect(geminicli.stream_request(body=body, native=False)))

    assert called["stream_post"] == 2
    assert called["record_error"] == 1
    assert called["record_success"] == 1
    assert any("final answer" in str(item) for item in chunks)


def test_non_stream_request_waits_once_per_retry_attempt(monkeypatch):
    wait_calls = {"count": 0}
    responses = [
        httpx.Response(
            status_code=429,
            json={"error": {"code": 429, "message": "too many requests"}},
        ),
        httpx.Response(status_code=200, json={"ok": True}),
    ]

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        wait_calls["count"] += 1
        metrics_ctx = kwargs.get("metrics_ctx")
        if isinstance(metrics_ctx, dict):
            metrics_ctx["retry_count"] = int(metrics_ctx.get("retry_count", 0) or 0) + 1
        return True

    async def fake_sleep_with_observability(*args, **kwargs):
        wait_calls["count"] += 1

    async def fake_post_async(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(
        geminicli, "record_api_call_success", fake_record_api_call_success
    )
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(
        geminicli, "handle_error_with_retry", fake_handle_error_with_retry
    )
    monkeypatch.setattr(
        geminicli, "_sleep_with_observability", fake_sleep_with_observability
    )
    monkeypatch.setattr(geminicli, "post_async", fake_post_async)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    response = asyncio.run(geminicli.non_stream_request(body=body))

    assert response.status_code == 200
    assert wait_calls["count"] == 1


class _SequentialCredentialManager:
    def __init__(self):
        self._credentials = [
            ("cred-a.json", {"token": "token-a", "project_id": "project-a"}),
            ("cred-b.json", {"token": "token-b", "project_id": "project-b"}),
        ]
        self._index = 0

    async def get_valid_credential(self, mode=None, model_name=None):
        idx = min(self._index, len(self._credentials) - 1)
        self._index += 1
        return self._credentials[idx]

    async def update_credential_state(self, *args, **kwargs):
        return None

    async def record_api_call_result(self, *args, **kwargs):
        return None

    async def set_cred_disabled(self, *args, **kwargs):
        return None


def test_stream_request_retry_policy_v2_on_keeps_current_credential(monkeypatch):
    captured = {"auth": [], "flags": []}

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_get_proxy_config():
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        captured["flags"].append(kwargs.get("retry_policy_v2_enabled"))
        return True

    async def fake_stream_post_async(*args, **kwargs):
        captured["auth"].append(kwargs.get("headers", {}).get("Authorization"))
        if len(captured["auth"]) == 1:
            yield Response(
                content=b'{"error":{"message":"too many requests"}}',
                status_code=429,
                media_type="application/json",
            )
        else:
            yield "data: ok"

    monkeypatch.setattr(geminicli, "credential_manager", _SequentialCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
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

    assert chunks == ["data: ok"]
    assert captured["flags"] == [True]
    assert captured["auth"] == ["Bearer token-a", "Bearer token-a"]


def test_stream_request_daily_quota_does_not_keep_current_credential(monkeypatch):
    captured = {"auth": [], "flags": []}

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_get_proxy_config():
        return None

    async def fake_parse_and_log_cooldown(*args, **kwargs):
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        captured["flags"].append(kwargs.get("retry_policy_v2_enabled"))
        return True

    async def fake_stream_post_async(*args, **kwargs):
        captured["auth"].append(kwargs.get("headers", {}).get("Authorization"))
        if len(captured["auth"]) == 1:
            yield Response(
                content=json.dumps(
                    {
                        "error": {
                            "status": "RESOURCE_EXHAUSTED",
                            "details": [
                                {
                                    "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                    "reason": "RESOURCE_EXHAUSTED",
                                    "metadata": {
                                        "quota_unit": "1/d/{project}",
                                    },
                                }
                            ],
                        }
                    },
                    ensure_ascii=False,
                ).encode("utf-8"),
                status_code=429,
                media_type="application/json",
            )
        else:
            yield "data: ok"

    monkeypatch.setattr(geminicli, "credential_manager", _SequentialCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(geminicli, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(
        geminicli, "parse_and_log_cooldown", fake_parse_and_log_cooldown
    )
    monkeypatch.setattr(
        geminicli, "record_api_call_success", fake_record_api_call_success
    )
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

    assert chunks == ["data: ok"]
    assert captured["flags"] == [True]
    assert captured["auth"] == ["Bearer token-a", "Bearer token-b"]


def test_stream_request_daily_quota_missing_cooldown_falls_back_to_utc_midnight(
    monkeypatch,
):
    captured = {"cooldown_until": None, "warnings": []}
    fixed_now = datetime(2026, 3, 11, 15, 30, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 0, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_get_proxy_config():
        return None

    async def fake_parse_and_log_cooldown(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        captured["cooldown_until"] = args[3]

    async def fake_handle_error_with_retry(*args, **kwargs):
        return False

    async def fake_stream_post_async(*args, **kwargs):
        yield Response(
            content=json.dumps(
                {
                    "error": {
                        "status": "RESOURCE_EXHAUSTED",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                "reason": "RESOURCE_EXHAUSTED",
                                "metadata": {
                                    "quota_limit": "GenerateContentRequestsPerDayPerProjectPerModel-FreeTier",
                                },
                            }
                        ],
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            status_code=429,
            media_type="application/json",
        )

    def fake_warning(message):
        captured["warnings"].append(message)

    monkeypatch.setattr(geminicli, "datetime", _FixedDateTime)
    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(geminicli, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(
        geminicli, "parse_and_log_cooldown", fake_parse_and_log_cooldown
    )
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(
        geminicli, "handle_error_with_retry", fake_handle_error_with_retry
    )
    monkeypatch.setattr(geminicli, "stream_post_async", fake_stream_post_async)
    monkeypatch.setattr(geminicli.log, "warning", fake_warning)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    chunks = asyncio.run(_collect(geminicli.stream_request(body=body, native=False)))

    expected_cooldown = datetime(2026, 3, 12, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert len(chunks) == 1
    assert isinstance(chunks[0], Response)
    assert chunks[0].status_code == 429
    assert captured["cooldown_until"] == expected_cooldown
    assert any("回退到下一个UTC午夜" in message for message in captured["warnings"])


def test_stream_request_retry_policy_v2_off_rotates_credential(monkeypatch):
    captured = {"auth": [], "flags": []}

    async def fake_get_ff_retry_policy_v2():
        return False

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_get_proxy_config():
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        captured["flags"].append(kwargs.get("retry_policy_v2_enabled"))
        return True

    async def fake_stream_post_async(*args, **kwargs):
        captured["auth"].append(kwargs.get("headers", {}).get("Authorization"))
        if len(captured["auth"]) == 1:
            yield Response(
                content=b'{"error":{"message":"too many requests"}}',
                status_code=429,
                media_type="application/json",
            )
        else:
            yield "data: ok"

    monkeypatch.setattr(
        geminicli, "get_ff_retry_policy_v2", fake_get_ff_retry_policy_v2
    )
    monkeypatch.setattr(geminicli, "credential_manager", _SequentialCredentialManager())
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

    assert chunks == ["data: ok"]
    assert captured["flags"] == [False]
    assert captured["auth"] == ["Bearer token-a", "Bearer token-b"]


def test_non_stream_request_passes_retry_policy_v2_flag_to_helper(monkeypatch):
    captured = {"flags": []}
    responses = [
        httpx.Response(
            status_code=429,
            json={"error": {"code": 429, "message": "too many requests"}},
        ),
        httpx.Response(status_code=200, json={"ok": True}),
    ]

    async def fake_get_ff_retry_policy_v2():
        return False

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        captured["flags"].append(kwargs.get("retry_policy_v2_enabled"))
        return True

    async def fake_post_async(*args, **kwargs):
        return responses.pop(0)

    monkeypatch.setattr(
        geminicli, "get_ff_retry_policy_v2", fake_get_ff_retry_policy_v2
    )
    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(
        geminicli, "record_api_call_success", fake_record_api_call_success
    )
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(
        geminicli, "handle_error_with_retry", fake_handle_error_with_retry
    )
    monkeypatch.setattr(geminicli, "post_async", fake_post_async)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    response = asyncio.run(geminicli.non_stream_request(body=body))

    assert response.status_code == 200
    assert captured["flags"] == [False]


def test_non_stream_request_daily_quota_does_not_keep_current_credential(monkeypatch):
    captured = {"auth": [], "flags": []}
    responses = [
        httpx.Response(
            status_code=429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "RESOURCE_EXHAUSTED",
                            "metadata": {
                                "quota_unit": "1/d/{project}",
                            },
                        }
                    ],
                }
            },
        ),
        httpx.Response(status_code=200, json={"ok": True}),
    ]

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 1, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_parse_and_log_cooldown(*args, **kwargs):
        return None

    async def fake_record_api_call_success(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        return None

    async def fake_handle_error_with_retry(*args, **kwargs):
        captured["flags"].append(kwargs.get("retry_policy_v2_enabled"))
        return True

    async def fake_post_async(*args, **kwargs):
        captured["auth"].append(kwargs.get("headers", {}).get("Authorization"))
        return responses.pop(0)

    monkeypatch.setattr(geminicli, "credential_manager", _SequentialCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(
        geminicli, "parse_and_log_cooldown", fake_parse_and_log_cooldown
    )
    monkeypatch.setattr(
        geminicli, "record_api_call_success", fake_record_api_call_success
    )
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(
        geminicli, "handle_error_with_retry", fake_handle_error_with_retry
    )
    monkeypatch.setattr(geminicli, "post_async", fake_post_async)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    response = asyncio.run(geminicli.non_stream_request(body=body))

    assert response.status_code == 200
    assert captured["flags"] == [True]
    assert captured["auth"] == ["Bearer token-a", "Bearer token-b"]


def test_non_stream_request_daily_quota_missing_cooldown_falls_back_to_utc_midnight(
    monkeypatch,
):
    captured = {"cooldown_until": None, "warnings": []}
    fixed_now = datetime(2026, 3, 11, 15, 30, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return fixed_now.replace(tzinfo=None)
            return fixed_now.astimezone(tz)

    async def fake_get_code_assist_endpoint():
        return "https://example.com"

    async def fake_get_retry_config():
        return {"retry_enabled": True, "max_retries": 0, "retry_interval": 0.0}

    async def fake_get_auto_ban_error_codes():
        return []

    async def fake_parse_and_log_cooldown(*args, **kwargs):
        return None

    async def fake_record_api_call_error(*args, **kwargs):
        captured["cooldown_until"] = args[3]

    async def fake_handle_error_with_retry(*args, **kwargs):
        return False

    async def fake_post_async(*args, **kwargs):
        return httpx.Response(
            status_code=429,
            json={
                "error": {
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                            "reason": "RESOURCE_EXHAUSTED",
                            "metadata": {
                                "quota_limit": "GenerateContentRequestsPerDayPerProjectPerModel-FreeTier",
                            },
                        }
                    ],
                }
            },
        )

    def fake_warning(message):
        captured["warnings"].append(message)

    monkeypatch.setattr(geminicli, "datetime", _FixedDateTime)
    monkeypatch.setattr(geminicli, "credential_manager", _DummyCredentialManager())
    monkeypatch.setattr(geminicli, "get_ff_retry_policy_v2", _retry_policy_v2_enabled)
    monkeypatch.setattr(
        geminicli, "get_code_assist_endpoint", fake_get_code_assist_endpoint
    )
    monkeypatch.setattr(geminicli, "get_retry_config", fake_get_retry_config)
    monkeypatch.setattr(
        geminicli, "get_auto_ban_error_codes", fake_get_auto_ban_error_codes
    )
    monkeypatch.setattr(
        geminicli, "parse_and_log_cooldown", fake_parse_and_log_cooldown
    )
    monkeypatch.setattr(geminicli, "record_api_call_error", fake_record_api_call_error)
    monkeypatch.setattr(
        geminicli, "handle_error_with_retry", fake_handle_error_with_retry
    )
    monkeypatch.setattr(geminicli, "post_async", fake_post_async)
    monkeypatch.setattr(geminicli.log, "warning", fake_warning)

    body = {
        "model": "gemini-2.5-pro",
        "request": {"contents": [{"role": "user", "parts": [{"text": "hi"}]}]},
    }
    response = asyncio.run(geminicli.non_stream_request(body=body))

    expected_cooldown = datetime(2026, 3, 12, 0, 0, 0, tzinfo=timezone.utc).timestamp()
    assert response.status_code == 429
    assert captured["cooldown_until"] == expected_cooldown
    assert any("回退到下一个UTC午夜" in message for message in captured["warnings"])


def test_handle_error_with_retry_v2_controls_internal_sleep(monkeypatch):
    sleep_calls = []

    async def fake_check_should_auto_ban(*args, **kwargs):
        return False

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)

    monkeypatch.setattr(api_utils, "check_should_auto_ban", fake_check_should_auto_ban)
    monkeypatch.setattr(api_utils.asyncio, "sleep", fake_sleep)

    should_retry = asyncio.run(
        api_utils.handle_error_with_retry(
            credential_manager=None,
            status_code=429,
            credential_name="dummy.json",
            retry_enabled=True,
            attempt=0,
            max_retries=1,
            retry_interval=0.3,
            retry_policy_v2_enabled=True,
        )
    )

    assert should_retry is True
    assert sleep_calls == [0.3]

    sleep_calls.clear()
    should_retry = asyncio.run(
        api_utils.handle_error_with_retry(
            credential_manager=None,
            status_code=429,
            credential_name="dummy.json",
            retry_enabled=True,
            attempt=0,
            max_retries=1,
            retry_interval=0.3,
            retry_policy_v2_enabled=False,
        )
    )

    assert should_retry is True
    assert sleep_calls == []


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
