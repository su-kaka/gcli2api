"""
Usage statistics module for tracking API calls per credential file.
只保留24小时调用计数，不存储时间戳列表
"""

import os
import time
from threading import Lock
from typing import Any, Dict, Optional

from config import get_credentials_dir, is_mongodb_mode
from log import log

from .state_manager import get_state_manager
from .storage_adapter import get_storage_adapter


class UsageStats:
    """
    优化的使用统计管理器 - 只保留24小时调用总数和最后重置时间
    不再存储每个时间戳列表
    """

    def __init__(self):
        self._lock = Lock()
        self._state_file = None
        self._state_manager = None
        self._storage_adapter = None
        # 优化：只保留计数和重置时间，不保留时间戳列表
        self._stats_cache: Dict[str, Dict[str, Any]] = {}  # {filename: {calls_24h: int, last_reset: timestamp}}
        self._initialized = False
        self._cache_dirty = False
        self._last_save_time = 0
        self._save_interval = 60  # Save at most once per minute
        self._max_cache_size = 100

    async def initialize(self):
        """Initialize the usage stats module."""
        if self._initialized:
            return

        self._storage_adapter = await get_storage_adapter()

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

    async def _load_stats(self):
        """从统一存储加载统计数据 - 优化版本，只加载计数"""
        try:
            import asyncio

            async def load_stats_with_timeout():
                all_usage_stats = await self._storage_adapter.get_all_usage_stats()

                stats_cache = {}
                processed_count = 0

                current_time = time.time()
                for filename, stats_data in all_usage_stats.items():
                    if isinstance(stats_data, dict):
                        normalized_filename = self._normalize_filename(filename)

                        # 优化：只加载计数和重置时间
                        last_reset = stats_data.get("last_reset", current_time)
                        calls_24h = stats_data.get("calls_24h", 0)

                        # 检查是否需要重置（超过24小时）
                        if current_time - last_reset > 86400:
                            calls_24h = 0
                            last_reset = current_time

                        usage_data = {
                            "calls_24h": calls_24h,
                            "last_reset": last_reset,
                        }

                        stats_cache[normalized_filename] = usage_data
                        processed_count += 1

                return stats_cache, processed_count

            try:
                self._stats_cache, processed_count = await asyncio.wait_for(
                    load_stats_with_timeout(), timeout=15.0
                )
                log.debug(f"Loaded usage statistics for {processed_count} credential files")
            except asyncio.TimeoutError:
                log.error("Loading usage statistics timed out after 15 seconds, using empty cache")
                self._stats_cache = {}
                return

        except Exception as e:
            log.error(f"Failed to load usage statistics: {e}")
            self._stats_cache = {}

    async def _save_stats(self):
        """保存统计数据到统一存储 - 优化版本，只存储计数"""
        current_time = time.time()

        if not self._cache_dirty or (current_time - self._last_save_time < self._save_interval):
            return

        try:
            saved_count = 0
            for filename, stats in self._stats_cache.items():
                try:
                    stats_data = {
                        "calls_24h": stats.get("calls_24h", 0),
                        "last_reset": stats.get("last_reset", current_time),
                    }

                    success = await self._storage_adapter.update_usage_stats(filename, stats_data)
                    if success:
                        saved_count += 1
                except Exception as e:
                    log.error(f"Failed to save stats for {filename}: {e}")
                    continue

            self._cache_dirty = False
            self._last_save_time = current_time
            log.debug(
                f"Successfully saved {saved_count}/{len(self._stats_cache)} usage statistics to unified storage"
            )
        except Exception as e:
            log.error(f"Failed to save usage statistics: {e}")

    def _get_or_create_stats(self, filename: str) -> Dict[str, Any]:
        """Get or create statistics entry for a credential file."""
        normalized_filename = self._normalize_filename(filename)

        if normalized_filename not in self._stats_cache:
            # Control cache size - remove oldest entry if limit reached
            if len(self._stats_cache) >= self._max_cache_size:
                # Remove entry with lowest call count
                oldest_key = min(
                    self._stats_cache.keys(),
                    key=lambda k: self._stats_cache[k].get("calls_24h", 0),
                )
                del self._stats_cache[oldest_key]
                self._cache_dirty = True
                log.debug(f"Removed lowest usage stats cache entry: {oldest_key}")

            self._stats_cache[normalized_filename] = {
                "calls_24h": 0,
                "last_reset": time.time(),
            }
            self._cache_dirty = True

        return self._stats_cache[normalized_filename]

    def _check_and_reset_if_needed(self, stats: Dict[str, Any]):
        """检查并重置过期的统计数据"""
        current_time = time.time()
        last_reset = stats.get("last_reset", current_time)

        # 如果超过24小时，重置计数
        if current_time - last_reset > 86400:
            stats["calls_24h"] = 0
            stats["last_reset"] = current_time
            self._cache_dirty = True

    async def record_successful_call(self, filename: str, model_name: str = None):
        """Record a successful API call."""
        if not self._initialized:
            await self.initialize()

        with self._lock:
            try:
                normalized_filename = self._normalize_filename(filename)
                stats = self._get_or_create_stats(normalized_filename)

                # 检查并重置
                self._check_and_reset_if_needed(stats)

                # 增加计数
                stats["calls_24h"] += 1
                self._cache_dirty = True

                log.debug(
                    f"Usage recorded - File: {normalized_filename}, "
                    f"24h calls: {stats['calls_24h']}"
                )

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
                self._check_and_reset_if_needed(stats)

                return {
                    "filename": normalized_filename,
                    "calls_24h": stats.get("calls_24h", 0),
                }
            else:
                # Return all statistics
                all_stats = {}
                for filename, stats in self._stats_cache.items():
                    self._check_and_reset_if_needed(stats)
                    all_stats[filename] = {
                        "calls_24h": stats.get("calls_24h", 0),
                    }

                return all_stats

    async def get_aggregated_stats(self) -> Dict[str, Any]:
        """Get aggregated statistics across all credential files."""
        if not self._initialized:
            await self.initialize()

        all_stats = await self.get_usage_stats()

        total_calls = 0
        total_files = len(all_stats)

        for stats in all_stats.values():
            total_calls += stats["calls_24h"]

        return {
            "total_files": total_files,
            "total_calls_24h": total_calls,
            "avg_calls_per_file": total_calls / max(total_files, 1),
        }

    async def reset_stats(self, filename: str = None):
        """Reset usage statistics."""
        if not self._initialized:
            await self.initialize()

        with self._lock:
            if filename:
                normalized_filename = self._normalize_filename(filename)
                if normalized_filename in self._stats_cache:
                    self._stats_cache[normalized_filename]["calls_24h"] = 0
                    self._stats_cache[normalized_filename]["last_reset"] = time.time()
                    self._cache_dirty = True
                    log.info(f"Reset usage statistics for {normalized_filename}")
            else:
                # Reset all statistics
                current_time = time.time()
                for stats in self._stats_cache.values():
                    stats["calls_24h"] = 0
                    stats["last_reset"] = current_time
                self._cache_dirty = True
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


async def record_successful_call(filename: str, model_name: str = None):
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
