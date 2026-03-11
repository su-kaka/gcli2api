from typing import Any

from src import httpx_client
from src.httpx_client import HttpxClientManager, _encode_json_body_safely, post_async


def test_encode_json_body_safely_keeps_valid_unicode():
    payload = {"x": "😀"}
    encoded = _encode_json_body_safely(payload)
    assert encoded.decode("utf-8") == '{"x":"😀"}'


def test_encode_json_body_safely_falls_back_on_lone_surrogate():
    payload = {"x": "\ud83d"}
    encoded = _encode_json_body_safely(payload)
    assert encoded.decode("utf-8") == '{"x":"\\ud83d"}'


class _FakeAsyncClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.closed = False
        self.post_calls = []

    async def aclose(self):
        self.closed = True

    async def post(self, *args, **kwargs):
        self.post_calls.append((args, kwargs))


async def test_httpx_manager_reuses_pooled_clients(monkeypatch):
    created_clients = []

    async def fake_get_proxy_config():
        return None

    async def fake_get_ff_http2_pool_tuning():
        return True

    def fake_async_client(**kwargs):
        client = _FakeAsyncClient(**kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr(httpx_client, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(
        httpx_client, "get_ff_http2_pool_tuning", fake_get_ff_http2_pool_tuning
    )
    monkeypatch.setattr(httpx_client.httpx, "AsyncClient", fake_async_client)

    manager = HttpxClientManager()

    first: Any
    second: Any
    async with manager.get_client() as first_client:
        first = first_client
        pass
    async with manager.get_client() as second_client:
        second = second_client
        pass

    assert first is second
    assert len(created_clients) == 1
    assert created_clients[0].kwargs["http2"] is True

    await manager.close_all_clients()
    assert created_clients[0].closed is True


async def test_httpx_manager_refreshes_pool_when_proxy_changes(monkeypatch):
    created_clients = []
    proxy_state = {"proxy": "http://proxy-1:8888"}

    async def fake_get_proxy_config():
        return proxy_state["proxy"]

    async def fake_get_ff_http2_pool_tuning():
        return True

    def fake_async_client(**kwargs):
        client = _FakeAsyncClient(**kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr(httpx_client, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(
        httpx_client, "get_ff_http2_pool_tuning", fake_get_ff_http2_pool_tuning
    )
    monkeypatch.setattr(httpx_client.httpx, "AsyncClient", fake_async_client)

    manager = HttpxClientManager()

    first: Any
    second: Any
    async with manager.get_client() as first_client:
        first = first_client
        pass

    proxy_state["proxy"] = "http://proxy-2:8888"

    async with manager.get_client() as second_client:
        second = second_client
        pass

    assert first is not second
    assert first.closed is True
    assert second.kwargs["proxy"] == "http://proxy-2:8888"

    await manager.close_all_clients()


async def test_post_async_applies_request_timeout_with_pooled_client(monkeypatch):
    created_clients = []

    async def fake_get_proxy_config():
        return None

    async def fake_get_ff_http2_pool_tuning():
        return True

    def fake_async_client(**kwargs):
        client = _FakeAsyncClient(**kwargs)
        created_clients.append(client)
        return client

    monkeypatch.setattr(httpx_client, "get_proxy_config", fake_get_proxy_config)
    monkeypatch.setattr(
        httpx_client, "get_ff_http2_pool_tuning", fake_get_ff_http2_pool_tuning
    )
    monkeypatch.setattr(httpx_client.httpx, "AsyncClient", fake_async_client)

    original_manager = httpx_client.http_client
    manager = HttpxClientManager()
    monkeypatch.setattr(httpx_client, "http_client", manager)

    try:
        await post_async("https://example.com", data="hello", timeout=123.0)
    finally:
        await manager.close_all_clients()
        monkeypatch.setattr(httpx_client, "http_client", original_manager)

    assert len(created_clients) == 1
    assert created_clients[0].post_calls
    _, call_kwargs = created_clients[0].post_calls[0]
    assert call_kwargs["timeout"] == 123.0
