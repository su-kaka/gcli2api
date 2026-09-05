"""
Base API Client - 共用的 API 客户端基础功能
提供错误处理、自动封禁、重试逻辑等共同功能
"""

import asyncio
import json
import math
import re
import time
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
    2. 503错误(服务不可用)
    3. 500错误(服务临时不可用)
    4. 导致凭证封禁的错误(AUTO_BAN_ERROR_CODES配置)

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

    # 如果不触发自动封禁，仅对429、503和500错误进行重试
    if status_code in (429, 500, 503) and retry_enabled and attempt < max_retries:
        log.info(
            f"[{mode.upper()} RETRY] {status_code} error encountered, retrying "
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
    model_name: Optional[str] = None
) -> None:
    """
    记录API调用成功
    
    Args:
        credential_manager: 凭证管理器实例
        credential_name: 凭证名称
        mode: 模式（geminicli 或 antigravity）
        model_name: 模型名称（用于模型级CD）
    """
    if credential_manager and credential_name:
        await credential_manager.record_api_call_result(
            credential_name, True, mode=mode, model_name=model_name
        )


async def record_api_call_error(
    credential_manager: CredentialManager,
    credential_name: str,
    status_code: int,
    cooldown_until: Optional[float] = None,
    mode: str = "geminicli",
    model_name: Optional[str] = None,
    error_message: Optional[str] = None
) -> None:
    """
    记录API调用错误

    Args:
        credential_manager: 凭证管理器实例
        credential_name: 凭证名称
        status_code: HTTP状态码
        cooldown_until: 冷却截止时间（Unix时间戳）
        mode: 模式（geminicli 或 antigravity）
        model_name: 模型名称（用于模型级CD）
        error_message: 错误信息（可选）
    """
    if credential_manager and credential_name:
        # Antigravity can return a model alias in the request while the 429
        # metadata contains the canonical model that actually exhausted its
        # quota.  Prefer the upstream observation so the persisted cooldown
        # overlays the matching entry in the quota panel.  This also repairs
        # callers that only passed the reset timestamp and request model.
        effective_model_name = model_name
        effective_cooldown_until = cooldown_until
        if mode.lower() == "antigravity" and status_code == 429 and error_message:
            try:
                if isinstance(error_message, str):
                    error_data = json.loads(error_message)
                elif isinstance(error_message, (bytes, bytearray)):
                    error_data = json.loads(error_message.decode("utf-8", errors="replace"))
                elif isinstance(error_message, dict):
                    error_data = error_message
                else:
                    error_data = None
                observation = extract_quota_exhaustion(
                    error_data,
                    mode=mode,
                )
                if observation:
                    observed_model = observation.get("model")
                    observed_reset = observation.get("reset_timestamp")
                    if observed_model:
                        effective_model_name = observed_model
                    if observed_reset and (
                        effective_cooldown_until is None
                        or observed_reset > effective_cooldown_until
                    ):
                        effective_cooldown_until = observed_reset
            except (TypeError, ValueError, json.JSONDecodeError):
                # A non-JSON upstream error must still be recorded normally.
                pass

        await credential_manager.record_api_call_result(
            credential_name,
            False,
            status_code,
            cooldown_until=effective_cooldown_until,
            mode=mode,
            model_name=effective_model_name,
            error_message=error_message
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
        cooldown_until = parse_quota_reset_timestamp(error_data, mode=mode)
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

async def collect_streaming_response(stream_generator) -> Response:
    """
    将Gemini流式响应收集为一条完整的非流式响应

    Args:
        stream_generator: 流式响应生成器，产生 "data: {json}" 格式的行或Response对象

    Returns:
        Response: 合并后的完整响应对象

    Example:
        >>> async for line in stream_generator:
        ...     # line format: "data: {...}" or Response object
        >>> response = await collect_streaming_response(stream_generator)
    """
    # 初始化响应结构
    merged_response = {
        "response": {
            "candidates": [{
                "content": {
                    "parts": [],
                    "role": "model"
                },
                "finishReason": None,
                "safetyRatings": [],
                "citationMetadata": None
            }],
            "usageMetadata": {
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
                "totalTokenCount": 0
            }
        }
    }

    collected_text = []  # 用于收集文本内容
    collected_thought_text = []  # 用于收集思维链内容
    collected_other_parts = []  # 用于收集其他类型的parts（图片、文件、工具调用等）
    collected_tool_parts_count = 0  # 记录工具调用相关part数量
    has_data = False
    line_count = 0

    log.debug("[STREAM COLLECTOR] Starting to collect streaming response")

    try:
        async for line in stream_generator:
            line_count += 1

            # 如果收到的是Response对象（错误），直接返回
            if isinstance(line, Response):
                log.debug(f"[STREAM COLLECTOR] 收到错误Response，状态码: {line.status_code}")
                return line

            # 处理 bytes 类型
            if isinstance(line, bytes):
                line_str = line.decode('utf-8', errors='ignore')
                log.debug(f"[STREAM COLLECTOR] Processing bytes line {line_count}: {line_str[:200] if line_str else 'empty'}")
            elif isinstance(line, str):
                line_str = line
                log.debug(f"[STREAM COLLECTOR] Processing line {line_count}: {line_str[:200] if line_str else 'empty'}")
            else:
                log.debug(f"[STREAM COLLECTOR] Skipping non-string/bytes line: {type(line)}")
                continue

            # 解析流式数据行
            if not line_str.startswith("data: "):
                log.debug(f"[STREAM COLLECTOR] Skipping line without 'data: ' prefix: {line_str[:100]}")
                continue

            raw = line_str[6:].strip()
            if raw == "[DONE]":
                log.debug("[STREAM COLLECTOR] Received [DONE] marker")
                break

            try:
                log.debug(f"[STREAM COLLECTOR] Parsing JSON: {raw[:200]}")
                chunk = json.loads(raw)
                has_data = True
                log.debug(f"[STREAM COLLECTOR] Chunk keys: {chunk.keys() if isinstance(chunk, dict) else type(chunk)}")

                # 提取响应对象
                response_obj = chunk.get("response", {})
                if not response_obj:
                    log.debug("[STREAM COLLECTOR] No 'response' key in chunk, trying direct access")
                    response_obj = chunk  # 尝试直接使用chunk

                candidates = response_obj.get("candidates", [])
                log.debug(f"[STREAM COLLECTOR] Found {len(candidates)} candidates")
                if not candidates:
                    log.debug(f"[STREAM COLLECTOR] No candidates in chunk, chunk structure: {list(chunk.keys()) if isinstance(chunk, dict) else type(chunk)}")
                    continue

                candidate = candidates[0]

                # 收集文本内容
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                log.debug(f"[STREAM COLLECTOR] Processing {len(parts)} parts from candidate")

                for part in parts:
                    if not isinstance(part, dict):
                        continue

                    # 优先保留工具调用相关 part（functionCall / functionResponse）
                    # 避免在 stream2nostream 模式下工具调用丢失
                    if "functionCall" in part or "functionResponse" in part or "function_call" in part:
                        collected_other_parts.append(part)
                        collected_tool_parts_count += 1
                        log.debug(f"[STREAM COLLECTOR] Collected tool part: {list(part.keys())}")
                        continue

                    # 处理文本内容
                    text = part.get("text", "")
                    if text:
                        # 区分普通文本和思维链
                        if part.get("thought", False):
                            collected_thought_text.append(text)
                            log.debug(f"[STREAM COLLECTOR] Collected thought text: {text[:100]}")
                        else:
                            collected_text.append(text)
                            log.debug(f"[STREAM COLLECTOR] Collected regular text: {text[:100]}")
                    # 处理非文本内容（图片、文件等）
                    elif "inlineData" in part or "fileData" in part or "executableCode" in part or "codeExecutionResult" in part:
                        collected_other_parts.append(part)
                        log.debug(f"[STREAM COLLECTOR] Collected non-text part: {list(part.keys())}")

                # 收集其他信息（使用最后一个块的值）
                if candidate.get("finishReason"):
                    merged_response["response"]["candidates"][0]["finishReason"] = candidate["finishReason"]

                if candidate.get("safetyRatings"):
                    merged_response["response"]["candidates"][0]["safetyRatings"] = candidate["safetyRatings"]

                if candidate.get("citationMetadata"):
                    merged_response["response"]["candidates"][0]["citationMetadata"] = candidate["citationMetadata"]

                # 更新使用元数据
                usage = response_obj.get("usageMetadata", {})
                if usage:
                    merged_response["response"]["usageMetadata"].update(usage)

            except json.JSONDecodeError as e:
                log.debug(f"[STREAM COLLECTOR] Failed to parse JSON chunk: {e}")
                continue
            except Exception as e:
                log.debug(f"[STREAM COLLECTOR] Error processing chunk: {e}")
                continue

    except Exception as e:
        log.error(f"[STREAM COLLECTOR] Error collecting stream after {line_count} lines: {e}")
        return Response(
            content=json.dumps({"error": f"收集流式响应失败: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )

    log.debug(f"[STREAM COLLECTOR] Finished iteration, has_data={has_data}, line_count={line_count}")

    # 如果没有收集到任何数据，返回错误
    if not has_data:
        log.error(f"[STREAM COLLECTOR] No data collected from stream after {line_count} lines")
        return Response(
            content=json.dumps({"error": "No data collected from stream"}),
            status_code=500,
            media_type="application/json"
        )

    # 组装最终的parts
    final_parts = []

    # 先添加思维链内容（如果有）
    if collected_thought_text:
        final_parts.append({
            "text": "".join(collected_thought_text),
            "thought": True
        })

    # 再添加普通文本内容
    if collected_text:
        final_parts.append({
            "text": "".join(collected_text)
        })

    # 添加其他类型的parts（图片、文件等）
    final_parts.extend(collected_other_parts)

    # 如果没有任何内容，添加空文本
    if not final_parts:
        final_parts.append({"text": ""})

    merged_response["response"]["candidates"][0]["content"]["parts"] = final_parts

    log.info(
        f"[STREAM COLLECTOR] Collected {len(collected_text)} text chunks, "
        f"{len(collected_thought_text)} thought chunks, {len(collected_other_parts)} other parts "
        f"(tool parts: {collected_tool_parts_count})"
    )

    # 去掉嵌套的 "response" 包装（Antigravity格式 -> 标准Gemini格式）
    if "response" in merged_response and "candidates" not in merged_response:
        log.debug(f"[STREAM COLLECTOR] 展开response包装")
        merged_response = merged_response["response"]

    # 返回纯JSON格式
    return Response(
        content=json.dumps(merged_response, ensure_ascii=False).encode('utf-8'),
        status_code=200,
        headers={},
        media_type="application/json"
    )


RESOURCE_EXHAUSTED_COOLDOWN_HOURS = 4  # RESOURCE_EXHAUSTED 错误的默认冷却时间（小时）

_QUOTA_RESET_DELAY_RE = re.compile(
    r"^\s*(?:(?P<days>\d+(?:\.\d+)?)d)?"
    r"(?:(?P<hours>\d+(?:\.\d+)?)h)?"
    r"(?:(?P<minutes>\d+(?:\.\d+)?)m)?"
    r"(?:(?P<seconds>\d+(?:\.\d+)?)s)?\s*$",
    re.IGNORECASE,
)


def _parse_quota_reset_delay(value: Any, now: Optional[float] = None) -> Optional[float]:
    """Convert Google's quotaResetDelay value into an absolute timestamp."""
    if not isinstance(value, str) or not value.strip():
        return None

    match = _QUOTA_RESET_DELAY_RE.fullmatch(value)
    if not match or not any(match.groupdict().values()):
        return None

    try:
        seconds = (
            float(match.group("days") or 0) * 86400
            + float(match.group("hours") or 0) * 3600
            + float(match.group("minutes") or 0) * 60
            + float(match.group("seconds") or 0)
        )
    except (TypeError, ValueError, OverflowError):
        return None
    if seconds <= 0 or not math.isfinite(seconds):
        return None
    timestamp = (time.time() if now is None else now) + seconds
    return timestamp if math.isfinite(timestamp) else None


def _parse_quota_reset_time(value: Any) -> Optional[float]:
    """Parse Google's ISO quota reset timestamp as UTC epoch seconds."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        reset_text = value.strip()
        if reset_text.endswith("Z"):
            reset_text = reset_text[:-1] + "+00:00"
        reset_dt = datetime.fromisoformat(reset_text)
        if reset_dt.tzinfo is None:
            reset_dt = reset_dt.replace(tzinfo=timezone.utc)
        return reset_dt.astimezone(timezone.utc).timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def extract_quota_exhaustion(
    error_response: dict,
    mode: str = "geminicli",
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Extract a confirmed quota exhaustion observation from a Google error."""
    if not isinstance(error_response, dict):
        return None

    error_obj = error_response.get("error")
    if not isinstance(error_obj, dict):
        return None

    quota_detail = None
    details = error_obj.get("details", [])
    if isinstance(details, list):
        for detail in details:
            if (
                isinstance(detail, dict)
                and str(detail.get("reason", "")).upper() == "QUOTA_EXHAUSTED"
            ):
                quota_detail = detail
                break

    # Antigravity also uses generic RESOURCE_EXHAUSTED responses for failures
    # that are not quota limits. Only its explicit QUOTA_EXHAUSTED reason is safe.
    if mode.lower() == "antigravity" and quota_detail is None:
        return None

    if quota_detail is None and error_obj.get("status") != "RESOURCE_EXHAUSTED":
        return None

    metadata = quota_detail.get("metadata", {}) if quota_detail else {}
    if not isinstance(metadata, dict):
        metadata = {}

    reset_timestamp = _parse_quota_reset_time(metadata.get("quotaResetTimeStamp"))
    if reset_timestamp is None:
        reset_timestamp = _parse_quota_reset_delay(metadata.get("quotaResetDelay"), now=now)

    if reset_timestamp is None:
        message = str(error_obj.get("message", ""))
        delay_match = re.search(r"Resets\s+in\s+([0-9dhms.]+)", message, re.IGNORECASE)
        if delay_match:
            reset_timestamp = _parse_quota_reset_delay(delay_match.group(1), now=now)

    if reset_timestamp is None:
        # RATE_LIMIT_EXCEEDED messages: "Your quota will reset after 6s." / "6h 30m 15s."
        message = str(error_obj.get("message", ""))
        will_reset_match = re.search(r"Your quota will reset after (.+?)\.", message)
        if will_reset_match:
            compact_delay = will_reset_match.group(1).strip().replace(" ", "")
            reset_timestamp = _parse_quota_reset_delay(compact_delay, now=now)

    if reset_timestamp is None and (
        quota_detail is not None
        or error_obj.get("message") == "Resource has been exhausted (e.g. check quota)."
    ):
        reset_timestamp = (time.time() if now is None else now) + (
            RESOURCE_EXHAUSTED_COOLDOWN_HOURS * 3600
        )

    model = metadata.get("model")
    return {
        "model": model.strip() if isinstance(model, str) else None,
        "reset_timestamp": reset_timestamp,
        "reason": "QUOTA_EXHAUSTED" if quota_detail is not None else "RESOURCE_EXHAUSTED",
        "explicit": quota_detail is not None,
    }


def parse_quota_reset_timestamp(
    error_response: dict,
    mode: str = "geminicli",
    now: Optional[float] = None,
) -> Optional[float]:
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
    observation = extract_quota_exhaustion(error_response, mode=mode, now=now)
    if observation is None:
        return None
    return observation.get("reset_timestamp")
