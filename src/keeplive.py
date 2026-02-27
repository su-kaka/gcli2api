"""
保活服务模块
定期向配置的URL发送GET请求，保持服务在线
未配置保活URL时不启动任何任务，零资源占用
"""

import asyncio
from typing import Optional

from config import get_keepalive_interval, get_keepalive_url
from log import log
from src.httpx_client import get_async


class KeepAliveService:
    """保活服务：定期向指定URL发送GET请求"""

    def __init__(self):
        self._task: Optional[asyncio.Task] = None

    async def _run(self, url: str, interval: int):
        """保活循环，读取到有效URL才会被调用"""
        log.info(f"[KeepAlive] 保活任务启动，URL={url}，间隔={interval}s")
        while True:
            try:
                response = await get_async(url, timeout=30.0)
                log.info(f"[KeepAlive] GET {url} -> {response.status_code}")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"[KeepAlive] GET {url} 失败: {e}")

            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise

    async def start(self):
        """
        启动保活服务。
        仅当配置了有效的保活URL时才创建后台任务，否则零开销。
        """
        if self._task and not self._task.done():
            # 已有任务在运行，不重复启动
            return

        url = await get_keepalive_url()
        interval = await get_keepalive_interval()

        if not url or not url.strip():
            log.debug("[KeepAlive] 未配置保活URL，保活服务不启动")
            return

        if interval <= 0:
            log.warning(f"[KeepAlive] 保活间隔无效（{interval}），保活服务不启动")
            return

        self._task = asyncio.create_task(
            self._run(url.strip(), interval), name="keepalive_service"
        )

    async def stop(self):
        """停止保活服务"""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            log.info("[KeepAlive] 保活服务已停止")
        self._task = None

    async def restart(self):
        """
        重启保活服务。
        配置变更时调用，会停止旧任务并根据最新配置决定是否启动新任务。
        """
        await self.stop()
        await self.start()

    @property
    def is_running(self) -> bool:
        """当前保活任务是否在运行"""
        return self._task is not None and not self._task.done()


# 全局保活服务实例
keepalive_service = KeepAliveService()
