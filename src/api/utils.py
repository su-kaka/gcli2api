"""
API 客户端共用基础功能：自动封禁、错误重试、调用结果记录、冷却解析
"""

import asyncio
import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import (
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log
from src.credential_manager import record_api_call_result, set_cred_disabled


# ==================== 错误检查与处理 ====================

async def check_should_auto_ban(status_code: int) -> bool:
    """检查是否应该触发自动封禁"""
    return (
        await get_auto_ban_enabled()
        and status_code in await get_auto_ban_error_codes()
    )


async def handle_auto_ban(status_code: int, email: str) -> None:
    """处理自动封禁：直接禁用凭证"""
    if email:
        log.warning(f"[AUTO_BAN] Status {status_code} triggers auto-ban for credential: {email}")
        await set_cred_disabled(email, True)


async def handle_error_with_retry(
    status_code: int,
    email: str,
    retry_enabled: bool,
    attempt: int,
    max_retries: int,
    retry_interval: float,
) -> bool:
    """
    统一处理错误和重试逻辑

    仅在以下情况下进行自动重试:
    1. 429错误(速率限制)
    2. 503错误(服务不可用)
    3. 500错误(服务临时不可用)
    4. 导致凭证封禁的错误(AUTO_BAN_ERROR_CODES配置)

    Returns:
        bool: True表示需要继续重试，False表示不需要重试
    """
    # 优先检查自动封禁
    if await check_should_auto_ban(status_code):
        await handle_auto_ban(status_code, email)

        # 自动封禁后，仍然尝试重试（会在下次循环中自动获取新凭证）
        if retry_enabled and attempt < max_retries:
            log.info(
                f"[RETRY] Retrying with next credential after auto-ban "
                f"(status {status_code}, attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(retry_interval)
            return True
        return False

    # 如果不触发自动封禁，仅对429、503和500错误进行重试
    if status_code in (429, 500, 503) and retry_enabled and attempt < max_retries:
        log.info(
            f"[RETRY] {status_code} error encountered, retrying "
            f"(attempt {attempt + 1}/{max_retries})"
        )
        await asyncio.sleep(retry_interval)
        return True

    return False


# ==================== 重试配置获取 ====================

async def get_retry_config() -> Dict[str, Any]:
    """获取重试配置"""
    return {
        "retry_enabled": await get_retry_429_enabled(),
        "max_retries": await get_retry_429_max_retries(),
        "retry_interval": await get_retry_429_interval(),
    }


# ==================== API调用结果记录 ====================

async def record_api_call_success(
    email: str,
    model_name: Optional[str] = None,
) -> None:
    """记录API调用成功"""
    if email:
        await record_api_call_result(
            email, True, model_name=model_name
        )


async def record_api_call_error(
    email: str,
    status_code: int,
    cooldown_until: Optional[float] = None,
    model_name: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """记录API调用错误"""
    if email:
        await record_api_call_result(
            email,
            False,
            status_code,
            cooldown_until=cooldown_until,
            model_name=model_name,
            error_message=error_message,
        )


# ==================== 429错误处理 ====================

async def parse_and_log_cooldown(error_text: str) -> Optional[float]:
    """
    解析并记录冷却时间

    Returns:
        冷却截止时间（Unix时间戳），解析失败返回None
    """
    try:
        error_data = json.loads(error_text)
        cooldown_until = parse_quota_reset_timestamp(error_data)
        if cooldown_until:
            log.info(
                f"检测到quota冷却时间: "
                f"{datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
            )
            return cooldown_until
    except Exception as parse_err:
        log.debug(f"Failed to parse cooldown time: {parse_err}")
    return None


def parse_quota_reset_timestamp(error_response: dict) -> Optional[float]:
    """
    从 Antigravity API 429 错误响应中提取quota重置时间戳

    解析优先级：
    1. ErrorInfo.metadata.quotaResetTimeStamp（绝对时间戳，最精确）
    2. ErrorInfo.metadata.quotaResetDelay（相对时长，如 "154h21m14.468080073s"）
    3. RetryInfo.retryDelay（纯秒数，如 "555674.468080073s"）
    """
    try:
        error_obj = error_response.get("error", {})
        details = error_obj.get("details", [])

        error_info_metadata: Optional[dict] = None
        retry_delay_str: Optional[str] = None

        for detail in details:
            detail_type = detail.get("@type", "")
            if detail_type == "type.googleapis.com/google.rpc.ErrorInfo":
                error_info_metadata = detail.get("metadata", {})
            elif detail_type == "type.googleapis.com/google.rpc.RetryInfo":
                retry_delay_str = detail.get("retryDelay")

        unit_to_seconds = {"s": 1, "m": 60, "h": 3600, "d": 86400}

        # 优先级1: quotaResetTimeStamp（绝对时间戳）
        if error_info_metadata:
            reset_timestamp_str = error_info_metadata.get("quotaResetTimeStamp")
            if reset_timestamp_str:
                if reset_timestamp_str.endswith("Z"):
                    reset_timestamp_str = reset_timestamp_str.replace("Z", "+00:00")

                reset_dt = datetime.fromisoformat(reset_timestamp_str)
                if reset_dt.tzinfo is None:
                    reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                return reset_dt.astimezone(timezone.utc).timestamp()

        # 优先级2: quotaResetDelay（相对时长，支持小数秒）
        if error_info_metadata:
            reset_delay_str = error_info_metadata.get("quotaResetDelay")
            if reset_delay_str:
                parts = re.findall(r"(\d+(?:\.\d+)?)([smhd])", reset_delay_str)
                if parts:
                    cooldown_seconds = sum(
                        float(value) * unit_to_seconds[unit] for value, unit in parts
                    )
                    if cooldown_seconds > 0:
                        return time.time() + cooldown_seconds

        # 优先级3: RetryInfo.retryDelay（纯秒数）
        if retry_delay_str:
            parts = re.findall(r"(\d+(?:\.\d+)?)([smhd])", retry_delay_str)
            if parts:
                cooldown_seconds = sum(
                    float(value) * unit_to_seconds[unit] for value, unit in parts
                )
                if cooldown_seconds > 0:
                    return time.time() + cooldown_seconds

        return None

    except Exception:
        return None
