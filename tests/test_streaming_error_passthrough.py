import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pytest
from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.router.antigravity import anthropic as antigravity_anthropic_router
from src.router.antigravity import gemini as antigravity_gemini_router
from src.router.antigravity import openai as antigravity_openai_router
from src.router.geminicli import anthropic as geminicli_anthropic_router
from src.router.geminicli import gemini as geminicli_gemini_router
from src.router.geminicli import openai as geminicli_openai_router


ERROR_BODY = {
    "error": {
        "code": 429,
        "message": "Resource has been exhausted (e.g. check quota).",
        "status": "RESOURCE_EXHAUSTED",
    }
}


@dataclass(frozen=True)
class RouteCase:
    name: str
    router_module: object
    path_builder: Callable[[str], str]
    body_builder: Callable[[str], dict]


OPENAI_BODY = lambda model: {
    "model": model,
    "messages": [{"role": "user", "content": "hello"}],
    "stream": True,
}

ANTHROPIC_BODY = lambda model: {
    "model": model,
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 32,
    "stream": True,
}

GEMINI_BODY = lambda _model: {
    "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
}


ROUTE_CASES = [
    RouteCase(
        name="geminicli-openai",
        router_module=geminicli_openai_router,
        path_builder=lambda _model: "/v1/chat/completions",
        body_builder=OPENAI_BODY,
    ),
    RouteCase(
        name="geminicli-anthropic",
        router_module=geminicli_anthropic_router,
        path_builder=lambda _model: "/v1/messages",
        body_builder=ANTHROPIC_BODY,
    ),
    RouteCase(
        name="geminicli-gemini",
        router_module=geminicli_gemini_router,
        path_builder=lambda model: f"/v1/models/{model}:streamGenerateContent",
        body_builder=GEMINI_BODY,
    ),
    RouteCase(
        name="antigravity-openai",
        router_module=antigravity_openai_router,
        path_builder=lambda _model: "/antigravity/v1/chat/completions",
        body_builder=OPENAI_BODY,
    ),
    RouteCase(
        name="antigravity-anthropic",
        router_module=antigravity_anthropic_router,
        path_builder=lambda _model: "/antigravity/v1/messages",
        body_builder=ANTHROPIC_BODY,
    ),
    RouteCase(
        name="antigravity-gemini",
        router_module=antigravity_gemini_router,
        path_builder=lambda model: f"/antigravity/v1/models/{model}:streamGenerateContent",
        body_builder=GEMINI_BODY,
    ),
]


def _make_error_response() -> Response:
    return Response(
        content=json.dumps(ERROR_BODY),
        status_code=429,
        media_type="application/json",
    )


def _build_client(route_case: RouteCase) -> TestClient:
    app = FastAPI()
    app.include_router(route_case.router_module.router)

    if hasattr(route_case.router_module, "authenticate_bearer"):
        app.dependency_overrides[route_case.router_module.authenticate_bearer] = lambda: "test-key"
    if hasattr(route_case.router_module, "authenticate_gemini_flexible"):
        app.dependency_overrides[route_case.router_module.authenticate_gemini_flexible] = lambda: "test-key"

    return TestClient(app)


@pytest.fixture(autouse=True)
def stub_request_normalization(monkeypatch):
    async def normalize_gemini_request(payload, mode=None):
        return payload

    async def convert_openai_to_gemini_request(_payload):
        return {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}

    async def anthropic_to_gemini_request(_payload):
        return {"contents": [{"role": "user", "parts": [{"text": "hello"}]}]}

    async def get_anti_truncation_max_attempts():
        return 1

    monkeypatch.setattr(
        "src.converter.gemini_fix.normalize_gemini_request",
        normalize_gemini_request,
    )
    monkeypatch.setattr(
        "src.converter.openai2gemini.convert_openai_to_gemini_request",
        convert_openai_to_gemini_request,
    )
    monkeypatch.setattr(
        "src.converter.anthropic2gemini.anthropic_to_gemini_request",
        anthropic_to_gemini_request,
    )
    monkeypatch.setattr(
        "config.get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )
    monkeypatch.setattr(
        geminicli_openai_router,
        "get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )
    monkeypatch.setattr(
        geminicli_gemini_router,
        "get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )
    monkeypatch.setattr(
        geminicli_anthropic_router,
        "get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )
    monkeypatch.setattr(
        antigravity_openai_router,
        "get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )
    monkeypatch.setattr(
        antigravity_gemini_router,
        "get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )
    monkeypatch.setattr(
        antigravity_anthropic_router,
        "get_anti_truncation_max_attempts",
        get_anti_truncation_max_attempts,
    )


@pytest.mark.parametrize("route_case", ROUTE_CASES, ids=lambda case: case.name)
def test_normal_streaming_429_is_returned_as_http_429(route_case: RouteCase, monkeypatch):
    async def stream_request(*_args, **_kwargs):
        yield _make_error_response()

    if route_case.name.startswith("geminicli"):
        monkeypatch.setattr("src.api.geminicli.stream_request", stream_request)
    else:
        monkeypatch.setattr("src.api.antigravity.stream_request", stream_request)

    client = _build_client(route_case)
    model = "gemini-2.5-flash"
    response = client.post(route_case.path_builder(model), json=route_case.body_builder(model))

    assert response.status_code == 429
    assert response.json() == ERROR_BODY


@pytest.mark.parametrize("route_case", ROUTE_CASES, ids=lambda case: case.name)
def test_fake_streaming_429_is_returned_as_http_429(route_case: RouteCase, monkeypatch):
    async def non_stream_request(*_args, **_kwargs):
        return _make_error_response()

    if route_case.name.startswith("geminicli"):
        monkeypatch.setattr("src.api.geminicli.non_stream_request", non_stream_request)
    else:
        monkeypatch.setattr("src.api.antigravity.non_stream_request", non_stream_request)

    client = _build_client(route_case)
    model = "假流式/gemini-2.5-flash"
    response = client.post(route_case.path_builder(model), json=route_case.body_builder(model))

    assert response.status_code == 429
    assert response.json() == ERROR_BODY


@pytest.mark.parametrize("route_case", ROUTE_CASES, ids=lambda case: case.name)
def test_anti_truncation_429_is_returned_as_http_429(route_case: RouteCase, monkeypatch):
    async def stream_request(*_args, **_kwargs):
        yield _make_error_response()

    if route_case.name.startswith("geminicli"):
        monkeypatch.setattr("src.api.geminicli.stream_request", stream_request)
    else:
        monkeypatch.setattr("src.api.antigravity.stream_request", stream_request)

    client = _build_client(route_case)
    model = "流式抗截断/gemini-2.5-flash"
    response = client.post(route_case.path_builder(model), json=route_case.body_builder(model))

    assert response.status_code == 429
    assert response.json() == ERROR_BODY
