"""
Usage statistics module for tracking API calls per credential file.
Uses the simpler logic: compare current time with next_reset_time.
"""
import os
import time
from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Dict, Any, Optional

from config import get_credentials_dir, is_mongodb_mode
from log import log
from .state_manager import get_state_manager
from .storage_adapter import get_storage_adapter


def _get_next_utc_7am() -> datetime:
    """
    Calculate the next UTC 07:00 time for quota reset.
    """
    now = datetime.now(timezone.utc)
    today_7am = now.replace(hour=7, minute=0, second=0, microsecond=0)
    
    if now < today_7am:
        return today_7am
    else:
        return today_7am + timedelta(days=1)


class UsageStats:
    """
    Simplified usage statistics manager with clear reset logic.
    """
    
    def __init__(self):
        self._lock = Lock()
        # 状态文件路径将在初始化时异步设置
        self._state_file = None
        self._state_manager = None
        self._storage_adapter = None
        self._stats_cache: Dict[str, Dict[str, Any]] = {}
        self._initialized = False
        self._cache_dirty = False  # 缓存脏标记，减少不必要的写入
        self._last_save_time = 0
        self._save_interval = 60  # 最多每分钟保存一次，减少I/O
        self._max_cache_size = 100  # 严格限制缓存大小
    
    async def initialize(self):
        """Initialize the usage stats module."""
        if self._initialized:
            return
        
        # 初始化存储适配器
        self._storage_adapter = await get_storage_adapter()
        
        # 只在文件模式下创建本地状态文件
        if not await is_mongodb_mode():
            credentials_dir = await get_credentials_dir()
            self._state_file = os.path.join(credentials_dir, "creds_state.toml")
            self._state_manager = get_state_manager(self._state_file)
        
        await self._load_stats()
        self._initialized = True
        storage_type = "MongoDB" if await is_mongodb_mode() else "File"
        log.debug(f"Usage statistics module initialized with {storage_type} storage backend")
        
    
    def _normalize_filename(self, filename: str) -> str:
        """Normalize filename to relative path for consistent storage."""
        if not filename:
            return ""
            
        if os.path.sep not in filename and "/" not in filename:
            return filename
            
        return os.path.basename(filename)
    
    def _is_gemini_2_5_pro(self, model_name: str) -> bool:
        """
        Check if model is gemini-2.5-pro variant (including prefixes and suffixes).
        """
        if not model_name:
            return False
        
        try:
            from config import get_base_model_name, get_base_model_from_feature_model
            
            # Remove feature prefixes (流式抗截断/, 假流式/)
            base_with_suffix = get_base_model_from_feature_model(model_name)
            
            # Remove thinking/search suffixes (-maxthinking, -nothinking, -search)
            pure_base_model = get_base_model_name(base_with_suffix)
            
            # Check if the pure base model is exactly "gemini-2.5-pro"
            return pure_base_model == "gemini-2.5-pro"
            
        except ImportError:
            # Fallback logic if config import fails
            clean_model = model_name
            for prefix in ["流式抗截断/", "假流式/"]:
                if clean_model.startswith(prefix):
                    clean_model = clean_model[len(prefix):]
                    break
            
            for suffix in ["-maxthinking", "-nothinking", "-search"]:
                if clean_model.endswith(suffix):
                    clean_model = clean_model[:-len(suffix)]
                    break
            
            return clean_model == "gemini-2.5-pro"
    
    async def _load_stats(self):
        """Load statistics from unified storage"""
        try:
            # 从统一存储获取所有使用统计，添加超时机制防止卡死
            import asyncio
            
            async def load_stats_with_timeout():
                all_usage_stats = await self._storage_adapter.get_all_usage_stats()
                
                log.debug(f"Processing {len(all_usage_stats)} usage statistics items...")
                
                # 直接处理统计数据
                stats_cache = {}
                processed_count = 0
                
                for filename, stats_data in all_usage_stats.items():
                    if isinstance(stats_data, dict):
                        normalized_filename = self._normalize_filename(filename)
                        
                        # 提取使用统计字段
                        usage_data = {
                            "gemini_2_5_pro_calls": stats_data.get("gemini_2_5_pro_calls", 0),
                            "total_calls": stats_data.get("total_calls", 0),
                            "next_reset_time": stats_data.get("next_reset_time"),
                            "daily_limit_gemini_2_5_pro": stats_data.get("daily_limit_gemini_2_5_pro", 100),
                            "daily_limit_total": stats_data.get("daily_limit_total", 1000)
                        }
                        
                        # 只加载有实际使用数据的统计，或者有reset时间的
                        if (usage_data.get("gemini_2_5_pro_calls", 0) > 0 or 
                            usage_data.get("total_calls", 0) > 0 or 
                            usage_data.get("next_reset_time")):
                            stats_cache[normalized_filename] = usage_data
                            processed_count += 1
                
                return stats_cache, processed_count
            
            # 设置15秒超时防止卡死
            try:
                self._stats_cache, processed_count = await asyncio.wait_for(
                    load_stats_with_timeout(), timeout=15.0
                )
                log.debug(f"Loaded usage statistics for {processed_count} credential files")
            except asyncio.TimeoutError:
                log.error("Loading usage statistics timed out after 30 seconds, using empty cache")
                self._stats_cache = {}
                return
            
        except Exception as e:
            log.error(f"Failed to load usage statistics: {e}")
            self._stats_cache = {}
    
    async def _save_stats(self):
        """Save statistics to unified storage."""
        current_time = time.time()
        
        # 使用脏标记和时间间隔控制，减少不必要的写入
        if not self._cache_dirty or (current_time - self._last_save_time < self._save_interval):
            return
            
        try:
            # 批量更新使用统计到存储适配器
            log.debug(f"Saving {len(self._stats_cache)} usage statistics items...")
            
            saved_count = 0
            for filename, stats in self._stats_cache.items():
                try:
                    stats_data = {
                        "gemini_2_5_pro_calls": stats.get("gemini_2_5_pro_calls", 0),
                        "total_calls": stats.get("total_calls", 0),
                        "next_reset_time": stats.get("next_reset_time"),
                        "daily_limit_gemini_2_5_pro": stats.get("daily_limit_gemini_2_5_pro", 100),
                        "daily_limit_total": stats.get("daily_limit_total", 1000)
                    }
                    
                    success = await self._storage_adapter.update_usage_stats(filename, stats_data)
                    if success:
                        saved_count += 1
                except Exception as e:
                    log.error(f"Failed to save stats for {filename}: {e}")
                    continue
                
            self._cache_dirty = False  # 清除脏标记
            self._last_save_time = current_time
            log.debug(f"Successfully saved {saved_count}/{len(self._stats_cache)} usage statistics to unified storage")
        except Exception as e:
            log.error(f"Failed to save usage statistics: {e}")
    
    def _get_or_create_stats(self, filename: str) -> Dict[str, Any]:
        """Get or create statistics entry for a credential file."""
        normalized_filename = self._normalize_filename(filename)
        
        if normalized_filename not in self._stats_cache:
            # 严格控制缓存大小 - 超过限制时删除最旧的条目
            if len(self._stats_cache) >= self._max_cache_size:
                # 删除最旧的统计数据（基于next_reset_time或没有该字段的）
                oldest_key = min(self._stats_cache.keys(),
                               key=lambda k: self._stats_cache[k].get('next_reset_time', ''))
                del self._stats_cache[oldest_key]
                self._cache_dirty = True
                log.debug(f"Removed oldest usage stats cache entry: {oldest_key}")
            
            next_reset = _get_next_utc_7am()
            self._stats_cache[normalized_filename] = {
                "gemini_2_5_pro_calls": 0,
                "total_calls": 0,
                "next_reset_time": next_reset.isoformat(),
                "daily_limit_gemini_2_5_pro": 100,
                "daily_limit_total": 1000
            }
            self._cache_dirty = True  # 标记缓存已修改
        
        return self._stats_cache[normalized_filename]
    
    def _check_and_reset_daily_quota(self, stats: Dict[str, Any]) -> bool:
        """
        Simple reset logic: if current time >= next_reset_time, then reset.
        """
        try:
            next_reset_str = stats.get("next_reset_time")
            if not next_reset_str:
                # No next reset time recorded, set it up
                next_reset = _get_next_utc_7am()
                stats["next_reset_time"] = next_reset.isoformat()
                return False
            
            next_reset = datetime.fromisoformat(next_reset_str)
            now = datetime.now(timezone.utc)
            
            # Simple comparison: if current time >= next reset time, then reset
            if now >= next_reset:
                old_gemini_calls = stats.get("gemini_2_5_pro_calls", 0)
                old_total_calls = stats.get("total_calls", 0)
                
                # Reset counters and set new next reset time
                new_next_reset = _get_next_utc_7am()
                stats.update({
                    "gemini_2_5_pro_calls": 0,
                    "total_calls": 0,
                    "next_reset_time": new_next_reset.isoformat()
                })
                
                self._cache_dirty = True  # 标记缓存已修改
                log.info(f"Daily quota reset performed. Previous stats - Gemini 2.5 Pro: {old_gemini_calls}, Total: {old_total_calls}")
                return True
            
            return False
        except Exception as e:
            log.error(f"Error in daily quota reset check: {e}")
            return False
    
    async def record_successful_call(self, filename: str, model_name: str):
        """Record a successful API call for statistics."""
        if not self._initialized:
            await self.initialize()
        
        with self._lock:
            try:
                normalized_filename = self._normalize_filename(filename)
                stats = self._get_or_create_stats(normalized_filename)
                
                # Check and perform daily reset if needed
                reset_performed = self._check_and_reset_daily_quota(stats)
                
                # Increment counters
                is_gemini_2_5_pro = self._is_gemini_2_5_pro(model_name)
                
                stats["total_calls"] += 1
                if is_gemini_2_5_pro:
                    stats["gemini_2_5_pro_calls"] += 1
                
                self._cache_dirty = True  # 标记缓存已修改
                
                log.debug(f"Usage recorded - File: {normalized_filename}, Model: {model_name}, "
                         f"Gemini 2.5 Pro: {stats['gemini_2_5_pro_calls']}/{stats.get('daily_limit_gemini_2_5_pro', 100)}, "
                         f"Total: {stats['total_calls']}/{stats.get('daily_limit_total', 1000)}")
                
                if reset_performed:
                    log.info(f"Daily quota was reset for {normalized_filename}")
                
            except Exception as e:
                log.error(f"Failed to record usage statistics: {e}")
        
        # Save stats asynchronously
        try:
            await self._save_stats()
        except Exception as e:
            log.error(f"Failed to save usage statistics after recording: {e}")
    
    async def get_usage_stats(self, filename: str = None) -> Dict[str, Any]:
        """Get usage statistics."""
        if not self._initialized:
            await self.initialize()
        
        with self._lock:
            if filename:
                normalized_filename = self._normalize_filename(filename)
                stats = self._get_or_create_stats(normalized_filename)
                # Check for daily reset before returning stats
                self._check_and_reset_daily_quota(stats)
                return {
                    "filename": normalized_filename,
                    "gemini_2_5_pro_calls": stats.get("gemini_2_5_pro_calls", 0),
                    "total_calls": stats.get("total_calls", 0),
                    "daily_limit_gemini_2_5_pro": stats.get("daily_limit_gemini_2_5_pro", 100),
                    "daily_limit_total": stats.get("daily_limit_total", 1000),
                    "next_reset_time": stats.get("next_reset_time")
                }
            else:
                # Return all statistics
                all_stats = {}
                for filename, stats in self._stats_cache.items():
                    # Check for daily reset for each file
                    self._check_and_reset_daily_quota(stats)
                    all_stats[filename] = {
                        "gemini_2_5_pro_calls": stats.get("gemini_2_5_pro_calls", 0),
                        "total_calls": stats.get("total_calls", 0),
                        "daily_limit_gemini_2_5_pro": stats.get("daily_limit_gemini_2_5_pro", 100),
                        "daily_limit_total": stats.get("daily_limit_total", 1000),
                        "next_reset_time": stats.get("next_reset_time")
                    }
                
                return all_stats
    
    async def get_aggregated_stats(self) -> Dict[str, Any]:
        """Get aggregated statistics across all credential files."""
        if not self._initialized:
            await self.initialize()
        
        all_stats = await self.get_usage_stats()
        
        total_gemini_2_5_pro = 0
        total_all_models = 0
        total_files = len(all_stats)
        
        for stats in all_stats.values():
            total_gemini_2_5_pro += stats["gemini_2_5_pro_calls"]
            total_all_models += stats["total_calls"]
        
        return {
            "total_files": total_files,
            "total_gemini_2_5_pro_calls": total_gemini_2_5_pro,
            "total_all_model_calls": total_all_models,
            "avg_gemini_2_5_pro_per_file": total_gemini_2_5_pro / max(total_files, 1),
            "avg_total_per_file": total_all_models / max(total_files, 1),
            "next_reset_time": _get_next_utc_7am().isoformat()
        }
    
    async def update_daily_limits(self, filename: str, gemini_2_5_pro_limit: int = None, 
                                total_limit: int = None):
        """Update daily limits for a specific credential file."""
        if not self._initialized:
            await self.initialize()
        
        with self._lock:
            try:
                normalized_filename = self._normalize_filename(filename)
                stats = self._get_or_create_stats(normalized_filename)
                
                if gemini_2_5_pro_limit is not None:
                    stats["daily_limit_gemini_2_5_pro"] = gemini_2_5_pro_limit
                
                if total_limit is not None:
                    stats["daily_limit_total"] = total_limit
                
                log.info(f"Updated daily limits for {normalized_filename}: "
                        f"Gemini 2.5 Pro = {stats.get('daily_limit_gemini_2_5_pro', 100)}, "
                        f"Total = {stats.get('daily_limit_total', 1000)}")
                
            except Exception as e:
                log.error(f"Failed to update daily limits: {e}")
                raise
        
        await self._save_stats()
    
    async def reset_stats(self, filename: str = None):
        """Reset usage statistics."""
        if not self._initialized:
            await self.initialize()
        
        with self._lock:
            if filename:
                normalized_filename = self._normalize_filename(filename)
                if normalized_filename in self._stats_cache:
                    # Manual reset: reset counters and set new next reset time
                    next_reset = _get_next_utc_7am()
                    self._stats_cache[normalized_filename].update({
                        "gemini_2_5_pro_calls": 0,
                        "total_calls": 0,
                        "next_reset_time": next_reset.isoformat()
                    })
                    log.info(f"Reset usage statistics for {normalized_filename}")
            else:
                # Reset all statistics
                next_reset = _get_next_utc_7am()
                for filename, stats in self._stats_cache.items():
                    stats.update({
                        "gemini_2_5_pro_calls": 0,
                        "total_calls": 0,
                        "next_reset_time": next_reset.isoformat()
                    })
                log.info("Reset usage statistics for all credential files")
        
        await self._save_stats()

# Global instance
_usage_stats_instance: Optional[UsageStats] = None

async def get_usage_stats_instance() -> UsageStats:
    """Get the global usage statistics instance."""
    global _usage_stats_instance
    if _usage_stats_instance is None:
        _usage_stats_instance = UsageStats()
        await _usage_stats_instance.initialize()
    return _usage_stats_instance


async def record_successful_call(filename: str, model_name: str):
    """Convenience function to record a successful API call."""
    stats = await get_usage_stats_instance()
    await stats.record_successful_call(filename, model_name)


async def get_usage_stats(filename: str = None) -> Dict[str, Any]:
    """Convenience function to get usage statistics."""
    stats = await get_usage_stats_instance()
    return await stats.get_usage_stats(filename)


async def get_aggregated_stats() -> Dict[str, Any]:
    """Convenience function to get aggregated statistics."""
    stats = await get_usage_stats_instance()
    return await stats.get_aggregated_stats()