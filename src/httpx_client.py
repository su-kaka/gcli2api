"""
通用的HTTP客户端模块
为所有需要使用httpx的模块提供统一的客户端配置和方法
保持通用性，不与特定业务逻辑耦合
"""

from contextlib import asynccontextmanager
import json as jsonlib
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from config import get_proxy_config
from log import log


class HttpxClientManager:
    """通用HTTP客户端管理器（启用 HTTP/2 以匹配 Google API 预期）"""

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
        self, timeout: float = 30.0, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取配置好的异步HTTP客户端"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)

        async with httpx.AsyncClient(**client_kwargs) as client:
            yield client

    @asynccontextmanager
    async def get_streaming_client(
        self, timeout: Optional[float] = None, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取用于流式请求的HTTP客户端（无超时限制）"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)

        # 创建独立的客户端实例用于流式处理
        client = httpx.AsyncClient(**client_kwargs)
        try:
            yield client
        finally:
            # 确保无论发生什么都关闭客户端
            try:
                await client.aclose()
            except Exception as e:
                log.warning(f"Error closing streaming client: {e}")


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
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    """通用异步GET请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.get(url, headers=headers)


async def post_async(
    url: str,
    data: Any = None,
    json: Any = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 600.0,
    **kwargs,
) -> httpx.Response:
    """通用异步POST请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        if json is not None and data is None:
            request_headers = dict(headers or {})
            request_headers.setdefault("Content-Type", "application/json")
            safe_json_bytes = _encode_json_body_safely(json)
            return await client.post(
                url,
                content=safe_json_bytes,
                headers=request_headers,
            )
        return await client.post(url, data=data, json=json, headers=headers)


async def stream_post_async(
    url: str,
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
    **kwargs,
):
    """流式异步POST请求"""
    async with http_client.get_streaming_client(**kwargs) as client:
        request_headers = dict(headers or {})
        request_headers.setdefault("Content-Type", "application/json")
        safe_json_bytes = _encode_json_body_safely(body)
        async with client.stream(
            "POST", url, content=safe_json_bytes, headers=request_headers
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
