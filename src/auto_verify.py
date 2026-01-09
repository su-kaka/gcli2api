"""
Auto Verify Module - 自动检验凭证错误码并恢复
定时检查凭证状态，发现错误码自动执行检验恢复
"""

import asyncio
from typing import Optional

from log import log


class AutoVerifyService:
    """自动检验服务 - 后台定时检查并恢复错误凭证"""

    _instance: Optional["AutoVerifyService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._initialized = True

    async def start(self):
        """启动自动检验服务"""
        if self._running:
            log.debug("AutoVerifyService already running")
            return

        from config import get_config_value

        enabled = await get_config_value("auto_verify_enabled", False)
        if not enabled:
            log.info("自动检验服务未启用 (auto_verify_enabled=False)")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="auto_verify_loop")
        log.info("自动检验服务已启动")

    async def stop(self):
        """停止自动检验服务"""
        if not self._running:
            return

        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

        log.info("自动检验服务已停止")

    async def _run_loop(self):
        """主循环 - 定时检查凭证状态"""
        from config import get_auto_verify_interval

        while self._running:
            try:
                interval = await get_auto_verify_interval()

                await self._check_and_verify_credentials()

                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                log.error(f"自动检验循环出错: {e}")
                await asyncio.sleep(60)

    async def _check_and_verify_credentials(self):
        """检查所有凭证状态，对有错误码的凭证执行检验"""
        from config import get_auto_verify_error_codes
        from .storage_adapter import get_storage_adapter

        try:
            storage_adapter = await get_storage_adapter()

            auto_verify_error_codes = await get_auto_verify_error_codes()

            for mode in ["geminicli", "antigravity"]:
                await self._check_mode_credentials(
                    storage_adapter, mode, auto_verify_error_codes
                )

        except Exception as e:
            log.error(f"检查凭证状态失败: {e}")

    async def _check_mode_credentials(
        self, storage_adapter, mode: str, error_codes_to_verify: list
    ):
        """检查指定模式的凭证"""
        try:
            credentials = await storage_adapter.list_credentials(mode=mode)
            if not credentials:
                return

            verified_count = 0
            failed_count = 0

            for filename in credentials:
                try:
                    state = await storage_adapter.get_credential_state(filename, mode=mode)
                    if not state:
                        continue

                    current_error_codes = state.get("error_codes", [])
                    if not current_error_codes:
                        continue

                    needs_verify = any(
                        code in error_codes_to_verify for code in current_error_codes
                    )

                    if needs_verify:
                        log.info(
                            f"[自动检验] 检测到错误码 {current_error_codes}，"
                            f"开始检验凭证: {filename} (mode={mode})"
                        )

                        success = await self._verify_credential(filename, mode)

                        if success:
                            verified_count += 1
                            log.info(f"[自动检验] 凭证检验成功: {filename} (mode={mode})")
                        else:
                            failed_count += 1
                            log.warning(f"[自动检验] 凭证检验失败: {filename} (mode={mode})")

                        await asyncio.sleep(1)

                except Exception as e:
                    log.error(f"[自动检验] 处理凭证 {filename} 时出错: {e}")

            if verified_count > 0 or failed_count > 0:
                log.info(
                    f"[自动检验] {mode} 模式完成: "
                    f"成功 {verified_count} 个, 失败 {failed_count} 个"
                )

        except Exception as e:
            log.error(f"[自动检验] 检查 {mode} 凭证失败: {e}")

    async def _verify_credential(self, filename: str, mode: str) -> bool:
        """执行单个凭证的检验"""
        try:
            from .storage_adapter import get_storage_adapter
            from .google_oauth_api import Credentials
            from .web_routes import fetch_project_id
            from config import get_antigravity_api_url, get_code_assist_endpoint

            storage_adapter = await get_storage_adapter()

            credential_data = await storage_adapter.get_credential(filename, mode=mode)
            if not credential_data:
                return False

            credentials = Credentials.from_dict(credential_data)

            token_refreshed = await credentials.refresh_if_needed()
            if token_refreshed:
                credential_data = credentials.to_dict()
                await storage_adapter.store_credential(filename, credential_data, mode=mode)

            if mode == "antigravity":
                api_base_url = await get_antigravity_api_url()
                user_agent = "anthropic-vertex/0.1.0"
            else:
                api_base_url = await get_code_assist_endpoint()
                user_agent = "anthropic-vertex/0.1.0"

            project_id = await fetch_project_id(
                access_token=credentials.access_token,
                user_agent=user_agent,
                api_base_url=api_base_url
            )

            if project_id:
                credential_data["project_id"] = project_id
                await storage_adapter.store_credential(filename, credential_data, mode=mode)

                await storage_adapter.update_credential_state(filename, {
                    "disabled": False,
                    "error_codes": []
                }, mode=mode)

                return True
            else:
                return False

        except Exception as e:
            log.error(f"[自动检验] 检验凭证 {filename} 失败: {e}")
            return False

    async def trigger_verify_now(self) -> dict:
        """立即触发一次检验（供API调用）"""
        try:
            await self._check_and_verify_credentials()
            return {"success": True, "message": "检验完成"}
        except Exception as e:
            return {"success": False, "message": str(e)}


_auto_verify_service: Optional[AutoVerifyService] = None


async def get_auto_verify_service() -> AutoVerifyService:
    """获取自动检验服务实例"""
    global _auto_verify_service
    if _auto_verify_service is None:
        _auto_verify_service = AutoVerifyService()
    return _auto_verify_service


async def start_auto_verify_service():
    """启动自动检验服务"""
    service = await get_auto_verify_service()
    await service.start()


async def stop_auto_verify_service():
    """停止自动检验服务"""
    service = await get_auto_verify_service()
    await service.stop()
