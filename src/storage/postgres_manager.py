"""
Postgres数据库管理器，采用单行设计并兼容 UnifiedCacheManager。
实现与 mongodb_manager.py 风格一致的接口（异步）。
需要环境变量: POSTGRES_DSN (例如: postgresql://user:pass@host:port/dbname)
"""
import asyncio
import os
import time
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from collections import deque

import asyncpg
from log import log
from .cache_manager import UnifiedCacheManager, CacheBackend


class PostgresCacheBackend(CacheBackend):
    """Postgres缓存后端，数据存储为key, data(JSONB), updated_at
    单行/单表设计：表名由管理器指定，每行以key区分。
    """

    def __init__(self, conn_pool, table_name: str, row_key: str):
        self._pool = conn_pool
        self._table_name = table_name
        self._row_key = row_key

    async def load_data(self) -> Dict[str, Any]:
        try:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"SELECT data FROM {self._table_name} WHERE key = $1",
                    self._row_key
                )
                if row and row.get('data') is not None:
                    data = row['data']
                    # JSONB字段返回JSON字符串，需要解析为字典
                    if isinstance(data, str):
                        return json.loads(data)
                    elif isinstance(data, dict):
                        return data
                    else:
                        log.warning(f"Unexpected data type from JSONB field: {type(data)}")
                        return {}
                return {}
        except Exception as e:
            log.error(f"Error loading data from Postgres row {self._row_key}: {e}")
            return {}

    async def write_data(self, data: Dict[str, Any]) -> bool:
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    f"INSERT INTO {self._table_name}(key, data, updated_at) VALUES($1, $2::jsonb, $3)"
                    " ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data, updated_at = EXCLUDED.updated_at",
                    self._row_key, json.dumps(data, default=str), datetime.now(timezone.utc)
                )
                return True
        except Exception as e:
            log.error(f"Error writing data to Postgres row {self._row_key}: {e}")
            return False


class PostgresManager:
    """Postgres管理器。
    使用单表单行设计存储凭证和配置数据。
    """

    def __init__(self):
        self._pool: Optional[asyncpg.pool.Pool] = None
        self._initialized = False
        self._lock = asyncio.Lock()

        self._dsn = None
        self._table_name = 'unified_storage'

        self._operation_count = 0

        self._operation_times = deque(maxlen=5000)

        self._credentials_cache_manager: Optional[UnifiedCacheManager] = None
        self._config_cache_manager: Optional[UnifiedCacheManager] = None

        self._credentials_row_key = 'all_credentials'
        self._config_row_key = 'config_data'

        self._write_delay = 1.0
        self._cache_ttl = 300

    async def initialize(self):
        async with self._lock:
            if self._initialized:
                return
            try:
                self._dsn = os.getenv('POSTGRES_DSN')
                if not self._dsn:
                    raise ValueError('POSTGRES_DSN environment variable is required')

                self._pool = await asyncpg.create_pool(dsn=self._dsn, max_size=20, min_size=1)

                # 确保表存在
                await self._ensure_table()

                # 创建缓存管理器后端
                credentials_backend = PostgresCacheBackend(self._pool, self._table_name, self._credentials_row_key)
                config_backend = PostgresCacheBackend(self._pool, self._table_name, self._config_row_key)

                self._credentials_cache_manager = UnifiedCacheManager(
                    credentials_backend, cache_ttl=self._cache_ttl, write_delay=self._write_delay, name='credentials'
                )
                self._config_cache_manager = UnifiedCacheManager(
                    config_backend, cache_ttl=self._cache_ttl, write_delay=self._write_delay, name='config'
                )

                await self._credentials_cache_manager.start()
                await self._config_cache_manager.start()

                self._initialized = True
                log.info('Postgres connection established with unified cache')
            except Exception as e:
                log.error(f'Error initializing Postgres: {e}')
                raise

    async def _ensure_table(self):
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {self._table_name}(\n                        key TEXT PRIMARY KEY,\n                        data JSONB,\n                        updated_at TIMESTAMPTZ\n                    )"
                )
        except Exception as e:
            log.error(f'Error ensuring Postgres table: {e}')
            raise

    async def close(self):
        if self._credentials_cache_manager:
            await self._credentials_cache_manager.stop()
        if self._config_cache_manager:
            await self._config_cache_manager.stop()
        if self._pool:
            await self._pool.close()
            self._initialized = False
            log.info('Postgres connection closed with unified cache flushed')

    def _ensure_initialized(self):
        if not self._initialized:
            raise RuntimeError('Postgres manager not initialized')

    def _get_default_state(self) -> Dict[str, Any]:
        return {
            'error_codes': [],
            'disabled': False,
            'last_success': time.time(),
            'user_email': None,
        }

    def _get_default_stats(self) -> Dict[str, Any]:
        return {
            'gemini_2_5_pro_calls': 0,
            'total_calls': 0,
            'next_reset_time': None,
            'daily_limit_gemini_2_5_pro': 100,
            'daily_limit_total': 1000
        }

    # 以下方法委托给 UnifiedCacheManager
    async def store_credential(self, filename: str, credential_data: Dict[str, Any]) -> bool:
        self._ensure_initialized()
        start_time = time.time()
        try:
            existing_data = await self._credentials_cache_manager.get(filename, {})
            credential_entry = {
                'credential': credential_data,
                'state': existing_data.get('state', self._get_default_state()),
                'stats': existing_data.get('stats', self._get_default_stats())
            }
            success = await self._credentials_cache_manager.set(filename, credential_entry)
            self._operation_count += 1
            self._operation_times.append(time.time() - start_time)
            log.debug(f'Stored credential to unified cache (postgres): {filename}')
            return success
        except Exception as e:
            log.error(f'Error storing credential {filename} in Postgres: {e}')
            return False

    async def get_credential(self, filename: str) -> Optional[Dict[str, Any]]:
        self._ensure_initialized()
        try:
            credential_entry = await self._credentials_cache_manager.get(filename)
            self._operation_count += 1
            if credential_entry and 'credential' in credential_entry:
                return credential_entry['credential']
            return None
        except Exception as e:
            log.error(f'Error retrieving credential {filename} from Postgres: {e}')
            return None

    async def list_credentials(self) -> List[str]:
        self._ensure_initialized()
        try:
            all_data = await self._credentials_cache_manager.get_all()
            return list(all_data.keys())
        except Exception as e:
            log.error(f'Error listing credentials from Postgres: {e}')
            return []

    async def delete_credential(self, filename: str) -> bool:
        self._ensure_initialized()
        try:
            return await self._credentials_cache_manager.delete(filename)
        except Exception as e:
            log.error(f'Error deleting credential {filename} from Postgres: {e}')
            return False

    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any]) -> bool:
        self._ensure_initialized()
        try:
            existing_data = await self._credentials_cache_manager.get(filename, {})
            if not existing_data:
                existing_data = {'credential': {}, 'state': self._get_default_state(), 'stats': self._get_default_stats()}
            existing_data['state'].update(state_updates)
            return await self._credentials_cache_manager.set(filename, existing_data)
        except Exception as e:
            log.error(f'Error updating credential state {filename} in Postgres: {e}')
            return False

    async def get_credential_state(self, filename: str) -> Dict[str, Any]:
        self._ensure_initialized()
        try:
            credential_entry = await self._credentials_cache_manager.get(filename)
            if credential_entry and 'state' in credential_entry:
                return credential_entry['state']
            return self._get_default_state()
        except Exception as e:
            log.error(f'Error getting credential state {filename} from Postgres: {e}')
            return self._get_default_state()

    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_initialized()
        try:
            all_data = await self._credentials_cache_manager.get_all()
            states = {fn: data.get('state', self._get_default_state()) for fn, data in all_data.items()}
            return states
        except Exception as e:
            log.error(f'Error getting all credential states from Postgres: {e}')
            return {}

    async def set_config(self, key: str, value: Any) -> bool:
        self._ensure_initialized()
        return await self._config_cache_manager.set(key, value)

    async def get_config(self, key: str, default: Any = None) -> Any:
        self._ensure_initialized()
        return await self._config_cache_manager.get(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        self._ensure_initialized()
        return await self._config_cache_manager.get_all()

    async def delete_config(self, key: str) -> bool:
        self._ensure_initialized()
        return await self._config_cache_manager.delete(key)

    async def update_usage_stats(self, filename: str, stats_updates: Dict[str, Any]) -> bool:
        self._ensure_initialized()
        try:
            existing_data = await self._credentials_cache_manager.get(filename, {})
            if not existing_data:
                existing_data = {'credential': {}, 'state': self._get_default_state(), 'stats': self._get_default_stats()}
            existing_data['stats'].update(stats_updates)
            return await self._credentials_cache_manager.set(filename, existing_data)
        except Exception as e:
            log.error(f'Error updating usage stats for {filename} in Postgres: {e}')
            return False

    async def get_usage_stats(self, filename: str) -> Dict[str, Any]:
        self._ensure_initialized()
        try:
            credential_entry = await self._credentials_cache_manager.get(filename)
            if credential_entry and 'stats' in credential_entry:
                return credential_entry['stats']
            return self._get_default_stats()
        except Exception as e:
            log.error(f'Error getting usage stats for {filename} from Postgres: {e}')
            return self._get_default_stats()

    async def get_all_usage_stats(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_initialized()
        try:
            all_data = await self._credentials_cache_manager.get_all()
            stats = {fn: data.get('stats', self._get_default_stats()) for fn, data in all_data.items()}
            return stats
        except Exception as e:
            log.error(f'Error getting all usage stats from Postgres: {e}')
            return {}

