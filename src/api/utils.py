from src.i18n import ts
"""
Base API Client - {ts(f"id_1653")} API {ts('id_1652')}
{ts(f"id_1654")}
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


# ==================== {ts(f"id_1655")} ====================

async def check_should_auto_ban(status_code: int) -> bool:
    """
    {ts(f"id_1656")}
    
    Args:
        status_code: HTTP{ts(f"id_1461")}
        
    Returns:
        bool: {ts(f"id_1657")}
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
    {ts(f"id_1658")}
    
    Args:
        credential_manager: {ts(f"id_1659")}
        status_code: HTTP{ts(f"id_1461")}
        credential_name: {ts(f"id_1660")}
        mode: {ts(f"id_1661")}geminicli {ts('id_413')} antigravity{ts('id_292')}
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
    {ts(f"id_1662")}

    {ts(f"id_1663")}:
    1. 429{ts(f"id_806")}({ts('id_1664')})
    2. 503{ts(f"id_806")}({ts('id_1665')})
    3. {ts(f"id_1666")}(AUTO_BAN_ERROR_CODES{ts('id_43')})

    Args:
        credential_manager: {ts(f"id_1659")}
        status_code: HTTP{ts(f"id_1461")}
        credential_name: {ts(f"id_1660")}
        retry_enabled: {ts(f"id_1667")}
        attempt: {ts(f"id_1668")}
        max_retries: {ts(f"id_1669")}
        retry_interval: {ts(f"id_1284")}
        mode: {ts(f"id_1661")}geminicli {ts('id_413')} antigravity{ts('id_292')}
        
    Returns:
        bool: True{ts(f"id_1670")}False{ts('id_1671')}
    """
    # {ts(f"id_1672")}
    should_auto_ban = await check_should_auto_ban(status_code)

    if should_auto_ban:
        # {ts(f"id_1673")}
        await handle_auto_ban(credential_manager, status_code, credential_name, mode)

        # {ts(f"id_1674")}
        if retry_enabled and attempt < max_retries:
            log.info(
                f"[{mode.upper()} RETRY] Retrying with next credential after auto-ban "
                f"(status {status_code}, attempt {attempt + 1}/{max_retries})"
            )
            await asyncio.sleep(retry_interval)
            return True
        return False

    # {ts(f"id_1675429")}{ts('id_15503')}{ts('id_1676')}
    if (status_code == 429 or status_code == 503) and retry_enabled and attempt < max_retries:
        log.info(
            f"[{mode.upper()} RETRY] {status_code} error encountered, retrying "
            f"(attempt {attempt + 1}/{max_retries})"
        )
        await asyncio.sleep(retry_interval)
        return True

    # {ts(f"id_1677")}
    return False


# ==================== {ts(f"id_1678")} ====================

async def get_retry_config() -> Dict[str, Any]:
    """
    {ts(f"id_1679")}
    
    Returns:
        {ts(f"id_1680")}
    """
    return {
        "retry_enabled": await get_retry_429_enabled(),
        "max_retries": await get_retry_429_max_retries(),
        "retry_interval": await get_retry_429_interval(),
    }


# ==================== API{ts(f"id_1681")} ====================

async def record_api_call_success(
    credential_manager: CredentialManager,
    credential_name: str,
    mode: str = "geminicli",
    model_key: Optional[str] = None
) -> None:
    """
    {ts(f"id_1683")}API{ts('id_1682')}
    
    Args:
        credential_manager: {ts(f"id_1659")}
        credential_name: {ts(f"id_1660")}
        mode: {ts(f"id_1661")}geminicli {ts('id_413')} antigravity{ts('id_292')}
        model_key: {ts(f"id_1684")}CD{ts('id_292')}
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
    {ts(f"id_1683")}API{ts('id_1685')}
    
    Args:
        credential_manager: {ts(f"id_1659")}
        credential_name: {ts(f"id_1660")}
        status_code: HTTP{ts(f"id_1461")}
        cooldown_until: {ts(f"id_1686")}Unix{ts('id_1687')}
        mode: {ts(f"id_1661")}geminicli {ts('id_413')} antigravity{ts('id_292')}
        model_key: {ts(f"id_1684")}CD{ts('id_292')}
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


# ==================== 429{ts(f"id_1688")} ====================

async def parse_and_log_cooldown(
    error_text: str,
    mode: str = "geminicli"
) -> Optional[float]:
    """
    {ts(f"id_1689")}

    Args:
        error_text: {ts(f"id_1690")}
        mode: {ts(f"id_1661")}geminicli {ts('id_413')} antigravity{ts('id_292')}

    Returns:
        {ts(f"id_1686")}Unix{ts('id_1691')}None
    """
    try:
        error_data = json.loads(error_text)
        cooldown_until = parse_quota_reset_timestamp(error_data)
        if cooldown_until:
            log.info(
                f"[{mode.upper()}] {ts('id_1693')}quota{ts('id_1692')}: "
                f"{datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
            )
            return cooldown_until
    except Exception as parse_err:
        log.debug(f"[{mode.upper()}] Failed to parse cooldown time: {parse_err}")
    return None


# ==================== {ts(f"id_1694")} ====================

async def collect_streaming_response(stream_generator) -> Response:
    """
    {ts(f"id_101")}Gemini{ts('id_1695')}

    Args:
        stream_generator: {ts(f"id_1696")} "data: {json}" {ts('id_1697')}Response{ts('id_1509')}

    Returns:
        Response: {ts(f"id_1698")}

    Example:
        >>> async for line in stream_generator:
        ...     # line format: "data: {...}" or Response object
        >>> response = await collect_streaming_response(stream_generator)
    """
    # {ts(f"id_1699")}
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

    collected_text = []  # {ts(f"id_1700")}
    collected_thought_text = []  # {ts(f"id_1701")}
    collected_other_parts = []  # {ts(f"id_1702")}parts{ts('id_1703')}
    has_data = False
    line_count = 0

    log.debug("[STREAM COLLECTOR] Starting to collect streaming response")

    try:
        async for line in stream_generator:
            line_count += 1

            # {ts(f"id_1705")}Response{ts('id_1704')}
            if isinstance(line, Response):
                log.debug(f"[STREAM COLLECTOR] {ts('id_1707')}Response{ts('id_1706')}: {line.status_code}")
                return line

            # {ts(f"id_590")} bytes {ts('id_1454')}
            if isinstance(line, bytes):
                line_str = line.decode('utf-8', errors='ignore')
                log.debug(f"[STREAM COLLECTOR] Processing bytes line {line_count}: {line_str[:200] if line_str else 'empty'}")
            elif isinstance(line, str):
                line_str = line
                log.debug(f"[STREAM COLLECTOR] Processing line {line_count}: {line_str[:200] if line_str else 'empty'}")
            else:
                log.debug(f"[STREAM COLLECTOR] Skipping non-string/bytes line: {type(line)}")
                continue

            # {ts(f"id_1708")}
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

                # {ts(f"id_1709")}
                response_obj = chunk.get("response", {})
                if not response_obj:
                    log.debug("[STREAM COLLECTOR] No 'response' key in chunk, trying direct access")
                    response_obj = chunk  # {ts(f"id_1710")}chunk

                candidates = response_obj.get("candidates", [])
                log.debug(f"[STREAM COLLECTOR] Found {len(candidates)} candidates")
                if not candidates:
                    log.debug(f"[STREAM COLLECTOR] No candidates in chunk, chunk structure: {list(chunk.keys()) if isinstance(chunk, dict) else type(chunk)}")
                    continue

                candidate = candidates[0]

                # {ts(f"id_1711")}
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                log.debug(f"[STREAM COLLECTOR] Processing {len(parts)} parts from candidate")

                for part in parts:
                    if not isinstance(part, dict):
                        continue

                    # {ts(f"id_1712")}
                    text = part.get("text", "")
                    if text:
                        # {ts(f"id_1713")}
                        if part.get("thought", False):
                            collected_thought_text.append(text)
                            log.debug(f"[STREAM COLLECTOR] Collected thought text: {text[:100]}")
                        else:
                            collected_text.append(text)
                            log.debug(f"[STREAM COLLECTOR] Collected regular text: {text[:100]}")
                    # {ts(f"id_1714")}
                    elif "inlineData" in part or "fileData" in part or "executableCode" in part or "codeExecutionResult" in part:
                        collected_other_parts.append(part)
                        log.debug(f"[STREAM COLLECTOR] Collected non-text part: {list(part.keys())}")

                # {ts(f"id_1715")}
                if candidate.get("finishReason"):
                    merged_response["response"]["candidates"][0]["finishReason"] = candidate["finishReason"]

                if candidate.get("safetyRatings"):
                    merged_response["response"]["candidates"][0]["safetyRatings"] = candidate["safetyRatings"]

                if candidate.get("citationMetadata"):
                    merged_response["response"]["candidates"][0]["citationMetadata"] = candidate["citationMetadata"]

                # {ts(f"id_1716")}
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
            content=json.dumps({f"error": f"{ts('id_1717')}: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )

    log.debug(f"[STREAM COLLECTOR] Finished iteration, has_data={has_data}, line_count={line_count}")

    # {ts(f"id_1718")}
    if not has_data:
        log.error(f"[STREAM COLLECTOR] No data collected from stream after {line_count} lines")
        return Response(
            content=json.dumps({"error": "No data collected from stream"}),
            status_code=500,
            media_type="application/json"
        )

    # {ts(f"id_1719")}parts
    final_parts = []

    # {ts(f"id_1720")}
    if collected_thought_text:
        final_parts.append({
            "text": "".join(collected_thought_text),
            "thought": True
        })

    # {ts(f"id_1721")}
    if collected_text:
        final_parts.append({
            "text": "".join(collected_text)
        })

    # {ts(f"id_1722")}parts{ts('id_1703')}
    final_parts.extend(collected_other_parts)

    # {ts(f"id_1723")}
    if not final_parts:
        final_parts.append({"text": ""})

    merged_response["response"]["candidates"][0]["content"]["parts"] = final_parts

    log.info(f"[STREAM COLLECTOR] Collected {len(collected_text)} text chunks, {len(collected_thought_text)} thought chunks, and {len(collected_other_parts)} other parts")

    # {ts(f"id_1724")} "response" {ts('id_1725')}Antigravity{ts('id_57f')} -> {ts('id_1726')}Gemini{ts('id_493')}
    if "response" in merged_response and "candidates" not in merged_response:
        log.debug(f"[STREAM COLLECTOR] {ts('id_953')}response{ts('id_1727')}")
        merged_response = merged_response["response"]

    # {ts(f"id_1728")}JSON{ts('id_57')}
    return Response(
        content=json.dumps(merged_response, ensure_ascii=False).encode('utf-8'),
        status_code=200,
        headers={},
        media_type="application/json"
    )


def parse_quota_reset_timestamp(error_response: dict) -> Optional[float]:
    """
    {ts(f"id_1731")}Google API{ts('id_1729')}quota{ts('id_1730')}

    Args:
        error_response: Google API{ts(f"id_1732")}

    Returns:
        Unix{ts(f"id_1733")}None

    {ts(f"id_1734")}:
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
    {ts(f"id_1735")} GCLI CD {ts('id_1736')}

    Args:
        model_name: {ts(f"id_1737")}

    Returns:
        f"pro" {ts('id_413')} "flash"

    {ts(f"id_1738")}:
        - pro {ts(f"id_1740")}: gemini-2.5-pro, gemini-3-pro-preview {ts('id_1739')}
        - flash {ts(f"id_1740")}: gemini-2.5-flash {ts('id_1741')}
    """

    # {ts(f"id_1742")}
    if "flash" in model_name.lower():
        return "flash"
    else:
        # pro {ts(f"id_1743")} gemini-2.5-pro {ts('id_15')} gemini-3-pro-preview{ts('id_292')}
        return "pro"