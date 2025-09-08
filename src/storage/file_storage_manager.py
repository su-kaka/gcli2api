"""
本地文件存储管理器，使用统一缓存支持队列写入优化。
所有凭证和状态数据存储在creds.toml中，配置数据存储在config.toml中。
"""
import asyncio
import os
import json
import time
from typing import Dict, Any, List, Optional

import aiofiles
import toml

from log import log
from .cache_manager import UnifiedCacheManager, CacheBackend


class FileCacheBackend(CacheBackend):
    """文件缓存后端实现"""
    
    def __init__(self, file_path: str):
        self._file_path = file_path
    
    async def load_data(self) -> Dict[str, Any]:
        """从TOML文件加载数据"""
        try:
            if not os.path.exists(self._file_path):
                return {}
            
            async with aiofiles.open(self._file_path, "r", encoding="utf-8") as f:
                content = await f.read()
            
            if not content.strip():
                return {}
            
            return toml.loads(content)
            
        except Exception as e:
            log.error(f"Error loading data from file {self._file_path}: {e}")
            return {}
    
    async def write_data(self, data: Dict[str, Any]) -> bool:
        """将数据写入TOML文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self._file_path), exist_ok=True)
            
            # 写入TOML文件
            toml_content = toml.dumps(data)
            async with aiofiles.open(self._file_path, "w", encoding="utf-8") as f:
                await f.write(toml_content)
            
            return True
            
        except Exception as e:
            log.error(f"Error writing data to file {self._file_path}: {e}")
            return False


class FileStorageManager:
    """基于本地文件的存储管理器（使用统一缓存）"""
    
    # 状态字段常量
    STATE_FIELDS = {
        "error_codes", "disabled", "last_success", "user_email",
        "gemini_2_5_pro_calls", "total_calls", "next_reset_time",
        "daily_limit_gemini_2_5_pro", "daily_limit_total"
    }
    
    # 默认状态数据模板（不包含动态值）
    _DEFAULT_STATE_TEMPLATE = {
        "error_codes": [],
        "disabled": False,
        "user_email": None,
        "gemini_2_5_pro_calls": 0,
        "total_calls": 0,
        "next_reset_time": None,
        "daily_limit_gemini_2_5_pro": 100,
        "daily_limit_total": 1000
    }
    
    @classmethod
    def get_default_state(cls) -> Dict[str, Any]:
        """获取默认状态数据（包含当前时间戳）"""
        state = cls._DEFAULT_STATE_TEMPLATE.copy()
        state["last_success"] = time.time()
        return state
    
    def __init__(self):
        self._credentials_dir = None  # 将通过异步初始化设置
        self._state_file = None
        self._config_file = None
        self._lock = asyncio.Lock()
        self._initialized = False
        
        # 统一缓存管理器
        self._credentials_cache_manager: Optional[UnifiedCacheManager] = None
        self._config_cache_manager: Optional[UnifiedCacheManager] = None
        
        # 配置参数
        self._write_delay = 0.5  # 写入延迟（秒）
        self._cache_ttl = 300  # 缓存TTL（秒）
    
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
        
        # 创建缓存管理器
        credentials_backend = FileCacheBackend(self._state_file)
        config_backend = FileCacheBackend(self._config_file)
        
        self._credentials_cache_manager = UnifiedCacheManager(
            credentials_backend,
            cache_ttl=self._cache_ttl,
            write_delay=self._write_delay,
            name="credentials"
        )
        
        self._config_cache_manager = UnifiedCacheManager(
            config_backend,
            cache_ttl=self._cache_ttl,
            write_delay=self._write_delay,
            name="config"
        )
        
        # 启动缓存管理器
        await self._credentials_cache_manager.start()
        await self._config_cache_manager.start()
        
        self._initialized = True
        log.debug("File storage manager initialized with unified cache")
    
    async def close(self) -> None:
        """关闭文件存储"""
        # 停止缓存管理器
        if self._credentials_cache_manager:
            await self._credentials_cache_manager.stop()
        if self._config_cache_manager:
            await self._config_cache_manager.stop()
        
        self._initialized = False
        log.debug("File storage manager closed with unified cache flushed")
    
    def _normalize_filename(self, filename: str) -> str:
        """标准化文件名"""
        return os.path.basename(filename)
    
    def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            raise RuntimeError("File storage manager not initialized")
    
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
            toml_data = {}
            if os.path.exists(self._state_file):
                try:
                    async with aiofiles.open(self._state_file, "r", encoding="utf-8") as f:
                        content = await f.read()
                    if content.strip():
                        toml_data = toml.loads(content)
                except Exception as e:
                    log.error(f"Failed to load existing TOML file: {e}")
            
            # 加载旧的creds_state.toml文件（稍后处理）
            old_state_data = {}
            if has_old_state:
                try:
                    async with aiofiles.open(old_state_file, "r", encoding="utf-8") as f:
                        content = await f.read()
                    old_state_data = toml.loads(content)
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
                    section_data.update(self.get_default_state())
                    
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
                try:
                    toml_content = toml.dumps(toml_data)
                    async with aiofiles.open(self._state_file, "w", encoding="utf-8") as f:
                        await f.write(toml_content)
                    
                    # 删除已迁移的JSON文件
                    for filename in json_files:
                        try:
                            if filename in toml_data:  # 确保文件确实被迁移了
                                filepath = os.path.join(self._credentials_dir, filename)
                                os.remove(filepath)
                                log.debug(f"Removed migrated JSON file: {filename}")
                        except Exception as e:
                            log.warning(f"Failed to remove {filename}: {e}")
                    
                    # 删除旧的状态文件（如果存在）
                    if has_old_state:
                        try:
                            os.remove(old_state_file)
                            log.debug("Removed old state file: creds_state.toml")
                        except Exception as e:
                            log.warning(f"Failed to remove old state file: {e}")
                    
                    log.info(f"Migration completed: {migrated_count} files migrated to TOML format")
                
                except Exception as e:
                    log.error(f"Failed to save migrated TOML file: {e}")
            
        except Exception as e:
            log.error(f"Error during JSON to TOML migration: {e}")
    
    # ============ 凭证管理 ============
    
    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        """存储凭证数据到统一缓存"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            
            # 获取现有数据或创建新数据
            all_data = await self._credentials_cache_manager.get_all()
            existing_state = all_data.get(filename, {})
            
            # 创建新的section数据：凭证数据 + 状态数据
            final_data = self.get_default_state()
            final_data.update(existing_state)
            final_data.update(credential_data)  # 凭证数据覆盖状态数据中的同名字段
            
            # 更新整个数据集
            all_data[filename] = final_data
            
            success = await self._credentials_cache_manager.update_multi({filename: final_data})
            log.debug(f"Stored credential to unified cache: {filename}")
            return success
            
        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False
    
    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        """从统一缓存获取凭证数据"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            all_data = await self._credentials_cache_manager.get_all()
            
            if filename not in all_data:
                return None
            
            section_data = all_data[filename]
            
            # 提取凭证数据（排除状态字段）
            credential_data = {k: v for k, v in section_data.items() if k not in self.STATE_FIELDS}
            return credential_data
            
        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None
    
    async def list_credentials(self) -> List[str]:
        """从统一缓存列出所有凭证文件名"""
        self._ensure_initialized()
        
        try:
            all_data = await self._credentials_cache_manager.get_all()
            return list(all_data.keys())
            
        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []
    
    async def delete_credential(self, filename: str) -> bool:
        """从统一缓存删除凭证"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            success = await self._credentials_cache_manager.delete(filename)
            log.debug(f"Deleted credential from unified cache: {filename}")
            return success
            
        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False
    
    # ============ 状态管理 ============
    
    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any]) -> bool:
        """更新凭证状态"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            all_data = await self._credentials_cache_manager.get_all()
            
            if filename not in all_data:
                all_data[filename] = self.get_default_state()
            
            # 更新状态
            all_data[filename].update(state_updates)
            
            success = await self._credentials_cache_manager.update_multi({filename: all_data[filename]})
            log.debug(f"Updated credential state in unified cache: {filename}")
            return success
            
        except Exception as e:
            log.error(f"Error updating credential state {filename}: {e}")
            return False
    
    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        """从统一缓存获取凭证状态"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            all_data = await self._credentials_cache_manager.get_all()
            
            if filename not in all_data:
                # 返回基本的状态字段
                default_state = self.get_default_state()
                return {k: v for k, v in default_state.items() if k in {"error_codes", "disabled", "last_success", "user_email"}}
            
            section_data = all_data[filename]
            
            # 提取状态字段
            state_data = {k: v for k, v in section_data.items() if k in self.STATE_FIELDS}
            
            # 确保必要字段存在
            basic_fields = {"error_codes", "disabled", "last_success", "user_email"}
            default_state = self.get_default_state()
            
            for field in basic_fields:
                if field not in state_data:
                    state_data[field] = default_state[field]
            
            return state_data
            
        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return self.get_default_state()
    
    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """从统一缓存获取所有凭证状态"""
        self._ensure_initialized()
        
        try:
            all_data = await self._credentials_cache_manager.get_all()
            
            states = {}
            for filename, section_data in all_data.items():
                # 提取状态字段
                state_data = {k: v for k, v in section_data.items() if k in self.STATE_FIELDS}
                
                # 确保必要字段存在
                basic_fields = {"error_codes", "disabled", "last_success", "user_email"}
                default_state = self.get_default_state()
                
                for field in basic_fields:
                    if field not in state_data:
                        state_data[field] = default_state[field]
                
                states[filename] = state_data
            
            return states
            
        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}
    
    # ============ 配置管理 ============
    
    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置到统一缓存"""
        self._ensure_initialized()
        return await self._config_cache_manager.set(key, value)
    
    async def get_config(self, key: str, default: Any = None) -> Any:
        """从统一缓存获取配置"""
        self._ensure_initialized()
        return await self._config_cache_manager.get(key, default)
    
    async def get_all_config(self) -> Dict[str, Any]:
        """从统一缓存获取所有配置"""
        self._ensure_initialized()
        return await self._config_cache_manager.get_all()
    
    async def delete_config(self, key: str) -> bool:
        """从统一缓存删除配置"""
        self._ensure_initialized()
        return await self._config_cache_manager.delete(key)
    
    # ============ 使用统计管理 ============
    
    async def update_usage_stats(self, filename: str, stats_updates: Dict[str, Any]) -> bool:
        """更新使用统计"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            all_data = await self._credentials_cache_manager.get_all()
            
            if filename not in all_data:
                all_data[filename] = self.get_default_state()
            
            # 更新统计数据
            all_data[filename].update(stats_updates)
            
            success = await self._credentials_cache_manager.update_multi({filename: all_data[filename]})
            log.debug(f"Updated usage stats in unified cache: {filename}")
            return success
            
        except Exception as e:
            log.error(f"Error updating usage stats {filename}: {e}")
            return False
    
    async def get_usage_stats(self, filename: str) -> Dict[str, Any]:
        """从统一缓存获取使用统计"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            all_data = await self._credentials_cache_manager.get_all()
            
            if filename not in all_data:
                # 返回基本的统计字段
                default_state = self.get_default_state()
                return {k: v for k, v in default_state.items() if k in {"gemini_2_5_pro_calls", "total_calls", "next_reset_time", "daily_limit_gemini_2_5_pro", "daily_limit_total"}}
            
            section_data = all_data[filename]
            
            # 提取统计字段
            stats_fields = {"gemini_2_5_pro_calls", "total_calls", "next_reset_time", "daily_limit_gemini_2_5_pro", "daily_limit_total"}
            stats_data = {k: v for k, v in section_data.items() if k in stats_fields}
            
            # 确保必要字段存在
            default_state = self.get_default_state()
            for field in stats_fields:
                if field not in stats_data:
                    stats_data[field] = default_state[field]
            
            return stats_data
            
        except Exception as e:
            log.error(f"Error getting usage stats {filename}: {e}")
            return self.get_default_state()
    
    async def get_all_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        """从统一缓存获取所有使用统计"""
        self._ensure_initialized()
        
        try:
            all_data = await self._credentials_cache_manager.get_all()
            
            stats = {}
            stats_fields = {"gemini_2_5_pro_calls", "total_calls", "next_reset_time", "daily_limit_gemini_2_5_pro", "daily_limit_total"}
            
            for filename, section_data in all_data.items():
                # 提取统计字段
                stats_data = {k: v for k, v in section_data.items() if k in stats_fields}
                
                # 确保必要字段存在
                default_state = self.get_default_state()
                for field in stats_fields:
                    if field not in stats_data:
                        stats_data[field] = default_state[field]
                
                stats[filename] = stats_data
            
            return stats
            
        except Exception as e:
            log.error(f"Error getting all usage stats: {e}")
            return {}
    
    # ============ 工具方法 ============
    
    async def export_credential_to_json(self, filename: str, output_path: str = None) -> bool:
        """将TOML中的凭证导出为JSON文件（用于兼容性和备份）"""
        self._ensure_initialized()
        
        try:
            filename = self._normalize_filename(filename)
            credential_data = await self.get_credential(filename)
            
            if credential_data is None:
                log.warning(f"Credential not found for export: {filename}")
                return False
            
            if output_path is None:
                output_path = os.path.join(self._credentials_dir, f"{filename}.json")
            
            # 写入JSON文件
            json_content = json.dumps(credential_data, indent=2, ensure_ascii=False)
            async with aiofiles.open(output_path, "w", encoding="utf-8") as f:
                await f.write(json_content)
            
            log.info(f"Credential exported to JSON: {output_path}")
            return True
            
        except Exception as e:
            log.error(f"Error exporting credential {filename} to JSON: {e}")
            return False
    
    async def import_credential_from_json(self, json_path: str, filename: str = None) -> bool:
        """从JSON文件导入凭证到TOML"""
        self._ensure_initialized()
        
        try:
            if not os.path.exists(json_path):
                log.error(f"JSON file not found: {json_path}")
                return False
            
            # 读取JSON文件
            async with aiofiles.open(json_path, "r", encoding="utf-8") as f:
                json_content = await f.read()
            
            credential_data = json.loads(json_content)
            
            if filename is None:
                filename = os.path.basename(json_path)
            
            filename = self._normalize_filename(filename)
            
            # 存储凭证
            success = await self.store_credential(filename, credential_data)
            
            if success:
                log.info(f"Credential imported from JSON: {json_path} -> {filename}")
            
            return success
            
        except Exception as e:
            log.error(f"Error importing credential from JSON {json_path}: {e}")
            return False