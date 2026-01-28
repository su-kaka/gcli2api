from src.i18n import ts
"""
SQLite {ts(f"id_3386")}
"""

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

from log import log


class SQLiteManager:
    f"""SQLite {ts('id_3387')}"""

    # {ts(f"id_3388")}
    STATE_FIELDS = {
        "error_codes",
        "disabled",
        "last_success",
        "user_email",
        "model_cooldowns",
    }

    # {ts(f"id_3521")}
    REQUIRED_COLUMNS = {
        "credentials": [
            ("disabled", "INTEGER DEFAULT 0"),
            ("error_codes", "TEXT DEFAULT '[]'"),
            ("last_success", "REAL"),
            ("user_email", "TEXT"),
            ("model_cooldowns", "TEXT DEFAULT '{}'"),
            ("rotation_order", "INTEGER DEFAULT 0"),
            ("call_count", "INTEGER DEFAULT 0"),
            ("created_at", "REAL DEFAULT (unixepoch())"),
            ("updated_at", "REAL DEFAULT (unixepoch())")
        ],
        "antigravity_credentials": [
            ("disabled", "INTEGER DEFAULT 0"),
            ("error_codes", "TEXT DEFAULT '[]'"),
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

        # {ts(f"id_3395")} - {ts('id_3394')}
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

    async def initialize(self) -> None:
        f"""{ts('id_1111')} SQLite {ts('id_88')}"""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            try:
                # {ts(f"id_3522")}
                self._credentials_dir = os.getenv("CREDENTIALS_DIR", "./creds")
                self._db_path = os.path.join(self._credentials_dir, "credentials.db")

                # {ts(f"id_3523")}
                os.makedirs(self._credentials_dir, exist_ok=True)

                # {ts(f"id_3524")}
                async with aiosqlite.connect(self._db_path) as db:
                    # {ts(f"id_126")} WAL {ts('id_3525')}
                    await db.execute("PRAGMA journal_mode=WAL")
                    await db.execute("PRAGMA foreign_keys=ON")

                    # {ts(f"id_3526")}
                    await self._ensure_schema_compatibility(db)

                    # {ts(f"id_3527")}
                    await self._create_tables(db)

                    await db.commit()

                # {ts(f"id_3398")}
                await self._load_config_cache()

                self._initialized = True
                log.info(f"SQLite storage initialized at {self._db_path}")

            except Exception as e:
                log.error(f"Error initializing SQLite: {e}")
                raise

    async def _ensure_schema_compatibility(self, db: aiosqlite.Connection) -> None:
        """
        {ts(f"id_3528")}
        """
        try:
            # {ts(f"id_3529")}
            for table_name, columns in self.REQUIRED_COLUMNS.items():
                # {ts(f"id_3530")}
                async with db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,)
                ) as cursor:
                    if not await cursor.fetchone():
                        log.debug(f"Table {table_name} does not exist, will be created")
                        continue

                # {ts(f"id_3531")}
                async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
                    existing_columns = {row[1] for row in await cursor.fetchall()}

                # {ts(f"id_3532")}
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
            # {ts(f"id_3533")}

    async def _create_tables(self, db: aiosqlite.Connection):
        f"""{ts('id_3534')}"""
        # {ts(f"id_3535")}
        await db.execute("""
            CREATE TABLE IF NOT EXISTS credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                credential_data TEXT NOT NULL,

                -- {ts(f"id_3536")}
                disabled INTEGER DEFAULT 0,
                error_codes TEXT DEFAULT '[]',
                last_success REAL,
                user_email TEXT,

                -- {ts(f"id_3537")} CD {ts('id_56')} (JSON: {model_key: cooldown_timestamp})
                model_cooldowns TEXT DEFAULT '{}',

                -- {ts(f"id_3538")}
                rotation_order INTEGER DEFAULT 0,
                call_count INTEGER DEFAULT 0,

                -- {ts(f"id_3539")}
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        # Antigravity {ts(f"id_3540")}
        await db.execute("""
            CREATE TABLE IF NOT EXISTS antigravity_credentials (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT UNIQUE NOT NULL,
                credential_data TEXT NOT NULL,

                -- {ts(f"id_3536")}
                disabled INTEGER DEFAULT 0,
                error_codes TEXT DEFAULT '[]',
                last_success REAL,
                user_email TEXT,

                -- {ts(f"id_3537")} CD {ts('id_56')} (JSON: {model_name: cooldown_timestamp})
                model_cooldowns TEXT DEFAULT '{}',

                -- {ts(f"id_3538")}
                rotation_order INTEGER DEFAULT 0,
                call_count INTEGER DEFAULT 0,

                -- {ts(f"id_3539")}
                created_at REAL DEFAULT (unixepoch()),
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        # {ts(f"id_3397")} - {ts('id_3541')}
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_disabled
            ON credentials(disabled)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_rotation_order
            ON credentials(rotation_order)
        """)

        # {ts(f"id_3397")} - Antigravity {ts('id_3535')}
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ag_disabled
            ON antigravity_credentials(disabled)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_ag_rotation_order
            ON antigravity_credentials(rotation_order)
        """)

        # {ts(f"id_3542")}
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL DEFAULT (unixepoch())
            )
        """)

        log.debug("SQLite tables and indexes created")

    async def _load_config_cache(self):
        f"""{ts('id_3403')}"""
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
        f"""{ts('id_3543')}"""
        self._initialized = False
        log.debug("SQLite storage closed")

    def _ensure_initialized(self):
        f"""{ts('id_2978')}"""
        if not self._initialized:
            raise RuntimeError("SQLite manager not initialized")

    def _get_table_name(self, mode: str) -> str:
        f"""{ts('id_2136')} mode {ts('id_3544')}"""
        if mode == "antigravity":
            return "antigravity_credentials"
        elif mode == "geminicli":
            return "credentials"
        else:
            raise ValueError(f"Invalid mode: {mode}. Must be 'geminicli' or 'antigravity'")

    # ============ SQL {ts(f"id_3405")} ============

    async def get_next_available_credential(
        self, mode: str = "geminicli", model_key: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        {ts(f"id_3406")}
        - {ts(f"id_3407")}
        - {ts(f"id_2996")} model_key{ts('id_3408')}
        - {ts(f"id_3409")}

        Args:
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")
            model_key: {ts(f"id_3410")}antigravity {ts('id_3411')}gcli {ts('id_3412')} pro/flash{ts('id_292')}

        Note:
            - {ts(f"id_2608")} antigravity: model_key {ts('id_3413')} "gemini-2.0-flash-exp"{ts('id_292')}
            - {ts(f"id_2608")} gcli: model_key {ts('id_150')} "prof" {ts('id_413')} "flash"
        """
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                current_time = time.time()

                # {ts(f"id_3545")}
                async with db.execute(f"""
                    SELECT filename, credential_data, model_cooldowns
                    FROM {table_name}
                    WHERE disabled = 0
                    ORDER BY RANDOM()
                """) as cursor:
                    rows = await cursor.fetchall()

                    # {ts(f"id_3547")} model_key{ts('id_3546')}
                    if not model_key:
                        if rows:
                            filename, credential_json, _ = rows[0]
                            credential_data = json.loads(credential_json)
                            return filename, credential_data
                        return None

                    # {ts(f"id_2996")} model_key{ts('id_3548')}
                    for filename, credential_json, model_cooldowns_json in rows:
                        model_cooldowns = json.loads(model_cooldowns_json or '{}')

                        # {ts(f"id_3549")}
                        model_cooldown = model_cooldowns.get(model_key)
                        if model_cooldown is None or current_time >= model_cooldown:
                            # {ts(f"id_3550")}
                            credential_data = json.loads(credential_json)
                            return filename, credential_data

                    return None

        except Exception as e:
            log.error(f"Error getting next available credential (mode={mode}, model_key={model_key}): {e}")
            return None

    async def get_available_credentials_list(self) -> List[str]:
        """
        {ts(f"id_3430")}
        - {ts(f"id_3407")}
        - {ts(f"id_3431")}
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

    # ============ StorageBackend {ts(f"id_3432")} ============

    async def store_credential(self, filename: str, credential_data: Dict[str, Any], mode: str = "geminicli") -> bool:
        f"""{ts('id_3433')}"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3551")}
                async with db.execute(f"""
                    SELECT disabled, error_codes, last_success, user_email,
                           rotation_order, call_count
                    FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    existing = await cursor.fetchone()

                if existing:
                    # {ts(f"id_3552")}
                    await db.execute(f"""
                        UPDATE {table_name}
                        SET credential_data = ?,
                            updated_at = unixepoch()
                        WHERE filename = ?
                    """, (json.dumps(credential_data), filename))
                else:
                    # {ts(f"id_3553")}
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
        f"""{ts('id_3443')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3470")}
                async with db.execute(f"""
                    SELECT credential_data FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return json.loads(row[0])

                # {ts(f"id_3447")}basename{ts('id_3446')}
                async with db.execute(f"""
                    SELECT credential_data FROM {table_name}
                    WHERE filename LIKE '%' || ? OR filename = ?
                """, (filename, filename)) as cursor:
                    rows = await cursor.fetchall()
                    # {ts(f"id_3554")}basename{ts('id_3555')}
                    for row in rows:
                        return json.loads(row[0])

                return None

        except Exception as e:
            log.error(f"Error getting credential {filename}: {e}")
            return None

    async def list_credentials(self, mode: str = "geminicli") -> List[str]:
        f"""{ts('id_3556')}"""
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
        f"""{ts('id_3452')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3453")}
                result = await db.execute(f"""
                    DELETE FROM {table_name} WHERE filename = ?
                """, (filename,))
                deleted_count = result.rowcount

                # {ts(f"id_3454")}basename{ts('id_3455')}
                if deleted_count == 0:
                    result = await db.execute(f"""
                        DELETE FROM {table_name} WHERE filename LIKE '%' || ?
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
        f"""{ts('id_3465')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            log.debug(f"[DB] update_credential_state {ts('id_588')}: filename={filename}, state_updates={state_updates}, mode={mode}, table={table_name}")
            
            # {ts(f"id_3557")} SQL
            set_clauses = []
            values = []

            for key, value in state_updates.items():
                if key in self.STATE_FIELDS:
                    if key == "error_codes":
                        set_clauses.append(f"{key} = ?")
                        values.append(json.dumps(value))
                    elif key == "model_cooldowns":
                        set_clauses.append(f"{key} = ?")
                        values.append(json.dumps(value))
                    else:
                        set_clauses.append(f"{key} = ?")
                        values.append(value)

            if not set_clauses:
                log.info(f"[DB] {ts('id_3558')}")
                return True

            set_clauses.append("updated_at = unixepoch()")
            values.append(filename)

            log.debug(f"[DB] SQL{ts('id_226')}: set_clauses={set_clauses}, values={values}")

            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3467")}
                sql_exact = f"""
                    UPDATE {table_name}
                    SET {', '.join(set_clauses)}
                    WHERE filename = ?
                """
                log.debug(f"[DB] {ts('id_3559')}SQL: {sql_exact}")
                log.debug(f"[DB] SQL{ts('id_3560')}: {values}")
                
                result = await db.execute(sql_exact, values)
                updated_count = result.rowcount
                log.debug(f"[DB] {ts('id_3561')} rowcount={updated_count}")

                # {ts(f"id_3468")}basename{ts('id_3455')}
                if updated_count == 0:
                    sql_basename = f"""
                        UPDATE {table_name}
                        SET {', '.join(set_clauses)}
                        WHERE filename LIKE '%' || ?
                    """
                    log.debug(f"[DB] {ts('id_3562')}basename{ts('id_3455')}SQL: {sql_basename}")
                    result = await db.execute(sql_basename, values)
                    updated_count = result.rowcount
                    log.info(f"[DB] basename{ts('id_3455')} rowcount={updated_count}")

                # {ts(f"id_3563")}
                log.debug(f"[DB] {ts('id_1452')}commit{ts('id_1451')}={updated_count}")
                await db.commit()
                log.debug(f"[DB] commit{ts('id_405')}")
                
                success = updated_count > 0
                log.debug(f"[DB] update_credential_state {ts('id_1453')}: success={success}, updated_count={updated_count}")
                return success

        except Exception as e:
            log.error(f"[DB] Error updating credential state {filename}: {e}", exc_info=True)
            return False

    async def get_credential_state(self, filename: str, mode: str = "geminicli") -> Dict[str, Any]:
        f"""{ts('id_3469')}basename{ts('id_3444')}"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3470")}
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

                # {ts(f"id_3473")}basename{ts('id_3455')}
                async with db.execute(f"""
                    SELECT disabled, error_codes, last_success, user_email, model_cooldowns
                    FROM {table_name} WHERE filename LIKE '%' || ?
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

                # {ts(f"id_3474")}
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
        f"""{ts('id_3475')}"""
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
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

                        # {ts(f"id_3477")}CD
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
        cooldown_filter: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        {ts(f"id_3478")}- {ts('id_3479')}

        Args:
            offset: {ts(f"id_34800")}{ts('id_292')}
            limit: {ts(f"id_3481")}None{ts('id_3482')}
            status_filter: {ts(f"id_3483")}all={ts('id_1238')}, enabled={ts('id_724')}, disabled={ts('id_3484')}
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")
            error_code_filter: {ts(f"id_3486")}"400"{ts('id_413')}"403"{ts('id_3485')}
            cooldown_filter: {ts(f"id_3487")}"in_cooldown"={ts('id_3489')}, "no_cooldown"={ts('id_3488')}

        Returns:
            {ts(f"id_906")} items{ts('id_3490')}total{ts('id_3491f')}offset{ts('id_189')}limit {ts('id_2782')}
        """
        self._ensure_initialized()

        try:
            # {ts(f"id_2136")} mode {ts('id_3564')}
            table_name = self._get_table_name(mode)

            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3565")}
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

                # {ts(f"id_1475")}WHERE{ts('id_3566')}
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

                # {ts(f"id_1475")}WHERE{ts('id_3566')}
                where_clause = ""
                if where_clauses:
                    where_clause = "WHERE " + " AND ".join(where_clauses)

                # {ts(f"id_3567")}Python{ts('id_3498')}
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

                        # {ts(f"id_3477")}CD
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

                        # {ts(f"id_3499")}
                        if cooldown_filter == "in_cooldown":
                            # {ts(f"id_3500")}
                            if active_cooldowns:
                                all_summaries.append(summary)
                        elif cooldown_filter == "no_cooldown":
                            # {ts(f"id_3501")}
                            if not active_cooldowns:
                                all_summaries.append(summary)
                        else:
                            # {ts(f"id_3502")}
                            all_summaries.append(summary)

                    # {ts(f"id_3503")}
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
        {ts(f"id_3456")}
        {ts(f"id_3457")}

        Args:
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")

        Returns:
            {ts(f"id_906")} email_groups{ts('id_3460')}duplicate_count{ts('id_3459')}no_email_count{ts('id_3458')}
        """
        self._ensure_initialized()

        try:
            # {ts(f"id_2136")} mode {ts('id_3564')}
            table_name = self._get_table_name(mode)

            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3568")}
                query = f"""
                    SELECT filename, user_email
                    FROM {table_name}
                    ORDER BY filename
                """

                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()

                    # {ts(f"id_3462")}
                    email_to_files = {}
                    no_email_files = []

                    for filename, user_email in rows:
                        if user_email:
                            if user_email not in email_to_files:
                                email_to_files[user_email] = []
                            email_to_files[user_email].append(filename)
                        else:
                            no_email_files.append(filename)

                    # {ts(f"id_3463")}
                    duplicate_groups = []
                    total_duplicate_count = 0

                    for email, files in email_to_files.items():
                        if len(files) > 1:
                            # {ts(f"id_3464")}
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

    # ============ {ts(f"id_3504")}============

    async def set_config(self, key: str, value: Any) -> bool:
        f"""{ts('id_3505')} + {ts('id_3506')}"""
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

            # {ts(f"id_3507")}
            self._config_cache[key] = value
            return True

        except Exception as e:
            log.error(f"Error setting config {key}: {e}")
            return False

    async def reload_config_cache(self):
        f"""{ts('id_3508')}"""
        self._ensure_initialized()
        self._config_loaded = False
        await self._load_config_cache()
        log.info("Config cache reloaded from database")

    async def get_config(self, key: str, default: Any = None) -> Any:
        f"""{ts('id_3509')}"""
        self._ensure_initialized()
        return self._config_cache.get(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        f"""{ts('id_3510')}"""
        self._ensure_initialized()
        return self._config_cache.copy()

    async def delete_config(self, key: str) -> bool:
        f"""{ts('id_3511')}"""
        self._ensure_initialized()

        try:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute("DELETE FROM config WHERE key = ?", (key,))
                await db.commit()

            # {ts(f"id_3512")}
            self._config_cache.pop(key, None)
            return True

        except Exception as e:
            log.error(f"Error deleting config {key}: {e}")
            return False

    # ============ {ts(f"id_3513")} ============

    async def set_model_cooldown(
        self,
        filename: str,
        model_key: str,
        cooldown_until: Optional[float],
        mode: str = "geminicli"
    ) -> bool:
        """
        {ts(f"id_3514")}

        Args:
            filename: {ts(f"id_3515")}
            model_key: {ts(f"id_3516")}antigravity {ts('id_3411')}gcli {ts('id_3412')} pro/flash{ts('id_292')}
            cooldown_until: {ts(f"id_2991")}None {ts('id_3517')}
            mode: {ts(f"id_1808")} ("geminicli" {ts('id_413')} "antigravity")

        Returns:
            {ts(f"id_2989")}
        """
        self._ensure_initialized()

        try:
            table_name = self._get_table_name(mode)
            async with aiosqlite.connect(self._db_path) as db:
                # {ts(f"id_3569")} model_cooldowns
                async with db.execute(f"""
                    SELECT model_cooldowns FROM {table_name} WHERE filename = ?
                """, (filename,)) as cursor:
                    row = await cursor.fetchone()

                    if not row:
                        log.warning(f"Credential {filename} not found")
                        return False

                    model_cooldowns = json.loads(row[0] or '{}')

                    # {ts(f"id_3570")}
                    if cooldown_until is None:
                        model_cooldowns.pop(model_key, None)
                    else:
                        model_cooldowns[model_key] = cooldown_until

                    # {ts(f"id_3571")}
                    await db.execute(f"""
                        UPDATE {table_name}
                        SET model_cooldowns = ?,
                            updated_at = unixepoch()
                        WHERE filename = ?
                    """, (json.dumps(model_cooldowns), filename))
                    await db.commit()

                    log.debug(f"Set model cooldown: {filename}, model_key={model_key}, cooldown_until={cooldown_until}")
                    return True

        except Exception as e:
            log.error(f"Error setting model cooldown for {filename}: {e}")
            return False
