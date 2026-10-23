"""
会话凭证缓存（凭证亲和）
同一会话（由请求内容派生的稳定 key）绑定同一凭证，命中上游 prompt cache。

双后端:
- Redis（可选）: 设置 REDIS_URL 后启用，多实例共享绑定
- 内存（默认）: 无 Redis 时自动使用，进程内字典 + 过期清理

行为:
- get: 命中时自动续期 TTL（活跃会话保持绑定，空闲会话过期）
- set: 每次成功调用后写入/刷新绑定
- Redis 故障仅跳过该次操作并告警，不影响主流程；内存后端始终可用兜底
"""

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, Optional

from log import log

# 绑定 TTL（秒）：会话空闲超过该时长后解绑
SESSION_BINDING_TTL = 3600

_KEY_PREFIX = "agy2api:sess:"

# Redis 客户端（None = 未启用，使用内存后端）
_redis_client = None

# 内存后端: {session_key: (email, expire_at)}
_memory_store: Dict[str, tuple] = {}
_memory_lock = asyncio.Lock()

# 内存后端容量上限（防止无限增长），超限时清理过期项后仍超限则丢弃最旧的
_MEMORY_MAX_ENTRIES = 50000


def _now() -> float:
    return time.time()


def _get_client():
    """返回已初始化的 Redis 客户端；未启用时返回 None"""
    return _redis_client


async def init_redis(redis_url: str) -> bool:
    """初始化 Redis 连接。连接失败返回 False（回落内存后端），不抛异常。"""
    global _redis_client
    if not redis_url:
        return False
    try:
        import redis.asyncio as redis_aiolib

        client = redis_aiolib.from_url(
            redis_url,
            decode_responses=False,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
            health_check_interval=30,
        )
        await client.ping()
        _redis_client = client
        log.info("会话凭证缓存已连接 Redis")
        return True
    except Exception as e:
        log.warning(f"Redis 连接失败，使用内存后端: {e}")
        _redis_client = None
        return False


async def close() -> None:
    """关闭 Redis 连接（应用关闭时调用）"""
    global _redis_client
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception:
            pass
        _redis_client = None
    _memory_store.clear()


# ====================== 会话 key 派生 ======================

def derive_session_key(model_name: str, gemini_request: Dict[str, Any]) -> Optional[str]:
    """
    从规范化后的 Gemini 请求派生会话 key:
    hash(模型 + systemInstruction 或 第一条 user 消息)

    必须在格式转换完成后调用（openai2gemini / anthropic2gemini / 原生 gemini 都已
    转成 Gemini 格式），保证不同 API 格式的相同内容派生相同的 key。

    Returns:
        "auto:<hash>" 或 None（无法派生时）
    """
    if not isinstance(gemini_request, dict):
        return None

    model_part = model_name if isinstance(model_name, str) else ""

    # 优先 systemInstruction（对话稳定的锚点）
    anchor = None
    system_instruction = gemini_request.get("systemInstruction")
    if isinstance(system_instruction, dict):
        parts = system_instruction.get("parts")
        if isinstance(parts, list) and parts:
            anchor = _stable_json(parts)

    # 没有 system 时取第一条 user 消息的 parts
    if anchor is None:
        contents = gemini_request.get("contents")
        if isinstance(contents, list):
            for content in contents:
                if isinstance(content, dict) and content.get("role") == "user":
                    parts = content.get("parts")
                    if isinstance(parts, list) and parts:
                        anchor = _stable_json(parts)
                        break

    if anchor is None:
        return None

    digest = hashlib.sha256(f"{model_part}|{anchor}".encode("utf-8")).hexdigest()
    return f"auto:{digest[:32]}"


def _stable_json(value: Any) -> Optional[str]:
    """稳定序列化（排序键），失败返回 None"""
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


# ====================== 绑定读写 ======================

async def get_session_binding(session_key: str) -> Optional[str]:
    """查询会话绑定的凭证 email；命中时续期 TTL。未绑定/后端故障返回 None。"""
    if not session_key:
        return None

    client = _get_client()
    if client is not None:
        redis_key = f"{_KEY_PREFIX}{session_key}"
        try:
            raw = await client.get(redis_key)
            if raw is None:
                return None
            await client.expire(redis_key, SESSION_BINDING_TTL)
            return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
        except Exception as e:
            log.warning(f"Redis get 会话绑定失败: {e}")
            # Redis 故障回落内存后端

    return await _memory_get(session_key)


async def set_session_binding(session_key: str, email: str) -> None:
    """写入/刷新会话绑定（凭证亲和成功后调用）。后端故障仅告警。"""
    if not session_key or not email:
        return

    client = _get_client()
    if client is not None:
        try:
            await client.setex(
                f"{_KEY_PREFIX}{session_key}", SESSION_BINDING_TTL, email
            )
        except Exception as e:
            log.warning(f"Redis set 会话绑定失败: {e}")

    await _memory_set(session_key, email)


async def delete_session_binding(session_key: str) -> None:
    """删除会话绑定（绑定凭证不可用时清除）"""
    if not session_key:
        return

    client = _get_client()
    if client is not None:
        try:
            await client.delete(f"{_KEY_PREFIX}{session_key}")
        except Exception as e:
            log.warning(f"Redis delete 会话绑定失败: {e}")

    async with _memory_lock:
        _memory_store.pop(session_key, None)


# ====================== 内存后端 ======================

async def _memory_get(session_key: str) -> Optional[str]:
    async with _memory_lock:
        entry = _memory_store.get(session_key)
        if not entry:
            return None
        email, expire_at = entry
        if _now() >= expire_at:
            _memory_store.pop(session_key, None)
            return None
        # 命中续期
        _memory_store[session_key] = (email, _now() + SESSION_BINDING_TTL)
        return email


async def _memory_set(session_key: str, email: str) -> None:
    async with _memory_lock:
        # 容量控制：超过上限先清理过期项
        if len(_memory_store) >= _MEMORY_MAX_ENTRIES:
            _cleanup_expired_locked()
        # 仍超限则丢弃最旧的一半（按过期时间排序）
        if len(_memory_store) >= _MEMORY_MAX_ENTRIES:
            sorted_items = sorted(_memory_store.items(), key=lambda kv: kv[1][1])
            for k, _ in sorted_items[: len(sorted_items) // 2]:
                _memory_store.pop(k, None)
        _memory_store[session_key] = (email, _now() + SESSION_BINDING_TTL)


def _cleanup_expired_locked() -> None:
    """清理过期项（须持有锁时调用）"""
    now = _now()
    expired = [k for k, (_, exp) in _memory_store.items() if now >= exp]
    for k in expired:
        _memory_store.pop(k, None)


def memory_store_size() -> int:
    """内存后端条目数（诊断用）"""
    return len(_memory_store)
