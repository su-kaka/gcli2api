from src.i18n import ts
"""
{ts("id_3160")}HTTP{ts("id_3159")}
{ts("id_3162")}httpx{ts("id_3161")}
{ts("id_3163")}
"""

from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from config import get_proxy_config
from log import log


class HttpxClientManager:
    f"""{ts("id_3165")}HTTP{ts("id_3164")}"""

    async def get_client_kwargs(self, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
        f"""{ts("id_712")}httpx{ts("id_3166")}"""
        client_kwargs = {"timeout": timeout, **kwargs}

        # {ts("id_3167")}
        current_proxy_config = await get_proxy_config()
        if current_proxy_config:
            client_kwargs["proxy"] = current_proxy_config

        return client_kwargs

    @asynccontextmanager
    async def get_client(
        self, timeout: float = 30.0, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        f"""{ts("id_3168")}HTTP{ts("id_1597")}"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)

        async with httpx.AsyncClient(**client_kwargs) as client:
            yield client

    @asynccontextmanager
    async def get_streaming_client(
        self, timeout: float = None, **kwargs
    ) -> AsyncGenerator[httpx.AsyncClient, None]:
        f"""{ts("id_3170")}HTTP{ts("id_3169")}"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)

        # {ts("id_3171")}
        client = httpx.AsyncClient(**client_kwargs)
        try:
            yield client
        finally:
            # {ts("id_3172")}
            try:
                await client.aclose()
            except Exception as e:
                log.warning(f"Error closing streaming client: {e}")


# {ts("id_3174")}HTTP{ts("id_3173")}
http_client = HttpxClientManager()


# {ts("id_3175")}
async def get_async(
    url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, **kwargs
) -> httpx.Response:
    f"""{ts("id_3176")}GET{ts("id_2282")}"""
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
    f"""{ts("id_3176")}POST{ts("id_2282")}"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.post(url, data=data, json=json, headers=headers)


async def stream_post_async(
    url: str,
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
    **kwargs,
):
    f"""{ts("id_3177")}POST{ts("id_2282")}"""
    async with http_client.get_streaming_client(**kwargs) as client:
        async with client.stream("POST", url, json=body, headers=headers) as r:
            # {ts("id_3178")}
            if r.status_code != 200:
                from fastapi import Response
                yield Response(await r.aread(), r.status_code, dict(r.headers))
                return

            # {ts(f"id_2183")}native=True{ts("id_3179")}bytes{ts("id_1486")}
            if native:
                async for chunk in r.aiter_bytes():
                    yield chunk
            else:
                # {ts(f"id_935")}aiter_lines{ts("id_3181")}str{ts("id_3180")}
                async for line in r.aiter_lines():
                    yield line
