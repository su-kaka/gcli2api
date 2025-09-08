"""
存储适配器，提供统一的接口来处理Redis、MongoDB和本地文件存储。
根据配置自动选择存储后端，优先级：Redis > MongoDB > 本地文件。
"""
import asyncio
import os
import json
from typing import Dict, Any, List, Optional, Protocol

from log import log


class StorageBackend(Protocol):
    """存储后端协议"""
    
    async def initialize(self) -> None:
        """初始化存储后端"""
        ...
    
    async def close(self) -> None:
        """关闭存储后端"""
        ...
    
    # 凭证管理
    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        """存储凭证数据"""
        ...
    
    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        ...
    
    async def list_credentials(self) -> List[str]:
        """列出所有凭证文件名"""
        ...
    
    async def delete_credential(self, filename: str) -> bool:
        """删除凭证"""
        ...
    
    # 状态管理
    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any]) -> bool:
        """更新凭证状态"""
        ...
    
    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        """获取凭证状态"""
        ...
    
    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        ...
    
    # 配置管理
    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置项"""
        ...
    
    async def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        ...
    
    async def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        ...
    
    async def delete_config(self, key: str) -> bool:
        """删除配置项"""
        ...
    
    # 使用统计管理
    async def update_usage_stats(self, filename: str, stats_updates: Dict[str, Any]) -> bool:
        """更新使用统计"""
        ...
    
    async def get_usage_stats(self, filename: str) -> Dict[str, Any]:
        """获取使用统计"""
        ...
    
    async def get_all_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有使用统计"""
        ...




class StorageAdapter:
    """存储适配器，根据配置选择存储后端"""
    
    def __init__(self):
        self._backend: Optional["StorageBackend"] = None
        self._initialized = False
        self._lock = asyncio.Lock()
    
    async def initialize(self) -> None:
        """初始化存储适配器"""
        async with self._lock:
            if self._initialized:
                return
            
            # 按优先级检查存储后端：Redis > MongoDB > 本地文件
            redis_uri = os.getenv("REDIS_URI", "")
            mongodb_uri = os.getenv("MONGODB_URI", "")
            
            # 优先尝试Redis存储
            if redis_uri:
                try:
                    from .storage.redis_manager import RedisManager
                    self._backend = RedisManager()
                    await self._backend.initialize()
                    log.info("Using Redis storage backend")
                except ImportError as e:
                    log.error(f"Failed to import Redis backend: {e}")
                    log.info("Falling back to next available storage backend")
                except Exception as e:
                    log.error(f"Failed to initialize Redis backend: {e}")
                    log.info("Falling back to next available storage backend")
            
            # 如果Redis不可用或未配置，接下来尝试Postgres（优先级低于Redis）
            postgres_dsn = os.getenv("POSTGRES_DSN", "")
            if not self._backend and postgres_dsn:
                try:
                    from .storage.postgres_manager import PostgresManager
                    self._backend = PostgresManager()
                    await self._backend.initialize()
                    log.info("Using Postgres storage backend")
                except ImportError as e:
                    log.error(f"Failed to import Postgres backend: {e}")
                    log.info("Falling back to next available storage backend")
                except Exception as e:
                    log.error(f"Failed to initialize Postgres backend: {e}")
                    log.info("Falling back to next available storage backend")

            # 如果Redis和Postgres不可用，尝试MongoDB存储
            if not self._backend and mongodb_uri:
                try:
                    from .storage.mongodb_manager import MongoDBManager
                    self._backend = MongoDBManager()
                    await self._backend.initialize()
                    log.info("Using MongoDB storage backend")
                except ImportError as e:
                    log.error(f"Failed to import MongoDB backend: {e}")
                    log.info("Falling back to file storage backend")
                except Exception as e:
                    log.error(f"Failed to initialize MongoDB backend: {e}")
                    log.info("Falling back to file storage backend")
            
            # 如果Redis和MongoDB都不可用，使用文件存储
            if not self._backend:
                from .storage.file_storage_manager import FileStorageManager
                self._backend = FileStorageManager()
                await self._backend.initialize()
                log.info("Using file storage backend")
            
            self._initialized = True
    
    async def close(self) -> None:
        """关闭存储适配器"""
        if self._backend:
            await self._backend.close()
            self._backend = None
            self._initialized = False
    
    def _ensure_initialized(self):
        """确保存储适配器已初始化"""
        if not self._initialized or not self._backend:
            raise RuntimeError("Storage adapter not initialized")
    
    # ============ 凭证管理 ============
    
    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        """存储凭证数据"""
        self._ensure_initialized()
        return await self._backend.store_credential(filename, credential_data)
    
    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()
        return await self._backend.get_credential(filename)
    
    async def list_credentials(self) -> List[str]:
        """列出所有凭证文件名"""
        self._ensure_initialized()
        return await self._backend.list_credentials()
    
    async def delete_credential(self, filename: str) -> bool:
        """删除凭证"""
        self._ensure_initialized()
        return await self._backend.delete_credential(filename)
    
    # ============ 状态管理 ============
    
    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any]) -> bool:
        """更新凭证状态"""
        self._ensure_initialized()
        return await self._backend.update_credential_state(filename, state_updates)
    
    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        """获取凭证状态"""
        self._ensure_initialized()
        return await self._backend.get_credential_state(filename)
    
    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        self._ensure_initialized()
        return await self._backend.get_all_credential_states()
    
    # ============ 配置管理 ============
    
    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置项"""
        self._ensure_initialized()
        return await self._backend.set_config(key, value)
    
    async def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        self._ensure_initialized()
        return await self._backend.get_config(key, default)
    
    async def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        self._ensure_initialized()
        return await self._backend.get_all_config()
    
    async def delete_config(self, key: str) -> bool:
        """删除配置项"""
        self._ensure_initialized()
        return await self._backend.delete_config(key)
    
    # ============ 使用统计管理 ============
    
    async def update_usage_stats(self, filename: str, stats_updates: Dict[str, Any]) -> bool:
        """更新使用统计"""
        self._ensure_initialized()
        return await self._backend.update_usage_stats(filename, stats_updates)
    
    async def get_usage_stats(self, filename: str) -> Dict[str, Any]:
        """获取使用统计"""
        self._ensure_initialized()
        return await self._backend.get_usage_stats(filename)
    
    async def get_all_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有使用统计"""
        self._ensure_initialized()
        return await self._backend.get_all_usage_stats()
    
    # ============ 工具方法 ============
    
    async def export_credential_to_json(self, filename: str, output_path: str = None) -> bool:
        """将凭证导出为JSON文件"""
        self._ensure_initialized()
        if hasattr(self._backend, 'export_credential_to_json'):
            return await self._backend.export_credential_to_json(filename, output_path)
        # MongoDB后端的fallback实现
        credential_data = await self.get_credential(filename)
        if credential_data is None:
            return False
        
        if output_path is None:
            output_path = f"{filename}.json"
        
        import aiofiles
        try:
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(credential_data, indent=2, ensure_ascii=False))
            return True
        except Exception:
            return False
    
    async def import_credential_from_json(self, json_path: str, filename: str = None) -> bool:
        """从JSON文件导入凭证"""
        self._ensure_initialized()
        if hasattr(self._backend, 'import_credential_from_json'):
            return await self._backend.import_credential_from_json(json_path, filename)
        # MongoDB后端的fallback实现
        try:
            import aiofiles
            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                content = await f.read()
            
            credential_data = json.loads(content)
            
            if filename is None:
                filename = os.path.basename(json_path)
            
            return await self.store_credential(filename, credential_data)
        except Exception:
            return False
    
    def get_backend_type(self) -> str:
        """获取当前存储后端类型"""
        if not self._backend:
            return "none"
        
        # 检查后端类型
        backend_class_name = self._backend.__class__.__name__
        if "File" in backend_class_name or "file" in backend_class_name.lower():
            return "file"
        elif "MongoDB" in backend_class_name or "mongo" in backend_class_name.lower():
            return "mongodb"
        elif "Redis" in backend_class_name or "redis" in backend_class_name.lower():
            return "redis"
        else:
            return "unknown"
    
    async def get_backend_info(self) -> Dict[str, Any]:
        """获取存储后端信息"""
        self._ensure_initialized()
        
        backend_type = self.get_backend_type()
        info = {
            "backend_type": backend_type,
            "initialized": self._initialized
        }
        
        # 获取底层存储信息
        if hasattr(self._backend, 'get_database_info'):
            try:
                db_info = await self._backend.get_database_info()
                info.update(db_info)
            except Exception as e:
                info["database_error"] = str(e)
        else:
            backend_type = self.get_backend_type()
            if backend_type == "file":
                info.update({
                    "credentials_dir": getattr(self._backend, '_credentials_dir', None),
                    "state_file": getattr(self._backend, '_state_file', None),
                    "config_file": getattr(self._backend, '_config_file', None)
                })
            elif backend_type == "redis":
                info.update({
                    "redis_url": getattr(self._backend, '_redis_url', None),
                    "connection_pool_size": getattr(self._backend, '_pool_size', None)
                })
        
        return info


# 全局存储适配器实例
_storage_adapter: Optional[StorageAdapter] = None


async def get_storage_adapter() -> StorageAdapter:
    """获取全局存储适配器实例"""
    global _storage_adapter
    
    if _storage_adapter is None:
        _storage_adapter = StorageAdapter()
        await _storage_adapter.initialize()
    
    return _storage_adapter


async def close_storage_adapter():
    """关闭全局存储适配器"""
    global _storage_adapter
    
    if _storage_adapter:
        await _storage_adapter.close()
        _storage_adapter = None
