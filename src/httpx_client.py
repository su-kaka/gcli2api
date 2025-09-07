"""
通用的HTTP客户端模块
为所有需要使用httpx的模块提供统一的客户端配置和方法
保持通用性，不与特定业务逻辑耦合
"""
import httpx
from typing import Optional, Dict, Any, AsyncGenerator
from contextlib import asynccontextmanager

from config import get_proxy_config
from log import log


class HttpxClientManager:
    """通用HTTP客户端管理器"""
    
    async def get_client_kwargs(self, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
        """获取httpx客户端的通用配置参数"""
        client_kwargs = {
            "timeout": timeout,
            **kwargs
        }
        
        # 动态读取代理配置，支持热更新
        current_proxy_config = await get_proxy_config()
        if current_proxy_config:
            client_kwargs["proxy"] = current_proxy_config
        
        return client_kwargs
    
    @asynccontextmanager
    async def get_client(self, timeout: float = 30.0, **kwargs) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取配置好的异步HTTP客户端"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
        
        async with httpx.AsyncClient(**client_kwargs) as client:
            yield client
    
    @asynccontextmanager
    async def get_streaming_client(self, timeout: float = None, **kwargs) -> AsyncGenerator[httpx.AsyncClient, None]:
        """获取用于流式请求的HTTP客户端（无超时限制）"""
        client_kwargs = await self.get_client_kwargs(timeout=timeout, **kwargs)
        
        # 创建独立的客户端实例用于流式处理
        client = httpx.AsyncClient(**client_kwargs)
        try:
            yield client
        finally:
            await client.aclose()


# 全局HTTP客户端管理器实例
http_client = HttpxClientManager()


# 通用的异步方法
async def get_async(url: str, headers: Optional[Dict[str, str]] = None, 
                   timeout: float = 30.0, **kwargs) -> httpx.Response:
    """通用异步GET请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.get(url, headers=headers)


async def post_async(url: str, data: Any = None, json: Any = None,
                    headers: Optional[Dict[str, str]] = None,
                    timeout: float = 30.0, **kwargs) -> httpx.Response:
    """通用异步POST请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.post(url, data=data, json=json, headers=headers)


async def put_async(url: str, data: Any = None, json: Any = None,
                   headers: Optional[Dict[str, str]] = None,
                   timeout: float = 30.0, **kwargs) -> httpx.Response:
    """通用异步PUT请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.put(url, data=data, json=json, headers=headers)


async def delete_async(url: str, headers: Optional[Dict[str, str]] = None,
                      timeout: float = 30.0, **kwargs) -> httpx.Response:
    """通用异步DELETE请求"""
    async with http_client.get_client(timeout=timeout, **kwargs) as client:
        return await client.delete(url, headers=headers)


# 错误处理装饰器
def handle_http_errors(func):
    """HTTP错误处理装饰器"""
    async def wrapper(*args, **kwargs):
        try:
            response = await func(*args, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as e:
            log.error(f"HTTP错误: {e.response.status_code} - {e.response.text}")
            raise
        except httpx.RequestError as e:
            log.error(f"请求错误: {e}")
            raise
        except Exception as e:
            log.error(f"未知错误: {e}")
            raise
    return wrapper


# 应用错误处理的安全方法
@handle_http_errors
async def safe_get_async(url: str, headers: Optional[Dict[str, str]] = None,
                        timeout: float = 30.0, **kwargs) -> httpx.Response:
    """安全的异步GET请求（自动错误处理）"""
    return await get_async(url, headers=headers, timeout=timeout, **kwargs)


@handle_http_errors
async def safe_post_async(url: str, data: Any = None, json: Any = None,
                         headers: Optional[Dict[str, str]] = None,
                         timeout: float = 30.0, **kwargs) -> httpx.Response:
    """安全的异步POST请求（自动错误处理）"""
    return await post_async(url, data=data, json=json, headers=headers, timeout=timeout, **kwargs)


@handle_http_errors
async def safe_put_async(url: str, data: Any = None, json: Any = None,
                        headers: Optional[Dict[str, str]] = None,
                        timeout: float = 30.0, **kwargs) -> httpx.Response:
    """安全的异步PUT请求（自动错误处理）"""
    return await put_async(url, data=data, json=json, headers=headers, timeout=timeout, **kwargs)


@handle_http_errors
async def safe_delete_async(url: str, headers: Optional[Dict[str, str]] = None,
                           timeout: float = 30.0, **kwargs) -> httpx.Response:
    """安全的异步DELETE请求（自动错误处理）"""
    return await delete_async(url, headers=headers, timeout=timeout, **kwargs)


# 流式请求支持
class StreamingContext:
    """流式请求上下文管理器"""
    
    def __init__(self, client: httpx.AsyncClient, stream_context):
        self.client = client
        self.stream_context = stream_context
        self.response = None
    
    async def __aenter__(self):
        self.response = await self.stream_context.__aenter__()
        return self.response
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.stream_context:
                await self.stream_context.__aexit__(exc_type, exc_val, exc_tb)
        finally:
            if self.client:
                await self.client.aclose()


@asynccontextmanager
async def get_streaming_post_context(url: str, data: Any = None, json: Any = None,
                                   headers: Optional[Dict[str, str]] = None,
                                   timeout: float = None, **kwargs) -> AsyncGenerator[StreamingContext, None]:
    """获取流式POST请求的上下文管理器"""
    async with http_client.get_streaming_client(timeout=timeout, **kwargs) as client:
        stream_ctx = client.stream("POST", url, data=data, json=json, headers=headers)
        streaming_context = StreamingContext(client, stream_ctx)
        yield streaming_context


async def create_streaming_client_with_kwargs(**kwargs) -> httpx.AsyncClient:
    """创建用于流式处理的独立客户端实例（手动管理生命周期）"""
    client_kwargs = await http_client.get_client_kwargs(timeout=None, **kwargs)
    return httpx.AsyncClient(**client_kwargs)