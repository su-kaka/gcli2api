"""
凭证管理（无状态函数封装）
以 email 为唯一键管理 Antigravity 凭证：负载均衡选取、自动 token 刷新、错误记录与模型级冷却。

全部函数直接使用全局数据库单例（get_database），无实例状态；
唯一的状态是 sticky 模式的当前粘性凭证指针（进程内模块级变量）。
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from log import log

from config import get_credential_select_mode
from src.google_oauth_api import Credentials
from src.storage.database import get_database

try:
    from src import session_cache
except ImportError:  # 允许独立导入（测试场景）
    session_cache = None

# sticky 模式的当前粘性凭证（进程内唯一状态；重置函数供测试使用）
_sticky_email: Optional[str] = None


def _reset_sticky() -> None:
    """清除粘性凭证指针（测试用）"""
    global _sticky_email
    _sticky_email = None


# ====================== 凭证选取 ======================

async def get_valid_credential(
    model_name: Optional[str] = None,
    session_key: Optional[str] = None,
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """
    获取可用凭证：按凭证选择模式选取，自动跳过禁用/冷却凭证。
    Token 失效时自动刷新；刷新失败（凭证永久失效）自动禁用并尝试下一个。

    凭证选择模式（config: credential_select_mode）:
    - random: 完全随机负载均衡（忽略 session_key）
    - cache:  会话缓存匹配——session_key 命中绑定时复用该凭证（prompt cache 亲和）
    - sticky: 粘性——尽量保持同一凭证直到其进入 CD 或被禁用

    Returns:
        (email, credential_data) 或 None
    """
    global _sticky_email
    database = await get_database()
    mode = await get_credential_select_mode()

    # cache 模式：先查会话绑定
    if mode == "cache" and session_key:
        bound_email = await session_cache.get_session_binding(session_key)
        if bound_email:
            result = await database.get_available_credential_by_email(
                bound_email, model_name=model_name
            )
            if result:
                log.debug(f"会话缓存命中: {bound_email}")
                final = await _validate_or_refresh(result[0], result[1])
                if final is None:
                    # 绑定凭证 token 永久失效等，解绑走正常选取
                    await session_cache.delete_session_binding(session_key)
                else:
                    return final
            else:
                # 绑定凭证不可用（禁用/CD/删除），解绑
                await session_cache.delete_session_binding(session_key)

    # sticky 模式：优先当前粘性凭证
    if mode == "sticky" and _sticky_email:
        result = await database.get_available_credential_by_email(
            _sticky_email, model_name=model_name
        )
        if result:
            final = await _validate_or_refresh(result[0], result[1])
            if final is not None:
                return final
        # 粘性凭证 CD/禁用/失效，清除指针换新
        _sticky_email = None

    # 通用随机选取（random 模式 / cache 未命中 / sticky 无可用粘性凭证）
    max_retries = 3
    for attempt in range(max_retries):
        result = await database.get_next_available_credential(model_name=model_name)

        if not result:
            if attempt == 0:
                log.warning(f"没有可用凭证 (model_name={model_name})")
            return None

        email, credential_data = result

        if await should_refresh_token(credential_data):
            log.debug(f"Token需要刷新: {email}")
            refreshed = await refresh_token(credential_data, email)
            if refreshed:
                await _after_picked(mode, email, session_key)
                return email, refreshed
            log.warning(
                f"Token刷新失败，尝试下一个凭证: {email} (attempt={attempt + 1}/{max_retries})"
            )
            continue

        await _after_picked(mode, email, session_key)
        return email, credential_data

    log.error(f"重试{max_retries}次后仍无可用凭证 (model_name={model_name})")
    return None


async def _validate_or_refresh(
    email: str, credential_data: Dict[str, Any]
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """对已选中的凭证做 token 校验；需要刷新则刷新并返回，永久失效返回 None"""
    if await should_refresh_token(credential_data):
        refreshed = await refresh_token(credential_data, email)
        if refreshed is None:
            return None
        credential_data = refreshed
    return email, credential_data


async def _after_picked(mode: str, email: str, session_key: Optional[str]) -> None:
    """凭证选取成功后的模式相关处理"""
    global _sticky_email
    try:
        if mode == "cache" and session_key:
            await session_cache.set_session_binding(session_key, email)
        elif mode == "sticky":
            _sticky_email = email
    except Exception as e:
        log.debug(f"记录凭证亲和失败（不影响请求）: {e}")


# ====================== 凭证增删改 ======================

async def add_credential(email: str, credential_data: Dict[str, Any]):
    """新增或更新凭证（同 email 覆盖，自动去重）"""
    database = await get_database()
    await database.store_credential(email, credential_data)
    log.info(f"Credential added/updated: {email}")


async def remove_credential(email: str) -> bool:
    """删除凭证"""
    database = await get_database()
    try:
        await database.delete_credential(email)
        log.info(f"Credential removed: {email}")
        return True
    except Exception as e:
        log.error(f"Error removing credential {email}: {e}")
        return False


async def update_credential_state(email: str, state_updates: Dict[str, Any]) -> bool:
    """更新凭证状态字段（disabled/error_codes/tier/project_id 等）"""
    database = await get_database()
    try:
        success = await database.update_credential_state(email, state_updates)
        if not success:
            log.warning(f"Failed to update credential state: {email}")
        return success
    except Exception as e:
        log.error(f"Error updating credential state {email}: {e}")
        return False


async def set_cred_disabled(email: str, disabled: bool):
    """设置凭证的启用/禁用状态"""
    success = await update_credential_state(email, {"disabled": disabled})
    if success:
        log.info(f"Credential {'disabled' if disabled else 'enabled'}: {email}")
    return success


# ====================== API 调用结果记录 ======================

async def record_api_call_result(
    email: str,
    success: bool,
    error_code: Optional[int] = None,
    cooldown_until: Optional[float] = None,
    model_name: Optional[str] = None,
    error_message: Optional[str] = None,
):
    """
    记录 API 调用结果：
    - 成功：清除错误状态与该模型的冷却（fire-and-forget）
    - 失败：记录错误码/错误信息，必要时设置模型级冷却
    """
    database = await get_database()
    try:
        if success:
            asyncio.create_task(database.record_success(email, model_name=model_name))
        elif error_code:
            error_messages = {}
            if error_message:
                error_messages[str(error_code)] = error_message

            await update_credential_state(email, {
                "error_codes": [error_code],
                "error_messages": error_messages,
            })

            if cooldown_until is not None and model_name:
                await database.set_model_cooldown(email, model_name, cooldown_until)
                log.info(
                    f"设置模型级冷却: {email}, model={model_name}, "
                    f"冷却至: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                )
    except Exception as e:
        log.error(f"Error recording API call result for {email}: {e}")


# ====================== Token 刷新 ======================

async def should_refresh_token(credential_data: Dict[str, Any]) -> bool:
    """检查 token 是否需要刷新（剩余有效期不足 5 分钟视为需要刷新）"""
    access_token = credential_data.get("access_token") or credential_data.get("token")
    if not access_token:
        return True

    expiry_str = credential_data.get("expiry")
    if not expiry_str:
        return True

    try:
        if not isinstance(expiry_str, str):
            return True
        if "+" in expiry_str:
            expiry = datetime.fromisoformat(expiry_str)
        elif expiry_str.endswith("Z"):
            expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        else:
            expiry = datetime.fromisoformat(expiry_str)

        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)

        time_left = (expiry - datetime.now(timezone.utc)).total_seconds()
        return time_left <= 300  # 5 分钟缓冲

    except Exception as e:
        log.warning(f"解析过期时间失败: {e}，需要刷新")
        return True


async def refresh_token(
    credential_data: Dict[str, Any], email: str
) -> Optional[Dict[str, Any]]:
    """刷新 token 并保存；凭证永久失效时自动禁用，返回 None"""
    database = await get_database()
    try:
        creds = Credentials.from_dict(credential_data)

        if not creds.refresh_token:
            log.error(f"没有refresh_token，无法刷新: {email}")
            await update_credential_state(email, {"disabled": True})
            log.warning(f"凭证已自动禁用（缺少refresh_token）: {email}")
            return None

        log.debug(f"正在刷新token: {email}")
        await creds.refresh()

        if creds.access_token:
            credential_data["access_token"] = creds.access_token
            credential_data["token"] = creds.access_token
        if creds.expires_at:
            credential_data["expiry"] = creds.expires_at

        await database.store_credential(email, credential_data)
        log.info(f"Token刷新成功并已保存: {email}")
        return credential_data

    except Exception as e:
        error_msg = str(e)
        log.error(f"Token刷新失败 {email}: {error_msg}")

        status_code = getattr(e, "status_code", None)

        if _is_permanent_refresh_failure(error_msg, status_code):
            log.warning(f"检测到凭证永久失效 (HTTP {status_code}): {email}")
            await record_api_call_result(email, False, error_code=status_code or 400)
            if await update_credential_state(email, {"disabled": True}):
                log.warning(f"永久失效凭证已禁用: {email}")
        else:
            log.warning(f"Token刷新失败但非永久性错误 (HTTP {status_code})，不封禁凭证: {email}")

        return None


def _is_permanent_refresh_failure(error_msg: str, status_code: Optional[int] = None) -> bool:
    """判断是否是凭证永久失效（客户端错误/明确失效错误）"""
    if status_code is not None:
        if status_code in (400, 401, 403):
            return True
        if status_code in (500, 502, 503, 504):
            return False
        if status_code == 429:
            return False

    permanent_patterns = (
        "invalid_grant",
        "refresh_token_expired",
        "invalid_refresh_token",
        "unauthorized_client",
        "access_denied",
    )
    error_msg_lower = error_msg.lower()
    return any(p in error_msg_lower for p in permanent_patterns)
