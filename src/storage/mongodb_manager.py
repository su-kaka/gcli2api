"""
MongoDB 存储管理器
"""

import os
import random
import time
from typing import Any, Dict, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from log import log


class MongoDBManager:
    """MongoDB 数据库管理器"""

    # 状态字段常量
    STATE_FIELDS = {
        "error_codes",
        "error_messages",
        "disabled",
        "last_success",
        "user_email",
        "model_cooldowns",
        "preview",
    }

    @staticmethod
    def _escape_model_name(model_name: str) -> str:
        """
        转义模型名中的点号,避免 MongoDB 将其解释为嵌套结构

        Args:
            model_name: 原始模型名 (如 "gemini-2.5-flash")

        Returns:
            转义后的模型名 (如 "gemini-2-5-flash")
        """
        return model_name.replace(".", "-")

    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._db: Optional[AsyncIOMotorDatabase] = None
        self._initialized = False

        # 内存配置缓存 - 初始化时加载一次
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

        # Redis 缓存（仅当 REDIS_URL 环境变量存在时启用）
        self._redis = None
        self._redis_enabled: bool = False

    async def initialize(self) -> None:
        """初始化 MongoDB 连接"""
        if self._initialized:
            return

        try:
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

            # 尝试初始化 Redis（可选）
            await self._init_redis()

        except Exception as e:
            log.error(f"Error initializing MongoDB: {e}")
            raise

    async def _create_indexes(self):
        """
        创建索引
        """
        from pymongo import IndexModel, ASCENDING

        credentials_collection = self._db["credentials"]
        antigravity_credentials_collection = self._db["antigravity_credentials"]

        # ===== Geminicli 凭证索引 =====
        geminicli_indexes = [
            # 唯一索引 - 用于所有按文件名的精确查询
            IndexModel([("filename", ASCENDING)], unique=True, name="idx_filename_unique"),
            
            # 复合索引1 - 用于 get_next_available_credential
            # 查询模式: {disabled: False} + 可选 {preview: True/False} + $sample
            IndexModel(
                [("disabled", ASCENDING), ("preview", ASCENDING)],
                name="idx_disabled_preview"
            ),
            
            # 复合索引2 - 用于 get_available_credentials_list
            # 查询模式: {disabled: False} + sort by rotation_order
            IndexModel(
                [("disabled", ASCENDING), ("rotation_order", ASCENDING)],
                name="idx_disabled_rotation"
            ),
            
            # 单字段索引 - 用于 get_credentials_summary 的错误筛选
            IndexModel([("error_codes", ASCENDING)], name="idx_error_codes"),
            
            # 单字段索引 - 用于 get_duplicate_credentials_by_email 的去重查询
            IndexModel([("user_email", ASCENDING)], name="idx_user_email"),
        ]

        # ===== Antigravity 凭证索引 =====
        antigravity_indexes = [
            # 唯一索引
            IndexModel([("filename", ASCENDING)], unique=True, name="idx_filename_unique"),
            
            # 复合索引 - antigravity 不需要 preview 字段，一个复合索引即可覆盖主要场景
            # 查询模式: {disabled: False} + 可选 sort by rotation_order
            IndexModel(
                [("disabled", ASCENDING), ("rotation_order", ASCENDING)],
                name="idx_disabled_rotation"
            ),
            
            # 单字段索引 - 错误筛选
            IndexModel([("error_codes", ASCENDING)], name="idx_error_codes"),
            
            # 单字段索引 - 去重查询
            IndexModel([("user_email", ASCENDING)], name="idx_user_email"),
        ]

        # 并行创建新索引
        try:
            await credentials_collection.create_indexes(geminicli_indexes)
            await antigravity_credentials_collection.create_indexes(antigravity_indexes)
            log.debug("MongoDB indexes created successfully")
        except Exception as e:
            # 如果索引已存在，忽略错误
            if "already exists" not in str(e).lower():
                log.warning(f"Index creation warning: {e}")

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

    # ============ Redis 缓存（可选，仅当 REDIS_URL 存在时启用）============

    async def _init_redis(self) -> None:
        """初始化 Redis 连接并重建凭证池缓存（若 REDIS_URL 存在）"""
        redis_url = os.getenv("REDIS_URL")
        if not redis_url:
            return

        try:
            import redis.asyncio as aioredis  # type: ignore
        except ImportError:
            log.warning("redis package not installed, Redis cache disabled. Run: pip install redis")
            return

        try:
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
            await self._redis.ping()
            self._redis_enabled = True
            log.info("Redis connected, rebuilding credential pool cache...")

            # 并行重建两个 mode 的缓存
            import asyncio
            await asyncio.gather(
                self._rebuild_redis_cache("geminicli"),
                self._rebuild_redis_cache("antigravity"),
            )
            log.info("Redis credential pool cache ready")
        except Exception as e:
            log.warning(f"Redis init failed, falling back to MongoDB-only mode: {e}")
            self._redis = None
            self._redis_enabled = False

    # ---- Redis key 工具 ----

    def _rk_avail(self, mode: str) -> str:
        """所有未禁用凭证的 Redis Set key"""
        return f"gcli:avail:{mode}"

    def _rk_preview(self, mode: str) -> str:
        """未禁用且 preview=True 的凭证 Redis Set key（仅 geminicli）"""
        return f"gcli:preview:{mode}"

    def _rk_cd(self, mode: str, filename: str, escaped_model: str) -> str:
        """模型冷却 Redis key（带 TTL）"""
        return f"gcli:cd:{mode}:{filename}:{escaped_model}"

    # ---- Redis 缓存维护 ----

    async def _rebuild_redis_cache(self, mode: str) -> None:
        """
        从 MongoDB 重建指定 mode 的 Redis 凭证池缓存。

        使用临时 key + RENAME 原子替换
        """
        if not self._redis:
            return
        try:
            collection = self._db[self._get_collection_name(mode)]
            # 同时投影 model_cooldowns，以便重建冷却 TTL Key
            projection: Dict[str, Any] = {"filename": 1, "disabled": 1, "model_cooldowns": 1, "_id": 0}
            if mode == "geminicli":
                projection["preview"] = 1

            avail: List[str] = []
            preview: List[str] = []
            cooldown_entries: List[tuple] = []  # (cd_key, ttl_seconds, value)
            current_time = time.time()

            async for doc in collection.find({}, projection=projection):
                if not doc.get("disabled", False):
                    filename = doc["filename"]
                    avail.append(filename)
                    if mode == "geminicli" and doc.get("preview", True):
                        preview.append(filename)

                    # 收集未过期的模型冷却，重建 Redis TTL Key
                    model_cooldowns = doc.get("model_cooldowns") or {}
                    for escaped_model, cooldown_until in model_cooldowns.items():
                        if isinstance(cooldown_until, (int, float)) and cooldown_until > current_time:
                            ttl = int(cooldown_until - current_time)
                            if ttl > 0:
                                cd_key = self._rk_cd(mode, filename, escaped_model)
                                cooldown_entries.append((cd_key, ttl, str(cooldown_until)))

            tmp_avail = self._rk_avail(mode) + ":tmp"
            tmp_preview = self._rk_preview(mode) + ":tmp"

            pipe = self._redis.pipeline()
            # 先写临时 key（此时正式 key 仍完整可用）
            pipe.delete(tmp_avail)
            pipe.delete(tmp_preview)
            if avail:
                pipe.sadd(tmp_avail, *avail)
            if mode == "geminicli" and preview:
                pipe.sadd(tmp_preview, *preview)
            await pipe.execute()

            # RENAME 是原子操作：瞬间切换，不存在空窗
            pipe2 = self._redis.pipeline()
            if avail:
                pipe2.rename(tmp_avail, self._rk_avail(mode))
            else:
                pipe2.delete(self._rk_avail(mode))
                pipe2.delete(tmp_avail)
            if mode == "geminicli":
                if preview:
                    pipe2.rename(tmp_preview, self._rk_preview(mode))
                else:
                    pipe2.delete(self._rk_preview(mode))
                    pipe2.delete(tmp_preview)
            await pipe2.execute()

            # 批量恢复未过期的模型冷却 TTL Key
            # 这一步必须在重建池之后执行，否则 Redis 重启后冷却 key 丢失，
            # 导致 Redis 快速路径选出仍处于冷却中的凭证
            if cooldown_entries:
                pipe3 = self._redis.pipeline()
                for cd_key, ttl, value in cooldown_entries:
                    pipe3.setex(cd_key, ttl, value)
                await pipe3.execute()

            log.debug(
                f"Redis cache rebuilt [{mode}]: {len(avail)} avail, {len(preview)} preview, "
                f"{len(cooldown_entries)} cooldown key(s) restored"
            )
        except Exception as e:
            log.warning(f"Redis rebuild cache error [{mode}]: {e}")

    async def _redis_add_cred(self, mode: str, filename: str, preview: bool = True) -> None:
        """将凭证加入 Redis 可用池"""
        if not self._redis_enabled:
            return
        try:
            pipe = self._redis.pipeline()
            pipe.sadd(self._rk_avail(mode), filename)
            if mode == "geminicli" and preview:
                pipe.sadd(self._rk_preview(mode), filename)
            await pipe.execute()
        except Exception as e:
            log.warning(f"Redis add_cred error: {e}")

    async def _redis_remove_cred(self, mode: str, filename: str) -> None:
        """从 Redis 所有池中移除凭证"""
        if not self._redis_enabled:
            return
        try:
            pipe = self._redis.pipeline()
            pipe.srem(self._rk_avail(mode), filename)
            pipe.srem(self._rk_preview(mode), filename)
            await pipe.execute()
        except Exception as e:
            log.warning(f"Redis remove_cred error: {e}")

    async def _redis_sync_cred(self, mode: str, filename: str, disabled: bool, preview: bool) -> None:
        """根据最新状态同步单个凭证在 Redis 中的集合成员"""
        if not self._redis_enabled:
            return
        try:
            pipe = self._redis.pipeline()
            if disabled:
                pipe.srem(self._rk_avail(mode), filename)
                pipe.srem(self._rk_preview(mode), filename)
            else:
                pipe.sadd(self._rk_avail(mode), filename)
                if mode == "geminicli":
                    if preview:
                        pipe.sadd(self._rk_preview(mode), filename)
                    else:
                        pipe.srem(self._rk_preview(mode), filename)
            await pipe.execute()
        except Exception as e:
            log.warning(f"Redis sync_cred error: {e}")

    async def _get_next_available_from_redis(
        self, mode: str, model_name: Optional[str]
    ) -> Optional[tuple]:
        """
        Redis 快速路径：随机取候选凭证，跳过冷却中的，返回 (filename, credential_data)。
        失败或池为空时返回 None，由调用方降级到 MongoDB。
        """
        try:
            # 选择候选池
            if mode == "geminicli" and model_name and "preview" in model_name.lower():
                pool_key = self._rk_preview(mode)
            else:
                pool_key = self._rk_avail(mode)

            pool_size = await self._redis.scard(pool_key)
            if pool_size == 0:
                log.debug(f"[Redis MISS] mode={mode} pool_key={pool_key}: pool empty, fallback to MongoDB")
                return None

            # 一次取多个随机成员，减少 round-trip
            sample_size = min(pool_size, 10)
            candidates = await self._redis.srandmember(pool_key, sample_size)
            if not candidates:
                return None

            # 过滤冷却中的凭证
            if model_name:
                escaped = self._escape_model_name(model_name)
                for filename in candidates:
                    cd_key = self._rk_cd(mode, filename, escaped)
                    if not await self._redis.exists(cd_key):
                        credential_data = await self.get_credential(filename, mode)
                        log.debug(f"[Redis HIT] mode={mode} model={model_name} -> {filename}")
                        return filename, credential_data
                # 所有候选都在冷却中，降级到 MongoDB
                log.debug(f"[Redis MISS] mode={mode} model={model_name}: all {len(candidates)} candidates in cooldown, fallback to MongoDB")
                return None
            else:
                filename = candidates[0]
                credential_data = await self.get_credential(filename, mode)
                log.debug(f"[Redis HIT] mode={mode} -> {filename}")
                return filename, credential_data
        except Exception as e:
            log.warning(f"Redis get_next_available error: {e}")
            return None

    async def close(self) -> None:
        """关闭 MongoDB 连接"""
        if self._redis:
            await self._redis.aclose()
            self._redis = None
            self._redis_enabled = False
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

    def _get_collection_name(self, mode: str) -> str:
        """根据 mode 获取对应的集合名"""
        if mode == "antigravity":
            return "antigravity_credentials"
        elif mode == "geminicli":
            return "credentials"
        else:
            raise ValueError(f"Invalid mode: {mode}. Must be 'geminicli' or 'antigravity'")

    # ============ SQL 方法 ============

    async def get_next_available_credential(
        self, mode: str = "geminicli", model_name: Optional[str] = None
    ) -> Optional[tuple[str, Dict[str, Any]]]:
        """
        随机获取一个可用凭证（负载均衡）
        - 未禁用
        - 如果提供了 model_name，还会检查模型级冷却和preview状态
        - 随机选择

        Args:
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            model_name: 完整模型名（如 "gemini-2.0-flash-exp", "gemini-2.0-flash-thinking-exp-01-21"）

        Note:
            - 对于 geminicli 模式:
              - 如果模型名包含 "preview": 只能使用 preview=True 的凭证
              - 如果模型名包含 "flash": 直接混用所有可用凭证，不区分 preview 状态
              - 如果模型名不包含 "preview" 且不包含 "flash": 优先使用 preview=False 的凭证，没有时才使用 preview=True
            - 对于 antigravity: 不检查 preview 状态
            - 开启 Redis 时：利用 Redis Set 随机选凭证 + TTL key 判断冷却
            - 未开启 Redis 时：使用 count + random skip + limit(1)
        """
        self._ensure_initialized()

        # Redis 快速路径
        if self._redis_enabled:
            result = await self._get_next_available_from_redis(mode, model_name)
            if result is not None:
                return result
            # result 为 None 有两种可能：池为空或所有候选都冷却中
            # 后者需降级到 MongoDB 以得到更大的样本空间
            log.debug(f"[MongoDB fallback] mode={mode} model={model_name}")

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            current_time = time.time()

            # 构建普通查询（避免 $sample 聚合导致全集合扫描）
            match_query: Dict[str, Any] = {"disabled": False}

            # 冷却检查：直接用 MongoDB 查询表达，无需 $addFields
            if model_name:
                escaped_model_name = self._escape_model_name(model_name)
                field = f"model_cooldowns.{escaped_model_name}"
                match_query["$or"] = [
                    {field: {"$exists": False}},
                    {field: {"$lte": current_time}},
                ]

            # geminicli preview 筛选
            if mode == "geminicli" and model_name and "preview" in model_name.lower():
                match_query["preview"] = True

            # 统计符合条件的凭证总数（走索引，极快）
            count = await collection.count_documents(match_query)
            if count == 0:
                return None

            # 随机偏移 + limit(1)，替代 $sample，避免全集合随机排序
            skip_n = random.randint(0, count - 1)
            projection = {"filename": 1, "credential_data": 1, "_id": 0}
            docs = await collection.find(match_query, projection).skip(skip_n).limit(1).to_list(1)

            if docs:
                doc = docs[0]
                return doc["filename"], doc.get("credential_data")

            return None

        except Exception as e:
            log.error(f"Error getting next available credential (mode={mode}, model_name={model_name}): {e}")
            return None

    async def get_available_credentials_list(self, mode: str = "geminicli") -> List[str]:
        """
        获取所有可用凭证列表
        - 未禁用
        - 按轮换顺序排序
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            pipeline = [
                {"$match": {"disabled": False}},
                {"$sort": {"rotation_order": 1}},
                {"$project": {"filename": 1, "_id": 0}}
            ]

            docs = await collection.aggregate(pipeline).to_list(length=None)
            return [doc["filename"] for doc in docs]

        except Exception as e:
            log.error(f"Error getting available credentials list (mode={mode}): {e}")
            return []

    # ============ StorageBackend 协议方法 ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        """存储或更新凭证"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            current_ts = time.time()

            # 使用 upsert + $setOnInsert
            # 如果文档存在，只更新 credential_data 和 updated_at
            # 如果文档不存在，设置所有默认字段

            # 先尝试更新现有文档
            result = await collection.update_one(
                {"filename": filename},
                {
                    "$set": {
                        "credential_data": credential_data,
                        "updated_at": current_ts,
                    }
                }
            )

            # 如果没有匹配到（新凭证），需要插入
            if result.matched_count == 0:
                # 获取下一个 rotation_order
                pipeline = [
                    {"$group": {"_id": None, "max_order": {"$max": "$rotation_order"}}},
                    {"$project": {"_id": 0, "next_order": {"$add": ["$max_order", 1]}}}
                ]

                result_list = await collection.aggregate(pipeline).to_list(length=1)
                next_order = result_list[0]["next_order"] if result_list else 0

                # 插入新凭证（使用 insert_one，因为我们已经确认不存在）
                try:
                    new_credential = {
                        "filename": filename,
                        "credential_data": credential_data,
                        "disabled": False,
                        "error_codes": [],
                        "error_messages": [],
                        "last_success": current_ts,
                        "user_email": None,
                        "model_cooldowns": {},
                        "rotation_order": next_order,
                        "call_count": 0,
                        "created_at": current_ts,
                        "updated_at": current_ts,
                    }
                    # preview状态只对geminicli模式有效，默认为True
                    if mode == "geminicli":
                        new_credential["preview"] = True

                    await collection.insert_one(new_credential)
                    # 新凭证插入成功，添加到 Redis 可用池
                    await self._redis_add_cred(mode, filename, preview=True)
                except Exception as insert_error:
                    # 处理并发插入导致的重复键错误
                    if "duplicate key" in str(insert_error).lower():
                        # 重试更新（已存在的凭证，无需更新 Redis）
                        await collection.update_one(
                            {"filename": filename},
                            {"$set": {"credential_data": credential_data, "updated_at": current_ts}}
                        )
                    else:
                        raise

            log.debug(f"Stored credential: {filename} (mode={mode})")
            return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str, mode: str = "geminicli") -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 精确匹配，只投影需要的字段
            doc = await collection.find_one(
                {"filename": filename},
                {"credential_data": 1, "_id": 0}
            )
            if doc:
                return doc.get("credential_data")

            return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        """列出所有凭证文件名"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 使用聚合管道
            pipeline = [
                {"$sort": {"rotation_order": 1}},
                {"$project": {"filename": 1, "_id": 0}}
            ]

            docs = await collection.aggregate(pipeline).to_list(length=None)
            return [doc["filename"] for doc in docs]

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str, mode: str = "geminicli") -> bool:
        """删除凭证"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 精确匹配删除
            result = await collection.delete_one({"filename": filename})
            deleted_count = result.deleted_count

            if deleted_count > 0:
                # 从 Redis 池中移除
                await self._redis_remove_cred(mode, filename)
                log.debug(f"Deleted {deleted_count} credential(s): {filename} (mode={mode})")
                return True
            else:
                log.warning(f"No credential found to delete: {filename} (mode={mode})")
                return False

        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False

    async def get_duplicate_credentials_by_email(self, mode: str = "geminicli") -> Dict[str, Any]:
        """
        获取按邮箱分组的重复凭证信息（只查询邮箱和文件名，不加载完整凭证数据）
        用于去重操作

        Args:
            mode: 凭证模式 ("geminicli" 或 "antigravity")

        Returns:
            包含 email_groups（邮箱分组）、duplicate_count（重复数量）、no_email_count（无邮箱数量）的字典
        """
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 使用聚合管道，只查询 filename 和 user_email 字段
            pipeline = [
                {
                    "$project": {
                        "filename": 1,
                        "user_email": 1,
                        "_id": 0
                    }
                },
                {
                    "$sort": {"filename": 1}
                }
            ]

            docs = await collection.aggregate(pipeline).to_list(length=None)

            # 按邮箱分组
            email_to_files = {}
            no_email_files = []

            for doc in docs:
                filename = doc.get("filename")
                user_email = doc.get("user_email")

                if user_email:
                    if user_email not in email_to_files:
                        email_to_files[user_email] = []
                    email_to_files[user_email].append(filename)
                else:
                    no_email_files.append(filename)

            # 找出重复的邮箱组
            duplicate_groups = []
            total_duplicate_count = 0

            for email, files in email_to_files.items():
                if len(files) > 1:
                    # 保留第一个文件，其他为重复
                    duplicate_groups.append({
                        "email": email,
                        "kept_file": files[0],
                        "duplicate_files": files[1:],
                        "duplicate_count": len(files) - 1,
                    })
                    total_duplicate_count += len(files) - 1

            return {
                "email_groups": email_to_files,
                "duplicate_groups": duplicate_groups,
                "duplicate_count": total_duplicate_count,
                "no_email_files": no_email_files,
                "no_email_count": len(no_email_files),
                "unique_email_count": len(email_to_files),
                "total_count": len(docs),
            }

        except Exception as e:
            log.error(f"Error getting duplicate credentials by email: {e}")
            return {
                "email_groups": {},
                "duplicate_groups": [],
                "duplicate_count": 0,
                "no_email_files": [],
                "no_email_count": 0,
                "unique_email_count": 0,
                "total_count": 0,
            }

    async def update_credential_state(
        self, filename: str, state_updates: Dict[str, Any], mode: str = "geminicli"
    ) -> bool:
        """更新凭证状态"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 过滤只更新状态字段
            valid_updates = {
                k: v for k, v in state_updates.items() if k in self.STATE_FIELDS
            }

            if not valid_updates:
                return True

            valid_updates["updated_at"] = time.time()

            # 精确匹配更新
            result = await collection.update_one(
                {"filename": filename}, {"$set": valid_updates}
            )
            updated_count = result.modified_count + result.matched_count

            # 如果 disabled 或 preview 发生变化，同步 Redis 池成员关系
            if self._redis_enabled and ("disabled" in valid_updates or "preview" in valid_updates):
                if "disabled" in valid_updates and valid_updates["disabled"]:
                    # 直接禁用：从两个集合中移除
                    await self._redis_remove_cred(mode, filename)
                else:
                    # 启用或修改 preview：需知道最新的 disabled + preview 状态
                    # 从 valid_updates 中取，无则向 MongoDB 查一次
                    if "disabled" in valid_updates and "preview" in valid_updates:
                        await self._redis_sync_cred(
                            mode, filename,
                            disabled=bool(valid_updates["disabled"]),
                            preview=bool(valid_updates["preview"]),
                        )
                    else:
                        # 只知部分信息，查一次 MongoDB 获取完整状态
                        snap = await collection.find_one(
                            {"filename": filename},
                            {"disabled": 1, "preview": 1, "_id": 0}
                        )
                        if snap:
                            await self._redis_sync_cred(
                                mode, filename,
                                disabled=bool(snap.get("disabled", False)),
                                preview=bool(snap.get("preview", True)),
                            )

            return updated_count > 0

        except Exception as e:
            log.error(f"Error updating credential state {filename}: {e}")
            return False

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        """获取凭证状态（不包含error_messages）"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            current_time = time.time()

            # 精确匹配
            doc = await collection.find_one({"filename": filename})

            if doc:
                model_cooldowns = doc.get("model_cooldowns", {})
                # 过滤掉损坏的数据(dict类型)和过期的冷却
                if model_cooldowns:
                    model_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
                    }

                state = {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", current_time),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": model_cooldowns,
                }
                # preview状态只对geminicli模式有效
                if mode == "geminicli":
                    state["preview"] = doc.get("preview", True)
                return state

            # 返回默认状态
            default_state = {
                "disabled": False,
                "error_codes": [],
                "last_success": current_time,
                "user_email": None,
                "model_cooldowns": {},
            }
            # preview状态只对geminicli模式有效
            if mode == "geminicli":
                default_state["preview"] = True
            return default_state

        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {}

    async def get_all_credential_states(self, mode: str = "geminicli") -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态（不包含error_messages）"""
        self._ensure_initialized()

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 使用投影只获取需要的字段（不包含error_messages）
            projection = {
                "filename": 1,
                "disabled": 1,
                "error_codes": 1,
                "last_success": 1,
                "user_email": 1,
                "model_cooldowns": 1,
                "_id": 0
            }
            # preview状态只对geminicli模式有效
            if mode == "geminicli":
                projection["preview"] = 1

            cursor = collection.find({}, projection=projection)

            states = {}
            current_time = time.time()

            async for doc in cursor:
                filename = doc["filename"]
                model_cooldowns = doc.get("model_cooldowns", {})

                # 自动过滤掉已过期的模型CD
                if model_cooldowns:
                    model_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
                    }

                state = {
                    "disabled": doc.get("disabled", False),
                    "error_codes": doc.get("error_codes", []),
                    "last_success": doc.get("last_success", time.time()),
                    "user_email": doc.get("user_email"),
                    "model_cooldowns": model_cooldowns,
                }
                # preview状态只对geminicli模式有效
                if mode == "geminicli":
                    state["preview"] = doc.get("preview", True)
                states[filename] = state

            return states

        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}

    async def get_credentials_summary(
        self,
        offset: int = 0,
        limit: Optional[int] = None,
        status_filter: str = "all",
        mode: str = "geminicli",
        error_code_filter: Optional[str] = None,
        cooldown_filter: Optional[str] = None,
        preview_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取凭证的摘要信息（不包含完整凭证数据）- 支持分页和状态筛选

        Args:
            offset: 跳过的记录数（默认0）
            limit: 返回的最大记录数（None表示返回所有）
            status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            error_code_filter: 错误码筛选（格式如"400"或"403"，筛选包含该错误码的凭证）
            cooldown_filter: 冷却状态筛选（"in_cooldown"=冷却中, "no_cooldown"=未冷却）
            preview_filter: Preview筛选（"preview"=支持preview, "no_preview"=不支持preview，仅geminicli模式有效）

        Returns:
            包含 items（凭证列表）、total（总数）、offset、limit 的字典
        """
        self._ensure_initialized()

        try:
            # 根据 mode 选择集合名
            collection_name = self._get_collection_name(mode)
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
            stats_pipeline = [
                {
                    "$group": {
                        "_id": "$disabled",
                        "count": {"$sum": 1}
                    }
                }
            ]

            stats_result = await collection.aggregate(stats_pipeline).to_list(length=10)
            for item in stats_result:
                count = item["count"]
                global_stats["total"] += count
                if item["_id"]:
                    global_stats["disabled"] = count
                else:
                    global_stats["normal"] = count

            # 获取所有匹配的文档（用于冷却筛选，因为需要在Python中判断）
            projection = {
                "filename": 1,
                "disabled": 1,
                "error_codes": 1,
                "last_success": 1,
                "user_email": 1,
                "rotation_order": 1,
                "model_cooldowns": 1,
                "_id": 0
            }
            # preview状态只对geminicli模式有效
            if mode == "geminicli":
                projection["preview"] = 1

            cursor = collection.find(query, projection=projection).sort("rotation_order", 1)

            all_summaries = []
            current_time = time.time()

            async for doc in cursor:
                model_cooldowns = doc.get("model_cooldowns", {})

                # 自动过滤掉已过期的模型CD
                active_cooldowns = {}
                if model_cooldowns:
                    active_cooldowns = {
                        k: v for k, v in model_cooldowns.items()
                        if isinstance(v, (int, float)) and v > current_time
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
                # preview状态只对geminicli模式有效
                if mode == "geminicli":
                    summary["preview"] = doc.get("preview", True)

                # 应用 preview 筛选（仅对 geminicli 模式）
                if mode == "geminicli" and preview_filter:
                    preview_value = summary.get("preview", True)
                    if preview_filter == "preview" and not preview_value:
                        continue  # 跳过不支持 preview 的凭证
                    elif preview_filter == "no_preview" and preview_value:
                        continue  # 跳过支持 preview 的凭证

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

    async def get_credential_errors(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        """
        专门获取凭证的错误信息（包含 error_codes 和 error_messages）

        Args:
            filename: 凭证文件名
            mode: 凭证模式 ("geminicli" 或 "antigravity")

        Returns:
            包含 error_codes 和 error_messages 的字典
        """
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 精确匹配
            doc = await collection.find_one(
                {"filename": filename},
                {"error_codes": 1, "error_messages": 1, "_id": 0}
            )

            if doc:
                return {
                    "filename": filename,
                    "error_codes": doc.get("error_codes", []),
                    "error_messages": doc.get("error_messages", []),
                }

            # 凭证不存在，返回空错误信息
            return {
                "filename": filename,
                "error_codes": [],
                "error_messages": [],
            }

        except Exception as e:
            log.error(f"Error getting credential errors {filename}: {e}")
            return {
                "filename": filename,
                "error_codes": [],
                "error_messages": [],
                "error": str(e)
            }

    # ============ 模型级冷却管理 ============

    async def set_model_cooldown(
        self,
        filename: str,
        model_name: str,
        cooldown_until: Optional[float],
        mode: str = "geminicli"
    ) -> bool:
        """
        设置特定模型的冷却时间

        Args:
            filename: 凭证文件名
            model_name: 模型名（完整模型名，如 "gemini-2.0-flash-exp"）
            cooldown_until: 冷却截止时间戳（None 表示清除冷却）
            mode: 凭证模式 ("geminicli" 或 "antigravity")

        Returns:
            是否成功
        """
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]

            # 转义模型名中的点号
            escaped_model_name = self._escape_model_name(model_name)

            # 使用原子操作直接更新，避免竞态条件
            if cooldown_until is None:
                # 删除指定模型的冷却
                result = await collection.update_one(
                    {"filename": filename},
                    {
                        "$unset": {f"model_cooldowns.{escaped_model_name}": ""},
                        "$set": {"updated_at": time.time()}
                    }
                )
            else:
                # 设置冷却时间
                result = await collection.update_one(
                    {"filename": filename},
                    {
                        "$set": {
                            f"model_cooldowns.{escaped_model_name}": cooldown_until,
                            "updated_at": time.time()
                        }
                    }
                )

            if result.matched_count == 0:
                log.warning(f"Credential {filename} not found")
                return False

            # 同步写入 Redis TTL key
            if self._redis_enabled:
                cd_key = self._rk_cd(mode, filename, escaped_model_name)
                if cooldown_until is None:
                    await self._redis.delete(cd_key)
                else:
                    ttl = int(cooldown_until - time.time())
                    if ttl > 0:
                        await self._redis.setex(cd_key, ttl, str(cooldown_until))
                    else:
                        # 冷却已经过期，确保清除
                        await self._redis.delete(cd_key)

            log.debug(f"Set model cooldown: {filename}, model_name={model_name}, cooldown_until={cooldown_until}")
            return True

        except Exception as e:
            log.error(f"Error setting model cooldown for {filename}: {e}")
            return False

    async def record_success(
        self,
        filename: str,
        model_name: Optional[str] = None,
        mode: str = "geminicli"
    ) -> None:
        """
        成功调用后的条件写入：
        - 只有当前 error_codes 非空时才清除错误并写 last_success
        - 只有当前存在该模型的冷却键时才清除
        通过 MongoDB 服务端条件匹配实现
        """
        self._ensure_initialized()
        filename = os.path.basename(filename)

        try:
            collection_name = self._get_collection_name(mode)
            collection = self._db[collection_name]
            now = time.time()

            # 条件写入：只有 error_codes 非空时才触发，避免无意义的写 IO
            await collection.update_one(
                {"filename": filename, "error_codes": {"$ne": []}},
                {"$set": {
                    "last_success": now,
                    "error_codes": [],
                    "error_messages": {},
                    "updated_at": now,
                }}
            )

            # 条件删除模型冷却：只有该键存在时才写入
            if model_name:
                escaped = self._escape_model_name(model_name)
                await collection.update_one(
                    {"filename": filename, f"model_cooldowns.{escaped}": {"$exists": True}},
                    {"$unset": {f"model_cooldowns.{escaped}": ""}, "$set": {"updated_at": now}}
                )
                # 同步删除 Redis 冷却 key
                if self._redis_enabled:
                    await self._redis.delete(self._rk_cd(mode, filename, escaped))

        except Exception as e:
            log.error(f"Error recording success for {filename}: {e}")