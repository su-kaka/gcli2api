"""
共享工具模块 - 包含WebSocket连接管理、工具函数等
"""

import os
import time
from collections import deque
from typing import Set

from fastapi import HTTPException, WebSocket
from starlette.websockets import WebSocketState

import config
from log import log


# =============================================================================
# WebSocket连接管理
# =============================================================================


class ConnectionManager:
    def __init__(self, max_connections: int = 3):  # 进一步降低最大连接数
        # 使用双端队列严格限制内存使用
        self.active_connections: deque = deque(maxlen=max_connections)
        self.max_connections = max_connections
        self._last_cleanup = 0
        self._cleanup_interval = 120  # 120秒清理一次死连接

    async def connect(self, websocket: WebSocket):
        # 自动清理死连接
        self._auto_cleanup()

        # 限制最大连接数，防止内存无限增长
        if len(self.active_connections) >= self.max_connections:
            await websocket.close(code=1008, reason="Too many connections")
            return False

        await websocket.accept()
        self.active_connections.append(websocket)
        log.debug(f"WebSocket连接建立，当前连接数: {len(self.active_connections)}")
        return True

    def disconnect(self, websocket: WebSocket):
        # 使用更高效的方式移除连接
        try:
            self.active_connections.remove(websocket)
        except ValueError:
            pass  # 连接已不存在
        log.debug(f"WebSocket连接断开，当前连接数: {len(self.active_connections)}")

    async def send_personal_message(self, message: str, websocket: WebSocket):
        try:
            await websocket.send_text(message)
        except Exception:
            self.disconnect(websocket)

    async def broadcast(self, message: str):
        # 使用更高效的方式处理广播，避免索引操作
        dead_connections = []
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                dead_connections.append(conn)

        # 批量移除死连接
        for dead_conn in dead_connections:
            self.disconnect(dead_conn)

    def _auto_cleanup(self):
        """自动清理死连接"""
        current_time = time.time()
        if current_time - self._last_cleanup > self._cleanup_interval:
            self.cleanup_dead_connections()
            self._last_cleanup = current_time

    def cleanup_dead_connections(self):
        """清理已断开的连接"""
        original_count = len(self.active_connections)
        # 使用列表推导式过滤活跃连接，更高效
        alive_connections = deque(
            [
                conn
                for conn in self.active_connections
                if hasattr(conn, "client_state")
                and conn.client_state != WebSocketState.DISCONNECTED
            ],
            maxlen=self.max_connections,
        )

        self.active_connections = alive_connections
        cleaned = original_count - len(self.active_connections)
        if cleaned > 0:
            log.debug(f"清理了 {cleaned} 个死连接，剩余连接数: {len(self.active_connections)}")


# =============================================================================
# 工具函数
# =============================================================================


def is_mobile_user_agent(user_agent: str) -> bool:
    """检测是否为移动设备用户代理"""
    if not user_agent:
        return False

    user_agent_lower = user_agent.lower()
    mobile_keywords = [
        "mobile",
        "android",
        "iphone",
        "ipad",
        "ipod",
        "blackberry",
        "windows phone",
        "samsung",
        "htc",
        "motorola",
        "nokia",
        "palm",
        "webos",
        "opera mini",
        "opera mobi",
        "fennec",
        "minimo",
        "symbian",
        "psp",
        "nintendo",
        "tablet",
    ]

    return any(keyword in user_agent_lower for keyword in mobile_keywords)


def validate_mode(mode: str = "geminicli") -> str:
    """
    验证 mode 参数

    Args:
        mode: 模式字符串 ("geminicli" 或 "antigravity")

    Returns:
        str: 验证后的 mode 字符串

    Raises:
        HTTPException: 如果 mode 参数无效
    """
    if mode not in ["geminicli", "antigravity"]:
        raise HTTPException(
            status_code=400,
            detail=f"无效的 mode 参数: {mode}，只支持 'geminicli' 或 'antigravity'"
        )
    return mode


def get_env_locked_keys() -> Set:
    """获取被环境变量锁定的配置键集合"""
    env_locked_keys = set()

    # 使用 config.py 中统一维护的映射表
    for env_key, config_key in config.ENV_MAPPINGS.items():
        if os.getenv(env_key):
            env_locked_keys.add(config_key)

    return env_locked_keys
