"""
凭证管理器
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from log import log

from .google_oauth_api import Credentials
from .storage_adapter import get_storage_adapter

class CredentialManager:
    """
    统一凭证管理器
    所有存储操作通过storage_adapter进行
    """

    def __init__(self):
        # 核心状态
        self._initialized = False
        self._storage_adapter = None

        # 并发控制（简化）
        self._operation_lock = asyncio.Lock()

    async def _ensure_initialized(self):
        """确保管理器已初始化（内部使用）"""
        if not self._initialized or self._storage_adapter is None:
            await self.initialize()

    async def initialize(self):
        """初始化凭证管理器"""
        async with self._operation_lock:
            if self._initialized and self._storage_adapter is not None:
                return

            # 初始化统一存储适配器
            self._storage_adapter = await get_storage_adapter()
            self._initialized = True

    async def close(self):
        """清理资源"""
        log.debug("Closing credential manager...")
        self._initialized = False
        log.debug("Credential manager closed")

    async def get_valid_credential(
        self, mode: str = "geminicli", model_key: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        获取有效的凭证 - 随机负载均衡版
        每次随机选择一个可用的凭证（未禁用、未冷却）

        Args:
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            model_key: 模型键，用于模型级冷却检查
                      - antigravity: 模型名称（如 "gemini-2.0-flash-exp"）
                      - gcli: "pro" 或 "flash"
        """
        await self._ensure_initialized()
        async with self._operation_lock:
            # 使用 SQL 随机查询获取可用凭证
            if hasattr(self._storage_adapter._backend, 'get_next_available_credential'):
                # SQLite 后端：直接用智能 SQL（已经是随机选择）
                result = await self._storage_adapter._backend.get_next_available_credential(
                    mode=mode, model_key=model_key
                )
                if result:
                    filename, credential_data = result
                    # Token 刷新检查
                    if await self._should_refresh_token(credential_data):
                        log.debug(f"Token需要刷新 - 文件: {filename} (mode={mode})")
                        refreshed_data = await self._refresh_token(credential_data, filename, mode=mode)
                        if refreshed_data:
                            credential_data = refreshed_data
                            log.debug(f"Token刷新成功: {filename} (mode={mode})")
                        else:
                            log.error(f"Token刷新失败: {filename} (mode={mode})")
                            return None
                    return filename, credential_data
                return None
            else:
                # MongoDB/Postgres 后端：使用传统方法（随机选择）
                return await self._get_valid_credential_traditional(
                    mode=mode, model_key=model_key
                )

    async def _get_valid_credential_traditional(
        self, mode: str = "geminicli", model_key: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        传统方式获取凭证（用于 MongoDB/Postgres 后端）- 随机选择

        Args:
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            model_key: 模型键，用于模型级冷却检查
        """
        import random

        all_creds = await self._storage_adapter.list_credentials(mode=mode)
        if not all_creds:
            return None

        # 随机打乱凭证列表
        random.shuffle(all_creds)

        for filename in all_creds:
            try:
                # 检查禁用状态
                state = await self._storage_adapter.get_credential_state(filename, mode=mode)
                if state.get("disabled", False):
                    continue

                # 如果提供了 model_key，检查模型级冷却
                if model_key:
                    model_cooldowns = state.get("model_cooldowns", {})
                    model_cooldown = model_cooldowns.get(model_key)

                    if model_cooldown is not None:
                        current_time = time.time()
                        if current_time < model_cooldown:
                            # 该模型仍在冷却中
                            continue

                # 加载凭证
                credential_data = await self._storage_adapter.get_credential(filename, mode=mode)
                if not credential_data:
                    continue

                # Token 刷新
                if await self._should_refresh_token(credential_data):
                    refreshed_data = await self._refresh_token(credential_data, filename, mode=mode)
                    if refreshed_data:
                        credential_data = refreshed_data
                    else:
                        continue

                return filename, credential_data

            except Exception as e:
                log.error(f"Error checking credential {filename} (mode={mode}): {e}")
                continue

        return None

    async def add_credential(self, credential_name: str, credential_data: Dict[str, Any]):
        """
        新增或更新一个凭证
        存储层会自动处理轮换顺序
        """
        await self._ensure_initialized()
        async with self._operation_lock:
            await self._storage_adapter.store_credential(credential_name, credential_data)
            log.info(f"Credential added/updated: {credential_name}")

    async def add_antigravity_credential(self, credential_name: str, credential_data: Dict[str, Any]):
        """
        新增或更新一个Antigravity凭证
        存储层会自动处理轮换顺序
        """
        await self._ensure_initialized()
        async with self._operation_lock:
            await self._storage_adapter.store_credential(credential_name, credential_data, mode="antigravity")
            log.info(f"Antigravity credential added/updated: {credential_name}")

    async def remove_credential(self, credential_name: str, mode: str = "geminicli") -> bool:
        """删除一个凭证"""
        await self._ensure_initialized()
        async with self._operation_lock:
            try:
                await self._storage_adapter.delete_credential(credential_name, mode=mode)
                log.info(f"Credential removed: {credential_name} (mode={mode})")
                return True
            except Exception as e:
                log.error(f"Error removing credential {credential_name}: {e}")
                return False

    async def update_credential_state(self, credential_name: str, state_updates: Dict[str, Any], mode: str = "geminicli"):
        """更新凭证状态"""
        log.debug(f"[CredMgr] update_credential_state 开始: credential_name={credential_name}, state_updates={state_updates}, mode={mode}")
        log.debug(f"[CredMgr] 调用 _ensure_initialized...")
        await self._ensure_initialized()
        log.debug(f"[CredMgr] _ensure_initialized 完成")
        try:
            log.debug(f"[CredMgr] 调用 storage_adapter.update_credential_state...")
            success = await self._storage_adapter.update_credential_state(
                credential_name, state_updates, mode=mode
            )
            log.debug(f"[CredMgr] storage_adapter.update_credential_state 返回: {success}")
            if success:
                log.debug(f"Updated credential state: {credential_name} (mode={mode})")
            else:
                log.warning(f"Failed to update credential state: {credential_name} (mode={mode})")
            return success
        except Exception as e:
            log.error(f"Error updating credential state {credential_name}: {e}", exc_info=True)
            return False

    async def set_cred_disabled(self, credential_name: str, disabled: bool, mode: str = "geminicli"):
        """设置凭证的启用/禁用状态"""
        try:
            log.info(f"[CredMgr] set_cred_disabled 开始: credential_name={credential_name}, disabled={disabled}, mode={mode}")
            success = await self.update_credential_state(
                credential_name, {"disabled": disabled}, mode=mode
            )
            log.info(f"[CredMgr] update_credential_state 返回: success={success}")
            if success:
                action = "disabled" if disabled else "enabled"
                log.info(f"Credential {action}: {credential_name} (mode={mode})")
            else:
                log.warning(f"[CredMgr] 设置禁用状态失败: credential_name={credential_name}, disabled={disabled}")
            return success
        except Exception as e:
            log.error(f"Error setting credential disabled state {credential_name}: {e}")
            return False

    async def get_creds_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证的状态"""
        await self._ensure_initialized()
        try:
            return await self._storage_adapter.get_all_credential_states()
        except Exception as e:
            log.error(f"Error getting credential statuses: {e}")
            return {}

    async def get_creds_summary(self) -> List[Dict[str, Any]]:
        """
        获取所有凭证的摘要信息（轻量级，不包含完整凭证数据）
        优先使用后端的高性能查询
        """
        await self._ensure_initialized()
        try:
            # 如果后端支持高性能摘要查询，直接使用
            if hasattr(self._storage_adapter._backend, 'get_credentials_summary'):
                return await self._storage_adapter._backend.get_credentials_summary()

            # 否则回退到传统方式
            all_states = await self._storage_adapter.get_all_credential_states()
            summaries = []

            import time
            current_time = time.time()

            for filename, state in all_states.items():
                summaries.append({
                    "filename": filename,
                    "disabled": state.get("disabled", False),
                    "error_codes": state.get("error_codes", []),
                    "last_success": state.get("last_success", current_time),
                    "user_email": state.get("user_email"),
                    "model_cooldowns": state.get("model_cooldowns", {}),
                })

            return summaries

        except Exception as e:
            log.error(f"Error getting credentials summary: {e}")
            return []

    async def get_or_fetch_user_email(self, credential_name: str, mode: str = "geminicli") -> Optional[str]:
        """获取或获取用户邮箱地址"""
        try:
            # 确保已初始化
            await self._ensure_initialized()
            
            # 从状态中获取缓存的邮箱
            state = await self._storage_adapter.get_credential_state(credential_name, mode=mode)
            cached_email = state.get("user_email") if state else None

            if cached_email:
                return cached_email

            # 如果没有缓存，从凭证数据获取
            credential_data = await self._storage_adapter.get_credential(credential_name, mode=mode)
            if not credential_data:
                return None

            # 创建凭证对象并自动刷新 token
            from .google_oauth_api import Credentials, get_user_email

            credentials = Credentials.from_dict(credential_data)
            if not credentials:
                return None

            # 自动刷新 token（如果需要）
            token_refreshed = await credentials.refresh_if_needed()

            # 如果 token 被刷新了，更新存储
            if token_refreshed:
                log.info(f"Token已自动刷新: {credential_name} (mode={mode})")
                updated_data = credentials.to_dict()
                await self._storage_adapter.store_credential(credential_name, updated_data, mode=mode)

            # 获取邮箱
            email = await get_user_email(credentials)

            if email:
                # 缓存邮箱地址
                await self._storage_adapter.update_credential_state(
                    credential_name, {"user_email": email}, mode=mode
                )
                return email

            return None

        except Exception as e:
            log.error(f"Error fetching user email for {credential_name}: {e}")
            return None

    async def record_api_call_result(
        self,
        credential_name: str,
        success: bool,
        error_code: Optional[int] = None,
        cooldown_until: Optional[float] = None,
        mode: str = "geminicli",
        model_key: Optional[str] = None
    ):
        """
        记录API调用结果

        Args:
            credential_name: 凭证名称
            success: 是否成功
            error_code: 错误码（如果失败）
            cooldown_until: 冷却截止时间戳（Unix时间戳，针对429 QUOTA_EXHAUSTED）
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            model_key: 模型键（用于设置模型级冷却）
        """
        await self._ensure_initialized()
        try:
            state_updates = {}

            if success:
                state_updates["last_success"] = time.time()
                # 清除错误码
                state_updates["error_codes"] = []

                # 如果提供了 model_key，清除该模型的冷却
                if model_key:
                    if hasattr(self._storage_adapter._backend, 'set_model_cooldown'):
                        await self._storage_adapter._backend.set_model_cooldown(
                            credential_name, model_key, None, mode=mode
                        )

            elif error_code:
                # 记录错误码
                current_state = await self._storage_adapter.get_credential_state(credential_name, mode=mode)
                error_codes = current_state.get("error_codes", [])

                if error_code not in error_codes:
                    error_codes.append(error_code)
                    # 限制错误码列表长度
                    if len(error_codes) > 10:
                        error_codes = error_codes[-10:]

                state_updates["error_codes"] = error_codes

                # 如果提供了冷却时间和模型键，设置模型级冷却
                if cooldown_until is not None and model_key:
                    if hasattr(self._storage_adapter._backend, 'set_model_cooldown'):
                        await self._storage_adapter._backend.set_model_cooldown(
                            credential_name, model_key, cooldown_until, mode=mode
                        )
                        log.info(
                            f"设置模型级冷却: {credential_name}, model_key={model_key}, "
                            f"冷却至: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                        )

            if state_updates:
                await self.update_credential_state(credential_name, state_updates, mode=mode)

        except Exception as e:
            log.error(f"Error recording API call result for {credential_name}: {e}")

    async def _should_refresh_token(self, credential_data: Dict[str, Any]) -> bool:
        """检查token是否需要刷新"""
        try:
            # 如果没有access_token或过期时间，需要刷新
            if not credential_data.get("access_token") and not credential_data.get("token"):
                log.debug("没有access_token，需要刷新")
                return True

            expiry_str = credential_data.get("expiry")
            if not expiry_str:
                log.debug("没有过期时间，需要刷新")
                return True

            # 解析过期时间
            try:
                if isinstance(expiry_str, str):
                    if "+" in expiry_str:
                        file_expiry = datetime.fromisoformat(expiry_str)
                    elif expiry_str.endswith("Z"):
                        file_expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    else:
                        file_expiry = datetime.fromisoformat(expiry_str)
                else:
                    log.debug("过期时间格式无效，需要刷新")
                    return True

                # 确保时区信息
                if file_expiry.tzinfo is None:
                    file_expiry = file_expiry.replace(tzinfo=timezone.utc)

                # 检查是否还有至少5分钟有效期
                now = datetime.now(timezone.utc)
                time_left = (file_expiry - now).total_seconds()

                log.debug(
                    f"Token时间检查: "
                    f"当前UTC时间={now.isoformat()}, "
                    f"过期时间={file_expiry.isoformat()}, "
                    f"剩余时间={int(time_left/60)}分{int(time_left%60)}秒"
                )

                if time_left > 300:  # 5分钟缓冲
                    return False
                else:
                    log.debug(f"Token即将过期（剩余{int(time_left/60)}分钟），需要刷新")
                    return True

            except Exception as e:
                log.warning(f"解析过期时间失败: {e}，需要刷新")
                return True

        except Exception as e:
            log.error(f"检查token过期时出错: {e}")
            return True

    async def _refresh_token(
        self, credential_data: Dict[str, Any], filename: str, mode: str = "geminicli"
    ) -> Optional[Dict[str, Any]]:
        """刷新token并更新存储"""
        await self._ensure_initialized()
        try:
            # 创建Credentials对象
            creds = Credentials.from_dict(credential_data)

            # 检查是否可以刷新
            if not creds.refresh_token:
                log.error(f"没有refresh_token，无法刷新: {filename} (mode={mode})")
                # 自动禁用没有refresh_token的凭证
                try:
                    await self.update_credential_state(filename, {"disabled": True}, mode=mode)
                    log.warning(f"凭证已自动禁用（缺少refresh_token）: {filename}")
                except Exception as e:
                    log.error(f"禁用凭证失败 {filename}: {e}")
                return None

            # 刷新token
            log.debug(f"正在刷新token: {filename} (mode={mode})")
            await creds.refresh()

            # 更新凭证数据
            if creds.access_token:
                credential_data["access_token"] = creds.access_token
                # 保持兼容性
                credential_data["token"] = creds.access_token

            if creds.expires_at:
                credential_data["expiry"] = creds.expires_at.isoformat()

            # 保存到存储
            await self._storage_adapter.store_credential(filename, credential_data, mode=mode)
            log.info(f"Token刷新成功并已保存: {filename} (mode={mode})")

            return credential_data

        except Exception as e:
            error_msg = str(e)
            log.error(f"Token刷新失败 {filename} (mode={mode}): {error_msg}")

            # 尝试提取HTTP状态码（TokenError可能携带status_code属性）
            status_code = None
            if hasattr(e, 'status_code'):
                status_code = e.status_code

            # 检查是否是凭证永久失效的错误（只有明确的400/403等才判定为永久失效）
            is_permanent_failure = self._is_permanent_refresh_failure(error_msg, status_code)

            if is_permanent_failure:
                log.warning(f"检测到凭证永久失效 (HTTP {status_code}): {filename}")
                # 记录失效状态
                if status_code:
                    await self.record_api_call_result(filename, False, status_code, mode=mode)
                else:
                    await self.record_api_call_result(filename, False, 400, mode=mode)

                # 禁用失效凭证
                try:
                    # 直接禁用该凭证（随机选择机制会自动跳过它）
                    disabled_ok = await self.update_credential_state(filename, {"disabled": True}, mode=mode)
                    if disabled_ok:
                        log.warning(f"永久失效凭证已禁用: {filename}")
                    else:
                        log.warning("永久失效凭证禁用失败，将由上层逻辑继续处理")
                except Exception as e2:
                    log.error(f"禁用永久失效凭证时出错 {filename}: {e2}")
            else:
                # 网络错误或其他临时性错误，不封禁凭证
                log.warning(f"Token刷新失败但非永久性错误 (HTTP {status_code})，不封禁凭证: {filename}")

            return None

    def _is_permanent_refresh_failure(self, error_msg: str, status_code: Optional[int] = None) -> bool:
        """
        判断是否是凭证永久失效的错误

        Args:
            error_msg: 错误信息
            status_code: HTTP状态码（如果有）

        Returns:
            True表示凭证永久失效应封禁，False表示临时错误不应封禁
        """
        # 优先使用HTTP状态码判断
        if status_code is not None:
            # 400/401/403 明确表示凭证有问题，应该封禁
            if status_code in [400, 401, 403]:
                log.debug(f"检测到客户端错误状态码 {status_code}，判定为永久失效")
                return True
            # 500/502/503/504 是服务器错误，不应封禁凭证
            elif status_code in [500, 502, 503, 504]:
                log.debug(f"检测到服务器错误状态码 {status_code}，不应封禁凭证")
                return False
            # 429 (限流) 不应封禁凭证
            elif status_code == 429:
                log.debug("检测到限流错误 429，不应封禁凭证")
                return False

        # 如果没有状态码，回退到错误信息匹配（谨慎判断）
        # 只有明确的凭证失效错误才判定为永久失效
        permanent_error_patterns = [
            "invalid_grant",
            "refresh_token_expired",
            "invalid_refresh_token",
            "unauthorized_client",
            "access_denied",
        ]

        error_msg_lower = error_msg.lower()
        for pattern in permanent_error_patterns:
            if pattern.lower() in error_msg_lower:
                log.debug(f"错误信息匹配到永久失效模式: {pattern}")
                return True

        # 默认认为是临时错误（如网络问题），不应封禁凭证
        log.debug("未匹配到明确的永久失效模式，判定为临时错误")
        return False

# 全局实例管理（保持兼容性）
_credential_manager: Optional[CredentialManager] = None


async def get_credential_manager() -> CredentialManager:
    """获取全局凭证管理器实例"""
    global _credential_manager

    if _credential_manager is None:
        _credential_manager = CredentialManager()
        await _credential_manager.initialize()

    return _credential_manager
