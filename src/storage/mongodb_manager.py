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
        "cooldown_until",
    }

    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        self._initialized = False

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

            self._initialized = True
            log.info(f"MongoDB storage initialized (database: {database_name})")

        except Exception as e:
            log.error(f"Error initializing MongoDB: {e}")
            raise

    async def _create_indexes(self):
        """创建索引"""
        credentials_collection = self._db["credentials"]

        # 创建唯一索引
        await credentials_collection.create_index("filename", unique=True)
        await credentials_collection.create_index("disabled")
        await credentials_collection.create_index("cooldown_until")
        await credentials_collection.create_index("rotation_order")

        log.debug("MongoDB indexes created")

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

    # ============ StorageBackend 协议方法 ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        """存储或更新凭证"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]

            # 检查是否存在
            existing = await credentials_collection.find_one({"filename": filename})

            if existing:
                # 更新凭证数据，保留状态
                await credentials_collection.update_one(
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
                max_doc = await credentials_collection.find_one(
                    {}, sort=[("rotation_order", -1)]
                )
                next_order = (max_doc["rotation_order"] + 1) if max_doc else 0

                # 插入新凭证
                await credentials_collection.insert_one(
                    {
                        "filename": filename,
                        "credential_data": credential_data,
                        "disabled": False,
                        "error_codes": [],
                        "last_success": time.time(),
                        "user_email": None,
                        "cooldown_until": None,
                        "rotation_order": next_order,
                        "call_count": 0,
                        "created_at": time.time(),
                        "updated_at": time.time(),
                    }
                )

            log.debug(f"Stored credential: {filename}")
            return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]
            doc = await credentials_collection.find_one({"filename": filename})

            if doc:
                return doc.get("credential_data")
            return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self) -> List[str]:
        """列出所有凭证文件名"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]
            cursor = credentials_collection.find({}, {"filename": 1}).sort(
                "rotation_order", 1
            )

            filenames = []
            async for doc in cursor:
                filenames.append(doc["filename"])

            return filenames

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str) -> bool:
        """删除凭证"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]
            result = await credentials_collection.delete_one({"filename": filename})

            log.debug(f"Deleted credential: {filename}")
            return result.deleted_count > 0

        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False

    async def update_credential_state(
        self, filename: str, state_updates: Dict[str, Any]
    ) -> bool:
        """更新凭证状态"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]

            # 过滤只更新状态字段
            valid_updates = {
                k: v for k, v in state_updates.items() if k in self.STATE_FIELDS
            }

            if not valid_updates:
                return True

            valid_updates["updated_at"] = time.time()

            result = await credentials_collection.update_one(
                {"filename": filename}, {"$set": valid_updates}
            )

            return result.modified_count > 0 or result.matched_count > 0

        except Exception as e:
            log.error(f"Error updating credential state {filename}: {e}")
            return False

    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        """获取凭证状态"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]
            doc = await credentials_collection.find_one({"filename": filename})

            if doc:
                return {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", time.time()),
                    "user_email": doc.get("user_email"),
                    "cooldown_until": doc.get("cooldown_until"),
                }

            # 返回默认状态
            return {
                "disabled": False,
                "error_codes": [],
                "last_success": time.time(),
                "user_email": None,
                "cooldown_until": None,
            }

        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {}

    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        self._ensure_initialized()

        try:
            credentials_collection = self._db["credentials"]
            cursor = credentials_collection.find({})

            states = {}
            async for doc in cursor:
                filename = doc["filename"]
                states[filename] = {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", time.time()),
                    "user_email": doc.get("user_email"),
                    "cooldown_until": doc.get("cooldown_until"),
                }

            return states

        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}

    # ============ 配置管理 ============

    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            await config_collection.update_one(
                {"key": key},
                {"$set": {"value": value, "updated_at": time.time()}},
                upsert=True,
            )
            return True

        except Exception as e:
            log.error(f"Error setting config {key}: {e}")
            return False

    async def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            doc = await config_collection.find_one({"key": key})

            if doc:
                return doc.get("value", default)
            return default

        except Exception as e:
            log.error(f"Error getting config {key}: {e}")
            return default

    async def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            cursor = config_collection.find({})

            config = {}
            async for doc in cursor:
                config[doc["key"]] = doc.get("value")

            return config

        except Exception as e:
            log.error(f"Error getting all config: {e}")
            return {}

    async def delete_config(self, key: str) -> bool:
        """删除配置"""
        self._ensure_initialized()

        try:
            config_collection = self._db["config"]
            result = await config_collection.delete_one({"key": key})
            return result.deleted_count > 0

        except Exception as e:
            log.error(f"Error deleting config {key}: {e}")
            return False
