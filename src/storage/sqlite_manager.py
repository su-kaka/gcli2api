"""
SQLite 存储管理器
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from log import log


class SQLiteManager:
    """SQLite 数据库管理器"""

    # 状态字段常量
    STATE_FIELDS = {
        "error_codes",
        "error_messages",
        "disabled",
        "last_success",
        "user_email",
        "model_cooldowns",
        "preview",
    }

    # 所有必需的列定义（用于自动校验和修复）
    REQUIRED_COLUMNS = {
        "credentials": [
            ("disabled", "INTEGER DEFAULT 0"),
            ("error_codes", "TEXT DEFAULT '[]'"),
            ("error_messages", "TEXT DEFAULT '[]'"),
            ("last_success", "REAL"),
            ("user_email", "TEXT"),
            ("model_cooldowns", "TEXT DEFAULT '{}'"),
            ("preview", "INTEGER DEFAULT 1"),
            ("rotation_order", "INTEGER DEFAULT 0"),
            ("call_count", "INTEGER DEFAULT 0"),
            ("created_at", "REAL DEFAULT (unixepoch())"),
            ("updated_at", "REAL DEFAULT (unixepoch())")
        ],
        "antigravity_credentials": [
            ("disabled", "INTEGER DEFAULT 0"),
            ("error_codes", "TEXT DEFAULT '[]'"),
            ("error_messages", "TEXT DEFAULT '[]'"),
            ("last_success", "REAL"),
            ("user_email", "TEXT"),
            ("model_cooldowns", "TEXT DEFAULT '{}'"),
            ("rotation_order", "INTEGER DEFAULT 0"),
            ("call_count", "INTEGER DEFAULT 0"),
            ("created_at", "REAL DEFAULT (unixepoch())"),
            ("updated_at", "REAL DEFAULT (unixepoch())")
        ]
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

                # 创建数据库和表
                async with aiosqlite.connect(self._db_path) as db:
                    # 启用 WAL 模式（提升并发性能）
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA foreign_keys=ON")

                    # 检查并自动修复数据库结构
                    await self._ensure_schema_compatibility(db)

                    # 创建表
                    await self._create_tables(db)

                    # 修复可能包含路径的凭证文件名
                    await self._repair_credential_filenames(db)

                    await db.commit()

                # 加载配置到内存
                await self._load_config_cache()

                self._initialized = True
                log.info(f"SQLite storage initialized at {self._db_path}")

            except Exception as e:
                log.error(f"Error initializing SQLite: {e}")
                raise

    async def _ensure_schema_compatibility(self, db: aiosqlite.Connection) -> None:
        """
        确保数据库结构兼容，自动修复缺失的列
        """
        try:
            # 检查每个表
            for table_name, columns in self.REQUIRED_COLUMNS.items():
                # 检查表是否存在
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                ) as cursor:
                    if not await cursor.fetchone():
                        log.debug(f"Table {table_name} does not exist, will be created")
                        continue

                # 获取现有列
                async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
                    existing_columns = {row[1] for row in await cursor.fetchall()}

                # 添加缺失的列
                added_count = 0
                for col_name, col_def in columns:
                    if col_name not in existing_columns:
                        try:
                            await db.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_def}")
                            log.info(f"Added missing column {table_name}.{col_name}")
                            added_count += 1
                        except Exception as e:
                            log.error(f"Failed to add column {table_name}.{col_name}: {e}")

                if added_count > 0:
                    log.info(f"Table {table_name}: added {added_count} missing column(s)")

        except Exception as e:
            log.error(f"Error ensuring schema compatibility: {e}")
            # 不抛出异常，允许继续初始化

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
                error_messages TEXT DEFAULT '[]',
                last_success REAL,
                user_email TEXT,

                -- 模型级 CD 支持 (JSON: {model_name: cooldown_timestamp})
                model_cooldowns TEXT DEFAULT '{}',

                -- preview 状态 (只对 geminicli 有效，默认为 true)
                preview INTEGER DEFAULT 1,

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
                error_messages TEXT DEFAULT '[]',
                last_success REAL,
                user_email TEXT,

                -- 模型级 CD 支持 (JSON: {model_name: cooldown_timestamp})
                model_cooldowns TEXT DEFAULT '{}',

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
            CREATE INDEX IF NOT EXISTS idx_rotation_order
            ON credentials(rotation_order)
        """)

        # 创建索引 - Antigravity 凭证表
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ag_disabled
            ON antigravity_credentials(disabled)
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

    async def _repair_credential_filenames(self, db: aiosqlite.Connection):
        """
        修复凭证数据库中可能包含路径的文件名，确保所有文件名都是 basename
        """
        try:
            repaired_count = 0

            # 修复 credentials 表
            async with db.execute("SELECT filename FROM credentials") as cursor:
                rows = await cursor.fetchall()
                for (filename,) in rows:
                    basename = os.path.basename(filename)
                    if basename != filename:
                        # 检查是否会产生冲突
                        async with db.execute(
                            "SELECT COUNT(*) FROM credentials WHERE filename = ?",
                            (basename,)
                        ) as check_cursor:
                            count = (await check_cursor.fetchone())[0]

                        if count == 0:
                            # 无冲突，直接更新
                            await db.execute(
                                "UPDATE credentials SET filename = ? WHERE filename = ?",
                                (basename, filename)
                            )
                            repaired_count += 1
                            log.info(f"Repaired credential filename: {filename} -> {basename}")
                        else:
                            # 有冲突，删除带路径的旧记录（保留 basename 的记录）
                            await db.execute(
                                "DELETE FROM credentials WHERE filename = ?",
                                (filename,)
                            )
                            repaired_count += 1
                            log.warning(f"Removed duplicate credential with path: {filename} (kept {basename})")

            # 修复 antigravity_credentials 表
            async with db.execute("SELECT filename FROM antigravity_credentials") as cursor:
                rows = await cursor.fetchall()
                for (filename,) in rows:
                    basename = os.path.basename(filename)
                    if basename != filename:
                        # 检查是否会产生冲突
                        async with db.execute(
                            "SELECT COUNT(*) FROM antigravity_credentials WHERE filename = ?",
                            (basename,)
                        ) as check_cursor:
                            count = (await check_cursor.fetchone())[0]

                        if count == 0:
                            # 无冲突，直接更新
                            await db.execute(
                                "UPDATE antigravity_credentials SET filename = ? WHERE filename = ?",
                                (basename, filename)
                            )
                            repaired_count += 1
                            log.info(f"Repaired antigravity credential filename: {filename} -> {basename}")
                        else:
                            # 有冲突，删除带路径的旧记录（保留 basename 的记录）
                            await db.execute(
                                "DELETE FROM antigravity_credentials WHERE filename = ?",
                                (filename,)
                            )
                            repaired_count += 1
                            log.warning(f"Removed duplicate antigravity credential with path: {filename} (kept {basename})")

            if repaired_count > 0:
                log.info(f"Repaired {repaired_count} credential filename(s)")
            else:
                log.debug("No credential filenames need repair")

        except Exception as e:
            log.error(f"Error repairing credential filenames: {e}")
            # 不抛出异常，允许继续初始化

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

    def _get_table_name(self, mode: str) -> str:
        """根据 mode 获取对应的表名"""
        if mode == "antigravity":
            return "antigravity_credentials"
        elif mode == "geminicli":
            return "credentials"
        else:
            raise ValueError(f"Invalid mode: {mode}. Must be 'geminicli' or 'antigravity'")

    # ============ SQL 方法 ============

    async def get_next_available_credential(
        self, mode: str = "geminicli", model_name: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        随机获取一个可用凭证（负载均衡）
        - 未禁用
        - 如果提供了 model_name，还会检查模型级冷却和preview状态
        - 随机选择

        Args:
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            model_name: 完整模型名（如 "gemini-2.0-flash-exp", "gemini-2.0-flash-thinking-exp-01-21"）

        Note:
            - 对于 geminicli 模式:
              - 如果模型名包含 "preview": 只能使用 preview=True 的凭证
              - 如果模型名不包含 "preview": 除非没有 preview=False 的凭证，否则只使用 preview=False 的凭证
            - 对于 antigravity: 不检查 preview 状态
        """
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                current_time = time.time()

                # 确定模型名用于冷却检查
                if model_name:
                    # 所有模式都使用完整模型名
                    pass

                # 根据模式构建查询
                if mode == "geminicli":
                    # geminicli 模式，需要处理 preview 状态
                    async with db.execute(f"""
                        SELECT filename, credential_data, model_cooldowns, preview
                        FROM {table_name}
                        WHERE disabled = 0
                        ORDER BY RANDOM()
                    """) as cursor:
                        rows = await cursor.fetchall()

                        if not model_name:
                            # 没有提供模型名，返回第一个可用凭证
                            if rows:
                                filename, credential_json, _, _ = rows[0]
                                credential_data = json.loads(credential_json)
                                return filename, credential_data
                            return None

                        # 检查模型是否为 preview 模型
                        is_preview_model = "preview" in model_name.lower()

                        # 分别收集 preview=False 和 preview=True 的可用凭证
                        non_preview_creds = []
                        preview_creds = []

                        for filename, credential_json, model_cooldowns_json, preview in rows:
                            model_cooldowns = json.loads(model_cooldowns_json or '{}')

                            # 检查该模型是否在冷却中
                            model_cooldown = model_cooldowns.get(model_name)
                            if model_cooldown is None or current_time >= model_cooldown:
                                # 该模型未冷却或冷却已过期
                                if preview:
                                    preview_creds.append((filename, credential_json))
                                else:
                                    non_preview_creds.append((filename, credential_json))

                        # 根据模型类型选择凭证
                        if is_preview_model:
                            # preview 模型只能使用 preview=True 的凭证
                            if preview_creds:
                                filename, credential_json = preview_creds[0]
                                credential_data = json.loads(credential_json)
                                return filename, credential_data
                        else:
                            # 非 preview 模型
                            # 除非没有 preview=False 的凭证，否则只使用 preview=False 的凭证
                            if non_preview_creds:
                                # 存在 preview=False 的凭证，只使用它们
                                filename, credential_json = non_preview_creds[0]
                                credential_data = json.loads(credential_json)
                                return filename, credential_data
                            elif preview_creds:
                                # 不存在 preview=False 的凭证，使用 preview=True 作为后备
                                filename, credential_json = preview_creds[0]
                                credential_data = json.loads(credential_json)
                                return filename, credential_data

                        return None
                else:
                    # antigravity 模式，不需要处理 preview
                    async with db.execute(f"""
                        SELECT filename, credential_data, model_cooldowns
                        FROM {table_name}
                        WHERE disabled = 0
                        ORDER BY RANDOM()
                    """) as cursor:
                        rows = await cursor.fetchall()

                        # 如果没有提供 model_name，使用第一个可用凭证
                        if not model_name:
                            if rows:
                                filename, credential_json, _ = rows[0]
                                credential_data = json.loads(credential_json)
                                return filename, credential_data
                            return None

                        # 如果提供了 model_name，检查模型级冷却
                        for filename, credential_json, model_cooldowns_json in rows:
                            model_cooldowns = json.loads(model_cooldowns_json or '{}')

                            # 检查该模型是否在冷却中
                            model_cooldown = model_cooldowns.get(model_name)
                            if model_cooldown is None or current_time >= model_cooldown:
                                # 该模型未冷却或冷却已过期
                                credential_data = json.loads(credential_json)
                                return filename, credential_data

                        return None

        except Exception as e:
            log.error(f"Error getting next available credential (mode={mode}, model_name={model_name}): {e}")
            return None

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

    # ============ StorageBackend 协议方法 ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        """存储或更新凭证"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # 检查凭证是否存在
                async with db.execute(f"""
                    SELECT disabled, error_codes, last_success, user_email,
                           rotation_order, call_count
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
                log.debug(f"Stored credential: {filename} (mode={mode})")
                return True

        except Exception as e:
            log.error(f"Error storing credential {filename}: {e}")
            return False

    async def get_credential(self, filename: str, mode: str = "geminicli") -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # 精确匹配
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

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        """列出所有凭证文件名（包括禁用的）"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                async with db.execute(f"""
                    SELECT filename FROM {table_name} ORDER BY rotation_order
                """) as cursor:
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]

        except Exception as e:
            log.error(f"Error listing credentials: {e}")
            return []

    async def delete_credential(self, filename: str, mode: str = "geminicli") -> bool:
        """删除凭证"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # 精确匹配删除
                result = await db.execute(f"""
                    DELETE FROM {table_name} WHERE filename = ?
                """, (filename,))
                deleted_count = result.rowcount

                await db.commit()

                if deleted_count > 0:
                    log.debug(f"Deleted {deleted_count} credential(s): {filename} (mode={mode})")
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

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            log.debug(f"[DB] update_credential_state 开始: filename={filename}, state_updates={state_updates}, mode={mode}, table={table_name}")

            # 构建动态 SQL
            set_clauses = []
            values = []

            for key, value in state_updates.items():
                if key in self.STATE_FIELDS:
                    if key in ("error_codes", "error_messages", "model_cooldowns"):
                        # JSON 字段需要序列化
                        set_clauses.append(f"{key} = ?")
                        values.append(json.dumps(value))
                    else:
                        set_clauses.append(f"{key} = ?")
                        values.append(value)

            if not set_clauses:
                log.info(f"[DB] 没有需要更新的状态字段")
                return True

            set_clauses.append("updated_at = unixepoch()")
            values.append(filename)

            log.debug(f"[DB] SQL参数: set_clauses={set_clauses}, values={values}")

            async with aiosqlite.connect(self._db_path) as db:
                # 精确匹配更新
                sql_exact = f"""
                    UPDATE {table_name}
                    SET {', '.join(set_clauses)}
                    WHERE filename = ?
                """
                log.debug(f"[DB] 执行精确匹配SQL: {sql_exact}")
                log.debug(f"[DB] SQL参数值: {values}")

                result = await db.execute(sql_exact, values)
                updated_count = result.rowcount
                log.debug(f"[DB] 精确匹配 rowcount={updated_count}")

                # 提交前检查
                log.debug(f"[DB] 准备commit，总更新行数={updated_count}")
                await db.commit()
                log.debug(f"[DB] commit完成")

                success = updated_count > 0
                log.debug(f"[DB] update_credential_state 结束: success={success}, updated_count={updated_count}")
                return success

        except Exception as e:
            log.error(f"[DB] Error updating credential state {filename}: {e}")
            return False

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        """获取凭证状态（不包含error_messages）"""
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # 精确匹配
                if mode == "geminicli":
                    async with db.execute(f"""
                        SELECT disabled, error_codes, last_success, user_email, model_cooldowns, preview
                        FROM {table_name} WHERE filename = ?
                    """, (filename,)) as cursor:
                        row = await cursor.fetchone()

                        if row:
                            error_codes_json = row[1] or '[]'
                            model_cooldowns_json = row[4] or '{}'
                            return {
                                "disabled": bool(row[0]),
                                "error_codes": json.loads(error_codes_json),
                                "last_success": row[2] or time.time(),
                                "user_email": row[3],
                                "model_cooldowns": json.loads(model_cooldowns_json),
                                "preview": bool(row[5]) if row[5] is not None else True,
                            }

                    # 返回默认状态
                    return {
                        "disabled": False,
                        "error_codes": [],
                        "last_success": time.time(),
                        "user_email": None,
                        "model_cooldowns": {},
                        "preview": True,
                    }
                else:
                    # antigravity 模式
                    async with db.execute(f"""
                        SELECT disabled, error_codes, last_success, user_email, model_cooldowns
                        FROM {table_name} WHERE filename = ?
                    """, (filename,)) as cursor:
                        row = await cursor.fetchone()

                        if row:
                            error_codes_json = row[1] or '[]'
                            model_cooldowns_json = row[4] or '{}'
                            return {
                                "disabled": bool(row[0]),
                                "error_codes": json.loads(error_codes_json),
                                "last_success": row[2] or time.time(),
                                "user_email": row[3],
                                "model_cooldowns": json.loads(model_cooldowns_json),
                            }

                    # 返回默认状态
                    return {
                        "disabled": False,
                        "error_codes": [],
                        "last_success": time.time(),
                        "user_email": None,
                        "model_cooldowns": {},
                    }

        except Exception as e:
            log.error(f"Error getting credential state {filename}: {e}")
            return {}

    async def get_all_credential_states(self, mode: str = "geminicli") -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态（不包含error_messages）"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                if mode == "geminicli":
                    async with db.execute(f"""
                        SELECT filename, disabled, error_codes, last_success,
                               user_email, model_cooldowns, preview
                        FROM {table_name}
                    """) as cursor:
                        rows = await cursor.fetchall()

                        states = {}
                        current_time = time.time()

                        for row in rows:
                            filename = row[0]
                            error_codes_json = row[2] or '[]'
                            model_cooldowns_json = row[5] or '{}'
                            model_cooldowns = json.loads(model_cooldowns_json)

                            # 自动过滤掉已过期的模型CD
                            if model_cooldowns:
                                model_cooldowns = {
                                    k: v for k, v in model_cooldowns.items()
                                    if v > current_time
                                }

                            states[filename] = {
                                "disabled": bool(row[1]),
                                "error_codes": json.loads(error_codes_json),
                                "last_success": row[3] or time.time(),
                                "user_email": row[4],
                                "model_cooldowns": model_cooldowns,
                                "preview": bool(row[6]) if row[6] is not None else True,
                            }

                        return states
                else:
                    # antigravity 模式
                    async with db.execute(f"""
                        SELECT filename, disabled, error_codes, last_success,
                               user_email, model_cooldowns
                        FROM {table_name}
                    """) as cursor:
                        rows = await cursor.fetchall()

                        states = {}
                        current_time = time.time()

                        for row in rows:
                            filename = row[0]
                            error_codes_json = row[2] or '[]'
                            model_cooldowns_json = row[5] or '{}'
                            model_cooldowns = json.loads(model_cooldowns_json)

                            # 自动过滤掉已过期的模型CD
                            if model_cooldowns:
                                model_cooldowns = {
                                    k: v for k, v in model_cooldowns.items()
                                    if v > current_time
                                }

                            states[filename] = {
                                "disabled": bool(row[1]),
                                "error_codes": json.loads(error_codes_json),
                                "last_success": row[3] or time.time(),
                                "user_email": row[4],
                                "model_cooldowns": model_cooldowns,
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
        mode: str = "geminicli",
        error_code_filter: Optional[str] = None,
        cooldown_filter: Optional[str] = None,
        preview_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取凭证的摘要信息（不包含完整凭证数据）- 支持分页和状态筛选

        Args:
            offset: 跳过的记录数（默认0）
            limit: 返回的最大记录数（None表示返回所有）
            status_filter: 状态筛选（all=全部, enabled=仅启用, disabled=仅禁用）
            mode: 凭证模式 ("geminicli" 或 "antigravity")
            error_code_filter: 错误码筛选（格式如"400"或"403"，筛选包含该错误码的凭证）
            cooldown_filter: 冷却状态筛选（"in_cooldown"=冷却中, "no_cooldown"=未冷却）
            preview_filter: Preview筛选（"preview"=支持preview, "no_preview"=不支持preview，仅geminicli模式有效）

        Returns:
            包含 items（凭证列表）、total（总数）、offset、limit 的字典
        """
        self._ensure_initialized()

        try:
            # 根据 mode 选择表名
            table_name = self._get_table_name(mode)

            async with aiosqlite.connect(self._db_path) as db:
                # 先计算全局统计数据（不受筛选条件影响）
                global_stats = {"total": 0, "normal": 0, "disabled": 0}
                async with db.execute(f"""
                    SELECT disabled, COUNT(*) FROM {table_name} GROUP BY disabled
                """) as stats_cursor:
                    stats_rows = await stats_cursor.fetchall()
                    for disabled, count in stats_rows:
                        global_stats["total"] += count
                        if disabled:
                            global_stats["disabled"] = count
                        else:
                            global_stats["normal"] = count

                # 构建WHERE子句
                where_clauses = []
                count_params = []

                if status_filter == "enabled":
                    where_clauses.append("disabled = 0")
                elif status_filter == "disabled":
                    where_clauses.append("disabled = 1")

                filter_value = None
                filter_int = None
                if error_code_filter and str(error_code_filter).strip().lower() != "all":
                    filter_value = str(error_code_filter).strip()
                    try:
                        filter_int = int(filter_value)
                    except ValueError:
                        filter_int = None

                # 构建WHERE子句
                where_clause = ""
                if where_clauses:
                    where_clause = "WHERE " + " AND ".join(where_clauses)

                # 先获取所有数据（用于冷却筛选，因为需要在Python中判断）
                if mode == "geminicli":
                    all_query = f"""
                        SELECT filename, disabled, error_codes, last_success,
                               user_email, rotation_order, model_cooldowns, preview
                        FROM {table_name}
                        {where_clause}
                        ORDER BY rotation_order
                    """
                else:
                    all_query = f"""
                        SELECT filename, disabled, error_codes, last_success,
                               user_email, rotation_order, model_cooldowns
                        FROM {table_name}
                        {where_clause}
                        ORDER BY rotation_order
                    """

                async with db.execute(all_query, count_params) as cursor:
                    all_rows = await cursor.fetchall()

                    current_time = time.time()
                    all_summaries = []

                    for row in all_rows:
                        filename = row[0]
                        error_codes_json = row[2] or '[]'
                        model_cooldowns_json = row[6] or '{}'
                        model_cooldowns = json.loads(model_cooldowns_json)

                        # 自动过滤掉已过期的模型CD
                        active_cooldowns = {}
                        if model_cooldowns:
                            active_cooldowns = {
                                k: v for k, v in model_cooldowns.items()
                                if v > current_time
                            }

                        error_codes = json.loads(error_codes_json)
                        if filter_value:
                            match = False
                            for code in error_codes:
                                if code == filter_value or code == filter_int:
                                    match = True
                                    break
                                if isinstance(code, str) and filter_int is not None:
                                    try:
                                        if int(code) == filter_int:
                                            match = True
                                            break
                                    except ValueError:
                                        pass
                            if not match:
                                continue

                        summary = {
                            "filename": filename,
                            "disabled": bool(row[1]),
                            "error_codes": error_codes,
                            "last_success": row[3] or current_time,
                            "user_email": row[4],
                            "rotation_order": row[5],
                            "model_cooldowns": active_cooldowns,
                        }

                        # preview状态只对geminicli模式有效
                        if mode == "geminicli":
                            summary["preview"] = bool(row[7]) if row[7] is not None else True

                        # 应用 preview 筛选（仅对 geminicli 模式）
                        if mode == "geminicli" and preview_filter:
                            preview_value = summary.get("preview", True)
                            if preview_filter == "preview" and not preview_value:
                                continue  # 跳过不支持 preview 的凭证
                            elif preview_filter == "no_preview" and preview_value:
                                continue  # 跳过支持 preview 的凭证

                        # 应用冷却筛选
                        if cooldown_filter == "in_cooldown":
                            # 只保留有冷却的凭证
                            if active_cooldowns:
                                all_summaries.append(summary)
                        elif cooldown_filter == "no_cooldown":
                            # 只保留没有冷却的凭证
                            if not active_cooldowns:
                                all_summaries.append(summary)
                        else:
                            # 不筛选冷却状态
                            all_summaries.append(summary)

                    # 应用分页
                    total_count = len(all_summaries)
                    if limit is not None:
                        summaries = all_summaries[offset:offset + limit]
                    else:
                        summaries = all_summaries[offset:]

                    return {
                        "items": summaries,
                        "total": total_count,
                        "offset": offset,
                        "limit": limit,
                        "stats": global_stats,
                    }

        except Exception as e:
            log.error(f"Error getting credentials summary: {e}")
            return {
                "items": [],
                "total": 0,
                "offset": offset,
                "limit": limit,
                "stats": {"total": 0, "normal": 0, "disabled": 0},
            }

    async def get_duplicate_credentials_by_email(self, mode: str = "geminicli") -> Dict[str, Any]:
        """
        获取按邮箱分组的重复凭证信息（只查询邮箱和文件名，不加载完整凭证数据）
        用于去重操作

        Args:
            mode: 凭证模式 ("geminicli" 或 "antigravity")

        Returns:
            包含 email_groups（邮箱分组）、duplicate_count（重复数量）、no_email_count（无邮箱数量）的字典
        """
        self._ensure_initialized()

        try:
            # 根据 mode 选择表名
            table_name = self._get_table_name(mode)

            async with aiosqlite.connect(self._db_path) as db:
                # 查询所有凭证的文件名和邮箱（不加载完整凭证数据）
                query = f"""
                    SELECT filename, user_email
                    FROM {table_name}
                    ORDER BY filename
                """

                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()

                    # 按邮箱分组
                    email_to_files = {}
                    no_email_files = []

                    for filename, user_email in rows:
                        if user_email:
                            if user_email not in email_to_files:
                                email_to_files[user_email] = []
                            email_to_files[user_email].append(filename)
                        else:
                            no_email_files.append(filename)

                    # 找出重复的邮箱组
                    duplicate_groups = []
                    total_duplicate_count = 0

                    for email, files in email_to_files.items():
                        if len(files) > 1:
                            # 保留第一个文件，其他为重复
                            duplicate_groups.append({
                                "email": email,
                                "kept_file": files[0],
                                "duplicate_files": files[1:],
                                "duplicate_count": len(files) - 1,
                            })
                            total_duplicate_count += len(files) - 1

                    return {
                        "email_groups": email_to_files,
                        "duplicate_groups": duplicate_groups,
                        "duplicate_count": total_duplicate_count,
                        "no_email_files": no_email_files,
                        "no_email_count": len(no_email_files),
                        "unique_email_count": len(email_to_files),
                        "total_count": len(rows),
                    }

        except Exception as e:
            log.error(f"Error getting duplicate credentials by email: {e}")
            return {
                "email_groups": {},
                "duplicate_groups": [],
                "duplicate_count": 0,
                "no_email_files": [],
                "no_email_count": 0,
                "unique_email_count": 0,
                "total_count": 0,
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

    async def get_credential_errors(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        """
        专门获取凭证的错误信息（包含 error_codes 和 error_messages）

        Args:
            filename: 凭证文件名
            mode: 凭证模式 ("geminicli" 或 "antigravity")

        Returns:
            包含 error_codes 和 error_messages 的字典
        """
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # 精确匹配
                async with db.execute(f"""
                    SELECT error_codes, error_messages FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    row = await cursor.fetchone()

                    if row:
                        error_codes_json = row[0] or '[]'
                        error_messages_json = row[1] or '[]'
                        return {
                            "filename": filename,
                            "error_codes": json.loads(error_codes_json),
                            "error_messages": json.loads(error_messages_json),
                        }

                # 凭证不存在，返回空错误信息
                return {
                    "filename": filename,
                    "error_codes": [],
                    "error_messages": [],
                }

        except Exception as e:
            log.error(f"Error getting credential errors {filename}: {e}")
            return {
                "filename": filename,
                "error_codes": [],
                "error_messages": [],
                "error": str(e)
            }

    # ============ 模型级冷却管理 ============

    async def set_model_cooldown(
        self,
        filename: str,
        model_name: str,
        cooldown_until: Optional[float],
        mode: str = "geminicli"
    ) -> bool:
        """
        设置特定模型的冷却时间

        Args:
            filename: 凭证文件名
            model_name: 模型名（完整模型名，如 "gemini-2.0-flash-exp"）
            cooldown_until: 冷却截止时间戳（None 表示清除冷却）
            mode: 凭证模式 ("geminicli" 或 "antigravity")

        Returns:
            是否成功
        """
        self._ensure_initialized()

        # 统一使用 basename 处理文件名
        filename = os.path.basename(filename)

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # 获取当前的 model_cooldowns
                async with db.execute(f"""
                    SELECT model_cooldowns FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    row = await cursor.fetchone()

                    if not row:
                        log.warning(f"Credential {filename} not found")
                        return False

                    model_cooldowns = json.loads(row[0] or '{}')

                    # 更新或删除指定模型的冷却时间
                    if cooldown_until is None:
                        model_cooldowns.pop(model_name, None)
                    else:
                        model_cooldowns[model_name] = cooldown_until

                    # 写回数据库
                    await db.execute(f"""
                        UPDATE {table_name}
                        SET model_cooldowns = ?,
                            updated_at = unixepoch()
                        WHERE filename = ?
                    """, (json.dumps(model_cooldowns), filename))
                    await db.commit()

                    log.debug(f"Set model cooldown: {filename}, model_name={model_name}, cooldown_until={cooldown_until}")
                    return True

        except Exception as e:
            log.error(f"Error setting model cooldown for {filename}: {e}")
            return False
