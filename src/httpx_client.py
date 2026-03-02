"""
通用的HTTP客户端模块
为所有需要使用httpx的模块提供统一的客户端配置和方法
保持通用性，不与特定业务逻辑耦合
"""

import asyncio
from contextlib import asynccontextmanager
import json as jsonlib
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from config import get_ff_http2_pool_tuning, get_proxy_config
from log import log


class HttpxClientManager:
    """通用HTTP客户端管理器（启用 HTTP/2 以匹配 Google API 预期）"""

    def __init__(self) -> None:
        self._request_client: Optional[httpx.AsyncClient] = None
        self._streaming_client: Optional[httpx.AsyncClient] = None
        self._active_proxy_config: Optional[str] = None
        self._client_lock = asyncio.Lock()

    @staticmethod
    def _build_limits(streaming: bool) -> httpx.Limits:
        if streaming:
            return httpx.Limits(
                max_connections=120,
                max_keepalive_connections=40,
                keepalive_expiry=90.0,
            )
        return httpx.Limits(
            max_connections=80,
            max_keepalive_connections=20,
            keepalive_expiry=45.0,
        )

    @staticmethod
    def _build_default_timeout(streaming: bool) -> httpx.Timeout:
        if streaming:
            return httpx.Timeout(connect=20.0, read=None, write=30.0, pool=20.0)
        return httpx.Timeout(connect=20.0, read=120.0, write=30.0, pool=20.0)

    async def _close_client_safely(self, client: Optional[httpx.AsyncClient]) -> None:
        if client is None:
            return
        try:
            await client.aclose()
        except Exception as e:
            log.warning(f"Error closing httpx client: {e}")

    async def _create_pooled_client(
        self,
        *,
        proxy_config: Optional[str],
        streaming: bool,
    ) -> httpx.AsyncClient:
        client_kwargs = {
            "http2": True,
            "limits": self._build_limits(streaming),
            "timeout": self._build_default_timeout(streaming),
        }
        if proxy_config:
            client_kwargs["proxy"] = proxy_config
        return httpx.AsyncClient(**client_kwargs)

    async def _get_or_create_pooled_client(
        self, *, streaming: bool
    ) -> httpx.AsyncClient:
        current_proxy_config = await get_proxy_config()

        stale_request_client: Optional[httpx.AsyncClient] = None
        stale_streaming_client: Optional[httpx.AsyncClient] = None

        async with self._client_lock:
            proxy_changed = current_proxy_config != self._active_proxy_config
            if proxy_changed:
                stale_request_client = self._request_client
                stale_streaming_client = self._streaming_client
                self._request_client = None
                self._streaming_client = None
                self._active_proxy_config = current_proxy_config

            if self._active_proxy_config is None and current_proxy_config is not None:
                self._active_proxy_config = current_proxy_config

            if streaming:
                if self._streaming_client is None:
                    self._streaming_client = await self._create_pooled_client(
                        proxy_config=current_proxy_config,
                        streaming=True,
                    )
                selected_client = self._streaming_client
            else:
                if self._request_client is None:
                    self._request_client = await self._create_pooled_client(
                        proxy_config=current_proxy_config,
                        streaming=False,
                    )
                selected_client = self._request_client

        await self._close_client_safely(stale_request_client)
        await self._close_client_safely(stale_streaming_client)

        return selected_client

    async def close_all_clients(self) -> None:
        stale_request_client: Optional[httpx.AsyncClient] = None
        stale_streaming_client: Optional[httpx.AsyncClient] = None
        async with self._client_lock:
            stale_request_client = self._request_client
            stale_streaming_client = self._streaming_client
            self._request_client = None
            self._streaming_client = None
            self._active_proxy_config = None

        await self._close_client_safely(stale_request_client)
        await self._close_client_safely(stale_streaming_client)

    @staticmethod
    def _should_use_oneoff_client(
        timeout: Optional[Any], kwargs: Dict[str, Any]
    ) -> bool:
        return timeout is not None or bool(kwargs)

    async def get_client_kwargs(
        self, timeout: Optional[float] = 30.0, **kwargs
    ) -> Dict[str, Any]:
        """获取httpx客户端的通用配置参数"""
        client_kwargs = {
            "http2": True,  # Google cloudcode-pa 端点要求/优先 HTTP/2
            **kwargs,
        }
        if timeout is not None:
            client_kwargs["timeout"] = timeout

        # 动态读取代理配置，支持热更新
        current_proxy_config = await get_proxy_config()
        if current_proxy_config:
            client_kwargs["proxy"] = current_proxy_config

        return client_kwargs

    @asynccontextmanager
    async def get_client(
        self, timeout: Optional[Any] = None, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取配置好的异步HTTP客户端"""
        if not await get_ff_http2_pool_tuning():
            client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
            async with httpx.AsyncClient(**client_kwargs) as client:
                yield client
            return

        if self._should_use_oneoff_client(timeout=timeout, kwargs=kwargs):
            client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
            async with httpx.AsyncClient(**client_kwargs) as client:
                yield client
            return

        client = await self._get_or_create_pooled_client(streaming=False)
        yield client

    @asynccontextmanager
    async def get_streaming_client(
        self, timeout: Optional[Any] = None, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取用于流式请求的HTTP客户端（无超时限制）"""
        if not await get_ff_http2_pool_tuning():
            client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
            client = httpx.AsyncClient(**client_kwargs)
            try:
                yield client
            finally:
                await self._close_client_safely(client)
            return

        if self._should_use_oneoff_client(timeout=timeout, kwargs=kwargs):
            client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
            client = httpx.AsyncClient(**client_kwargs)
            try:
                yield client
            finally:
                await self._close_client_safely(client)
            return

        client = await self._get_or_create_pooled_client(streaming=True)
        yield client


# 全局HTTP客户端管理器实例
http_client = HttpxClientManager()


def _encode_json_body_safely(payload: Any) -> bytes:
    try:
        return jsonlib.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except UnicodeEncodeError:
        log.warning("检测到孤立代理项，使用ASCII转义发送JSON请求体")
        return jsonlib.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")


# 通用的异步方法
async def get_async(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[Any] = 30.0,
    **kwargs,
) -> httpx.Response:
    """通用异步GET请求"""
    request_timeout = kwargs.pop("request_timeout", timeout)
    async with http_client.get_client(**kwargs) as client:
        return await client.get(url, headers=headers, timeout=request_timeout)


async def post_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: Optional[Any] = 600.0,
    **kwargs,
) -> httpx.Response:
    """通用异步POST请求"""
    request_timeout = kwargs.pop("request_timeout", timeout)
    async with http_client.get_client(**kwargs) as client:
        if json is not None and data is None:
            request_headers = dict(headers or {})
            request_headers.setdefault("Content-Type", "application/json")
            safe_json_bytes = _encode_json_body_safely(json)
            return await client.post(
                url,
                content=safe_json_bytes,
                headers=request_headers,
                timeout=request_timeout,
            )
        return await client.post(
            url,
            data=data,
            json=json,
            headers=headers,
            timeout=request_timeout,
        )


async def stream_post_async(
    url: str,
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
    **kwargs,
):
    """流式异步POST请求"""
    request_timeout = kwargs.pop("timeout", None)
    async with http_client.get_streaming_client(**kwargs) as client:
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")
        safe_json_bytes = _encode_json_body_safely(body)
        async with client.stream(
            "POST",
            url,
            content=safe_json_bytes,
            headers=request_headers,
            timeout=request_timeout,
        ) as r:
            # 错误直接返回
            if r.status_code != 200:
                from fastapi import Response

                yield Response(await r.aread(), r.status_code, dict(r.headers))
                return

            # 如果native=True，直接返回bytes流
            if native:
                async for chunk in r.aiter_bytes():
                    yield chunk
            else:
                # 通过aiter_lines转化成str流返回
                async for line in r.aiter_lines():
                    yield line
