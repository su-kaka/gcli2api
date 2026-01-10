"""
Base API Client - 共用的 API 客户端基础功能
提供错误处理、自动封禁、重试逻辑等共同功能
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Response

from config import (
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log
from src.credential_manager import CredentialManager


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


# ==================== 流式响应收集 ====================

async def collect_streaming_response(stream) -> Response:
    """
    收集流式响应并拼接成完整的Response

    Args:
        stream: 异步流式生成器（str流或bytes流的Gemini API回复）

    Returns:
        Response: 完整的拼接后的响应对象
    """
    collected_chunks = []
    status_code = 200
    headers = {}

    try:
        async for chunk in stream:
            # 如果收到的是Response对象（错误），直接返回
            if isinstance(chunk, Response):
                log.debug(f"[STREAM COLLECT] 收到错误Response，状态码: {chunk.status_code}")
                return chunk

            # 收集chunk
            if isinstance(chunk, bytes):
                collected_chunks.append(chunk)
            elif isinstance(chunk, str):
                collected_chunks.append(chunk.encode('utf-8'))
            else:
                # 其他类型，尝试转换为字符串
                collected_chunks.append(str(chunk).encode('utf-8'))

        # 拼接所有chunks
        full_content = b''.join(collected_chunks)

        log.debug(f"[STREAM COLLECT] 成功收集流式响应，总大小: {len(full_content)} bytes")

        # 解析SSE格式，提取JSON内容
        content_str = full_content.decode('utf-8')

        # 如果是SSE格式（以 "data: " 开头），提取JSON
        if content_str.strip().startswith("data: "):
            log.debug(f"[STREAM COLLECT] 检测到SSE格式，提取JSON内容")

            # 解析SSE格式的每一行，合并所有chunk
            merged_response = None
            all_parts = []

            for line in content_str.split('\n'):
                line = line.strip()
                if line.startswith("data: "):
                    json_str = line[6:]  # 去掉 "data: " 前缀

                    # 跳过 [DONE] 标记
                    if json_str == "[DONE]":
                        continue

                    # 尝试解析JSON
                    try:
                        json_data = json.loads(json_str)

                        # 提取内容并累积
                        if "response" in json_data:
                            # 如果是第一个chunk，保存完整结构
                            if merged_response is None:
                                merged_response = json_data

                            # 提取并累积所有parts（包括text、inlineData、fileData等）
                            candidates = json_data.get("response", {}).get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                # 保留所有类型的parts
                                all_parts.extend(parts)

                        log.debug(f"[STREAM COLLECT] 处理chunk: {json.dumps(json_data, ensure_ascii=False)[:100]}")
                    except json.JSONDecodeError:
                        log.debug(f"[STREAM COLLECT] 跳过非JSON行: {json_str[:100]}")
                        continue

            if merged_response:
                # 将所有parts放回响应结构中
                if all_parts:
                    merged_response["response"]["candidates"][0]["content"]["parts"] = all_parts

                log.debug(f"[STREAM COLLECT] 合并了 {len(all_parts)} 个parts（包括文本、图片等）")

                # 返回纯JSON格式
                return Response(
                    content=json.dumps(merged_response, ensure_ascii=False).encode('utf-8'),
                    status_code=status_code,
                    headers=headers,
                    media_type="application/json"
                )
            else:
                log.warning(f"[STREAM COLLECT] 未能从SSE格式中提取有效JSON")
                # 如果提取失败，返回原始内容
                return Response(
                    content=full_content,
                    status_code=status_code,
                    headers=headers,
                    media_type="application/json"
                )
        else:
            # 不是SSE格式，直接返回原始内容
            log.debug(f"[STREAM COLLECT] 非SSE格式，直接返回")
            return Response(
                content=full_content,
                status_code=status_code,
                headers=headers,
                media_type="application/json"
            )

    except Exception as e:
        log.error(f"[STREAM COLLECT] 收集流式响应时出错: {e}")
        return Response(
            content=json.dumps({"error": f"收集流式响应失败: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )


def parse_quota_reset_timestamp(error_response: dict) -> Optional[float]:
    """
    从Google API错误响应中提取quota重置时间戳

    Args:
        error_response: Google API返回的错误响应字典

    Returns:
        Unix时间戳（秒），如果无法解析则返回None

    示例错误响应:
    {
      "error": {
        "code": 429,
        "message": "You have exhausted your capacity...",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
          {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "QUOTA_EXHAUSTED",
            "metadata": {
              "quotaResetTimeStamp": "2025-11-30T14:57:24Z",
              "quotaResetDelay": "13h19m1.20964964s"
            }
          }
        ]
      }
    }
    """
    try:
        details = error_response.get("error", {}).get("details", [])

        for detail in details:
            if detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
                reset_timestamp_str = detail.get("metadata", {}).get("quotaResetTimeStamp")

                if reset_timestamp_str:
                    if reset_timestamp_str.endswith("Z"):
                        reset_timestamp_str = reset_timestamp_str.replace("Z", "+00:00")

                    reset_dt = datetime.fromisoformat(reset_timestamp_str)
                    if reset_dt.tzinfo is None:
                        reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                    return reset_dt.astimezone(timezone.utc).timestamp()

        return None

    except Exception:
        return None

def get_model_group(model_name: str) -> str:
    """
    获取模型组，用于 GCLI CD 机制。

    Args:
        model_name: 模型名称

    Returns:
        "pro" 或 "flash"

    说明:
        - pro 组: gemini-2.5-pro, gemini-3-pro-preview 共享额度
        - flash 组: gemini-2.5-flash 单独额度
    """

    # 判断模型组
    if "flash" in model_name.lower():
        return "flash"
    else:
        # pro 模型（包括 gemini-2.5-pro 和 gemini-3-pro-preview）
        return "pro"