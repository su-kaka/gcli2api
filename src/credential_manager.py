"""
凭证管理器 - 完全基于统一存储中间层
"""

import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import get_calls_per_rotation, is_mongodb_mode
from log import log

from .google_oauth_api import Credentials, fetch_user_email_from_file
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

        # 凭证轮换相关
        self._credential_files: List[str] = []  # 存储凭证文件名列表
        self._call_count = 0

        # 并发控制
        self._state_lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()

        # 原子操作计数器
        self._atomic_counter = 0
        self._atomic_lock = asyncio.Lock()

    async def initialize(self):
        """初始化凭证管理器"""
        async with self._state_lock:
            if self._initialized:
                return

            # 初始化统一存储适配器
            self._storage_adapter = await get_storage_adapter()

            # 发现并加载凭证
            await self._discover_credentials()

            self._initialized = True
            storage_type = "MongoDB" if await is_mongodb_mode() else "File"
            log.debug(f"Credential manager initialized with {storage_type} storage backend")

    async def close(self):
        """清理资源"""
        log.debug("Closing credential manager...")

        self._initialized = False
        log.debug("Credential manager closed")

    async def _discover_credentials(self):
        """发现和加载所有可用凭证（轮换顺序持久化）"""
        try:
            # 从存储适配器获取所有凭证
            all_credentials = await self._storage_adapter.list_credentials()

            # 过滤出可用的凭证（排除被禁用的）- 批量读取状态以提升性能
            available_credentials = []

            # 批量获取所有凭证状态，避免多次读取状态文件
            if all_credentials:
                try:
                    all_states = await self._storage_adapter.get_all_credential_states()

                    for credential_name in all_credentials:
                        normalized_name = credential_name
                        # 标准化文件名以匹配状态数据中的键
                        if hasattr(self._storage_adapter._backend, "_normalize_filename"):
                            normalized_name = self._storage_adapter._backend._normalize_filename(
                                credential_name
                            )

                        state = all_states.get(normalized_name, {})
                        if not state.get("disabled", False):
                            available_credentials.append(credential_name)
                except Exception as e:
                    log.warning(
                        f"Failed to batch load credential states, falling back to individual checks: {e}"
                    )
                    # 如果批量读取失败，回退到逐个检查
                    for credential_name in all_credentials:
                        try:
                            state = await self._storage_adapter.get_credential_state(
                                credential_name
                            )
                            if not state.get("disabled", False):
                                available_credentials.append(credential_name)
                        except Exception as e2:
                            log.warning(
                                f"Failed to check state for credential {credential_name}: {e2}"
                            )

            # 更新凭证列表 - 使用循环队列优化
            old_credentials = set(self._credential_files)
            new_credentials = set(available_credentials)

            if old_credentials != new_credentials:
                # 记录变化（只在非初始状态时记录）
                is_initial_load = len(old_credentials) == 0
                added = new_credentials - old_credentials
                removed = old_credentials - new_credentials

                # 优化：维护轮换顺序
                if is_initial_load:
                    # 初始加载：尝试从存储中恢复保存的顺序
                    try:
                        saved_order = await self._storage_adapter.get_credential_order()
                        if saved_order:
                            # 过滤出仍然可用的凭证，保持顺序
                            valid_saved = [c for c in saved_order if c in new_credentials]
                            # 添加新发现但不在保存顺序中的凭证到末尾
                            new_unsaved = [c for c in available_credentials if c not in saved_order]
                            self._credential_files = valid_saved + new_unsaved
                            log.debug(f"初始加载：恢复保存的凭证顺序，共 {len(self._credential_files)} 个凭证")
                        else:
                            # 没有保存的顺序，使用默认顺序
                            self._credential_files = available_credentials
                            log.debug(f"初始加载发现 {len(available_credentials)} 个可用凭证")
                    except Exception as e:
                        log.warning(f"无法恢复保存的凭证顺序: {e}，使用默认顺序")
                        self._credential_files = available_credentials
                else:
                    # 运行时更新：保留现有顺序，新凭证添加到末尾
                    # 1. 保留现有列表中仍然可用的凭证（保持顺序）
                    existing = [c for c in self._credential_files if c in new_credentials]
                    # 2. 新发现的凭证添加到末尾
                    new_only = [c for c in available_credentials if c not in old_credentials]
                    # 3. 合并：已有的保持顺序 + 新的加到末尾
                    self._credential_files = existing + new_only

                    # 记录变化
                    if added:
                        log.info(f"发现新的可用凭证（已添加到队列末尾）: {list(added)}")
                    if removed:
                        log.info(f"移除不可用凭证: {list(removed)}")

            if not self._credential_files:
                log.warning("No available credential files found")
            else:
                log.debug(f"Available credentials: {len(self._credential_files)} files")

        except Exception as e:
            log.error(f"Failed to discover credentials: {e}")

    async def _load_current_credential(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """加载当前凭证数据 - 始终使用第一个（索引 0）"""
        if not self._credential_files:
            return None

        try:
            # 始终使用第一个凭证（循环队列头部）
            current_file = self._credential_files[0]

            # 从存储适配器加载凭证数据
            credential_data = await self._storage_adapter.get_credential(current_file)
            if not credential_data:
                log.error(f"Failed to load credential data for: {current_file}")
                return None

            # 检查refresh_token
            if "refresh_token" not in credential_data or not credential_data["refresh_token"]:
                log.warning(f"No refresh token in {current_file}")
                return None

            # Auto-add 'type' field if missing but has required OAuth fields
            if "type" not in credential_data and all(
                key in credential_data for key in ["client_id", "refresh_token"]
            ):
                credential_data["type"] = "authorized_user"
                log.debug(f"Auto-added 'type' field to credential from file {current_file}")

            # 兼容不同的token字段格式
            if "access_token" in credential_data and "token" not in credential_data:
                credential_data["token"] = credential_data["access_token"]
            if "scope" in credential_data and "scopes" not in credential_data:
                credential_data["scopes"] = credential_data["scope"].split()

            # token过期检测和刷新
            should_refresh = await self._should_refresh_token(credential_data)

            if should_refresh:
                log.debug(f"Token需要刷新 - 文件: {current_file}")
                refreshed_data = await self._refresh_token(credential_data, current_file)
                if refreshed_data:
                    credential_data = refreshed_data
                    log.debug(f"Token刷新成功: {current_file}")
                else:
                    log.error(f"Token刷新失败: {current_file}")
                    return None

            # 不再缓存凭证数据，直接返回（storage_adapter已经有缓存）
            return current_file, credential_data

        except Exception as e:
            log.error(f"Error loading current credential: {e}")
            return None

    async def add_credential(self, credential_name: str, credential_data: Dict[str, Any]):
        """
        新增或更新一个凭证，并确保它进入轮换队列（如果未被禁用）。

        使用场景：
        - 业务侧只需调用此 API，而不直接操作 storage_adapter。
        - 新凭证会立即参与轮换，无需等待后台轮询。
        """
        async with self._operation_lock:
            # 1. 写入凭证内容
            await self._storage_adapter.store_credential(credential_name, credential_data)

            # 2. 读取状态，判断是否禁用
            state = await self._storage_adapter.get_credential_state(credential_name)
            disabled = state.get("disabled", False)

            # 3. 如果未禁用，确保在队列中出现一次（若已存在则保持原先顺序）
            if not disabled:
                if credential_name not in self._credential_files:
                    self._credential_files.append(credential_name)
                    # 顺序持久化
                    try:
                        await self._storage_adapter.set_credential_order(self._credential_files)
                    except Exception as e:
                        log.warning(f"无法保存凭证顺序（add_credential）: {e}")
                # 如果已经在队列里，则不动它的位置，保持既有轮换顺序

            log.info(
                f"Credential added/updated via manager: {credential_name}, "
                f"disabled={disabled}"
            )

    async def remove_credential(self, credential_name: str) -> bool:
        """
        删除一个凭证：
        - 从存储中移除凭证（以及其状态，如果 storage_adapter 支持）
        - 从内存轮换队列中移除
        - 如有必要，切换当前凭证到下一个可用项
        """
        async with self._operation_lock:
            try:
                # 1. 从存储中删除凭证主体
                try:
                    await self._storage_adapter.delete_credential(credential_name)
                except AttributeError:
                    log.warning(
                        "storage_adapter 未实现 delete_credential，"
                        "仅从队列中移除，不删除底层文件/文档"
                    )

                # 2. 尝试删除对应状态
                try:
                    if hasattr(self._storage_adapter, "delete_credential_state"):
                        await self._storage_adapter.delete_credential_state(credential_name)
                except Exception as e:
                    log.warning(f"删除凭证状态失败 {credential_name}: {e}")

                # 3. 从队列中移除
                if credential_name in self._credential_files:
                    self._credential_files = [
                        c for c in self._credential_files if c != credential_name
                    ]
                    # 持久化新的顺序
                    try:
                        await self._storage_adapter.set_credential_order(self._credential_files)
                    except Exception as e:
                        log.warning(f"无法保存凭证顺序（remove_credential）: {e}")

                log.info(f"Credential removed via manager: {credential_name}")
                return True

            except Exception as e:
                log.error(f"Error removing credential {credential_name}: {e}")
                return False

    async def get_valid_credential(self) -> Optional[Tuple[str, Dict[str, Any]]]:
        """获取有效的凭证，自动处理轮换、失效凭证切换和冷却检查"""
        async with self._operation_lock:
            if not self._credential_files:
                await self._discover_credentials()
                if not self._credential_files:
                    return None

            tried: List[str] = []

            # 检查是否需要轮换
            if await self._should_rotate():
                await self._rotate_credential()

            # 动态循环：最多绕队列一圈
            while self._credential_files:
                current_file = self._credential_files[0]
                # 如果已经尝试过当前文件，说明我们绕了一圈，退出
                if current_file in tried:
                    log.error(
                        f"所有凭证都已尝试且无效，最后尝试: {current_file}, "
                        f"已尝试: {tried}"
                    )
                    return None

                tried.append(current_file)
                try:
                    # 检查凭证是否在冷却期
                    if await self._is_credential_in_cooldown(current_file):
                        log.info(f"凭证 {current_file} 在冷却期，跳过并轮换到下一个")
                        # 如果只有一个凭证，即使在冷却期也要返回（让上层处理）
                        if len(self._credential_files) == 1:
                            log.warning(
                                f"只有一个凭证 {current_file} 可用但在冷却期，仍然返回该凭证"
                            )
                            result = await self._load_current_credential()
                            if result:
                                return result
                        else:
                            # 有多个凭证，跳过冷却中的凭证
                            await self._rotate_credential()
                            continue

                    # 加载当前凭证
                    result = await self._load_current_credential()
                    if result:
                        return result

                    # 当前凭证加载失败，先轮换到队列尾，再标记为失效
                    log.warning(f"凭证失效，先轮换再禁用: {current_file}")

                    # 先将凭证移到队列尾部（如果有多个凭证）
                    if len(self._credential_files) > 1:
                        await self._rotate_credential()

                    # 再执行禁用操作（使用不加锁版本，因为已持有_operation_lock）
                    await self._set_cred_disabled_unlocked(current_file, True)

                    if not self._credential_files:
                        log.error("没有可用的凭证")
                        return None

                    log.info(f"切换到下一个可用凭证: {self._credential_files[0]}")

                except Exception as e:
                    log.error(
                        f"获取凭证时发生异常（当前: {current_file}, 已尝试: {tried}）: {e}"
                    )
                    # 异常时尝试轮换到下一个凭证继续
                    if len(self._credential_files) > 1:
                        await self._rotate_credential()
                    else:
                        return None

            log.error("credential_files 为空，无法获取有效凭证")
            return None

    async def _should_rotate(self) -> bool:
        """检查是否需要轮换凭证"""
        if not self._credential_files or len(self._credential_files) <= 1:
            return False

        current_calls_per_rotation = await get_calls_per_rotation()
        return self._call_count >= current_calls_per_rotation

    async def _rotate_credential(self):
        """轮换到下一个凭证 - 将当前凭证移到末尾（循环队列）"""
        if len(self._credential_files) <= 1:
            return

        # 将第一个凭证移到末尾，实现循环队列
        current = self._credential_files.pop(0)
        self._credential_files.append(current)

        self._call_count = 0

        # 持久化新的顺序
        try:
            await self._storage_adapter.set_credential_order(self._credential_files)
            log.info(f"轮换凭证: {current} -> 队列末尾，当前使用: {self._credential_files[0]}")
        except Exception as e:
            log.warning(f"无法保存凭证顺序: {e}")
            log.info(f"轮换凭证: {current} -> 队列末尾，当前使用: {self._credential_files[0]}")

    async def force_rotate_credential(self):
        """强制轮换到下一个凭证（用于429错误处理）"""
        async with self._operation_lock:
            if len(self._credential_files) <= 1:
                log.warning("Only one credential available, cannot rotate")
                return

            await self._rotate_credential()
            log.info("Forced credential rotation due to rate limit")

    async def increment_call_count(self):
        """增加调用计数（线程安全）"""
        async with self._state_lock:
            self._call_count += 1

    async def update_credential_state(self, credential_name: str, state_updates: Dict[str, Any]):
        """更新凭证状态（线程安全）"""
        async with self._state_lock:
            try:
                # 直接通过存储适配器更新状态
                success = await self._storage_adapter.update_credential_state(
                    credential_name, state_updates
                )

                if success:
                    log.debug(f"Updated credential state: {credential_name}")
                else:
                    log.warning(f"Failed to update credential state: {credential_name}")

                return success

            except Exception as e:
                log.error(f"Error updating credential state {credential_name}: {e}")
                return False

    async def _update_credential_state_unlocked(self, credential_name: str, state_updates: Dict[str, Any]):
        """更新凭证状态（内部方法，不加锁，供已加锁的方法调用）"""
        try:
            # 直接通过存储适配器更新状态
            success = await self._storage_adapter.update_credential_state(
                credential_name, state_updates
            )

            if success:
                log.debug(f"Updated credential state: {credential_name}")
            else:
                log.warning(f"Failed to update credential state: {credential_name}")

            return success

        except Exception as e:
            log.error(f"Error updating credential state {credential_name}: {e}")
            return False

    async def set_cred_disabled(self, credential_name: str, disabled: bool):
        """设置凭证的启用/禁用状态（线程安全）"""
        async with self._operation_lock:
            return await self._set_cred_disabled_unlocked(credential_name, disabled)

    async def _set_cred_disabled_unlocked(self, credential_name: str, disabled: bool):
        """设置凭证的启用/禁用状态（内部方法，不加锁，供已加锁的方法调用）"""
        try:
            state_updates = {"disabled": disabled}
            success = await self.update_credential_state(credential_name, state_updates)

            if success:
                action = "disabled" if disabled else "enabled"
                log.info(f"Credential {action}: {credential_name}")

                if disabled:
                    # 禁用凭证：从列表中移除
                    if credential_name in self._credential_files:
                        self._credential_files = [
                            c for c in self._credential_files if c != credential_name
                        ]
                        log.debug(f"从轮换队列中移除凭证: {credential_name}")
                        # 持久化新的顺序
                        try:
                            await self._storage_adapter.set_credential_order(self._credential_files)
                        except Exception as e:
                            log.warning(f"无法保存凭证顺序（禁用凭证）: {e}")
                else:
                    # 启用凭证：直接加回列表（如果不存在）
                    if credential_name not in self._credential_files:
                        self._credential_files.append(credential_name)
                        log.info(f"凭证已加回轮换队列（队列末尾）: {credential_name}")
                        # 持久化新的顺序
                        try:
                            await self._storage_adapter.set_credential_order(self._credential_files)
                        except Exception as e:
                            log.warning(f"无法保存凭证顺序（启用凭证）: {e}")
                    else:
                        log.debug(f"凭证已在轮换队列中，无需重复添加: {credential_name}")

                log.debug(
                    f"凭证状态更新完成，当前可用凭证数: {len(self._credential_files)}"
                )

            return success

        except Exception as e:
            log.error(f"Error setting credential disabled state {credential_name}: {e}")
            return False

    async def get_creds_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证的状态（线程安全）"""
        async with self._state_lock:
            try:
                # 从存储适配器获取所有状态
                all_states = await self._storage_adapter.get_all_credential_states()
                return all_states

            except Exception as e:
                log.error(f"Error getting credential statuses: {e}")
                return {}

    async def _is_credential_in_cooldown(self, credential_name: str) -> bool:
        """
        检查凭证是否在冷却期内（内部方法，调用者需要持有适当的锁）

        Args:
            credential_name: 凭证名称

        Returns:
            True表示在冷却期，False表示已过冷却期或无冷却
        """
        try:
            state = await self._storage_adapter.get_credential_state(credential_name)
            cooldown_until = state.get("cooldown_until")

            if cooldown_until is None:
                return False

            # 检查 cooldown_until 的类型和值
            log.debug(
                f"[冷却期检查] 凭证: {credential_name}, "
                f"cooldown_until类型: {type(cooldown_until)}, "
                f"cooldown_until值: {cooldown_until}"
            )

            # 如果是字符串（ISO格式），需要转换为时间戳
            if isinstance(cooldown_until, str):
                try:
                    # 解析ISO格式时间
                    if cooldown_until.endswith("Z"):
                        cooldown_until = cooldown_until.replace("Z", "+00:00")
                    reset_dt = datetime.fromisoformat(cooldown_until)
                    if reset_dt.tzinfo is None:
                        reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                    # 转换为Unix时间戳（避免本地时区影响）
                    reset_dt_utc = reset_dt.astimezone(timezone.utc)
                    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
                    cooldown_until = (reset_dt_utc - epoch).total_seconds()

                    log.debug(
                        f"[冷却期检查] 将ISO时间转换为时间戳: {cooldown_until}"
                    )
                except Exception as parse_err:
                    log.error(
                        f"[冷却期检查] 解析ISO时间失败 {credential_name}: {parse_err}, "
                        f"原始值: {cooldown_until}"
                    )
                    return False

            # time.time() 返回的是正确的 UTC 时间戳
            current_time = time.time()
            log.debug(
                f"[冷却期检查] 当前时间: {current_time} "
                f"({datetime.fromtimestamp(current_time, timezone.utc).isoformat()}), "
                f"冷却截止: {cooldown_until} "
                f"({datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()})"
            )

            if current_time < cooldown_until:
                remaining = cooldown_until - current_time
                remaining_minutes = int(remaining / 60)
                log.info(
                    f"凭证 {credential_name} 仍在冷却期，"
                    f"剩余时间: {remaining_minutes}分{int(remaining % 60)}秒 "
                    f"(截止时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()})"
                )
                return True
            else:
                # 冷却期已过，清除冷却状态
                log.info(f"凭证 {credential_name} 冷却期已过，恢复可用")
                # 直接调用不加锁的更新方法（调用者已经持有operation_lock）
                try:
                    await self._storage_adapter.update_credential_state(
                        credential_name, {"cooldown_until": None}
                    )
                except Exception as clear_err:
                    log.warning(f"清除冷却状态失败 {credential_name}: {clear_err}")
                return False

        except Exception as e:
            log.error(f"检查凭证冷却状态失败 {credential_name}: {e}")
            # 出错时默认认为不在冷却期
            return False

    async def get_or_fetch_user_email(self, credential_name: str) -> Optional[str]:
        """获取或获取用户邮箱地址（线程安全）"""
        async with self._state_lock:
            try:
                # 首先检查缓存的状态
                state = await self._storage_adapter.get_credential_state(credential_name)
                cached_email = state.get("user_email")

                if cached_email:
                    return cached_email

                # 如果没有缓存，从凭证数据获取
                credential_data = await self._storage_adapter.get_credential(credential_name)
                if not credential_data:
                    return None

                # 尝试获取邮箱
                email = await fetch_user_email_from_file(credential_data)

                if email:
                    # 缓存邮箱地址（使用内部不加锁版本避免死锁）
                    await self._update_credential_state_unlocked(credential_name, {"user_email": email})
                    return email

                return None

            except Exception as e:
                log.error(f"Error fetching user email for {credential_name}: {e}")
                return None

    async def record_api_call_result(
        self, credential_name: str, success: bool, error_code: Optional[int] = None,
        cooldown_until: Optional[float] = None
    ):
        """
        记录API调用结果

        Args:
            credential_name: 凭证名称
            success: 是否成功
            error_code: 错误码（如果失败）
            cooldown_until: 冷却截止时间戳（Unix时间戳，针对429 QUOTA_EXHAUSTED）
        """
        try:
            state_updates = {}

            if success:
                state_updates["last_success"] = time.time()
                # 清除错误码和冷却时间（如果之前有的话）
                state_updates["error_codes"] = []
                state_updates["cooldown_until"] = None
            elif error_code:
                # 记录错误码
                current_state = await self._storage_adapter.get_credential_state(credential_name)
                error_codes = current_state.get("error_codes", [])

                if error_code not in error_codes:
                    error_codes.append(error_code)
                    # 限制错误码列表长度
                    if len(error_codes) > 10:
                        error_codes = error_codes[-10:]

                state_updates["error_codes"] = error_codes

                # 如果提供了冷却时间，记录到状态中
                if cooldown_until is not None:
                    state_updates["cooldown_until"] = cooldown_until
                    log.info(
                        f"设置凭证冷却: {credential_name}, "
                        f"冷却至: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                    )

            if state_updates:
                await self.update_credential_state(credential_name, state_updates)

        except Exception as e:
            log.error(f"Error recording API call result for {credential_name}: {e}")

    # 原子操作支持
    @asynccontextmanager
    async def _atomic_operation(self, operation_name: str):
        """原子操作上下文管理器"""
        async with self._atomic_lock:
            self._atomic_counter += 1
            operation_id = self._atomic_counter
            log.debug(f"开始原子操作[{operation_id}]: {operation_name}")

            try:
                yield operation_id
                log.debug(f"完成原子操作[{operation_id}]: {operation_name}")
            except Exception as e:
                log.error(f"原子操作[{operation_id}]失败: {operation_name} - {e}")
                raise

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

                log.debug(f"Token剩余时间: {int(time_left/60)}分钟")

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
        self, credential_data: Dict[str, Any], filename: str
    ) -> Optional[Dict[str, Any]]:
        """刷新token并更新存储"""
        try:
            # 创建Credentials对象
            creds = Credentials.from_dict(credential_data)

            # 检查是否可以刷新
            if not creds.refresh_token:
                log.error(f"没有refresh_token，无法刷新: {filename}")
                return None

            # 刷新token
            log.debug(f"正在刷新token: {filename}")
            await creds.refresh()

            # 更新凭证数据
            if creds.access_token:
                credential_data["access_token"] = creds.access_token
                # 保持兼容性
                credential_data["token"] = creds.access_token

            if creds.expires_at:
                credential_data["expiry"] = creds.expires_at.isoformat()

            # 保存到存储
            await self._storage_adapter.store_credential(filename, credential_data)
            log.info(f"Token刷新成功并已保存: {filename}")

            return credential_data

        except Exception as e:
            error_msg = str(e)
            log.error(f"Token刷新失败 {filename}: {error_msg}")

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
                    await self.record_api_call_result(filename, False, status_code)
                else:
                    await self.record_api_call_result(filename, False, 400)

                # 先轮换到队列尾，再禁用该凭证
                try:
                    # 如果有多个凭证且当前凭证在队列头，先轮换
                    if len(self._credential_files) > 1 and self._credential_files[0] == filename:
                        await self._rotate_credential()

                    # 再执行禁用操作（使用不加锁版本，因为调用者已持有_operation_lock）
                    disabled_ok = await self._set_cred_disabled_unlocked(filename, True)
                    if disabled_ok:
                        log.warning(
                            "永久失效凭证已禁用并刷新列表，当前可用凭证数: "
                            f"{len(self._credential_files)}"
                        )
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
