"""
MongoDB 存储管理器
"""

import time
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from log import log


class MongoDBManager:
    """MongoDB 数据库管理器"""

    # 状态字段常量
    STATE_FIELDS = {
        "error_codes",
        "disabled",
        "last_success",
        "user_email",
        "model_cooldowns",
    }

    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        self._initialized = False

        # 内存配置缓存 - 初始化时加载一次
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

    async def initialize(self) -> None:
        """初始化 MongoDB 连接"""
        if self._initialized:
            return

        try:
            import os

            mongodb_uri = os.getenv("MONGODB_URI")
            if not mongodb_uri:
                raise ValueError("MONGODB_URI environment variable not set")

            database_name = os.getenv("MONGODB_DATABASE", "gcli2api")

            self._client = AsyncIOMotorClient(mongodb_uri)
            self._db = self._client[database_name]

            # 测试连接
            await self._db.command("ping")

            # 创建索引
            await self._create_indexes()

            # 加载配置到内存
            await self._load_config_cache()

            self._initialized = True
            log.info(f"MongoDB storage initialized (database: {database_name})")

        except Exception as e:
            log.error(f"Error initializing MongoDB: {e}")
            raise

    async def _create_indexes(self):
        """创建索引"""
        credentials_collection = self._db["credentials"]
        antigravity_credentials_collection = self._db["antigravity_credentials"]

        # 创建普通凭证索引
        await credentials_collection.create_index("filename", unique=True)
        await credentials_collection.create_index("disabled")
        await credentials_collection.create_index("rotation_order")

        # 创建 Antigravity 凭证索引
        await antigravity_credentials_collection.create_index("filename", unique=True)
        await antigravity_credentials_collection.create_index("disabled")
        await antigravity_credentials_collection.create_index("rotation_order")

        log.debug("MongoDB indexes created")

    async def _load_config_cache(self):
        """加载配置到内存缓存（仅在初始化时调用一次）"""
        if self._config_loaded:
            return

        try:
            config_collection = self._db["config"]
            cursor = config_collection.find({})

            async for doc in cursor:
                self._config_cache[doc["key"]] = doc.get("value")

            self._config_loaded = True
            log.debug(f"Loaded {len(self._config_cache)} config items into cache")

        except Exception as e:
            log.error(f"Error loading config cache: {e}")
            self._config_cache = {}

    async def close(self) -> None:
        """关闭 MongoDB 连接"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
        self._initialized = False
        log.debug("MongoDB storage closed")

    def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            raise RuntimeError("MongoDB manager not initialized")

    def _is_antigravity(self, filename: str) -> bool:
        """判断是否为 antigravity 凭证"""
        return filename.startswith("ag_")

    def _get_collection_name(self, is_antigravity: bool) -> str:
        """根据 is_antigravity 标志获取对应的集合名"""
        return "antigravity_credentials" if is_antigravity else "credentials"

    # ============ SQL 方法 ============

    async def get_next_available_credential(
        self, is_antigravity: bool = False, model_key: Optional[str] = None
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """
        随机获取一个可用凭证（负载均衡）
        - 未禁用
        - 如果提供了 model_key，还会检查模型级冷却
        - 随机选择

        Args:
            is_antigravity: 是否获取 antigravity 凭证（默认 False）
            model_key: 模型键（用于模型级冷却检查，antigravity 用模型名，gcli 用 pro/flash）

        Note:
            - 对于 antigravity: model_key 是具体模型名（如 "gemini-2.0-flash-exp"）
            - 对于 gcli: model_key 是 "pro" 或 "flash"
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            current_time = time.time()

            # 获取所有候选凭证（未禁用）
            query = {"disabled": False}

            # 使用 $sample 随机抽取
            pipeline = [
                {"$match": query},
                {"$sample": {"size": 100}}  # 随机抽取最多100个
            ]

            docs = await collection.aggregate(pipeline).to_list(length=100)

            # 如果没有提供 model_key，使用第一个可用凭证
            if not model_key:
                if docs:
                    doc = docs[0]
                    return doc["filename"], doc.get("credential_data")
                return None

            # 如果提供了 model_key，检查模型级冷却
            for doc in docs:
                model_cooldowns = doc.get("model_cooldowns", {})

                # 检查该模型是否在冷却中
                model_cooldown = model_cooldowns.get(model_key)
                if model_cooldown is None or current_time >= model_cooldown:
                    # 该模型未冷却或冷却已过期
                    return doc["filename"], doc.get("credential_data")

            return None

        except Exception as e:
            log.error(f"Error getting next available credential (antigravity={is_antigravity}, model_key={model_key}): {e}")
            return None

    async def get_available_credentials_list(self, is_antigravity: bool = False) -> List[str]:
        """
        获取所有可用凭证列表
        - 未禁用
        - 按轮换顺序排序
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]

            cursor = collection.find(
                {"disabled": False},
                {"filename": 1}
            ).sort("rotation_order", 1)

            filenames = []
            async for doc in cursor:
                filenames.append(doc["filename"])

            return filenames

        except Exception as e:
            log.error(f"Error getting available credentials list: {e}")
            return []

    async def check_and_clear_cooldowns(self) -> int:
        """
        批量清除已过期的模型级冷却
        返回清除的数量
        """
        self._ensure_initialized()

        try:
            # 直接调用模型级冷却清理方法
            cleared = 0
            cleared += await self.clear_expired_model_cooldowns(is_antigravity=False)
            cleared += await self.clear_expired_model_cooldowns(is_antigravity=True)
            return cleared

        except Exception as e:
            log.error(f"Error clearing cooldowns: {e}")
            return 0

    # ============ StorageBackend 协议方法 ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], is_antigravity: bool = False) -> bool:
        """存储或更新凭证"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]

            # 检查是否存在
            existing = await collection.find_one({"filename": filename})

            if existing:
                # 更新凭证数据，保留状态
                await collection.update_one(
                    {"filename": filename},
                    {
                        "$set": {
                            "credential_data": credential_data,
                            "updated_at": time.time(),
                        }
                    },
                )
            else:
                # 获取下一个 rotation_order
                max_doc = await collection.find_one(
                    {}, sort=[("rotation_order", -1)]
                )
                next_order = (max_doc["rotation_order"] + 1) if max_doc else 0

                # 插入新凭证
                await collection.insert_one(
                    {
                        "filename": filename,
                        "credential_data": credential_data,
                        "disabled": False,
                        "error_codes": [],
                        "last_success": time.time(),
                        "user_email": None,
                        "model_cooldowns": {},
                        "rotation_order": next_order,
                        "call_count": 0,
                        "created_at": time.time(),
                        "updated_at": time.time(),
                    }
                )

            log.debug(f"Stored credential: {filename} (antigravity={is_antigravity})")
            return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str, is_antigravity: bool = False) -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            doc = await collection.find_one({"filename": filename})

            if doc:
                return doc.get("credential_data")
            return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self, is_antigravity: bool = False) -> List[str]:
        """列出所有凭证文件名"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            cursor = collection.find({}, {"filename": 1}).sort(
                "rotation_order", 1
            )

            filenames = []
            async for doc in cursor:
                filenames.append(doc["filename"])

            return filenames

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str, is_antigravity: bool = False) -> bool:
        """删除凭证"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            result = await collection.delete_one({"filename": filename})

            log.debug(f"Deleted credential: {filename} (antigravity={is_antigravity})")
            return result.deleted_count > 0

        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False

    async def update_credential_state(
        self, filename: str, state_updates: Dict[str, Any], is_antigravity: bool = False
    ) -> bool:
        """更新凭证状态"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]

            # 过滤只更新状态字段
            valid_updates = {
                k: v for k, v in state_updates.items() if k in self.STATE_FIELDS
            }

            if not valid_updates:
                return True

            valid_updates["updated_at"] = time.time()

            result = await collection.update_one(
                {"filename": filename}, {"$set": valid_updates}
            )

            return result.modified_count > 0 or result.matched_count > 0

        except Exception as e:
            log.error(f"Error updating credential state {filename}: {e}")
            return False

    async def get_credential_state(self, filename: str, is_antigravity: bool = False) -> Dict[str, Any]:
        """获取凭证状态"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            doc = await collection.find_one({"filename": filename})

            if doc:
                return {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", time.time()),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": doc.get("model_cooldowns", {}),
                }

            # 返回默认状态
            return {
                "disabled": False,
                "error_codes": [],
                "last_success": time.time(),
                "user_email": None,
                "model_cooldowns": {},
            }

        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {}

    async def get_all_credential_states(self, is_antigravity: bool = False) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            cursor = collection.find({})

            states = {}
            current_time = time.time()

            async for doc in cursor:
                filename = doc["filename"]
                model_cooldowns = doc.get("model_cooldowns", {})

                # 自动过滤掉已过期的模型CD
                if model_cooldowns:
                    model_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if v > current_time
                    }

                states[filename] = {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", time.time()),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": model_cooldowns,
                }

            return states

        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}

    async def get_credentials_summary(
        self,
        offset: int = 0,
        limit: Optional[int] = None,
        status_filter: str = "all",
        is_antigravity: bool = False,
        error_code_filter: Optional[str] = None,
        cooldown_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取凭证的摘要信息（不包含完整凭证数据）- 支持分页和状态筛选

        Args:
            offset: 跳过的记录数（默认0）
            limit: 返回的最大记录数（None表示返回所有）
            status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
            is_antigravity: 是否查询antigravity凭证集合（默认False）
            error_code_filter: 错误码筛选（格式如"400"或"403"，筛选包含该错误码的凭证）
            cooldown_filter: 冷却状态筛选（"in_cooldown"=冷却中, "no_cooldown"=未冷却）

        Returns:
            包含 items（凭证列表）、total（总数）、offset、limit 的字典
        """
        self._ensure_initialized()

        try:
            # 根据 is_antigravity 选择集合名
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]

            # 构建查询条件
            query = {}
            if status_filter == "enabled":
                query["disabled"] = False
            elif status_filter == "disabled":
                query["disabled"] = True

            # 错误码筛选 - 兼容存储为数字或字符串的情况
            if error_code_filter and str(error_code_filter).strip().lower() != "all":
                filter_value = str(error_code_filter).strip()
                query_values = [filter_value]
                try:
                    query_values.append(int(filter_value))
                except ValueError:
                    pass
                query["error_codes"] = {"$in": query_values}

            # 计算全局统计数据（不受筛选条件影响）
            global_stats = {"total": 0, "normal": 0, "disabled": 0}
            stats_cursor = collection.find({})
            async for doc in stats_cursor:
                global_stats["total"] += 1
                if doc.get("disabled", False):
                    global_stats["disabled"] += 1
                else:
                    global_stats["normal"] += 1

            # 获取所有匹配的文档（用于冷却筛选，因为需要在Python中判断）
            cursor = collection.find(query).sort("rotation_order", 1)

            all_summaries = []
            current_time = time.time()

            async for doc in cursor:
                model_cooldowns = doc.get("model_cooldowns", {})

                # 自动过滤掉已过期的模型CD
                active_cooldowns = {}
                if model_cooldowns:
                    active_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if v > current_time
                    }

                summary = {
                    "filename": doc["filename"],
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", current_time),
                    "user_email": doc.get("user_email"),
                    "rotation_order": doc.get("rotation_order", 0),
                    "model_cooldowns": active_cooldowns,
                }

                # 应用冷却筛选
                if cooldown_filter == "in_cooldown":
                    # 只保留有冷却的凭证
                    if active_cooldowns:
                        all_summaries.append(summary)
                elif cooldown_filter == "no_cooldown":
                    # 只保留没有冷却的凭证
                    if not active_cooldowns:
                        all_summaries.append(summary)
                else:
                    # 不筛选冷却状态
                    all_summaries.append(summary)

            # 应用分页
            total_count = len(all_summaries)
            if limit is not None:
                summaries = all_summaries[offset:offset + limit]
            else:
                summaries = all_summaries[offset:]

            return {
                "items": summaries,
                "total": total_count,
                "offset": offset,
                "limit": limit,
                "stats": global_stats,
            }

        except Exception as e:
            log.error(f"Error getting credentials summary: {e}")
            return {
                "items": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
                "stats": {"total": 0, "normal": 0, "disabled": 0},
            }

    # ============ 配置管理（内存缓存）============

    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置（写入数据库 + 更新内存缓存）"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            await config_collection.update_one(
                {"key": key},
                {"$set": {"value": value, "updated_at": time.time()}},
                upsert=True,
            )

            # 更新内存缓存
            self._config_cache[key] = value
            return True

        except Exception as e:
            log.error(f"Error setting config {key}: {e}")
            return False

    async def reload_config_cache(self):
        """重新加载配置缓存（在批量修改配置后调用）"""
        self._ensure_initialized()
        self._config_loaded = False
        await self._load_config_cache()
        log.info("Config cache reloaded from database")

    async def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置（从内存缓存）"""
        self._ensure_initialized()
        return self._config_cache.get(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置（从内存缓存）"""
        self._ensure_initialized()
        return self._config_cache.copy()

    async def delete_config(self, key: str) -> bool:
        """删除配置"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            result = await config_collection.delete_one({"key": key})

            # 从内存缓存移除
            self._config_cache.pop(key, None)
            return result.deleted_count > 0

        except Exception as e:
            log.error(f"Error deleting config {key}: {e}")
            return False

    # ============ 模型级冷却管理 ============

    async def set_model_cooldown(
        self,
        filename: str,
        model_key: str,
        cooldown_until: Optional[float],
        is_antigravity: bool = False
    ) -> bool:
        """
        设置特定模型的冷却时间

        Args:
            filename: 凭证文件名
            model_key: 模型键（antigravity 用模型名，gcli 用 pro/flash）
            cooldown_until: 冷却截止时间戳（None 表示清除冷却）
            is_antigravity: 是否为 antigravity 凭证

        Returns:
            是否成功
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]

            # 获取当前的 model_cooldowns
            doc = await collection.find_one({"filename": filename})

            if not doc:
                log.warning(f"Credential {filename} not found")
                return False

            model_cooldowns = doc.get("model_cooldowns", {})

            # 更新或删除指定模型的冷却时间
            if cooldown_until is None:
                model_cooldowns.pop(model_key, None)
            else:
                model_cooldowns[model_key] = cooldown_until

            # 写回数据库
            await collection.update_one(
                {"filename": filename},
                {
                    "$set": {
                        "model_cooldowns": model_cooldowns,
                        "updated_at": time.time()
                    }
                }
            )

            log.debug(f"Set model cooldown: {filename}, model_key={model_key}, cooldown_until={cooldown_until}")
            return True

        except Exception as e:
            log.error(f"Error setting model cooldown for {filename}: {e}")
            return False

    async def clear_expired_model_cooldowns(self, is_antigravity: bool = False) -> int:
        """
        清除已过期的模型级冷却

        Args:
            is_antigravity: 是否为 antigravity 凭证集合

        Returns:
            清除的冷却项数量
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(is_antigravity)
            collection = self._db[collection_name]
            current_time = time.time()
            cleared_count = 0

            # 获取所有有 model_cooldowns 的凭证
            cursor = collection.find({"model_cooldowns": {"$ne": {}}})

            async for doc in cursor:
                filename = doc["filename"]
                model_cooldowns = doc.get("model_cooldowns", {})
                original_len = len(model_cooldowns)

                # 过滤掉已过期的冷却
                model_cooldowns = {
                    k: v for k, v in model_cooldowns.items()
                    if v > current_time
                }

                # 如果有变化，更新数据库
                if len(model_cooldowns) < original_len:
                    await collection.update_one(
                        {"filename": filename},
                        {
                            "$set": {
                                "model_cooldowns": model_cooldowns,
                                "updated_at": time.time()
                            }
                        }
                    )
                    cleared_count += (original_len - len(model_cooldowns))

            if cleared_count > 0:
                log.debug(f"Cleared {cleared_count} expired model cooldowns")

            return cleared_count

        except Exception as e:
            log.error(f"Error clearing expired model cooldowns: {e}")
            return 0
