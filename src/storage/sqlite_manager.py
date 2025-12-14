"""
SQLite 存储管理器
"""

import asyncio
import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiosqlite
import toml

from log import log


class SQLiteManager:
    """SQLite 数据库管理器"""

    # 状态字段常量
    STATE_FIELDS = {
        "error_codes",
        "disabled",
        "last_success",
        "user_email",
        "cooldown_until",
    }

    def __init__(self):
        self._db_path = None
        self._credentials_dir = None
        self._initialized = False
        self._lock = asyncio.Lock()

        # 内存配置缓存 - 初始化时加载一次
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

    async def initialize(self) -> None:
        """初始化 SQLite 数据库"""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                # 获取凭证目录
                self._credentials_dir = os.getenv("CREDENTIALS_DIR", "./creds")
                self._db_path = os.path.join(self._credentials_dir, "credentials.db")

                # 确保目录存在
                os.makedirs(self._credentials_dir, exist_ok=True)

                # 检查数据库是否是新建的
                is_new_db = not os.path.exists(self._db_path)

                # 创建数据库和表
                async with aiosqlite.connect(self._db_path) as db:
                    # 启用 WAL 模式（提升并发性能）
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA foreign_keys=ON")

                    # 创建表
                    await self._create_tables(db)
                    await db.commit()

                # 如果是新数据库，尝试从 TOML 迁移
                if is_new_db:
                    await self._migrate_from_toml()

                # 加载配置到内存
                await self._load_config_cache()

                self._initialized = True
                log.info(f"SQLite storage initialized at {self._db_path}")

            except Exception as e:
                log.error(f"Error initializing SQLite: {e}")
                raise

    async def _create_tables(self, db: aiosqlite.Connection):
        """创建数据库表和索引"""
        # 凭证表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                credential_data TEXT NOT NULL,

                -- 状态字段
                disabled INTEGER DEFAULT 0,
                error_codes TEXT DEFAULT '[]',
                last_success REAL,
                user_email TEXT,
                cooldown_until REAL,

                -- 轮换相关
                rotation_order INTEGER DEFAULT 0,
                call_count INTEGER DEFAULT 0,

                -- 时间戳
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        # Antigravity 凭证表（结构相同但独立存储）
        await db.execute("""
            CREATE TABLE IF NOT EXISTS antigravity_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                credential_data TEXT NOT NULL,

                -- 状态字段
                disabled INTEGER DEFAULT 0,
                error_codes TEXT DEFAULT '[]',
                last_success REAL,
                user_email TEXT,
                cooldown_until REAL,

                -- 轮换相关
                rotation_order INTEGER DEFAULT 0,
                call_count INTEGER DEFAULT 0,

                -- 时间戳
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        # 创建索引 - 普通凭证表
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_disabled
            ON credentials(disabled)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_cooldown
            ON credentials(cooldown_until)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rotation_order
            ON credentials(rotation_order)
        """)

        # 创建索引 - Antigravity 凭证表
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ag_disabled
            ON antigravity_credentials(disabled)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ag_cooldown
            ON antigravity_credentials(cooldown_until)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ag_rotation_order
            ON antigravity_credentials(rotation_order)
        """)

        # 配置表
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        log.debug("SQLite tables and indexes created")

    async def _migrate_from_toml(self):
        """从 TOML 文件迁移数据到 SQLite"""
        creds_toml = os.path.join(self._credentials_dir, "creds.toml")
        config_toml = os.path.join(self._credentials_dir, "config.toml")

        if not os.path.exists(creds_toml):
            log.debug("No creds.toml found, skipping migration")
            return

        try:
            log.info("Starting migration from TOML to SQLite...")

            # 读取 TOML 数据
            async with aiofiles.open(creds_toml, "r", encoding="utf-8") as f:
                content = await f.read()
            toml_data = toml.loads(content)

            if not toml_data:
                log.info("TOML file is empty, skipping migration")
                return

            # 批量插入到数据库
            async with aiosqlite.connect(self._db_path) as db:
                credentials_to_insert = []

                for filename, section_data in toml_data.items():
                    # 分离凭证数据和状态数据
                    credential_data = {k: v for k, v in section_data.items()
                                     if k not in self.STATE_FIELDS}

                    # 提取状态字段
                    disabled = section_data.get("disabled", 0)
                    error_codes = json.dumps(section_data.get("error_codes", []))
                    last_success = section_data.get("last_success", time.time())
                    user_email = section_data.get("user_email")
                    cooldown_until = section_data.get("cooldown_until")

                    credentials_to_insert.append((
                        filename,
                        json.dumps(credential_data),
                        disabled,
                        error_codes,
                        last_success,
                        user_email,
                        cooldown_until,
                        len(credentials_to_insert),  # rotation_order
                    ))

                # 批量插入
                await db.executemany("""
                    INSERT INTO credentials
                    (filename, credential_data, disabled, error_codes,
                     last_success, user_email, cooldown_until, rotation_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, credentials_to_insert)

                # 迁移配置
                if os.path.exists(config_toml):
                    async with aiofiles.open(config_toml, "r", encoding="utf-8") as f:
                        config_content = await f.read()
                    config_data = toml.loads(config_content)

                    config_to_insert = [
                        (key, json.dumps(value))
                        for key, value in config_data.items()
                    ]

                    await db.executemany("""
                        INSERT INTO config (key, value)
                        VALUES (?, ?)
                    """, config_to_insert)

                await db.commit()

            log.info(f"Migration completed: {len(credentials_to_insert)} credentials migrated")

            # 备份原始文件
            backup_path = f"{creds_toml}.backup"
            shutil.copy2(creds_toml, backup_path)
            os.remove(creds_toml)
            log.info(f"Original TOML backed up to {backup_path}")

            if os.path.exists(config_toml):
                config_backup = f"{config_toml}.backup"
                shutil.copy2(config_toml, config_backup)
                os.remove(config_toml)

        except Exception as e:
            log.error(f"Migration failed: {e}")
            raise

    async def _load_config_cache(self):
        """加载配置到内存缓存（仅在初始化时调用一次）"""
        if self._config_loaded:
            return

        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute("SELECT key, value FROM config") as cursor:
                    rows = await cursor.fetchall()

                for key, value in rows:
                    try:
                        self._config_cache[key] = json.loads(value)
                    except json.JSONDecodeError:
                        self._config_cache[key] = value

            self._config_loaded = True
            log.debug(f"Loaded {len(self._config_cache)} config items into cache")

        except Exception as e:
            log.error(f"Error loading config cache: {e}")
            self._config_cache = {}

    async def close(self) -> None:
        """关闭数据库连接"""
        self._initialized = False
        log.debug("SQLite storage closed")

    def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            raise RuntimeError("SQLite manager not initialized")

    def _is_antigravity(self, filename: str) -> bool:
        """判断是否为 antigravity 凭证"""
        return filename.startswith("ag_")

    def _get_table_name(self, is_antigravity: bool) -> str:
        """根据 is_antigravity 标志获取对应的表名"""
        return "antigravity_credentials" if is_antigravity else "credentials"

    # ============ SQL 方法 ============

    async def get_next_available_credential(self, is_antigravity: bool = False) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        随机获取一个可用凭证（负载均衡）
        - 未禁用
        - 未冷却（或冷却期已过）
        - 随机选择

        Args:
            is_antigravity: 是否获取 antigravity 凭证（默认 False）
        """
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                current_time = time.time()

                async with db.execute(f"""
                    SELECT filename, credential_data, id
                    FROM {table_name}
                    WHERE disabled = 0
                      AND (cooldown_until IS NULL OR cooldown_until < ?)
                    ORDER BY RANDOM()
                    LIMIT 1
                """, (current_time,)) as cursor:
                    row = await cursor.fetchone()

                    if row:
                        filename, credential_json, cred_id = row
                        credential_data = json.loads(credential_json)
                        return filename, credential_data

                return None

        except Exception as e:
            log.error(f"Error getting next available credential (antigravity={is_antigravity}): {e}")
            return None

    async def rotate_and_update_credential(self, filename: str, increment_call: bool = True):
        """
        轮换凭证并更新统计
        - 将当前凭证的 rotation_order 设为最大值+1（移到队尾）
        - 可选：增加 call_count
        - 一次事务完成
        """
        self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                # 获取当前最大 rotation_order
                async with db.execute("""
                    SELECT MAX(rotation_order) FROM credentials
                """) as cursor:
                    row = await cursor.fetchone()
                    max_order = row[0] if row[0] is not None else 0

                # 更新凭证
                if increment_call:
                    await db.execute("""
                        UPDATE credentials
                        SET rotation_order = ?,
                            call_count = call_count + 1,
                            updated_at = unixepoch()
                        WHERE filename = ?
                    """, (max_order + 1, filename))
                else:
                    await db.execute("""
                        UPDATE credentials
                        SET rotation_order = ?,
                            updated_at = unixepoch()
                        WHERE filename = ?
                    """, (max_order + 1, filename))

                await db.commit()
                log.debug(f"Rotated credential: {filename} to order {max_order + 1}")

        except Exception as e:
            log.error(f"Error rotating credential {filename}: {e}")
            raise

    async def get_available_credentials_list(self) -> List[str]:
        """
        获取所有可用凭证列表
        - 未禁用
        - 按轮换顺序排序
        """
        self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute("""
                    SELECT filename
                    FROM credentials
                    WHERE disabled = 0
                    ORDER BY rotation_order ASC
                """) as cursor:
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]

        except Exception as e:
            log.error(f"Error getting available credentials list: {e}")
            return []

    async def check_and_clear_cooldowns(self) -> int:
        """
        批量清除已过期的冷却期
        返回清除的数量
        """
        self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                current_time = time.time()

                cursor = await db.execute("""
                    UPDATE credentials
                    SET cooldown_until = NULL
                    WHERE cooldown_until IS NOT NULL
                      AND cooldown_until < ?
                """, (current_time,))

                count = cursor.rowcount
                await db.commit()

                if count > 0:
                    log.debug(f"Cleared {count} expired cooldowns")

                return count

        except Exception as e:
            log.error(f"Error clearing cooldowns: {e}")
            return 0

    # ============ StorageBackend 协议方法 ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], is_antigravity: bool = False) -> bool:
        """存储或更新凭证"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                # 检查凭证是否存在
                async with db.execute(f"""
                    SELECT disabled, error_codes, last_success, user_email,
                           cooldown_until, rotation_order, call_count
                    FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    existing = await cursor.fetchone()

                if existing:
                    # 更新现有凭证（保留状态）
                    await db.execute(f"""
                        UPDATE {table_name}
                        SET credential_data = ?,
                            updated_at = unixepoch()
                        WHERE filename = ?
                    """, (json.dumps(credential_data), filename))
                else:
                    # 插入新凭证
                    async with db.execute(f"""
                        SELECT COALESCE(MAX(rotation_order), -1) + 1 FROM {table_name}
                    """) as cursor:
                        row = await cursor.fetchone()
                        next_order = row[0]

                    await db.execute(f"""
                        INSERT INTO {table_name}
                        (filename, credential_data, rotation_order, last_success)
                        VALUES (?, ?, ?, ?)
                    """, (filename, json.dumps(credential_data), next_order, time.time()))

                await db.commit()
                log.debug(f"Stored credential: {filename} (antigravity={is_antigravity})")
                return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str, is_antigravity: bool = False) -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(f"""
                    SELECT credential_data FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    row = await cursor.fetchone()

                    if row:
                        return json.loads(row[0])
                    return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self, is_antigravity: bool = False) -> List[str]:
        """列出所有凭证文件名（包括禁用的）"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(f"""
                    SELECT filename FROM {table_name} ORDER BY rotation_order
                """) as cursor:
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str, is_antigravity: bool = False) -> bool:
        """删除凭证"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(f"""
                    DELETE FROM {table_name} WHERE filename = ?
                """, (filename,))
                await db.commit()
                log.debug(f"Deleted credential: {filename} (antigravity={is_antigravity})")
                return True

        except Exception as e:
            log.error(f"Error deleting credential {filename}: {e}")
            return False

    async def update_credential_state(self, filename: str, state_updates: Dict[str, Any], is_antigravity: bool = False) -> bool:
        """更新凭证状态"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            # 构建动态 SQL
            set_clauses = []
            values = []

            for key, value in state_updates.items():
                if key in self.STATE_FIELDS:
                    if key == "error_codes":
                        set_clauses.append(f"{key} = ?")
                        values.append(json.dumps(value))
                    else:
                        set_clauses.append(f"{key} = ?")
                        values.append(value)

            if not set_clauses:
                return True

            set_clauses.append("updated_at = unixepoch()")
            values.append(filename)

            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(f"""
                    UPDATE {table_name}
                    SET {', '.join(set_clauses)}
                    WHERE filename = ?
                """, values)
                await db.commit()
                return True

        except Exception as e:
            log.error(f"Error updating credential state {filename}: {e}")
            return False

    async def get_credential_state(self, filename: str, is_antigravity: bool = False) -> Dict[str, Any]:
        """获取凭证状态"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(f"""
                    SELECT disabled, error_codes, last_success, user_email, cooldown_until
                    FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    row = await cursor.fetchone()

                    if row:
                        error_codes_json = row[1] or '[]'
                        return {
                            "disabled": bool(row[0]),
                            "error_codes": json.loads(error_codes_json),
                            "last_success": row[2] or time.time(),
                            "user_email": row[3],
                            "cooldown_until": row[4],
                        }

                    # 返回默认状态
                    return {
                        "disabled": False,
                        "error_codes": [],
                        "last_success": time.time(),
                        "user_email": None,
                        "cooldown_until": None,
                    }

        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {}

    async def get_all_credential_states(self, is_antigravity: bool = False) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(is_antigravity)
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(f"""
                    SELECT filename, disabled, error_codes, last_success,
                           user_email, cooldown_until
                    FROM {table_name}
                """) as cursor:
                    rows = await cursor.fetchall()

                    states = {}
                    for row in rows:
                        filename = row[0]
                        error_codes_json = row[2] or '[]'
                        states[filename] = {
                            "disabled": bool(row[1]),
                            "error_codes": json.loads(error_codes_json),
                            "last_success": row[3] or time.time(),
                            "user_email": row[4],
                            "cooldown_until": row[5],
                        }

                    return states

        except Exception as e:
            log.error(f"Error getting all credential states: {e}")
            return {}

    async def get_credentials_summary(
        self,
        offset: int = 0,
        limit: Optional[int] = None,
        status_filter: str = "all",
        is_antigravity: bool = False
    ) -> Dict[str, Any]:
        """
        获取凭证的摘要信息（不包含完整凭证数据）- 支持分页和状态筛选

        Args:
            offset: 跳过的记录数（默认0）
            limit: 返回的最大记录数（None表示返回所有）
            status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
            is_antigravity: 是否查询antigravity凭证表（默认False）

        Returns:
            包含 items（凭证列表）、total（总数）、offset、limit 的字典
        """
        self._ensure_initialized()

        try:
            # 根据 is_antigravity 选择表名
            table_name = self._get_table_name(is_antigravity)

            async with aiosqlite.connect(self._db_path) as db:
                # 构建WHERE子句
                where_clause = ""
                count_params = []
                query_params = []

                if status_filter == "enabled":
                    where_clause = "WHERE disabled = 0"
                elif status_filter == "disabled":
                    where_clause = "WHERE disabled = 1"

                # 先获取符合筛选条件的总数
                count_query = f"SELECT COUNT(*) FROM {table_name} {where_clause}"
                async with db.execute(count_query, count_params) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0] if row else 0

                # 构建分页查询
                if limit is not None:
                    query = f"""
                        SELECT filename, disabled, error_codes, last_success,
                               user_email, cooldown_until, rotation_order
                        FROM {table_name}
                        {where_clause}
                        ORDER BY rotation_order
                        LIMIT ? OFFSET ?
                    """
                    query_params = (limit, offset)
                else:
                    query = f"""
                        SELECT filename, disabled, error_codes, last_success,
                               user_email, cooldown_until, rotation_order
                        FROM {table_name}
                        {where_clause}
                        ORDER BY rotation_order
                        OFFSET ?
                    """
                    query_params = (offset,)

                async with db.execute(query, query_params) as cursor:
                    rows = await cursor.fetchall()

                    summaries = []
                    current_time = time.time()

                    for row in rows:
                        filename = row[0]
                        error_codes_json = row[2] or '[]'
                        cooldown_until = row[5]

                        # 计算冷却状态
                        cooldown_status = "ready"
                        cooldown_remaining_seconds = 0
                        if cooldown_until:
                            if current_time < cooldown_until:
                                cooldown_status = "cooling"
                                cooldown_remaining_seconds = int(cooldown_until - current_time)

                        summaries.append({
                            "filename": filename,
                            "disabled": bool(row[1]),
                            "error_codes": json.loads(error_codes_json),
                            "last_success": row[3] or current_time,
                            "user_email": row[4],
                            "cooldown_until": cooldown_until,
                            "cooldown_status": cooldown_status,
                            "cooldown_remaining_seconds": cooldown_remaining_seconds,
                            "rotation_order": row[6],
                        })

                    return {
                        "items": summaries,
                        "total": total_count,
                        "offset": offset,
                        "limit": limit,
                    }

        except Exception as e:
            log.error(f"Error getting credentials summary: {e}")
            return {
                "items": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
            }

    # ============ 配置管理（内存缓存）============

    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置（写入数据库 + 更新内存缓存）"""
        self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("""
                    INSERT INTO config (key, value, updated_at)
                    VALUES (?, ?, unixepoch())
                    ON CONFLICT(key) DO UPDATE SET
                        value = excluded.value,
                        updated_at = excluded.updated_at
                """, (key, json.dumps(value)))
                await db.commit()

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
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("DELETE FROM config WHERE key = ?", (key,))
                await db.commit()

            # 从内存缓存移除
            self._config_cache.pop(key, None)
            return True

        except Exception as e:
            log.error(f"Error deleting config {key}: {e}")
            return False
