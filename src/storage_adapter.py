"""
存储适配器，提供统一的接口来处理本地文件存储和MongoDB存储。
根据配置自动选择存储后端。
"""
import asyncio
import os
import json
import time
from typing import Dict, Any, List, Optional, Protocol, TYPE_CHECKING, Set
from collections import deque
from dataclasses import dataclass, field

if TYPE_CHECKING:
    pass

import aiofiles
import toml

from log import log


@dataclass
class WriteOperation:
    """写入操作数据结构"""
    file_path: str
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    operation_type: str = "update"  # update, delete


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


class FileStorageBackend:
    """基于本地文件的存储后端（优化版：支持队列写入）"""
    
    def __init__(self):
        self._credentials_dir = None  # 将通过异步初始化设置
        self._state_file = None
        self._config_file = None
        self._lock = asyncio.Lock()
        self._initialized = False
        
        # 队列写入相关
        self._write_queue: deque = deque()
        self._write_cache: Dict[str, Dict[str, Any]] = {}  # 内存缓存
        self._config_cache: Dict[str, Any] = {}  # 配置缓存
        self._dirty_files: Set[str] = set()  # 标记需要写入的文件
        self._queue_lock = asyncio.Lock()
        self._write_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        # 配置参数
        self._write_delay = 0.5  # 写入延迟（秒）
        self._max_queue_size = 100  # 最大队列大小
        self._batch_size = 10  # 批量写入大小
    
    async def initialize(self) -> None:
        """初始化文件存储"""
        if self._initialized:
            return
        
        # 获取凭证目录配置（初始化时直接使用环境变量，避免循环依赖）
        self._credentials_dir = os.getenv("CREDENTIALS_DIR", "./creds")
        self._state_file = os.path.join(self._credentials_dir, "creds.toml")
        self._config_file = os.path.join(self._credentials_dir, "config.toml")
        
        # 确保目录存在
        os.makedirs(self._credentials_dir, exist_ok=True)
        
        # 执行JSON到TOML的迁移
        await self._migrate_json_to_toml()
        
        # 启动写入队列处理任务
        await self._start_write_queue_processor()
        
        self._initialized = True
        log.debug("File storage backend initialized with queue writing")
    
    async def close(self) -> None:
        """关闭文件存储"""
        # 停止写入队列处理
        await self._stop_write_queue_processor()
        
        # 刷新所有待写入数据
        await self._flush_all_caches()
        
        self._initialized = False
        log.debug("File storage backend closed with queue flushed")
    
    def _normalize_filename(self, filename: str) -> str:
        """标准化文件名"""
        return os.path.basename(filename)
    
    async def _migrate_json_to_toml(self) -> None:
        """将现有的JSON凭证文件和旧的creds_state.toml迁移到新的creds.toml文件中"""
        try:
            # 扫描JSON凭证文件
            json_files = []
            if os.path.exists(self._credentials_dir):
                for filename in os.listdir(self._credentials_dir):
                    if filename.endswith(".json"):
                        json_files.append(filename)
            
            # 检查旧的creds_state.toml文件
            old_state_file = os.path.join(self._credentials_dir, "creds_state.toml")
            has_old_state = os.path.exists(old_state_file)
            
            if not json_files and not has_old_state:
                log.debug("No JSON credential files or old state file found for migration")
                return
            
            # 加载现有TOML数据（如果存在）
            toml_data = await self._load_toml_file_with_cache(self._state_file)
            
            # 加载旧的creds_state.toml文件（稍后处理）
            old_state_data = {}
            if has_old_state:
                try:
                    old_state_data = await self._load_toml_file(old_state_file)
                    log.debug("Loaded old state file for potential migration")
                except Exception as e:
                    log.error(f"Failed to load old state file: {e}")
                    old_state_data = {}
            
            if json_files:
                log.info(f"Migrating {len(json_files)} JSON credential files to TOML")
            
            # 处理每个JSON文件
            migrated_count = 0
            for filename in json_files:
                try:
                    filepath = os.path.join(self._credentials_dir, filename)
                    
                    # 读取JSON凭证数据
                    async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
                        json_content = await f.read()
                    credential_data = json.loads(json_content)
                    
                    # 创建新的section：凭证数据 + 状态数据
                    section_data = credential_data.copy()
                    
                    # 首先添加默认状态数据
                    default_state = {
                        "error_codes": [],
                        "disabled": False,
                        "last_success": time.time(),
                        "user_email": None,
                        "gemini_2_5_pro_calls": 0,
                        "total_calls": 0,
                        "next_reset_time": None,
                        "daily_limit_gemini_2_5_pro": 100,
                        "daily_limit_total": 1000
                    }
                    section_data.update(default_state)
                    
                    # 如果旧状态文件中有该凭证的状态数据，则使用旧状态数据覆盖默认值
                    if filename in old_state_data and isinstance(old_state_data[filename], dict):
                        log.debug(f"Using old state data for: {filename}")
                        section_data.update(old_state_data[filename])
                    
                    # 如果当前TOML中已存在该凭证，保留其状态数据
                    if filename in toml_data and isinstance(toml_data[filename], dict):
                        log.debug(f"Merging with existing TOML state for: {filename}")
                        existing_state = toml_data[filename]
                        section_data.update(existing_state)
                    
                    # 最后确保凭证数据是最新的（覆盖任何冲突的字段）
                    section_data.update(credential_data)
                    
                    toml_data[filename] = section_data
                    
                    migrated_count += 1
                    log.debug(f"Migrated credential: {filename}")
                    
                except Exception as e:
                    log.error(f"Failed to migrate {filename}: {e}")
                    continue
            
            # 保存TOML文件（如果有新的迁移）
            if migrated_count > 0:
                if await self._save_toml_file(self._state_file, toml_data):
                    # 删除已迁移的JSON文件
                    for filename in json_files:
                        try:
                            if filename in toml_data:  # 确保文件确实被迁移了
                                filepath = os.path.join(self._credentials_dir, filename)
                                os.remove(filepath)
                                log.debug(f"Removed migrated JSON file: {filename}")
                        except Exception as e:
                            log.warning(f"Failed to remove {filename}: {e}")
                    
                    # 删除旧的creds_state.toml文件（无论是否用到了其中的数据）
                    if has_old_state:
                        try:
                            os.remove(old_state_file)
                            log.info("Removed old creds_state.toml file")
                        except Exception as e:
                            log.warning(f"Failed to remove old state file: {e}")
                    
                    log.info(f"Successfully migrated {migrated_count} JSON credentials to creds.toml")
                else:
                    log.error("Failed to save TOML file during migration")
            else:
                # 如果只有旧状态文件但没有JSON文件，直接删除旧文件（因为没有对应的凭证）
                if has_old_state:
                    try:
                        os.remove(old_state_file)
                        log.info("Removed old creds_state.toml file (no corresponding JSON credentials found)")
                    except Exception as e:
                        log.warning(f"Failed to remove old state file: {e}")
                else:
                    log.debug("No content to migrate")
                
        except Exception as e:
            log.error(f"Error during JSON to TOML migration: {e}")
    
    # ============ 队列写入优化方法 ============
    
    async def _start_write_queue_processor(self) -> None:
        """启动写入队列处理任务"""
        if self._write_task is not None:
            return
        
        self._shutdown_event.clear()
        self._write_task = asyncio.create_task(self._write_queue_processor())
        log.debug("Write queue processor started")
    
    async def _stop_write_queue_processor(self) -> None:
        """停止写入队列处理任务"""
        if self._write_task is None:
            return
        
        self._shutdown_event.set()
        try:
            await asyncio.wait_for(self._write_task, timeout=5.0)
        except asyncio.TimeoutError:
            self._write_task.cancel()
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
        
        self._write_task = None
        log.debug("Write queue processor stopped")
    
    async def _write_queue_processor(self) -> None:
        """写入队列处理循环"""
        try:
            while not self._shutdown_event.is_set():
                try:
                    # 等待写入延迟或关闭事件
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), 
                        timeout=self._write_delay
                    )
                    break  # 收到关闭信号
                except asyncio.TimeoutError:
                    pass  # 超时，继续处理队列
                
                # 处理写入队列
                await self._process_write_queue()
                
        except Exception as e:
            log.error(f"Write queue processor error: {e}")
    
    async def _process_write_queue(self) -> None:
        """处理写入队列中的操作"""
        async with self._queue_lock:
            if not self._dirty_files:
                return
            
            # 复制脏文件集合并清空
            files_to_write = self._dirty_files.copy()
            self._dirty_files.clear()
        
        # 批量写入文件
        for file_path in files_to_write:
            try:
                if file_path == self._state_file and file_path in self._write_cache:
                    await self._save_toml_file(file_path, self._write_cache[file_path])
                elif file_path == self._config_file and self._config_cache:
                    await self._save_toml_file(file_path, self._config_cache)
                    
            except Exception as e:
                log.error(f"Error writing file {file_path}: {e}")
                # 重新标记为脏文件以便重试
                async with self._queue_lock:
                    self._dirty_files.add(file_path)
    
    async def _queue_write_operation(self, file_path: str, data: Dict[str, Any]) -> None:
        """将写入操作加入队列"""
        async with self._queue_lock:
            # 更新缓存
            if file_path == self._state_file:
                self._write_cache[file_path] = data.copy()
            elif file_path == self._config_file:
                self._config_cache = data.copy()
            
            # 标记文件为脏文件
            self._dirty_files.add(file_path)
            
            # 如果队列过大，立即处理
            if len(self._dirty_files) >= self._max_queue_size:
                asyncio.create_task(self._process_write_queue())
    
    async def _flush_all_caches(self) -> None:
        """刷新所有缓存到磁盘"""
        await self._process_write_queue()
        
        # 确保所有数据都已写入
        async with self._queue_lock:
            while self._dirty_files:
                await self._process_write_queue()
                if self._dirty_files:  # 如果仍有脏文件，稍等片刻
                    await asyncio.sleep(0.1)
    
    async def _load_toml_file_with_cache(self, file_path: str) -> Dict[str, Any]:
        """从缓存或文件加载TOML数据"""
        # 优先从缓存读取
        if file_path == self._state_file and file_path in self._write_cache:
            return self._write_cache[file_path].copy()
        elif file_path == self._config_file and self._config_cache:
            return self._config_cache.copy()
        
        # 从文件读取并更新缓存
        data = await self._load_toml_file(file_path)
        
        async with self._queue_lock:
            if file_path == self._state_file:
                self._write_cache[file_path] = data.copy()
            elif file_path == self._config_file:
                self._config_cache = data.copy()
        
        return data
    
    
    async def export_credential_to_json(self, filename: str, output_path: str = None) -> bool:
        """将TOML中的凭证导出为JSON文件（用于兼容性和备份）"""
        self._ensure_initialized()
        
        try:
            credential_data = await self.get_credential(filename)
            if credential_data is None:
                log.error(f"Credential not found: {filename}")
                return False
            
            if output_path is None:
                output_path = os.path.join(self._credentials_dir, filename)
            
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(credential_data, indent=2, ensure_ascii=False))
            
            log.debug(f"Exported credential to JSON: {filename}")
            return True
            
        except Exception as e:
            log.error(f"Error exporting credential {filename}: {e}")
            return False
    
    async def import_credential_from_json(self, json_path: str, filename: str = None) -> bool:
        """从JSON文件导入凭证到TOML（用于兼容性）"""
        self._ensure_initialized()
        
        try:
            if not os.path.exists(json_path):
                log.error(f"JSON file not found: {json_path}")
                return False
                
            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                content = await f.read()
            
            credential_data = json.loads(content)
            
            if filename is None:
                filename = os.path.basename(json_path)
            
            return await self.store_credential(filename, credential_data)
            
        except Exception as e:
            log.error(f"Error importing credential from {json_path}: {e}")
            return False
    
    async def _load_toml_file(self, file_path: str) -> Dict[str, Any]:
        """加载TOML文件"""
        try:
            if os.path.exists(file_path):
                async with aiofiles.open(file_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                return toml.loads(content)
            return {}
        except Exception as e:
            log.error(f"Error loading TOML file {file_path}: {e}")
            return {}
    
    async def _save_toml_file(self, file_path: str, data: Dict[str, Any]) -> bool:
        """保存TOML文件"""
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
                await f.write(toml.dumps(data))
            return True
        except Exception as e:
            log.error(f"Error saving TOML file {file_path}: {e}")
            return False
    
    # ============ 凭证管理 ============
    
    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        """存储凭证数据到TOML文件（优化版：使用缓存和队列写入）"""
        self._ensure_initialized()
        
        async with self._lock:
            try:
                filename = self._normalize_filename(filename)
                toml_data = await self._load_toml_file_with_cache(self._state_file)
                
                
                # 获取现有状态数据（如果存在）
                existing_state = toml_data.get(filename, {})
                
                # 创建新的section数据：凭证数据 + 状态数据
                section_data = credential_data.copy()
                
                # 保留现有的状态数据，或使用默认值
                default_state = {
                    "error_codes": [],
                    "disabled": False,
                    "last_success": time.time(),
                    "user_email": None,
                    "gemini_2_5_pro_calls": 0,
                    "total_calls": 0,
                    "next_reset_time": None,
                    "daily_limit_gemini_2_5_pro": 100,
                    "daily_limit_total": 1000
                }
                
                # 合并：先用默认状态，再用现有状态，最后加入凭证数据
                final_data = default_state.copy()
                final_data.update(existing_state)
                final_data.update(credential_data)  # 凭证数据覆盖状态数据中的同名字段
                
                toml_data[filename] = final_data
                
                # 使用队列写入代替直接写入
                await self._queue_write_operation(self._state_file, toml_data)
                log.debug(f"Queued credential storage: {filename}")
                return True
                
            except Exception as e:
                log.error(f"Error storing credential {filename}: {e}")
                return False
    
    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        """从TOML文件获取凭证数据"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            toml_data = await self._load_toml_file_with_cache(self._state_file)
            
            if filename not in toml_data:
                return None
            
            section_data = toml_data[filename]
            
            # 提取凭证数据（排除状态字段）
            state_fields = {
                "error_codes", "disabled", "last_success", "user_email",
                "gemini_2_5_pro_calls", "total_calls", "next_reset_time",
                "daily_limit_gemini_2_5_pro", "daily_limit_total"
            }
            
            credential_data = {k: v for k, v in section_data.items() if k not in state_fields}
            return credential_data
            
        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None
    
    async def list_credentials(self) -> List[str]:
        """列出所有凭证文件名"""
        self._ensure_initialized()
        
        try:
            toml_data = await self._load_toml_file_with_cache(self._state_file)
            
            # 返回所有section名称
            credentials = list(toml_data.keys())
            return sorted(credentials)
            
        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []
    
    async def delete_credential(self, filename: str) -> bool:
        """从TOML文件删除凭证"""
        self._ensure_initialized()
        
        async with self._lock:
            try:
                filename = self._normalize_filename(filename)
                toml_data = await self._load_toml_file_with_cache(self._state_file)
                
                if filename in toml_data:
                    del toml_data[filename]
                    
                    # 使用队列写入代替直接写入
                    await self._queue_write_operation(self._state_file, toml_data)
                    log.debug(f"Queued credential deletion: {filename}")
                    return True
                else:
                    log.warning(f"Credential not found: {filename}")
                    return False
                    
            except Exception as e:
                log.error(f"Error deleting credential {filename}: {e}")
                return False
    
    # ============ 状态管理 ============
    
    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any]) -> bool:
        """更新凭证状态到TOML文件"""
        self._ensure_initialized()
        
        async with self._lock:
            try:
                filename = self._normalize_filename(filename)
                toml_data = await self._load_toml_file_with_cache(self._state_file)
                
                if filename not in toml_data:
                    # 如果凭证不存在，创建一个空的section（包含基本状态数据）
                    toml_data[filename] = {
                        "error_codes": [],
                        "disabled": False,
                        "last_success": time.time(),
                        "user_email": None,
                        "gemini_2_5_pro_calls": 0,
                        "total_calls": 0,
                        "next_reset_time": None,
                        "daily_limit_gemini_2_5_pro": 100,
                        "daily_limit_total": 1000
                    }
                
                # 更新状态数据
                toml_data[filename].update(state_updates)
                
                # 使用队列写入代替直接写入
                await self._queue_write_operation(self._state_file, toml_data)
                return True
                
            except Exception as e:
                log.error(f"Error updating credential state {filename}: {e}")
                return False
    
    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        """从TOML文件获取凭证状态"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            toml_data = await self._load_toml_file_with_cache(self._state_file)
            
            if filename not in toml_data:
                return {
                    "error_codes": [],
                    "disabled": False,
                    "last_success": time.time(),
                    "user_email": None,
                }
            
            section_data = toml_data[filename]
            
            # 提取状态字段
            state_fields = {
                "error_codes", "disabled", "last_success", "user_email",
                "gemini_2_5_pro_calls", "total_calls", "next_reset_time",
                "daily_limit_gemini_2_5_pro", "daily_limit_total"
            }
            
            state_data = {k: v for k, v in section_data.items() if k in state_fields}
            
            # 确保必要字段存在
            defaults = {
                "error_codes": [],
                "disabled": False,
                "last_success": time.time(),
                "user_email": None,
            }
            
            for key, default_value in defaults.items():
                if key not in state_data:
                    state_data[key] = default_value
            
            return state_data
            
        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {
                "error_codes": [],
                "disabled": False,
                "last_success": time.time(),
                "user_email": None,
            }
    
    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        self._ensure_initialized()
        
        try:
            toml_data = await self._load_toml_file_with_cache(self._state_file)
            
            # 提取状态字段
            state_fields = {
                "error_codes", "disabled", "last_success", "user_email",
                "gemini_2_5_pro_calls", "total_calls", "next_reset_time",
                "daily_limit_gemini_2_5_pro", "daily_limit_total"
            }
            
            states = {}
            for filename, section_data in toml_data.items():
                state_data = {k: v for k, v in section_data.items() if k in state_fields}
                states[filename] = state_data
            
            return states
            
        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}
    
    # ============ 配置管理 ============
    
    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置项到TOML文件"""
        self._ensure_initialized()
        
        async with self._lock:
            try:
                config_data = await self._load_toml_file_with_cache(self._config_file)
                config_data[key] = value
                # 使用队列写入代替直接写入
                await self._queue_write_operation(self._config_file, config_data)
                return True
                
            except Exception as e:
                log.error(f"Error setting config {key}: {e}")
                return False
    
    async def get_config(self, key: str, default: Any = None) -> Any:
        """从TOML文件获取配置项"""
        self._ensure_initialized()
        
        try:
            config_data = await self._load_toml_file_with_cache(self._config_file)
            return config_data.get(key, default)
        except Exception as e:
            log.error(f"Error getting config {key}: {e}")
            return default
    
    async def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        self._ensure_initialized()
        
        try:
            return await self._load_toml_file_with_cache(self._config_file)
        except Exception as e:
            log.error(f"Error getting all config: {e}")
            return {}
    
    async def delete_config(self, key: str) -> bool:
        """删除配置项"""
        self._ensure_initialized()
        
        async with self._lock:
            try:
                config_data = await self._load_toml_file_with_cache(self._config_file)
                
                if key in config_data:
                    del config_data[key]
                    # 使用队列写入代替直接写入
                    await self._queue_write_operation(self._config_file, config_data)
                    return True
                
                return True  # 键不存在视为成功
                
            except Exception as e:
                log.error(f"Error deleting config {key}: {e}")
                return False
    
    # ============ 使用统计管理 ============
    
    async def update_usage_stats(self, filename: str, stats_updates: Dict[str, Any]) -> bool:
        """更新使用统计（集成到状态文件中）"""
        return await self.update_credential_state(filename, stats_updates)
    
    async def get_usage_stats(self, filename: str) -> Dict[str, Any]:
        """获取使用统计"""
        state = await self.get_credential_state(filename)
        
        # 提取统计相关字段
        return {
            "gemini_2_5_pro_calls": state.get("gemini_2_5_pro_calls", 0),
            "total_calls": state.get("total_calls", 0),
            "next_reset_time": state.get("next_reset_time"),
            "daily_limit_gemini_2_5_pro": state.get("daily_limit_gemini_2_5_pro", 100),
            "daily_limit_total": state.get("daily_limit_total", 1000)
        }
    
    async def get_all_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        """获取所有使用统计"""
        all_states = await self.get_all_credential_states()
        
        stats = {}
        for filename, state in all_states.items():
            stats[filename] = {
                "gemini_2_5_pro_calls": state.get("gemini_2_5_pro_calls", 0),
                "total_calls": state.get("total_calls", 0),
                "next_reset_time": state.get("next_reset_time"),
                "daily_limit_gemini_2_5_pro": state.get("daily_limit_gemini_2_5_pro", 100),
                "daily_limit_total": state.get("daily_limit_total", 1000)
            }
        
        return stats
    
    def _ensure_initialized(self):
        """确保文件存储已初始化"""
        if not self._initialized:
            raise RuntimeError("File storage backend not initialized")
    
    # ============ 队列管理接口 ============
    
    async def flush_writes(self) -> None:
        """立即刷新所有待写入数据到磁盘"""
        await self._flush_all_caches()
        log.debug("All cached writes flushed to disk")
    
    async def get_queue_status(self) -> Dict[str, Any]:
        """获取写入队列状态"""
        async with self._queue_lock:
            return {
                "dirty_files_count": len(self._dirty_files),
                "cached_files": list(self._write_cache.keys()) + (["config"] if self._config_cache else []),
                "write_delay": self._write_delay,
                "max_queue_size": self._max_queue_size,
                "batch_size": self._batch_size,
                "queue_processor_running": self._write_task is not None and not self._write_task.done()
            }
    
    def set_write_config(self, write_delay: float = None, max_queue_size: int = None, batch_size: int = None) -> None:
        """配置写入队列参数"""
        if write_delay is not None:
            self._write_delay = max(0.1, write_delay)
        if max_queue_size is not None:
            self._max_queue_size = max(1, max_queue_size)
        if batch_size is not None:
            self._batch_size = max(1, batch_size)
        log.debug(f"Write config updated: delay={self._write_delay}, max_queue={self._max_queue_size}, batch={self._batch_size}")


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
            
            # 检查是否配置了MongoDB（初始化时直接使用环境变量，避免循环依赖）
            mongodb_uri = os.getenv("MONGODB_URI", "")
            
            if mongodb_uri:
                # 使用MongoDB存储
                try:
                    from .mongodb_manager import MongoDBManager
                    self._backend = MongoDBManager()
                    await self._backend.initialize()
                    log.info("Using MongoDB storage backend")
                except ImportError as e:
                    log.error(f"Failed to import MongoDB backend: {e}")
                    log.info("Falling back to file storage backend")
                    self._backend = FileStorageBackend()
                    await self._backend.initialize()
                except Exception as e:
                    log.error(f"Failed to initialize MongoDB backend: {e}")
                    log.info("Falling back to file storage backend")
                    self._backend = FileStorageBackend()
                    await self._backend.initialize()
            else:
                # 使用文件存储
                self._backend = FileStorageBackend()
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
        
        if isinstance(self._backend, FileStorageBackend):
            return "file"
        else:
            return "mongodb"
    
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
        elif isinstance(self._backend, FileStorageBackend):
            info.update({
                "credentials_dir": getattr(self._backend, '_credentials_dir', None),
                "state_file": getattr(self._backend, '_state_file', None),
                "config_file": getattr(self._backend, '_config_file', None)
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