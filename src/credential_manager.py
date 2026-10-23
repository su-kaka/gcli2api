"""
凭证管理器
以 email 为唯一键管理 Antigravity 凭证：负载均衡选取、自动 token 刷新、错误记录与模型级冷却。
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from log import log

from src.google_oauth_api import Credentials
from src.storage.database import get_database


class CredentialManager:
    """凭证管理器（单例，直接操作数据库层）"""

    def __init__(self):
        self._initialized = False
        self._database = None

    async def _ensure_initialized(self):
        if not self._initialized or self._database is None:
            await self.initialize()

    async def initialize(self):
        if self._initialized and self._database is not None:
            return
        self._database = await get_database()
        self._initialized = True

    async def close(self):
        log.debug("Closing credential manager...")
        self._initialized = False
        log.debug("Credential manager closed")

    # ---------- 凭证选取 ----------

    async def get_valid_credential(
        self, model_name: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        获取可用凭证：随机负载均衡，自动跳过禁用/冷却凭证。
        Token 失效时自动刷新；刷新失败（凭证永久失效）自动禁用并尝试下一个。

        Returns:
            (email, credential_data) 或 None
        """
        await self._ensure_initialized()

        max_retries = 3
        for attempt in range(max_retries):
            result = await self._database.get_next_available_credential(model_name=model_name)

            if not result:
                if attempt == 0:
                    log.warning(f"没有可用凭证 (model_name={model_name})")
                return None

            email, credential_data = result

            if await self._should_refresh_token(credential_data):
                log.debug(f"Token需要刷新: {email}")
                refreshed = await self._refresh_token(credential_data, email)
                if refreshed:
                    return email, refreshed
                log.warning(
                    f"Token刷新失败，尝试下一个凭证: {email} (attempt={attempt + 1}/{max_retries})"
                )
                continue

            return email, credential_data

        log.error(f"重试{max_retries}次后仍无可用凭证 (model_name={model_name})")
        return None

    # ---------- 凭证增删改 ----------

    async def add_credential(self, email: str, credential_data: Dict[str, Any]):
        """新增或更新凭证（同 email 覆盖，自动去重）"""
        await self._ensure_initialized()
        await self._database.store_credential(email, credential_data)
        log.info(f"Credential added/updated: {email}")

    async def remove_credential(self, email: str) -> bool:
        """删除凭证"""
        await self._ensure_initialized()
        try:
            await self._database.delete_credential(email)
            log.info(f"Credential removed: {email}")
            return True
        except Exception as e:
            log.error(f"Error removing credential {email}: {e}")
            return False

    async def update_credential_state(
        self, email: str, state_updates: Dict[str, Any]
    ) -> bool:
        """更新凭证状态字段（disabled/error_codes/tier/project_id 等）"""
        await self._ensure_initialized()
        try:
            success = await self._database.update_credential_state(email, state_updates)
            if not success:
                log.warning(f"Failed to update credential state: {email}")
            return success
        except Exception as e:
            log.error(f"Error updating credential state {email}: {e}")
            return False

    async def set_cred_disabled(self, email: str, disabled: bool):
        """设置凭证的启用/禁用状态"""
        success = await self.update_credential_state(email, {"disabled": disabled})
        if success:
            log.info(f"Credential {'disabled' if disabled else 'enabled'}: {email}")
        return success

    # ---------- API 调用结果记录 ----------

    async def record_api_call_result(
        self,
        email: str,
        success: bool,
        error_code: Optional[int] = None,
        cooldown_until: Optional[float] = None,
        model_name: Optional[str] = None,
        error_message: Optional[str] = None,
    ):
        """
        记录 API 调用结果：
        - 成功：清除错误状态与该模型的冷却（fire-and-forget）
        - 失败：记录错误码/错误信息，必要时设置模型级冷却
        """
        await self._ensure_initialized()
        try:
            if success:
                asyncio.create_task(
                    self._database.record_success(email, model_name=model_name)
                )
            elif error_code:
                error_messages = {}
                if error_message:
                    error_messages[str(error_code)] = error_message

                await self.update_credential_state(email, {
                    "error_codes": [error_code],
                    "error_messages": error_messages,
                })

                if cooldown_until is not None and model_name:
                    await self._database.set_model_cooldown(
                        email, model_name, cooldown_until
                    )
                    log.info(
                        f"设置模型级冷却: {email}, model={model_name}, "
                        f"冷却至: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                    )
        except Exception as e:
            log.error(f"Error recording API call result for {email}: {e}")

    # ---------- Token 刷新 ----------

    async def _should_refresh_token(self, credential_data: Dict[str, Any]) -> bool:
        """检查 token 是否需要刷新（剩余有效期不足 5 分钟视为需要刷新）"""
        access_token = credential_data.get("access_token") or credential_data.get("token")
        if not access_token:
            return True

        expiry_str = credential_data.get("expiry")
        if not expiry_str:
            return True

        try:
            if not isinstance(expiry_str, str):
                return True
            if "+" in expiry_str:
                expiry = datetime.fromisoformat(expiry_str)
            elif expiry_str.endswith("Z"):
                expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
            else:
                expiry = datetime.fromisoformat(expiry_str)

            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=timezone.utc)

            time_left = (expiry - datetime.now(timezone.utc)).total_seconds()
            return time_left <= 300  # 5 分钟缓冲

        except Exception as e:
            log.warning(f"解析过期时间失败: {e}，需要刷新")
            return True

    async def _refresh_token(
        self, credential_data: Dict[str, Any], email: str
    ) -> Optional[Dict[str, Any]]:
        """刷新 token 并保存；凭证永久失效时自动禁用，返回 None"""
        await self._ensure_initialized()
        try:
            creds = Credentials.from_dict(credential_data)

            if not creds.refresh_token:
                log.error(f"没有refresh_token，无法刷新: {email}")
                await self.update_credential_state(email, {"disabled": True})
                log.warning(f"凭证已自动禁用（缺少refresh_token）: {email}")
                return None

            log.debug(f"正在刷新token: {email}")
            await creds.refresh()

            if creds.access_token:
                credential_data["access_token"] = creds.access_token
                credential_data["token"] = creds.access_token
            if creds.expires_at:
                credential_data["expiry"] = creds.expires_at

            await self._database.store_credential(email, credential_data)
            log.info(f"Token刷新成功并已保存: {email}")
            return credential_data

        except Exception as e:
            error_msg = str(e)
            log.error(f"Token刷新失败 {email}: {error_msg}")

            status_code = getattr(e, "status_code", None)

            if self._is_permanent_refresh_failure(error_msg, status_code):
                log.warning(f"检测到凭证永久失效 (HTTP {status_code}): {email}")
                await self.record_api_call_result(
                    email, False, error_code=status_code or 400
                )
                if await self.update_credential_state(email, {"disabled": True}):
                    log.warning(f"永久失效凭证已禁用: {email}")
            else:
                log.warning(f"Token刷新失败但非永久性错误 (HTTP {status_code})，不封禁凭证: {email}")

            return None

    @staticmethod
    def _is_permanent_refresh_failure(error_msg: str, status_code: Optional[int] = None) -> bool:
        """判断是否是凭证永久失效（客户端错误/明确失效错误）"""
        if status_code is not None:
            if status_code in (400, 401, 403):
                return True
            if status_code in (500, 502, 503, 504):
                return False
            if status_code == 429:
                return False

        permanent_patterns = (
            "invalid_grant",
            "refresh_token_expired",
            "invalid_refresh_token",
            "unauthorized_client",
            "access_denied",
        )
        error_msg_lower = error_msg.lower()
        return any(p in error_msg_lower for p in permanent_patterns)


class _CredentialManagerSingleton:
    """单例包装器，支持懒加载"""

    _instance: Optional[CredentialManager] = None

    async def _get_or_create(self) -> CredentialManager:
        if self._instance is None:
            self._instance = CredentialManager()
            await self._instance.initialize()
            log.debug("CredentialManager singleton initialized")
        return self._instance

    def __getattr__(self, name):
        async def _async_wrapper(*args, **kwargs):
            manager = await self._get_or_create()
            return await getattr(manager, name)(*args, **kwargs)
        return _async_wrapper


# 全局单例
credential_manager = _CredentialManagerSingleton()
