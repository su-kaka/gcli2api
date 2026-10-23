"""
数据库层（SQLAlchemy 2.0 异步 ORM）
统一支持 SQLite（默认）与 PostgreSQL。
凭证以 email 为唯一键；schema 变更不做旧数据兼容（新表直接重建）。
"""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import (
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from log import log


# ====================== ORM 模型 ======================

class Base(DeclarativeBase):
    pass


class AntigravityCredential(Base):
    """Antigravity 凭证表（email 唯一键）"""
    __tablename__ = "antigravity_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)

    # 凭证结构化字段
    access_token: Mapped[str] = mapped_column(Text, default="")
    refresh_token: Mapped[str] = mapped_column(Text, default="")
    client_id: Mapped[str] = mapped_column(Text, default="")
    client_secret: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[str] = mapped_column(Text, default="")
    expiry: Mapped[str] = mapped_column(Text, default="")

    # 状态字段
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    error_codes: Mapped[str] = mapped_column(Text, default="[]")
    error_messages: Mapped[str] = mapped_column(Text, default="[]")
    last_success: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # 模型级冷却 (JSON: {model_name: cooldown_timestamp})
    model_cooldowns: Mapped[str] = mapped_column(Text, default="{}")

    # tier 状态 (free/pro/ultra)
    tier: Mapped[Optional[str]] = mapped_column(String(32), default="pro")

    # 时间戳
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time, onupdate=time.time)

    __table_args__ = (
        Index("idx_ag_disabled", "disabled"),
    )


class ConfigItem(Base):
    """配置表"""
    __tablename__ = "config"

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


class UsageLog(Base):
    """请求日志表：每次 API 调用记录一条（成功与失败均记录）"""
    __tablename__ = "usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)

    # 请求来源
    api_format: Mapped[str] = mapped_column(String(16), default="")        # openai / gemini / anthropic
    client_ip: Mapped[str] = mapped_column(String(64), default="")
    request_id: Mapped[str] = mapped_column(String(64), default="")

    # 调用信息
    model_name: Mapped[str] = mapped_column(String(128), default="")             # 客户端请求的模型名（原始，可能带功能前缀）
    upstream_model_name: Mapped[str] = mapped_column(String(128), default="")   # 后端实际使用的真实模型名（映射后）
    credential_email: Mapped[str] = mapped_column(String(320), default="")
    is_stream: Mapped[bool] = mapped_column(Boolean, default=False)

    # 结果
    status_code: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=False)
    use_time: Mapped[float] = mapped_column(Float, default=0.0)            # 耗时（秒）

    # Token 用量
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)

    # 失败时的错误摘要
    error_message: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        Index("idx_ul_created_model", "created_at", "model_name"),
        Index("idx_ul_created_email", "created_at", "credential_email"),
    )


# ====================== JSON 序列化辅助 ======================

def _dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_json(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _row_to_credential_dict(row: AntigravityCredential) -> Dict[str, Any]:
    """ORM 行 -> 凭证字典"""
    return {
        "access_token": row.access_token or "",
        "refresh_token": row.refresh_token or "",
        "client_id": row.client_id or "",
        "client_secret": row.client_secret or "",
        "project_id": row.project_id or "",
        "expiry": row.expiry or "",
    }


def get_cooldown_group(model_name: str) -> str:
    """
    模型 CD 分组：同家族模型共享一个冷却键
    - 名字带 argon -> "argon"
    - 名字带 gemini -> "gemini"
    - 名字带 claude 或 gpt -> "claude_gpt"
    - 其他 -> 原始模型名
    """
    lowered = (model_name or "").lower()
    if "argon" in lowered:
        return "argon"
    if "gemini" in lowered:
        return "gemini"
    if "claude" in lowered or "gpt" in lowered:
        return "claude_gpt"
    return model_name


# ====================== 数据库管理器 ======================

class Database:
    """统一数据库管理器（SQLite / PostgreSQL）"""

    def __init__(self):
        self._engine: Optional[AsyncEngine] = None
        self._session_factory: Optional[async_sessionmaker[AsyncSession]] = None
        self._initialized = False
        self._lock = asyncio.Lock()
        self._backend_type = "sqlite"

        # 内存配置缓存
        self._config_cache: Dict[str, Any] = {}
        self._config_loaded = False

    # ---------- 初始化 ----------

    async def initialize(self) -> None:
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            postgres_uri = os.getenv("POSTGRESQL_URI", "")
            credentials_dir = os.getenv("CREDENTIALS_DIR", "./creds")
            os.makedirs(credentials_dir, exist_ok=True)

            if postgres_uri:
                # PostgreSQL（postgres:// 或 postgresql:// -> postgresql+asyncpg://）
                dsn = postgres_uri
                if dsn.startswith("postgresql://"):
                    dsn = "postgresql+asyncpg://" + dsn[len("postgresql://"):]
                elif dsn.startswith("postgres://"):
                    dsn = "postgresql+asyncpg://" + dsn[len("postgres://"):]
                elif not dsn.startswith("postgresql+"):
                    dsn = "postgresql+asyncpg://" + dsn
                self._engine = create_async_engine(dsn, pool_size=5, max_overflow=5, pool_pre_ping=True)
                self._backend_type = "postgresql"
            else:
                # SQLite 默认
                db_path = os.path.join(credentials_dir, "credentials.db")
                self._engine = create_async_engine(
                    f"sqlite+aiosqlite:///{db_path}",
                    connect_args={"timeout": 30},
                )
                self._backend_type = "sqlite"

            self._session_factory = async_sessionmaker(
                self._engine, expire_on_commit=False, class_=AsyncSession
            )

            # 建表（不兼容旧结构：旧的 filename 键结构表会被重建为 email 键结构）
            async with self._engine.begin() as conn:
                await conn.run_sync(self._create_tables)

            # 标记初始化完成（供 session() 使用），再加载配置缓存
            self._initialized = True

            # 加载配置缓存
            await self._load_config_cache()

            log.info(f"Database initialized (backend={self._backend_type})")

    def _create_tables(self, sync_conn) -> None:
        """建表。
        - 凭证表：结构性变更（键从 filename 变为 email）时删除重建
        - 其余表：缺列时用 ALTER TABLE ADD COLUMN 补齐，保留原有数据
        """
        inspector = inspect(sync_conn)
        table_name = AntigravityCredential.__tablename__
        if inspector.has_table(table_name):
            existing_columns = {c["name"] for c in inspector.get_columns(table_name)}
            # 旧结构以 filename 为键、无 email 列 —— 删除重建（数据无法迁移）
            if "filename" in existing_columns or "email" not in existing_columns:
                log.info("检测到旧版凭证表结构（filename 键），重建为 email 键结构")
                sync_conn.execute(text(f"DROP TABLE {table_name}"))

        # 已存在的表补齐缺失列（ALTER TABLE ADD COLUMN，保留数据）
        for orm_model in (UsageLog,):
            table = orm_model.__table__
            if not inspector.has_table(table.name):
                continue
            existing_columns = {c["name"] for c in inspector.get_columns(table.name)}
            dialect = inspector.bind.dialect
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                column_type = column.type.compile(dialect)
                # 补列：有 server_default 用之；有 Python default 用字面量；否则 DEFAULT NULL
                if column.server_default is not None:
                    default_clause = f" DEFAULT {column.server_default.arg}"
                elif column.default is not None and column.default.is_scalar:
                    default_clause = f" DEFAULT {column.default.arg!r}"
                else:
                    default_clause = " DEFAULT NULL"
                sync_conn.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {column_type}{default_clause}")
                )
                log.info(f"表 {table.name} 补充列 {column.name} ({column_type})")

        Base.metadata.create_all(sync_conn)

    async def close(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None
        self._session_factory = None
        self._initialized = False
        log.debug("Database closed")

    @property
    def backend_type(self) -> str:
        return self._backend_type

    def _ensure_initialized(self):
        if not self._initialized or not self._session_factory:
            raise RuntimeError("Database not initialized")

    @asynccontextmanager
    async def session(self):
        """获取数据库会话（自动提交/回滚）"""
        self._ensure_initialized()
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    # ---------- 凭证管理 ----------

    async def store_credential(
        self, email: str, credential_data: Dict[str, Any]
    ) -> bool:
        """存储或更新凭证（以 email 为唯一键，保留状态字段）"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            access_token = credential_data.get("access_token") or credential_data.get("token") or ""
            refresh_token = credential_data.get("refresh_token") or ""
            client_id = credential_data.get("client_id") or ""
            client_secret = credential_data.get("client_secret") or ""
            project_id = credential_data.get("project_id") or ""
            expiry = credential_data.get("expiry") or ""
            tier = credential_data.get("tier")

            if row:
                row.access_token = access_token
                row.refresh_token = refresh_token
                row.client_id = client_id
                row.client_secret = client_secret
                row.project_id = project_id
                row.expiry = expiry
                if tier:
                    row.tier = tier
                row.updated_at = time.time()
            else:
                s.add(AntigravityCredential(
                    email=email,
                    access_token=access_token,
                    refresh_token=refresh_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    project_id=project_id,
                    expiry=expiry,
                    tier=tier or "pro",
                    last_success=time.time(),
                ))
            log.debug(f"Stored credential: {email}")
            return True

    async def get_credential(
        self, email: str
    ) -> Optional[Dict[str, Any]]:
        """获取凭证数据"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            return _row_to_credential_dict(row) if row else None

    async def list_credentials(self) -> List[str]:
        """列出所有凭证 email（创建顺序）"""
        async with self.session() as s:
            rows = await s.scalars(
                select(AntigravityCredential.email).order_by(AntigravityCredential.id)
            )
            return list(rows)

    async def delete_credential(self, email: str) -> bool:
        """删除凭证"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if row:
                await s.delete(row)
                return True
            return False

    async def get_next_available_credential(
        self, model_name: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """随机获取一个可用凭证（负载均衡 + 模型家族级冷却过滤），返回 (email, credential)"""
        current_time = time.time()
        cd_group = get_cooldown_group(model_name) if model_name else None
        async with self.session() as s:
            rows = (
                await s.scalars(
                    select(AntigravityCredential)
                    .where(AntigravityCredential.disabled == False)  # noqa: E712
                    .order_by(func.random())
                )
            ).all()
            for row in rows:
                if cd_group:
                    cooldowns = _load_json(row.model_cooldowns, {})
                    cd = cooldowns.get(cd_group)
                    if cd is not None and current_time < cd:
                        continue
                return row.email, _row_to_credential_dict(row)
            return None

    async def get_available_credential_by_email(
        self, email: str, model_name: Optional[str] = None
    ) -> Optional[Tuple[str, Dict[str, Any]]]:
        """
        按指定 email 获取可用凭证（凭证亲和/粘性模式使用）。
        凭证不存在/被禁用/该模型家族冷却中 -> None。
        """
        if not email:
            return None
        current_time = time.time()
        cd_group = get_cooldown_group(model_name) if model_name else None
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if not row or row.disabled:
                return None
            if cd_group:
                cooldowns = _load_json(row.model_cooldowns, {})
                cd = cooldowns.get(cd_group)
                if cd is not None and current_time < cd:
                    return None
            return row.email, _row_to_credential_dict(row)

    # ---------- 状态管理 ----------

    async def update_credential_state(
        self, email: str, state_updates: Dict[str, Any]
    ) -> bool:
        """更新凭证状态字段"""
        json_fields = {"error_codes", "error_messages", "model_cooldowns"}
        float_fields = {"last_success"}
        str_fields = {"user_email", "tier", "project_id"}
        bool_fields = {"disabled"}
        async with self.session() as s:
            values: Dict[str, Any] = {}
            for key, value in state_updates.items():
                if key in json_fields:
                    values[key] = _dump_json(value)
                elif key in float_fields:
                    values[key] = float(value) if value is not None else None
                elif key in str_fields:
                    values[key] = value
                elif key in bool_fields:
                    values[key] = bool(value)
            if not values:
                return True
            values["updated_at"] = time.time()
            result = await s.execute(
                AntigravityCredential.__table__.update()
                .where(AntigravityCredential.email == email)
                .values(**values)
            )
            return result.rowcount > 0

    async def get_credential_state(
        self, email: str
    ) -> Dict[str, Any]:
        """获取凭证状态（不含 error_messages）"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if not row:
                return {}
            return {
                "disabled": bool(row.disabled),
                "error_codes": _load_json(row.error_codes, []),
                "last_success": row.last_success or time.time(),
                "email": row.email,
                "model_cooldowns": _load_json(row.model_cooldowns, {}),
                "tier": row.tier or "pro",
                "project_id": row.project_id or "",
            }

    async def get_all_credential_states(self) -> Dict[str, Dict[str, Any]]:
        """获取所有凭证状态（自动过滤过期CD）"""
        current_time = time.time()
        async with self.session() as s:
            rows = (await s.scalars(select(AntigravityCredential))).all()
            states = {}
            for row in rows:
                cooldowns = _load_json(row.model_cooldowns, {})
                cooldowns = {k: v for k, v in cooldowns.items() if v > current_time}
                states[row.email] = {
                    "disabled": bool(row.disabled),
                    "error_codes": _load_json(row.error_codes, []),
                    "last_success": row.last_success or time.time(),
                    "email": row.email,
                    "model_cooldowns": cooldowns,
                    "tier": row.tier or "pro",
                    "project_id": row.project_id or "",
                }
            return states

    async def get_credentials_summary(
        self,
        offset: int = 0,
        limit: Optional[int] = None,
        status_filter: str = "all",
        error_code_filter: Optional[str] = None,
        cooldown_filter: Optional[str] = None,
        tier_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """凭证摘要（分页 + 状态筛选，内存过滤）"""
        current_time = time.time()
        async with self.session() as s:
            total = await s.scalar(select(func.count()).select_from(AntigravityCredential)) or 0
            disabled_count = await s.scalar(
                select(func.count()).select_from(AntigravityCredential)
                .where(AntigravityCredential.disabled == True)  # noqa: E712
            ) or 0

            stmt = select(AntigravityCredential).order_by(AntigravityCredential.id)
            if status_filter == "enabled":
                stmt = stmt.where(AntigravityCredential.disabled == False)  # noqa: E712
            elif status_filter == "disabled":
                stmt = stmt.where(AntigravityCredential.disabled == True)  # noqa: E712
            rows = (await s.scalars(stmt)).all()

            filter_value = None
            filter_int = None
            filter_none = False
            if error_code_filter and error_code_filter.strip().lower() != "all":
                if error_code_filter.strip().lower() == "none":
                    filter_none = True
                else:
                    filter_value = error_code_filter.strip()
                    try:
                        filter_int = int(filter_value)
                    except ValueError:
                        filter_int = None

            summaries = []
            for row in rows:
                error_codes = _load_json(row.error_codes, [])
                cooldowns = _load_json(row.model_cooldowns, {})
                active_cooldowns = {k: v for k, v in cooldowns.items() if v > current_time}

                if filter_none and error_codes:
                    continue
                if filter_value is not None:
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
                    "email": row.email,
                    "disabled": bool(row.disabled),
                    "error_codes": error_codes,
                    "last_success": row.last_success or current_time,
                    "model_cooldowns": active_cooldowns,
                    "tier": row.tier or "pro",
                    "project_id": row.project_id or "",
                }
                if tier_filter and tier_filter in ("free", "pro", "ultra"):
                    if summary["tier"] != tier_filter:
                        continue
                if cooldown_filter == "in_cooldown" and not active_cooldowns:
                    continue
                if cooldown_filter == "no_cooldown" and active_cooldowns:
                    continue
                summaries.append(summary)

            total_count = len(summaries)
            page_items = summaries[offset:offset + limit] if limit is not None else summaries[offset:]
            return {
                "items": page_items,
                "total": total_count,
                "offset": offset,
                "limit": limit,
                "stats": {
                    "total": total,
                    "normal": total - disabled_count,
                    "disabled": disabled_count,
                },
            }

    async def get_duplicate_credentials_by_email(self) -> Dict[str, Any]:
        """按邮箱分组的重复凭证（email 即唯一键，此结构保留用于展示统计）"""
        async with self.session() as s:
            rows = (await s.scalars(select(AntigravityCredential).order_by(AntigravityCredential.id))).all()
            emails = [row.email for row in rows]
            return {
                "email_groups": {},
                "duplicate_groups": [],
                "duplicate_count": 0,
                "no_email_files": [],
                "no_email_count": 0,
                "unique_email_count": len(emails),
                "total_count": len(rows),
            }

    async def get_credential_errors(self, email: str) -> Dict[str, Any]:
        """获取凭证的错误信息"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if row:
                return {
                    "email": email,
                    "error_codes": _load_json(row.error_codes, []),
                    "error_messages": _load_json(row.error_messages, []),
                }
            return {"email": email, "error_codes": [], "error_messages": []}

    # ---------- 模型级冷却 ----------

    async def set_model_cooldown(
        self,
        email: str,
        model_name: str,
        cooldown_until: Optional[float],
    ) -> bool:
        """设置/清除模型的冷却时间（按家族分组：argon / gemini / claude_gpt / 其他原名）"""
        cd_group = get_cooldown_group(model_name)
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if not row:
                return False
            cooldowns = _load_json(row.model_cooldowns, {})
            if cooldown_until is None:
                cooldowns.pop(cd_group, None)
            else:
                cooldowns[cd_group] = cooldown_until
            row.model_cooldowns = _dump_json(cooldowns)
            row.updated_at = time.time()
            return True

    async def clear_all_model_cooldowns(self, email: str) -> bool:
        """清除某凭证的所有模型冷却"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if not row:
                return False
            row.model_cooldowns = "{}"
            row.updated_at = time.time()
            return True

    async def record_success(
        self, email: str, model_name: Optional[str] = None
    ) -> None:
        """成功调用后的条件写入（清除错误 + 该模型家族的CD）"""
        async with self.session() as s:
            row = await s.scalar(
                select(AntigravityCredential).where(AntigravityCredential.email == email)
            )
            if not row:
                return
            changed = False
            if _load_json(row.error_codes, []):
                row.error_codes = "[]"
                row.error_messages = "{}"
                row.last_success = time.time()
                row.updated_at = time.time()
                changed = True
            if model_name:
                cd_group = get_cooldown_group(model_name)
                cooldowns = _load_json(row.model_cooldowns, {})
                if cd_group in cooldowns:
                    cooldowns.pop(cd_group)
                    row.model_cooldowns = _dump_json(cooldowns)
                    row.updated_at = time.time()
                    changed = True
            if changed:
                log.debug(f"Recorded success for {email} (model={model_name})")

    # ---------- 配置管理（内存缓存） ----------

    async def _load_config_cache(self) -> None:
        async with self.session() as s:
            rows = (await s.scalars(select(ConfigItem))).all()
            for item in rows:
                try:
                    self._config_cache[item.key] = json.loads(item.value)
                except (json.JSONDecodeError, TypeError):
                    self._config_cache[item.key] = item.value
            self._config_loaded = True
            log.debug(f"Loaded {len(self._config_cache)} config items")

    async def reload_config_cache(self) -> None:
        self._ensure_initialized()
        self._config_cache.clear()
        await self._load_config_cache()
        log.info("Config cache reloaded from database")

    async def set_config(self, key: str, value: Any) -> bool:
        """设置配置（写库 + 更新缓存）"""
        async with self.session() as s:
            item = await s.get(ConfigItem, key)
            serialized = _dump_json(value)
            if item:
                item.value = serialized
                item.updated_at = time.time()
            else:
                s.add(ConfigItem(key=key, value=serialized))
            self._config_cache[key] = value
            return True

    async def get_config(self, key: str, default: Any = None) -> Any:
        self._ensure_initialized()
        return self._config_cache.get(key, default)

    async def get_all_config(self) -> Dict[str, Any]:
        self._ensure_initialized()
        return self._config_cache.copy()

    async def delete_config(self, key: str) -> bool:
        async with self.session() as s:
            item = await s.get(ConfigItem, key)
            if item:
                await s.delete(item)
                self._config_cache.pop(key, None)
                return True
            return False

    # ---------- 请求日志（usage_log） ----------

    async def insert_usage_log(self, entry: Dict[str, Any]) -> bool:
        """写入一条请求日志（失败仅告警，不影响主流程）"""
        try:
            async with self.session() as s:
                s.add(UsageLog(
                    created_at=entry.get("created_at") or time.time(),
                    api_format=str(entry.get("api_format", "")),
                    client_ip=str(entry.get("client_ip", "")),
                    request_id=str(entry.get("request_id", "")),
                    model_name=str(entry.get("model_name", "")),
                    upstream_model_name=str(entry.get("upstream_model_name", "")),
                    credential_email=str(entry.get("credential_email", "")),
                    is_stream=bool(entry.get("is_stream", False)),
                    status_code=int(entry.get("status_code", 0) or 0),
                    success=bool(entry.get("success", False)),
                    use_time=float(entry.get("use_time", 0.0) or 0.0),
                    prompt_tokens=int(entry.get("prompt_tokens", 0) or 0),
                    completion_tokens=int(entry.get("completion_tokens", 0) or 0),
                    total_tokens=int(entry.get("total_tokens", 0) or 0),
                    error_message=str(entry.get("error_message", ""))[:2000],
                ))
                return True
        except SQLAlchemyError as e:
            log.warning(f"写入请求日志失败: {e}")
            return False

    async def query_usage_logs(
        self,
        offset: int = 0,
        limit: int = 50,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
        model_name: str = "",
        credential_email: str = "",
        api_format: str = "",
        status: str = "all",  # all / success / error
        is_stream: Optional[bool] = None,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """分页查询请求日志，返回 (日志列表, 总数)，新 -> 旧"""
        stmt = select(UsageLog)
        count_stmt = select(func.count()).select_from(UsageLog)

        if start_timestamp:
            stmt = stmt.where(UsageLog.created_at >= start_timestamp)
            count_stmt = count_stmt.where(UsageLog.created_at >= start_timestamp)
        if end_timestamp:
            stmt = stmt.where(UsageLog.created_at <= end_timestamp)
            count_stmt = count_stmt.where(UsageLog.created_at <= end_timestamp)
        if model_name:
            stmt = stmt.where(UsageLog.model_name == model_name)
            count_stmt = count_stmt.where(UsageLog.model_name == model_name)
        if credential_email:
            stmt = stmt.where(UsageLog.credential_email == credential_email)
            count_stmt = count_stmt.where(UsageLog.credential_email == credential_email)
        if api_format:
            stmt = stmt.where(UsageLog.api_format == api_format)
            count_stmt = count_stmt.where(UsageLog.api_format == api_format)
        if status == "success":
            stmt = stmt.where(UsageLog.success.is_(True))
            count_stmt = count_stmt.where(UsageLog.success.is_(True))
        elif status == "error":
            stmt = stmt.where(UsageLog.success.is_(False))
            count_stmt = count_stmt.where(UsageLog.success.is_(False))
        if is_stream is not None:
            stmt = stmt.where(UsageLog.is_stream.is_(is_stream))
            count_stmt = count_stmt.where(UsageLog.is_stream.is_(is_stream))

        async with self.session() as s:
            total = (await s.execute(count_stmt)).scalar() or 0
            rows = (
                (
                    await s.execute(
                        stmt.order_by(UsageLog.created_at.desc(), UsageLog.id.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )

        items = [
            {
                "id": r.id,
                "created_at": r.created_at,
                "api_format": r.api_format or "",
                "client_ip": r.client_ip or "",
                "request_id": r.request_id or "",
                "model_name": r.model_name or "",
                "upstream_model_name": r.upstream_model_name or "",
                "credential_email": r.credential_email or "",
                "is_stream": r.is_stream,
                "status_code": r.status_code,
                "success": r.success,
                "use_time": r.use_time,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "total_tokens": r.total_tokens,
                "error_message": r.error_message or "",
            }
            for r in rows
        ]
        return items, total

    async def usage_log_stats(
        self,
        start_timestamp: Optional[float] = None,
        end_timestamp: Optional[float] = None,
        model_name: str = "",
        credential_email: str = "",
        api_format: str = "",
    ) -> Dict[str, Any]:
        """聚合统计：总请求数 / 成功数 / 失败数 / token 汇总 / 平均耗时 / 每模型聚合"""
        conditions = []
        if start_timestamp:
            conditions.append(UsageLog.created_at >= start_timestamp)
        if end_timestamp:
            conditions.append(UsageLog.created_at <= end_timestamp)
        if model_name:
            conditions.append(UsageLog.model_name == model_name)
        if credential_email:
            conditions.append(UsageLog.credential_email == credential_email)
        if api_format:
            conditions.append(UsageLog.api_format == api_format)

        success_cond = func.cast(UsageLog.success, Integer).label("success_int")

        agg = select(
            func.count().label("total_requests"),
            func.coalesce(func.sum(success_cond), 0).label("success_count"),
            func.coalesce(func.sum(UsageLog.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(UsageLog.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.avg(UsageLog.use_time), 0).label("avg_use_time"),
        ).where(*conditions)

        by_model = (
            select(
                UsageLog.model_name.label("model_name"),
                func.count().label("count"),
                func.coalesce(func.sum(UsageLog.total_tokens), 0).label("total_tokens"),
            )
            .where(*conditions)
            .group_by(UsageLog.model_name)
            .order_by(func.count().desc())
            .limit(50)
        )

        async with self.session() as s:
            row = (await s.execute(agg)).one()
            model_rows = (await s.execute(by_model)).all()

        return {
            "total_requests": row.total_requests or 0,
            "success_count": row.success_count or 0,
            "error_count": (row.total_requests or 0) - (row.success_count or 0),
            "prompt_tokens": row.prompt_tokens or 0,
            "completion_tokens": row.completion_tokens or 0,
            "total_tokens": row.total_tokens or 0,
            "avg_use_time": round(float(row.avg_use_time or 0), 2),
            "by_model": [
                {"model_name": m.model_name or "(unknown)", "count": m.count, "total_tokens": m.total_tokens}
                for m in model_rows
            ],
        }

    async def delete_usage_logs_before(self, before_timestamp: float) -> int:
        """删除指定时间之前的日志，返回删除条数"""
        async with self.session() as s:
            result = await s.execute(
                text("DELETE FROM usage_log WHERE created_at < :ts"),
                {"ts": before_timestamp},
            )
            return result.rowcount or 0

    # ---------- 后端信息 ----------

    def get_backend_info(self) -> Dict[str, Any]:
        return {
            "backend_type": self._backend_type,
            "initialized": self._initialized,
            "database_path": (
                os.path.join(os.getenv("CREDENTIALS_DIR", "./creds"), "credentials.db")
                if self._backend_type == "sqlite"
                else None
            ),
            "credentials_dir": os.getenv("CREDENTIALS_DIR", "./creds"),
        }


# ====================== 全局单例 ======================

_database: Optional[Database] = None
_database_lock = asyncio.Lock()


async def get_database() -> Database:
    """获取全局数据库实例"""
    global _database
    if _database is None:
        async with _database_lock:
            if _database is None:
                db = Database()
                await db.initialize()
                _database = db
    return _database


async def close_database() -> None:
    """关闭全局数据库"""
    global _database
    if _database:
        await _database.close()
        _database = None
