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
    2. 503错误(服务不可用)
    3. 导致凭证封禁的错误(AUTO_BAN_ERROR_CODES配置)

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

    # 如果不触发自动封禁，仅对429和503错误进行重试
    if (status_code == 429 or status_code == 503) and retry_enabled and attempt < max_retries:
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
        await credential_manager.record_api_call_result(
            credential_name,
            False,
            status_code,
            cooldown_until=cooldown_until,
            mode=mode,
            model_name=model_name,
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

    ordered_segments = []  # 按原始顺序收集part，避免丢失 functionCall/functionResponse
    text_chunk_count = 0
    thought_chunk_count = 0
    other_part_count = 0
    tool_part_count = 0
    tool_call_part_count = 0
    tool_response_part_count = 0
    unique_tool_call_count = 0
    collected_tool_names = set()
    tool_call_segment_index = {}
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

                    # 1) 优先保留工具相关part（连续工具调用场景关键）
                    if "functionCall" in part or "functionResponse" in part:
                        function_call = part.get("functionCall")
                        function_call_id = ""
                        if isinstance(function_call, dict):
                            function_call_id = str(function_call.get("id", "")).strip()
                        is_new_tool_call = False

                        # 同一个 functionCall.id 多次出现时，用最后一个分片覆盖，避免重复工具调用
                        if function_call_id and function_call_id in tool_call_segment_index:
                            ordered_segments[tool_call_segment_index[function_call_id]] = {
                                "type": "raw",
                                "part": part.copy()
                            }
                        else:
                            ordered_segments.append({"type": "raw", "part": part.copy()})
                            if function_call_id:
                                tool_call_segment_index[function_call_id] = len(ordered_segments) - 1
                            if isinstance(function_call, dict):
                                is_new_tool_call = True

                        tool_part_count += 1

                        if isinstance(function_call, dict):
                            tool_call_part_count += 1
                            tool_name = function_call.get("name")
                            if isinstance(tool_name, str) and tool_name.strip():
                                collected_tool_names.add(tool_name.strip())
                            if is_new_tool_call:
                                unique_tool_call_count += 1

                        if isinstance(part.get("functionResponse"), dict):
                            tool_response_part_count += 1

                        log.debug(f"[STREAM COLLECTOR] Collected tool part: {list(part.keys())}")
                        continue

                    # 2) 处理文本内容（按块记录，最后再做相邻合并，保持整体顺序）
                    text = part.get("text", "")
                    if isinstance(text, str) and text:
                        if part.get("thought", False):
                            ordered_segments.append({"type": "thought_text", "text": text})
                            thought_chunk_count += 1
                            log.debug(f"[STREAM COLLECTOR] Collected thought text: {text[:100]}")
                        else:
                            ordered_segments.append({"type": "text", "text": text})
                            text_chunk_count += 1
                            log.debug(f"[STREAM COLLECTOR] Collected regular text: {text[:100]}")
                        continue

                    # 3) 处理明确的非文本内容（图片、文件、代码等）
                    if (
                        "inlineData" in part
                        or "fileData" in part
                        or "executableCode" in part
                        or "codeExecutionResult" in part
                    ):
                        ordered_segments.append({"type": "raw", "part": part.copy()})
                        other_part_count += 1
                        log.debug(f"[STREAM COLLECTOR] Collected non-text part: {list(part.keys())}")
                        continue

                    # 4) 保底：只要part有非空字段就保留，避免未来新增字段被吞掉
                    has_valid_payload = any(
                        value not in (None, "", {}, [])
                        for key, value in part.items()
                        if key != "thought"
                    )
                    if has_valid_payload:
                        ordered_segments.append({"type": "raw", "part": part.copy()})
                        other_part_count += 1
                        log.debug(f"[STREAM COLLECTOR] Collected fallback raw part: {list(part.keys())}")

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

    # 组装最终的parts（保持结构化part顺序，只合并相邻文本块）
    final_parts = []

    for segment in ordered_segments:
        seg_type = segment.get("type")

        if seg_type == "raw":
            part = segment.get("part")
            if isinstance(part, dict):
                final_parts.append(part)
            continue

        if seg_type not in ("text", "thought_text"):
            continue

        text_value = segment.get("text")
        if not isinstance(text_value, str) or not text_value:
            continue

        is_thought = seg_type == "thought_text"
        if final_parts:
            last_part = final_parts[-1]
            if (
                isinstance(last_part, dict)
                and isinstance(last_part.get("text"), str)
                and set(last_part.keys()).issubset({"text", "thought"})
                and bool(last_part.get("thought", False)) == is_thought
            ):
                last_part["text"] += text_value
                continue

        if is_thought:
            final_parts.append({
                "text": text_value,
                "thought": True
            })
        else:
            final_parts.append({
                "text": text_value
            })

    # 如果没有任何内容，添加空文本
    if not final_parts:
        final_parts.append({"text": ""})

    merged_response["response"]["candidates"][0]["content"]["parts"] = final_parts

    tool_names_str = ", ".join(sorted(collected_tool_names)) if collected_tool_names else "-"
    log.info(
        "[STREAM COLLECTOR] Collected "
        f"{text_chunk_count} text chunks, "
        f"{thought_chunk_count} thought chunks, "
        f"{tool_part_count} tool parts "
        f"({tool_call_part_count} functionCall/{tool_response_part_count} functionResponse, "
        f"{unique_tool_call_count} unique functionCall), "
        f"and {other_part_count} other parts; "
        f"tools={tool_names_str}"
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
