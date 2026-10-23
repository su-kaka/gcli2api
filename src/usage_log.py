"""
请求日志服务：usage_log 表的写入与统计
- record_usage_log: 请求完成时写入一条日志（不抛异常，不影响主流程）
- extract_usage_tokens: 从 Gemini 响应/流式块中提取 usageMetadata token 计数
"""

import time
import uuid
from typing import Any, Dict, Optional

from log import log


def new_request_id() -> str:
    """生成短请求 ID"""
    return uuid.uuid4().hex[:16]


def extract_usage_tokens(source: Any) -> Dict[str, int]:
    """
    从 Gemini 响应结构中提取 token 用量。

    兼容:
    - 非流式: {"usageMetadata": {...}} 或 {"response": {"usageMetadata": {...}}}
    - 流式块: data 字符串内的 usageMetadata（通常只在最后一个块出现）

    Gemini 语义（与 PR#432 对齐）:
    - promptTokenCount 含缓存 token
    - completion = candidatesTokenCount + thoughtsTokenCount
    """
    prompt_tokens = completion_tokens = total_tokens = 0

    if isinstance(source, (str, bytes)):
        # 流式 SSE 块：解析 JSON 提取 usageMetadata
        try:
            text = source.decode("utf-8") if isinstance(source, bytes) else source
            if text.startswith("data: "):
                text = text[len("data: "):].strip()
            if not text or text == "[DONE]":
                return {}
            source = __import__("json").loads(text)
        except Exception:
            return {}

    if not isinstance(source, dict):
        return {}

    metadata = source.get("usageMetadata")
    if not isinstance(metadata, dict):
        # 展开包装: {"response": {...}}
        response = source.get("response")
        if isinstance(response, dict):
            metadata = response.get("usageMetadata")
    if not isinstance(metadata, dict):
        return {}

    prompt_tokens = int(metadata.get("promptTokenCount", 0) or 0)
    reasoning_tokens = int(metadata.get("thoughtsTokenCount", 0) or 0)
    completion_tokens = int(metadata.get("candidatesTokenCount", 0) or 0) + reasoning_tokens
    total_tokens = int(
        metadata.get("totalTokenCount", prompt_tokens + completion_tokens) or 0
    )

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


async def record_usage_log(entry: Dict[str, Any]) -> None:
    """
    写入一条请求日志。任何异常仅记录告警，绝不影响主请求流程。

    必要字段: model_name, success
    可选字段: api_format, client_ip, request_id, credential_email, is_stream,
              status_code, use_time, prompt_tokens, completion_tokens, total_tokens, error_message
    """
    try:
        import config

        if not await config.get_usage_log_enabled():
            return

        from src.storage.database import get_database

        database = await get_database()
        entry = {**entry, "created_at": entry.get("created_at") or time.time()}
        await database.insert_usage_log(entry)
    except Exception as e:
        log.debug(f"请求日志写入失败（不影响主流程）: {e}")


async def clear_usage_logs(days: int) -> int:
    """清理 N 天前的日志，返回删除条数"""
    from src.storage.database import get_database

    database = await get_database()
    cutoff = time.time() - days * 86400
    return await database.delete_usage_logs_before(cutoff)
