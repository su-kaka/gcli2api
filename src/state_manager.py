"""
统一状态管理器
"""
import asyncio
import os
from typing import Dict, Any
from contextlib import asynccontextmanager

from config import is_mongodb_mode
from log import log
from .storage_adapter import get_storage_adapter


class StateManager:
    """
    统一状态管理器
    """
    
    def __init__(self, state_file_path: str):
        self.state_file_path = state_file_path
        self._lock = asyncio.Lock()
        self._storage_adapter = None
        self._initialized = False
        
        # 从文件路径推断存储用途
        self._storage_purpose = self._infer_storage_purpose(state_file_path)
    
    def _infer_storage_purpose(self, file_path: str) -> str:
        """根据文件路径推断存储用途"""
        filename = os.path.basename(file_path)
        
        if "creds_state" in filename:
            return "credential_state"
        elif "config" in filename:
            return "config"
        elif "usage" in filename or "stats" in filename:
            return "usage_stats"
        else:
            return "general"
    
    async def _ensure_initialized(self):
        """确保状态管理器已初始化"""
        if not self._initialized:
            self._storage_adapter = await get_storage_adapter()
            self._initialized = True
            
            if await is_mongodb_mode():
                log.debug(f"Unified state manager initialized with MongoDB backend for: {self._storage_purpose}")
            else:
                log.debug(f"Unified state manager initialized with file backend for: {self._storage_purpose}")
    
    async def _load_state(self) -> Dict[str, Any]:
        """加载状态数据"""
        await self._ensure_initialized()
        
        if self._storage_purpose == "credential_state":
            return await self._storage_adapter.get_all_credential_states()
        elif self._storage_purpose == "config":
            return await self._storage_adapter.get_all_config()
        elif self._storage_purpose == "usage_stats":
            return await self._storage_adapter.get_all_usage_stats()
        else:
            # 对于通用存储，尝试获取配置数据
            return await self._storage_adapter.get_all_config()
    
    async def _save_state(self, state: Dict[str, Any]):
        """保存状态数据"""
        await self._ensure_initialized()
        
        # 根据存储用途批量更新数据
        if self._storage_purpose == "credential_state":
            # 批量更新凭证状态
            for filename, file_state in state.items():
                await self._storage_adapter.update_credential_state(filename, file_state)
        elif self._storage_purpose == "config":
            # 批量更新配置
            for key, value in state.items():
                await self._storage_adapter.set_config(key, value)
        elif self._storage_purpose == "usage_stats":
            # 批量更新使用统计
            for filename, stats in state.items():
                await self._storage_adapter.update_usage_stats(filename, stats)
        else:
            # 通用存储，作为配置处理
            for key, value in state.items():
                await self._storage_adapter.set_config(key, value)
    
    @asynccontextmanager
    async def transaction(self):
        """
        事务上下文管理器，兼容原有接口。
        Usage:
            async with state_manager.transaction() as state:
                state['key'] = 'value'
                # State is automatically saved on exit
        """
        async with self._lock:
            state = await self._load_state()
            try:
                yield state
                await self._save_state(state)
            except Exception:
                # Don't save if there was an error
                raise
    
    async def read_file_state(self, filename: str) -> Dict[str, Any]:
        """读取特定文件的状态，兼容原有接口"""
        await self._ensure_initialized()
        
        if self._storage_purpose == "credential_state":
            return await self._storage_adapter.get_credential_state(filename)
        elif self._storage_purpose == "usage_stats":
            return await self._storage_adapter.get_usage_stats(filename)
        else:
            # 对于配置和通用存储，filename作为配置键
            value = await self._storage_adapter.get_config(filename)
            return value if isinstance(value, dict) else {}
    
    async def update_file_state(self, filename: str, updates: Dict[str, Any]):
        """更新特定文件的状态，兼容原有接口"""
        await self._ensure_initialized()
        
        if self._storage_purpose == "credential_state":
            await self._storage_adapter.update_credential_state(filename, updates)
        elif self._storage_purpose == "usage_stats":
            await self._storage_adapter.update_usage_stats(filename, updates)
        else:
            # 对于配置存储，如果updates是字典则作为嵌套配置处理
            if isinstance(updates, dict) and len(updates) == 1:
                # 如果只有一个键值对，可能是设置单个配置
                for key, value in updates.items():
                    await self._storage_adapter.set_config(f"{filename}.{key}", value)
            else:
                # 否则将整个updates作为配置值
                await self._storage_adapter.set_config(filename, updates)
    
    async def batch_update(self, updates: Dict[str, Dict[str, Any]]):
        """批量更新多个文件，兼容原有接口"""
        await self._ensure_initialized()
        
        for filename, file_updates in updates.items():
            await self.update_file_state(filename, file_updates)


# 全局状态管理器实例缓存
_state_managers: Dict[str, StateManager] = {}


def get_state_manager(state_file_path: str) -> StateManager:
    """获取或创建状态管理器实例，兼容原有接口"""
    if state_file_path not in _state_managers:
        _state_managers[state_file_path] = StateManager(state_file_path)
    return _state_managers[state_file_path]


async def close_all_state_managers():
    """关闭所有状态管理器（用于优雅关闭）"""
    global _state_managers
    
    # 关闭存储适配器（这会自动处理所有状态管理器）
    from .storage_adapter import close_storage_adapter
    await close_storage_adapter()
    
    # 清空缓存
    _state_managers.clear()
    log.debug("All state managers closed")