"""
Base Router - 共用的路由基础功能
提供模型列表处理、通用响应等共同功能
"""

from typing import List, Optional
from fastapi import Response

from src.models import Model, ModelList
from log import log
import json


# ==================== 错误响应 ====================

def create_error_response(
    message: str,
    status_code: int = 500,
    error_type: str = "api_error"
) -> Response:
    """
    创建标准化的错误响应
    
    Args:
        message: 错误消息
        status_code: HTTP状态码
        error_type: 错误类型
        
    Returns:
        FastAPI Response对象
    """
    return Response(
        content=json.dumps({
            "error": {
                "message": message,
                "type": error_type,
                "code": status_code
            }
        }),
        status_code=status_code,
        media_type="application/json",
    )


# ==================== 模型列表处理 ====================

def expand_models_with_features(
    base_models: List[str],
    features: Optional[List[str]] = None
) -> List[str]:
    """
    使用特性前缀扩展模型列表
    
    Args:
        base_models: 基础模型列表
        features: 特性前缀列表，如 ["流式抗截断", "假流式"]
        
    Returns:
        扩展后的模型列表（包含原始模型和特性变体）
    """
    if not features:
        return base_models.copy()
    
    expanded = []
    for model in base_models:
        # 添加原始模型
        expanded.append(model)
        
        # 添加特性变体
        for feature in features:
            expanded.append(f"{feature}/{model}")
    
    return expanded


def create_openai_model_list(
    model_ids: List[str],
    owned_by: str = "google"
) -> ModelList:
    """
    创建OpenAI格式的模型列表
    
    Args:
        model_ids: 模型ID列表
        owned_by: 模型所有者
        
    Returns:
        ModelList对象
    """
    from datetime import datetime, timezone
    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    
    models = [
        Model(
            id=model_id,
            object='model',
            created=current_timestamp,
            owned_by=owned_by
        )
        for model_id in model_ids
    ]
    
    return ModelList(data=models)


def create_gemini_model_list(
    model_ids: List[str],
    base_name_extractor=None
) -> dict:
    """
    创建Gemini格式的模型列表
    
    Args:
        model_ids: 模型ID列表
        base_name_extractor: 可选的基础模型名提取函数
        
    Returns:
        包含模型列表的字典
    """
    gemini_models = []
    
    for model_id in model_ids:
        base_model = model_id
        if base_name_extractor:
            try:
                base_model = base_name_extractor(model_id)
            except Exception:
                pass
        
        model_info = {
            "name": f"models/{model_id}",
            "baseModelId": base_model,
            "version": "001",
            "displayName": model_id,
            "description": f"Gemini {base_model} model",
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        }
        gemini_models.append(model_info)
    
    return {"models": gemini_models}


# ==================== 流式响应管理 ====================

async def cleanup_stream_resources(stream_ctx, client) -> None:
    """
    清理流式响应资源
    
    Args:
        stream_ctx: 流上下文
        client: HTTP客户端
    """
    # 按正确顺序清理资源：先关闭stream，再关闭client
    if stream_ctx:
        try:
            await stream_ctx.__aexit__(None, None, None)
        except Exception as e:
            log.debug(f"Error cleaning up stream_ctx: {e}")
    
    if client:
        try:
            await client.aclose()
        except Exception as e:
            log.debug(f"Error closing client: {e}")


async def create_error_stream(
    error_message: str,
    status_code: int = 500,
    format: str = "openai"
) -> bytes:
    """
    创建错误流数据
    
    Args:
        error_message: 错误消息
        status_code: HTTP状态码
        format: 响应格式（"openai" 或 "gemini"）
        
    Returns:
        SSE格式的错误数据
    """
    if format == "openai":
        error_data = {
            "error": {
                "message": error_message,
                "type": "api_error",
                "code": status_code,
            }
        }
    else:  # gemini
        error_data = {
            "error": {
                "message": error_message,
                "code": status_code,
                "status": "INTERNAL"
            }
        }
    
    return f"data: {json.dumps(error_data)}\n\n".encode()


# ==================== 模型名称处理 ====================

def extract_base_model_name(model_path: str) -> str:
    """
    从路径格式的模型名中提取基础模型名
    
    Args:
        model_path: 可能包含 "models/" 前缀的模型名
        
    Returns:
        基础模型名
    """
    # 去掉 "models/" 前缀
    if model_path.startswith("models/"):
        return model_path[7:]
    return model_path


# ==================== 日志辅助 ====================

def log_request_info(
    mode: str,
    model: str,
    credential_name: str,
    is_streaming: bool
) -> None:
    """
    记录请求信息
    
    Args:
        mode: 模式（如 "ANTIGRAVITY", "GEMINICLI"）
        model: 模型名称
        credential_name: 凭证名称
        is_streaming: 是否是流式请求
    """
    request_type = "streaming" if is_streaming else "non-streaming"
    log.info(
        f"[{mode}] {request_type.capitalize()} request for model: {model}, "
        f"using credential: {credential_name}"
    )


# ==================== 统一流式包装器 ====================

from typing import AsyncGenerator, Tuple, Any, Callable, Awaitable
from fastapi.responses import StreamingResponse


def wrap_stream_with_cleanup(
    filtered_lines: AsyncGenerator,
    stream_ctx: Any,
    client: Any
) -> AsyncGenerator:
    """
    包装流式响应，自动清理资源
    
    这个函数是所有流式响应的统一包装器，确保在流结束时正确清理资源。
    适用于 antigravity 和 geminicli 的所有流式 API。
    
    Args:
        filtered_lines: 原始行生成器
        stream_ctx: 流上下文管理器
        client: HTTP 客户端
        
    Returns:
        带资源清理的行生成器
        
    示例:
        ```python
        resources, _, _ = await send_xxx_request_stream(payload, cred_mgr)
        filtered_lines, stream_ctx, client = resources
        
        return StreamingResponse(
            wrap_stream_with_cleanup(filtered_lines, stream_ctx, client),
            media_type="text/event-stream"
        )
        ```
    """
    async def line_generator():
        try:
            async for line in filtered_lines:
                yield line
        finally:
            await cleanup_stream_resources(stream_ctx, client)
    
    return line_generator()


async def wrap_stream_with_processor(
    filtered_lines: AsyncGenerator,
    stream_ctx: Any,
    client: Any,
    processor: Callable[[Any], Awaitable[Any]]
) -> AsyncGenerator:
    """
    包装流式响应，应用处理器并清理资源
    
    这个函数用于需要对流式数据进行转换处理的场景（如 Anthropic SSE 转换）。
    
    Args:
        filtered_lines: 原始行生成器
        stream_ctx: 流上下文管理器
        client: HTTP 客户端
        processor: 异步处理器函数，接收原始流并产出处理后的数据
        
    Returns:
        带处理和资源清理的生成器
        
    示例:
        ```python
        resources, cred_name, _ = await send_xxx_request_stream(payload, cred_mgr)
        filtered_lines, stream_ctx, client = resources
        
        return StreamingResponse(
            wrap_stream_with_processor(
                filtered_lines, stream_ctx, client,
                lambda lines: gemini_sse_to_anthropic_sse(
                    lines, model=model, message_id=msg_id, ...
                )
            ),
            media_type="text/event-stream"
        )
        ```
    """
    async def processed_generator():
        try:
            async for chunk in processor(filtered_lines):
                yield chunk
        finally:
            await cleanup_stream_resources(stream_ctx, client)
    
    return processed_generator()


def create_streaming_response_from_resources(
    resources: Tuple[AsyncGenerator, Any, Any],
    media_type: str = "text/event-stream"
) -> StreamingResponse:
    """
    从流式资源创建 StreamingResponse（最简单的包装）
    
    Args:
        resources: (filtered_lines, stream_ctx, client) 元组
        media_type: 响应的媒体类型
        
    Returns:
        StreamingResponse 对象
        
    示例:
        ```python
        resources, _, _ = await send_xxx_request_stream(payload, cred_mgr)
        return create_streaming_response_from_resources(resources)
        ```
    """
    filtered_lines, stream_ctx, client = resources
    return StreamingResponse(
        wrap_stream_with_cleanup(filtered_lines, stream_ctx, client),
        media_type=media_type
    )
