"""
MongoDB数据库管理器，使用单集合设计。
将凭证、状态、配置、统计数据统一存储在一个集合中。
"""
import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import deque

import motor.motor_asyncio
from log import log

class MongoDBManager:
    """MongoDB数据库管理器"""
    
    def __init__(self):
        self._client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
        self._db: Optional[motor.motor_asyncio.AsyncIOMotorDatabase] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        
        # 配置
        self._connection_uri = None
        self._database_name = None
        
        # 单一集合设计 - 性能优化的核心
        self._unified_collection = "unified_storage"
        
        # 并发控制
        self._semaphore = asyncio.Semaphore(100)
        
        # 性能监控
        self._operation_count = 0
        self._operation_times = deque(maxlen=5000)
    
    async def initialize(self):
        """初始化MongoDB连接"""
        async with self._lock:
            if self._initialized:
                return
                
            try:
                # 获取连接配置
                self._connection_uri = os.getenv("MONGODB_URI")
                self._database_name = os.getenv("MONGODB_DATABASE", "gcli2api")
                
                if not self._connection_uri:
                    raise ValueError("MONGODB_URI environment variable is required")
                
                # 建立连接
                self._client = motor.motor_asyncio.AsyncIOMotorClient(
                    self._connection_uri,
                    serverSelectionTimeoutMS=5000,
                    maxPoolSize=100,
                    minPoolSize=10,
                    maxIdleTimeMS=45000,
                    waitQueueTimeoutMS=10000,
                )
                
                # 验证连接
                await self._client.admin.command('ping')
                
                # 获取数据库
                self._db = self._client[self._database_name]
                
                # 创建索引
                await self._create_indexes()
                
                self._initialized = True
                log.info(f"MongoDB connection established to {self._database_name}")
                
            except Exception as e:
                log.error(f"Error initializing MongoDB: {e}")
                raise
    
    async def _create_indexes(self):
        """创建优化的索引"""
        try:
            # 主复合索引 - 查询性能的关键
            await self._db[self._unified_collection].create_index([
                ("type", 1),  # 文档类型：credential 或 config
                ("key", 1)    # 凭证文件名或配置键名
            ], unique=True)
            
            # 辅助索引
            await self._db[self._unified_collection].create_index("created_at")
            await self._db[self._unified_collection].create_index("updated_at")
            await self._db[self._unified_collection].create_index("state.disabled")
            await self._db[self._unified_collection].create_index("state.last_success")
            await self._db[self._unified_collection].create_index("stats.next_reset_time")
            
            log.info("MongoDB indexes created successfully")
            
        except Exception as e:
            log.error(f"Error creating MongoDB indexes: {e}")
    
    async def close(self):
        """关闭MongoDB连接"""
        if self._client:
            self._client.close()
            self._initialized = False
            log.info("MongoDB connection closed")
    
    def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            raise RuntimeError("MongoDB manager not initialized")
    
    def _get_default_state(self) -> Dict[str, Any]:
        """获取默认状态数据"""
        return {
            "error_codes": [],
            "disabled": False,
            "last_success": time.time(),
            "user_email": None,
        }
    
    def _get_default_stats(self) -> Dict[str, Any]:
        """获取默认统计数据"""
        return {
            "gemini_2_5_pro_calls": 0,
            "total_calls": 0,
            "next_reset_time": None,
            "daily_limit_gemini_2_5_pro": 100,
            "daily_limit_total": 1000
        }
    
    # ============ 凭证管理 ============
    
    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        """存储凭证数据到统一集合"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                now = datetime.now(timezone.utc)
                
                # 获取现有文档（如果存在）保留状态和统计数据
                existing_doc = await self._db[self._unified_collection].find_one(
                    {"type": "credential", "key": filename}
                )
                
                # 准备统一文档
                doc = {
                    "type": "credential",
                    "key": filename,
                    "credential": credential_data,
                    "updated_at": now,
                }
                
                if existing_doc:
                    # 保留现有的状态和统计数据
                    doc["state"] = existing_doc.get("state", self._get_default_state())
                    doc["stats"] = existing_doc.get("stats", self._get_default_stats())
                    doc["created_at"] = existing_doc.get("created_at", now)
                else:
                    # 新凭证，使用默认状态和统计
                    doc["state"] = self._get_default_state()
                    doc["stats"] = self._get_default_stats()
                    doc["created_at"] = now
                
                # 使用upsert操作
                result = await self._db[self._unified_collection].replace_one(
                    {"type": "credential", "key": filename},
                    doc,
                    upsert=True
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if result.upserted_id or result.modified_count > 0:
                    log.debug(f"Stored credential: {filename} in {operation_time:.3f}s")
                    return True
                else:
                    log.warning(f"No changes made to credential: {filename}")
                    return False
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error storing credential {filename} in {operation_time:.3f}s: {e}")
                return False
    
    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        """从统一集合获取凭证数据"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                doc = await self._db[self._unified_collection].find_one(
                    {"type": "credential", "key": filename},
                    {"credential": 1, "_id": 0}
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if doc and "credential" in doc:
                    log.debug(f"Retrieved credential: {filename} in {operation_time:.3f}s")
                    return doc["credential"]
                return None
                
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error retrieving credential {filename} in {operation_time:.3f}s: {e}")
                return None
    
    async def list_credentials(self) -> List[str]:
        """从统一集合列出所有凭证文件名"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                cursor = self._db[self._unified_collection].find(
                    {"type": "credential"},
                    {"key": 1, "_id": 0}
                ).sort("created_at", 1)
                
                filenames = []
                async for doc in cursor:
                    filenames.append(doc["key"])
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                log.debug(f"Listed {len(filenames)} credentials in {operation_time:.3f}s")
                return filenames
                
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error listing credentials in {operation_time:.3f}s: {e}")
                return []
    
    async def delete_credential(self, filename: str) -> bool:
        """从统一集合删除凭证及所有相关数据"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                # 单次操作删除整个文档（包含凭证、状态、统计数据）
                result = await self._db[self._unified_collection].delete_one(
                    {"type": "credential", "key": filename}
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if result.deleted_count > 0:
                    log.debug(f"Deleted credential and all related data: {filename} in {operation_time:.3f}s")
                    return True
                else:
                    log.warning(f"Credential not found for deletion: {filename}")
                    return False
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error deleting credential {filename} in {operation_time:.3f}s: {e}")
                return False
    
    # ============ 状态管理 ============
    
    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any]) -> bool:
        """在统一集合中更新凭证状态"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                now = datetime.now(timezone.utc)
                
                # 使用$set更新状态字段
                update_operations = {}
                for key, value in state_updates.items():
                    update_operations[f"state.{key}"] = value
                
                update_operations["updated_at"] = now
                
                # 准备更新文档
                update_doc = {
                    "$set": update_operations,
                    "$setOnInsert": {
                        "type": "credential",
                        "key": filename,
                        "created_at": now,
                        "credential": {},
                        "state": self._get_default_state(),
                        "stats": self._get_default_stats()
                    }
                }
                
                # 使用upsert操作
                result = await self._db[self._unified_collection].update_one(
                    {"type": "credential", "key": filename},
                    update_doc,
                    upsert=True
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if result.modified_count > 0 or result.upserted_id:
                    log.debug(f"Updated credential state: {filename} in {operation_time:.3f}s")
                    return True
                else:
                    log.warning(f"No changes made to credential state: {filename}")
                    return False
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error updating credential state {filename} in {operation_time:.3f}s: {e}")
                return False
    
    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        """从统一集合获取凭证状态"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                doc = await self._db[self._unified_collection].find_one(
                    {"type": "credential", "key": filename},
                    {"state": 1, "_id": 0}
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if doc and "state" in doc:
                    log.debug(f"Retrieved credential state: {filename} in {operation_time:.3f}s")
                    return doc["state"]
                else:
                    # 返回默认状态
                    return self._get_default_state()
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error getting credential state {filename} in {operation_time:.3f}s: {e}")
                return self._get_default_state()
    
    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """从统一集合获取所有凭证状态"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                cursor = self._db[self._unified_collection].find(
                    {"type": "credential"},
                    {"key": 1, "state": 1, "_id": 0}
                )
                
                states = {}
                async for doc in cursor:
                    if "state" in doc:
                        states[doc["key"]] = doc["state"]
                    else:
                        states[doc["key"]] = self._get_default_state()
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                log.debug(f"Retrieved all credential states ({len(states)}) in {operation_time:.3f}s")
                return states
                
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error getting all credential states in {operation_time:.3f}s: {e}")
                return {}
    
    # ============ 配置管理 ============
    
    async def set_config(self, key: str, value: Any) -> bool:
        """在统一集合中设置配置"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                now = datetime.now(timezone.utc)
                doc = {
                    "type": "config",
                    "key": key,
                    "value": value,
                    "updated_at": now
                }
                
                result = await self._db[self._unified_collection].replace_one(
                    {"type": "config", "key": key},
                    doc,
                    upsert=True
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if result.upserted_id or result.modified_count > 0:
                    log.debug(f"Set config: {key} in {operation_time:.3f}s")
                    return True
                else:
                    return False
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error setting config {key} in {operation_time:.3f}s: {e}")
                return False
    
    async def get_config(self, key: str, default: Any = None) -> Any:
        """从统一集合获取配置"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                doc = await self._db[self._unified_collection].find_one(
                    {"type": "config", "key": key},
                    {"value": 1, "_id": 0}
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if doc:
                    log.debug(f"Retrieved config: {key} in {operation_time:.3f}s")
                    return doc["value"]
                else:
                    return default
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error getting config {key} in {operation_time:.3f}s: {e}")
                return default
    
    async def get_all_config(self) -> Dict[str, Any]:
        """从统一集合获取所有配置"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                cursor = self._db[self._unified_collection].find(
                    {"type": "config"},
                    {"key": 1, "value": 1, "_id": 0}
                )
                
                configs = {}
                async for doc in cursor:
                    configs[doc["key"]] = doc["value"]
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                log.debug(f"Retrieved all configs ({len(configs)}) in {operation_time:.3f}s")
                return configs
                
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error getting all configs in {operation_time:.3f}s: {e}")
                return {}
    
    async def delete_config(self, key: str) -> bool:
        """从统一集合删除配置"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                result = await self._db[self._unified_collection].delete_one(
                    {"type": "config", "key": key}
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if result.deleted_count > 0:
                    log.debug(f"Deleted config: {key} in {operation_time:.3f}s")
                    return True
                else:
                    return False
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error deleting config {key} in {operation_time:.3f}s: {e}")
                return False
    
    # ============ 使用统计管理 ============
    
    async def update_usage_stats(self, filename: str, stats_updates: Dict[str, Any]) -> bool:
        """在统一集合中更新使用统计"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                now = datetime.now(timezone.utc)
                
                # 使用$set更新统计字段
                update_operations = {}
                for key, value in stats_updates.items():
                    update_operations[f"stats.{key}"] = value
                
                update_operations["updated_at"] = now
                
                result = await self._db[self._unified_collection].update_one(
                    {"type": "credential", "key": filename},
                    {"$set": update_operations},
                    upsert=False  # 统计数据更新不创建新文档
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if result.modified_count > 0:
                    log.debug(f"Updated usage stats: {filename} in {operation_time:.3f}s")
                    return True
                else:
                    log.warning(f"No changes made to usage stats: {filename}")
                    return False
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error updating usage stats {filename} in {operation_time:.3f}s: {e}")
                return False
    
    async def get_usage_stats(self, filename: str) -> Dict[str, Any]:
        """从统一集合获取使用统计"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                doc = await self._db[self._unified_collection].find_one(
                    {"type": "credential", "key": filename},
                    {"stats": 1, "_id": 0}
                )
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                if doc and "stats" in doc:
                    log.debug(f"Retrieved usage stats: {filename} in {operation_time:.3f}s")
                    return doc["stats"]
                else:
                    return self._get_default_stats()
                    
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error getting usage stats {filename} in {operation_time:.3f}s: {e}")
                return self._get_default_stats()
    
    async def get_all_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        """从统一集合获取所有使用统计，带超时机制"""
        async with self._semaphore:
            self._ensure_initialized()
            start_time = time.time()
            
            try:
                # 添加超时机制防止卡死
                async def fetch_stats():
                    # 优化查询：只获取有统计数据的文档
                    cursor = self._db[self._unified_collection].find(
                        {
                            "type": "credential",
                            "$or": [
                                {"stats.gemini_2_5_pro_calls": {"$gt": 0}},
                                {"stats.total_calls": {"$gt": 0}},
                                {"stats.next_reset_time": {"$ne": None}}
                            ]
                        },
                        {"key": 1, "stats": 1, "_id": 0}
                    ).batch_size(50)  # 50批次大小
                    
                    stats = {}
                    doc_count = 0
                    
                    async for doc in cursor:
                        if "stats" in doc and doc["stats"]:
                            stats[doc["key"]] = doc["stats"]
                        
                        doc_count += 1
                        # 每处理50个文档yield一次，避免长时间阻塞
                        if doc_count % 50 == 0:
                            await asyncio.sleep(0.001)  # 让出控制权
                    
                    log.debug(f"Processed {doc_count} documents with usage statistics")
                    return stats
                
                # 设置10秒超时
                import asyncio
                stats = await asyncio.wait_for(fetch_stats(), timeout=10.0)
                
                # 性能监控
                self._operation_count += 1
                operation_time = time.time() - start_time
                self._operation_times.append(operation_time)
                
                log.debug(f"Retrieved all usage stats ({len(stats)}) in {operation_time:.3f}s")
                return stats
                
            except asyncio.TimeoutError:
                operation_time = time.time() - start_time
                log.error(f"Timeout getting all usage stats after {operation_time:.3f}s")
                return {}
            except Exception as e:
                operation_time = time.time() - start_time
                log.error(f"Error getting all usage stats in {operation_time:.3f}s: {e}")
                return {}