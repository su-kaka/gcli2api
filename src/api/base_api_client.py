"""
Base API Client - 共用的 API 客户端基础功能
提供错误处理、自动封禁、重试逻辑等共同功能
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Callable

from config import (
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log
from src.credential_manager import CredentialManager
from src.utils import parse_quota_reset_timestamp


# ==================== 错误检查与处理 ====================

async def check_should_auto_ban(status_code: int) -> bool:
    """
    检查是否应该触发自动封禁
    
    Args:
        status_code: HTTP状态码
        
    Returns:
        bool: 是否应该触发自动封禁
    """
    return (
        await get_auto_ban_enabled()
        and status_code in await get_auto_ban_error_codes()
    )


async def handle_auto_ban(
    credential_manager: CredentialManager,
    status_code: int,
    credential_name: str,
    mode: str = "geminicli"
) -> None:
    """
    处理自动封禁：直接禁用凭证
    
    Args:
        credential_manager: 凭证管理器实例
        status_code: HTTP状态码
        credential_name: 凭证名称
        mode: 模式（geminicli 或 antigravity）
    """
    if credential_manager and credential_name:
        log.warning(
            f"[{mode.upper()} AUTO_BAN] Status {status_code} triggers auto-ban for credential: {credential_name}"
        )
        await credential_manager.set_cred_disabled(
            credential_name, True, mode=mode
        )


async def handle_error_with_retry(
    credential_manager: CredentialManager,
    status_code: int,
    credential_name: str,
    retry_enabled: bool,
    attempt: int,
    max_retries: int,
    retry_interval: float,
    mode: str = "geminicli"
) -> bool:
    """
    统一处理错误和重试逻辑
    
    仅在以下情况下进行自动重试:
    1. 429错误(速率限制)
    2. 导致凭证封禁的错误(AUTO_BAN_ERROR_CODES配置)
    
    Args:
        credential_manager: 凭证管理器实例
        status_code: HTTP状态码
        credential_name: 凭证名称
        retry_enabled: 是否启用重试
        attempt: 当前重试次数
        max_retries: 最大重试次数
        retry_interval: 重试间隔
        mode: 模式（geminicli 或 antigravity）
        
    Returns:
        bool: True表示需要继续重试，False表示不需要重试
    """
    # 优先检查自动封禁
    should_auto_ban = await check_should_auto_ban(status_code)

    if should_auto_ban:
        # 触发自动封禁
        await handle_auto_ban(credential_manager, status_code, credential_name, mode)

        # 自动封禁后，仍然尝试重试（会在下次循环中自动获取新凭证）
        if retry_enabled and attempt < max_retries:
            log.info(
                f"[{mode.upper()} RETRY] Retrying with next credential after auto-ban "
                f"(status {status_code}, attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(retry_interval)
            return True
        return False

    # 如果不触发自动封禁，仅对429错误进行重试
    if status_code == 429 and retry_enabled and attempt < max_retries:
        log.info(
            f"[{mode.upper()} RETRY] 429 rate limit encountered, retrying "
            f"(attempt {attempt + 1}/{max_retries})"
        )
        await asyncio.sleep(retry_interval)
        return True

    # 其他错误不进行重试
    return False


# ==================== 重试配置获取 ====================

async def get_retry_config() -> Dict[str, Any]:
    """
    获取重试配置
    
    Returns:
        包含重试配置的字典
    """
    return {
        "retry_enabled": await get_retry_429_enabled(),
        "max_retries": await get_retry_429_max_retries(),
        "retry_interval": await get_retry_429_interval(),
    }


# ==================== API调用结果记录 ====================

async def record_api_call_success(
    credential_manager: CredentialManager,
    credential_name: str,
    mode: str = "geminicli",
    model_key: Optional[str] = None
) -> None:
    """
    记录API调用成功
    
    Args:
        credential_manager: 凭证管理器实例
        credential_name: 凭证名称
        mode: 模式（geminicli 或 antigravity）
        model_key: 模型键（用于模型级CD）
    """
    if credential_manager and credential_name:
        await credential_manager.record_api_call_result(
            credential_name, True, mode=mode, model_key=model_key
        )


async def record_api_call_error(
    credential_manager: CredentialManager,
    credential_name: str,
    status_code: int,
    cooldown_until: Optional[float] = None,
    mode: str = "geminicli",
    model_key: Optional[str] = None
) -> None:
    """
    记录API调用错误
    
    Args:
        credential_manager: 凭证管理器实例
        credential_name: 凭证名称
        status_code: HTTP状态码
        cooldown_until: 冷却截止时间（Unix时间戳）
        mode: 模式（geminicli 或 antigravity）
        model_key: 模型键（用于模型级CD）
    """
    if credential_manager and credential_name:
        await credential_manager.record_api_call_result(
            credential_name,
            False,
            status_code,
            cooldown_until=cooldown_until,
            mode=mode,
            model_key=model_key
        )


# ==================== 429错误处理 ====================

async def parse_and_log_cooldown(
    error_text: str,
    mode: str = "geminicli"
) -> Optional[float]:
    """
    解析并记录冷却时间
    
    Args:
        error_text: 错误响应文本
        mode: 模式（geminicli 或 antigravity）
        
    Returns:
        冷却截止时间（Unix时间戳），如果解析失败则返回None
    """
    try:
        import json
        error_data = json.loads(error_text)
        cooldown_until = parse_quota_reset_timestamp(error_data)
        if cooldown_until:
            log.info(
                f"[{mode.upper()}] 检测到quota冷却时间: "
                f"{datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
            )
            return cooldown_until
    except Exception as parse_err:
        log.debug(f"[{mode.upper()}] Failed to parse cooldown time: {parse_err}")
    return None


# ==================== 通用请求执行器 ====================

async def execute_request_with_retry(
    credential_manager: CredentialManager,
    request_executor: Callable,
    mode: str = "geminicli",
    model_key: Optional[str] = None,
    **executor_kwargs
) -> Any:
    """
    带重试机制的通用请求执行器
    
    Args:
        credential_manager: 凭证管理器实例
        request_executor: 请求执行函数，签名应为 async def executor(credential_name, credential_data, **kwargs)
        mode: 模式（geminicli 或 antigravity）
        model_key: 模型键（用于模型级CD和凭证获取）
        **executor_kwargs: 传递给执行器的额外参数
        
    Returns:
        请求执行器的返回值
        
    Raises:
        Exception: 如果所有重试都失败
    """
    retry_config = await get_retry_config()
    retry_enabled = retry_config["retry_enabled"]
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    for attempt in range(max_retries + 1):
        # 获取可用凭证
        cred_result = await credential_manager.get_valid_credential(
            mode=mode, model_key=model_key
        )
        if not cred_result:
            log.error(f"[{mode.upper()}] No valid credentials available")
            raise Exception(f"No valid {mode} credentials available")

        credential_name, credential_data = cred_result

        log.info(
            f"[{mode.upper()}] Using credential: {credential_name} "
            f"(model={model_key}, attempt {attempt + 1}/{max_retries + 1})"
        )

        try:
            # 执行请求
            return await request_executor(
                credential_name=credential_name,
                credential_data=credential_data,
                attempt=attempt,
                **executor_kwargs
            )

        except Exception as e:
            # 检查是否是HTTP错误
            if hasattr(e, "status_code"):
                status_code = e.status_code
                
                # 尝试记录错误
                cooldown_until = None
                if status_code == 429 and hasattr(e, "text"):
                    cooldown_until = await parse_and_log_cooldown(e.text, mode)
                
                await record_api_call_error(
                    credential_manager,
                    credential_name,
                    status_code,
                    cooldown_until,
                    mode,
                    model_key
                )
                
                # 处理重试逻辑
                should_retry = await handle_error_with_retry(
                    credential_manager,
                    status_code,
                    credential_name,
                    retry_enabled,
                    attempt,
                    max_retries,
                    retry_interval,
                    mode
                )
                
                if should_retry:
                    continue
            else:
                # 非HTTP错误，直接记录并可能重试
                log.error(f"[{mode.upper()}] Request failed with credential {credential_name}: {e}")
                if attempt < max_retries:
                    await asyncio.sleep(retry_interval)
                    continue
            
            raise

    raise Exception(f"All {mode} retry attempts failed")


# ==================== 响应数据处理 ====================

def unwrap_geminicli_response(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    从 GeminiCLI/Antigravity 响应中提取 response 字段

    GeminiCLI/Antigravity 返回格式: {"response": {...}, "traceId": ...}
    提取后格式: {...}

    Args:
        data: GeminiCLI/Antigravity 响应数据

    Returns:
        提取后的响应数据（无包装）
    """
    from log import log
    
    if isinstance(data, dict) and "response" in data:
        log.debug(f"[UNWRAP] Unwrapping response, keys before: {list(data.keys())}")
        unwrapped = data["response"]
        log.debug(f"[UNWRAP] Unwrapped keys: {list(unwrapped.keys()) if isinstance(unwrapped, dict) else type(unwrapped)}")
        return unwrapped
    
    log.debug(f"[UNWRAP] No unwrapping needed, data type: {type(data)}, keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
    return data
