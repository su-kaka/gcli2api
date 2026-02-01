"""
MySQL 存储管理器
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiomysql

from log import log


class MySQLManager:
    """MySQL 数据库管理器"""

    # 状态字段常量
    STATE_FIELDS = {
        "error_codes",
        "error_messages", 
        "disabled",
        "last_success",
        "user_email",
        "model_cooldowns",
    }

    def __init__(self):
        self._pool = None
        self._initialized = False
        self._lock = asyncio.Lock()

        # 内存配置缓存
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

    async def initialize(self) -> None:
        """初始化 MySQL 连接池"""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                # 使用 MYSQL_DSN 配置
                mysql_dsn = os.getenv("MYSQL_DSN")
                
                if not mysql_dsn:
                    raise ValueError("MYSQL_DSN environment variable is required")
                
                # 解析 DSN 格式：mysql://user:password@host:port/database
                connection_params = self._parse_mysql_dsn(mysql_dsn)
                mysql_host = connection_params["host"]
                mysql_port = connection_params["port"]
                mysql_user = connection_params["user"]
                mysql_password = connection_params["password"]
                mysql_database = connection_params["database"]

                # 创建连接池
                self._pool = await aiomysql.create_pool(
                    host=mysql_host,
                    port=mysql_port,
                    user=mysql_user,
                    password=mysql_password,
                    db=mysql_database,
                    minsize=1,
                    maxsize=10,
                    autocommit=True
                )

                # 创建表和索引
                await self._create_tables()

                # 加载配置到内存
                await self._load_config_cache()

                self._initialized = True
                log.info(f"MySQL storage initialized (database: {mysql_database})")

            except Exception as e:
                log.error(f"Error initializing MySQL: {e}")
                raise

    def _parse_mysql_dsn(self, dsn: str) -> Dict[str, Any]:
        """解析 MySQL DSN 格式：mysql://user:password@host:port/database"""
        import urllib.parse
        
        try:
            # 解析 DSN
            parsed = urllib.parse.urlparse(dsn)
            
            if parsed.scheme != "mysql":
                raise ValueError(f"Invalid DSN scheme: {parsed.scheme}. Expected 'mysql'")
            
            # 提取连接参数
            host = parsed.hostname or "localhost"
            port = parsed.port or 3306
            user = parsed.username or "root"
            password = parsed.password or ""
            
            # 数据库名（去掉开头的/）
            database = parsed.path.lstrip("/") or "gcli2api"
            
            # 解析查询参数（可选）
            query_params = urllib.parse.parse_qs(parsed.query)
            
            return {
                "host": host,
                "port": port,
                "user": user,
                "password": password,
                "database": database,
                "query_params": query_params
            }
            
        except Exception as e:
            log.error(f"Error parsing MySQL DSN: {e}")
            raise ValueError(f"Invalid MySQL DSN format: {dsn}") from e

    async def _create_tables(self):
        """创建数据库表和索引"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cursor:
                # 凭证表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS credentials (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        filename VARCHAR(255) UNIQUE NOT NULL,
                        credential_data JSON NOT NULL,
                        
                        -- 状态字段
                        disabled TINYINT DEFAULT 0,
                        error_codes JSON DEFAULT '[]',
                        last_success DOUBLE,
                        user_email VARCHAR(255),
                        
                        -- 模型级 CD 支持
                        model_cooldowns JSON DEFAULT '{}',
                        
                        -- 轮换相关
                        rotation_order INT DEFAULT 0,
                        call_count INT DEFAULT 0,
                        
                        -- 时间戳
                        created_at DOUBLE DEFAULT (UNIX_TIMESTAMP()),
                        updated_at DOUBLE DEFAULT (UNIX_TIMESTAMP()),
                        
                        INDEX idx_disabled (disabled),
                        INDEX idx_rotation_order (rotation_order),
                        INDEX idx_filename (filename)
                    )
                """)

                # Antigravity 凭证表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS antigravity_credentials (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        filename VARCHAR(255) UNIQUE NOT NULL,
                        credential_data JSON NOT NULL,
                        
                        -- 状态字段
                        disabled TINYINT DEFAULT 0,
                        error_codes JSON DEFAULT '[]',
                        last_success DOUBLE,
                        user_email VARCHAR(255),
                        
                        -- 模型级 CD 支持
                        model_cooldowns JSON DEFAULT '{}',
                        
                        -- 轮换相关
                        rotation_order INT DEFAULT 0,
                        call_count INT DEFAULT 0,
                        
                        -- 时间戳
                        created_at DOUBLE DEFAULT (UNIX_TIMESTAMP()),
                        updated_at DOUBLE DEFAULT (UNIX_TIMESTAMP()),
                        
                        INDEX idx_ag_disabled (disabled),
                        INDEX idx_ag_rotation_order (rotation_order),
                        INDEX idx_ag_filename (filename)
                    )
                """)

                # 配置表
                await cursor.execute("""
                    CREATE TABLE IF NOT EXISTS config (
                        key VARCHAR(255) PRIMARY KEY,
                        value JSON,
                        updated_at DOUBLE DEFAULT (UNIX_TIMESTAMP())
                    )
                """)

            await conn.commit()
            log.debug("MySQL tables and indexes created")

    async def _load_config_cache(self):
        """加载配置到内存缓存"""
        if self._config_loaded:
            return

        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT key, value FROM config")
                    rows = await cursor.fetchall()

                    for key, value in rows:
                        try:
                            self._config_cache[key] = json.loads(value) if value else None
                        except json.JSONDecodeError:
                            self._config_cache[key] = value

            self._config_loaded = True
            log.debug(f"Loaded {len(self._config_cache)} config items into cache")

        except Exception as e:
            log.error(f"Error loading config cache: {e}")
            self._config_cache = {}

    async def close(self) -> None:
        """关闭连接池"""
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
        self._initialized = False
        log.debug("MySQL storage closed")

    def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            raise RuntimeError("MySQL manager not initialized")

    def _get_table_name(self, mode: str) -> str:
        """根据 mode 获取对应的表名"""
        if mode == "antigravity":
            return "antigravity_credentials"
        elif mode == "geminicli":
            return "credentials"
        else:
            raise ValueError(f"Invalid mode: {mode}. Must be 'geminicli' or 'antigravity'")

    # ============ 核心方法 ============

    async def get_next_available_credential(
        self, mode: str = "geminicli", model_key: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        随机获取一个可用凭证（负载均衡）
        """
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    current_time = time.time()

                    if not model_key:
                        # 没有模型键，随机选择一个可用凭证
                        await cursor.execute(f"""
                            SELECT filename, credential_data
                            FROM {table_name}
                            WHERE disabled = 0
                            ORDER BY RAND()
                            LIMIT 1
                        """)
                        row = await cursor.fetchone()
                        if row:
                            filename, credential_json = row
                            credential_data = json.loads(credential_json)
                            return filename, credential_data
                        return None

                    # 有模型键，需要检查模型级冷却
                    await cursor.execute(f"""
                        SELECT filename, credential_data, model_cooldowns
                        FROM {table_name}
                        WHERE disabled = 0
                        ORDER BY RAND()
                    """)
                    rows = await cursor.fetchall()

                    for filename, credential_json, model_cooldowns_json in rows:
                        model_cooldowns = json.loads(model_cooldowns_json or '{}')
                        model_cooldown = model_cooldowns.get(model_key)
                        
                        if model_cooldown is None or current_time >= model_cooldown:
                            credential_data = json.loads(credential_json)
                            return filename, credential_data

                    return None

        except Exception as e:
            log.error(f"Error getting next available credential: {e}")
            return None

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        """存储或更新凭证"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 检查凭证是否存在
                    await cursor.execute(f"""
                        SELECT disabled, error_codes, last_success, user_email,
                               rotation_order, call_count
                        FROM {table_name} WHERE filename = %s
                    """, (filename,))
                    existing = await cursor.fetchone()

                    credential_json = json.dumps(credential_data)
                    current_time = time.time()

                    if existing:
                        # 更新现有凭证
                        await cursor.execute(f"""
                            UPDATE {table_name}
                            SET credential_data = %s, updated_at = %s
                            WHERE filename = %s
                        """, (credential_json, current_time, filename))
                    else:
                        # 获取下一个轮换顺序
                        await cursor.execute(f"""
                            SELECT COALESCE(MAX(rotation_order), -1) + 1 FROM {table_name}
                        """)
                        row = await cursor.fetchone()
                        next_order = row[0] if row else 0

                        # 插入新凭证
                        await cursor.execute(f"""
                            INSERT INTO {table_name}
                            (filename, credential_data, rotation_order, last_success)
                            VALUES (%s, %s, %s, %s)
                        """, (filename, credential_json, next_order, current_time))

                    await conn.commit()
                    log.debug(f"Stored credential: {filename} (mode={mode})")
                    return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str, mode: str = "geminicli") -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 精确匹配
                    await cursor.execute(f"""
                        SELECT credential_data FROM {table_name} WHERE filename = %s
                    """, (filename,))
                    row = await cursor.fetchone()
                    if row:
                        return json.loads(row[0])

                    # basename匹配
                    await cursor.execute(f"""
                        SELECT credential_data FROM {table_name}
                        WHERE filename LIKE CONCAT('%%', %s)
                    """, (filename,))
                    row = await cursor.fetchone()
                    if row:
                        return json.loads(row[0])

                    return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        """列出所有凭证文件名"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(f"""
                        SELECT filename FROM {table_name} ORDER BY rotation_order
                    """)
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str, mode: str = "geminicli") -> bool:
        """删除凭证"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 精确匹配删除
                    await cursor.execute(f"""
                        DELETE FROM {table_name} WHERE filename = %s
                    """, (filename,))
                    deleted_count = cursor.rowcount

                    # basename匹配删除
                    if deleted_count == 0:
                        await cursor.execute(f"""
                            DELETE FROM {table_name} WHERE filename LIKE CONCAT('%%', %s)
                        """, (filename,))
                        deleted_count = cursor.rowcount

                    await conn.commit()
                    
                    if deleted_count > 0:
                        log.debug(f"Deleted credential: {filename} (mode={mode})")
                        return True
                    else:
                        log.warning(f"No credential found to delete: {filename} (mode={mode})")
                        return False

        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False

    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any], mode: str = "geminicli") -> bool:
        """更新凭证状态"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 构建SET子句
                    set_clauses = []
                    values = []
                    
                    for key, value in state_updates.items():
                        if key in self.STATE_FIELDS:
                            if key in ("error_codes", "error_messages", "model_cooldowns"):
                                set_clauses.append(f"{key} = %s")
                                values.append(json.dumps(value))
                            else:
                                set_clauses.append(f"{key} = %s")
                                values.append(value)

                    if not set_clauses:
                        return True

                    set_clauses.append("updated_at = UNIX_TIMESTAMP()")
                    values.append(filename)

                    # 精确匹配更新
                    sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE filename = %s"
                    await cursor.execute(sql, values)
                    updated_count = cursor.rowcount

                    # basename匹配更新
                    if updated_count == 0:
                        values[-1] = filename  # 替换最后一个参数
                        sql = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE filename LIKE CONCAT('%%', %s)"
                        await cursor.execute(sql, values)
                        updated_count = cursor.rowcount

                    await conn.commit()
                    return updated_count > 0

        except Exception as e:
            log.error(f"Error updating credential state: {e}")
            return False

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        """获取凭证状态"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 精确匹配
                    await cursor.execute(f"""
                        SELECT disabled, error_codes, last_success, user_email, model_cooldowns
                        FROM {table_name} WHERE filename = %s
                    """, (filename,))
                    row = await cursor.fetchone()

                    if row:
                        error_codes = json.loads(row[1] or '[]')
                        model_cooldowns = json.loads(row[4] or '{}')
                        return {
                            "disabled": bool(row[0]),
                            "error_codes": error_codes,
                            "last_success": row[2] or time.time(),
                            "user_email": row[3],
                            "model_cooldowns": model_cooldowns,
                        }

                    # basename匹配
                    await cursor.execute(f"""
                        SELECT disabled, error_codes, last_success, user_email, model_cooldowns
                        FROM {table_name} WHERE filename LIKE CONCAT('%%', %s)
                    """, (filename,))
                    row = await cursor.fetchone()

                    if row:
                        error_codes = json.loads(row[1] or '[]')
                        model_cooldowns = json.loads(row[4] or '{}')
                        return {
                            "disabled": bool(row[0]),
                            "error_codes": error_codes,
                            "last_success": row[2] or time.time(),
                            "user_email": row[3],
                            "model_cooldowns": model_cooldowns,
                        }

                    # 默认状态
                    return {
                        "disabled": False,
                        "error_codes": [],
                        "last_success": time.time(),
                        "user_email": None,
                        "model_cooldowns": {},
                    }

        except Exception as e:
            log.error(f"Error getting credential state: {e}")
            return {}

    async def get_all_credential_states(self, mode: str = "geminicli") -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute(f"""
                        SELECT filename, disabled, error_codes, last_success, user_email, model_cooldowns
                        FROM {table_name}
                    """)
                    rows = await cursor.fetchall()

                    states = {}
                    current_time = time.time()

                    for row in rows:
                        filename = row[0]
                        error_codes = json.loads(row[2] or '[]')
                        model_cooldowns = json.loads(row[5] or '{}')

                        # 自动过滤掉已过期的模型CD
                        if model_cooldowns:
                            model_cooldowns = {
                                k: v for k, v in model_cooldowns.items()
                                if v > current_time
                            }

                        states[filename] = {
                            "disabled": bool(row[1]),
                            "error_codes": error_codes,
                            "last_success": row[3] or current_time,
                            "user_email": row[4],
                            "model_cooldowns": model_cooldowns,
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
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("""
                        INSERT INTO config (key, value, updated_at)
                        VALUES (%s, %s, UNIX_TIMESTAMP())
                        ON DUPLICATE KEY UPDATE
                            value = VALUES(value),
                            updated_at = VALUES(updated_at)
                    """, (key, json.dumps(value)))
                    await conn.commit()

            self._config_cache[key] = value
            return True

        except Exception as e:
            log.error(f"Error setting config {key}: {e}")
            return False

    async def get_config(self, key: str, default: Any = None) -> Any:
        """获取配置"""
        self._ensure_initialized()
        return self._config_cache.get(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        """获取所有配置"""
        self._ensure_initialized()
        return self._config_cache.copy()

    async def delete_config(self, key: str) -> bool:
        """删除配置"""
        self._ensure_initialized()

        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("DELETE FROM config WHERE key = %s", (key,))
                    await conn.commit()

            self._config_cache.pop(key, None)
            return True

        except Exception as e:
            log.error(f"Error deleting config {key}: {e}")
            return False

    async def set_model_cooldown(
        self,
        filename: str,
        model_key: str,
        cooldown_until: Optional[float],
        mode: str = "geminicli"
    ) -> bool:
        """设置模型冷却时间"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 获取当前冷却配置
                    await cursor.execute(f"""
                        SELECT model_cooldowns FROM {table_name} WHERE filename = %s
                    """, (filename,))
                    row = await cursor.fetchone()

                    if not row:
                        log.warning(f"Credential {filename} not found")
                        return False

                    model_cooldowns = json.loads(row[0] or '{}')

                    # 更新冷却配置
                    if cooldown_until is None:
                        model_cooldowns.pop(model_key, None)
                    else:
                        model_cooldowns[model_key] = cooldown_until

                    # 写回数据库
                    await cursor.execute(f"""
                        UPDATE {table_name}
                        SET model_cooldowns = %s, updated_at = UNIX_TIMESTAMP()
                        WHERE filename = %s
                    """, (json.dumps(model_cooldowns), filename))
                    await conn.commit()

                    return True

        except Exception as e:
            log.error(f"Error setting model cooldown: {e}")
            return False

    async def get_database_info(self) -> Dict[str, Any]:
        """获取数据库信息"""
        self._ensure_initialized()

        try:
            async with self._pool.acquire() as conn:
                async with conn.cursor() as cursor:
                    # 获取数据库基本信息
                    await cursor.execute("SELECT DATABASE()")
                    db_name = (await cursor.fetchone())[0]
                    
                    await cursor.execute("SELECT VERSION()")
                    db_version = (await cursor.fetchone())[0]
                    
                    # 获取表统计信息
                    await cursor.execute("""
                        SELECT 
                            table_name,
                            table_rows,
                            data_length,
                            index_length
                        FROM information_schema.tables 
                        WHERE table_schema = DATABASE()
                        AND table_name IN ('credentials', 'antigravity_credentials', 'config')
                    """)
                    
                    table_stats = {}
                    for table_name, table_rows, data_length, index_length in await cursor.fetchall():
                        table_stats[table_name] = {
                            "rows": table_rows,
                            "data_size": data_length,
                            "index_size": index_length,
                            "total_size": (data_length or 0) + (index_length or 0)
                        }
                    
                    # 获取连接池信息
                    pool_info = {
                        "minsize": self._pool.minsize if self._pool else 1,
                        "maxsize": self._pool.maxsize if self._pool else 10,
                        "size": self._pool.size if self._pool else 0,
                        "freesize": self._pool.freesize if self._pool else 0
                    }
                    
                    return {
                        "database_name": db_name,
                        "database_version": db_version,
                        "host": os.getenv("MYSQL_HOST", "localhost"),
                        "port": int(os.getenv("MYSQL_PORT", "3306")),
                        "user": os.getenv("MYSQL_USER", "root"),
                        "table_stats": table_stats,
                        "pool_info": pool_info,
                        "initialized": self._initialized
                    }

        except Exception as e:
            log.error(f"Error getting database info: {e}")
            return {
                "error": str(e),
                "initialized": self._initialized
            }
