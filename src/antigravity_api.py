"""
Antigravity API Client - Handles communication with Google's Antigravity API
处理与 Google Antigravity API 的通信
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Response

from config import (
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log

from .credential_manager import CredentialManager
from .httpx_client import create_streaming_client_with_kwargs, http_client
from .utils import parse_quota_reset_timestamp


# Antigravity API 配置
ANTIGRAVITY_URL = "https://daily-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_HOST = "daily-cloudcode-pa.sandbox.googleapis.com"
ANTIGRAVITY_USER_AGENT = "antigravity/1.11.3 windows/amd64"


def _create_error_response(message: str, status_code: int = 500) -> Response:
    """Create standardized error response."""
    return Response(
        content=json.dumps(
            {"error": {"message": message, "type": "api_error", "code": status_code}}
        ),
        status_code=status_code,
        media_type="application/json",
    )


async def _check_should_auto_ban(status_code: int) -> bool:
    """检查是否应该触发自动封禁"""
    return (
        await get_auto_ban_enabled()
        and status_code in await get_auto_ban_error_codes()
    )


async def _handle_auto_ban(
    credential_manager: CredentialManager,
    status_code: int,
    credential_name: str
) -> None:
    """处理自动封禁：直接禁用凭证"""
    if credential_manager and credential_name:
        log.warning(
            f"[ANTIGRAVITY AUTO_BAN] Status {status_code} triggers auto-ban for credential: {credential_name}"
        )
        await credential_manager.set_cred_disabled(credential_name, True, is_antigravity=True)


def build_antigravity_headers(access_token: str) -> Dict[str, str]:
    """构建 Antigravity API 请求头"""
    return {
        'Host': ANTIGRAVITY_HOST,
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip'
    }


def generate_request_id() -> str:
    """生成请求 ID"""
    import uuid
    return f"req-{uuid.uuid4()}"


def build_antigravity_request_body(
    contents: List[Dict[str, Any]],
    model: str,
    project_id: str,
    session_id: str,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    generation_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建 Antigravity 请求体

    Args:
        contents: 消息内容列表
        model: 模型名称
        project_id: 项目 ID
        session_id: 会话 ID
        system_instruction: 系统指令
        tools: 工具定义列表
        generation_config: 生成配置

    Returns:
        Antigravity 格式的请求体
    """
    request_body = {
        "project": project_id,
        "requestId": generate_request_id(),
        "model": model,
        "userAgent": "antigravity",
        "request": {
            "contents": contents,
            "sessionId": session_id,
        }
    }

    # 添加系统指令
    if system_instruction:
        request_body["request"]["systemInstruction"] = system_instruction

    # 添加工具定义
    if tools:
        request_body["request"]["tools"] = tools
        request_body["request"]["toolConfig"] = {
            "functionCallingConfig": {"mode": "VALIDATED"}
        }

    # 添加生成配置
    if generation_config:
        request_body["request"]["generationConfig"] = generation_config

    return request_body


async def send_antigravity_request_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
) -> Tuple[Any, str, Dict[str, Any]]:
    """
    发送 Antigravity 流式请求

    Returns:
        (response, credential_name, credential_data)
    """
    retry_enabled = await get_retry_429_enabled()
    max_retries = await get_retry_429_max_retries()
    retry_interval = await get_retry_429_interval()

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )
        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available")
            raise Exception("No valid antigravity credentials available")

        current_file, credential_data = cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
            continue

        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, attempt {attempt + 1}/{max_retries + 1})")

        # 构建请求头
        headers = build_antigravity_headers(access_token)

        try:
            # 发送流式请求
            client = await create_streaming_client_with_kwargs()

            try:
                # 使用stream方法但不在async with块中消费数据
                stream_ctx = client.stream(
                    "POST",
                    f"{ANTIGRAVITY_URL}/v1internal:streamGenerateContent?alt=sse",
                    json=request_body,
                    headers=headers,
                )
                response = await stream_ctx.__aenter__()

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                    # 注意: 不在这里记录成功,在流式生成器中第一次收到数据时记录
                    # 返回响应和资源管理对象,让调用者管理资源生命周期
                    return (response, stream_ctx, client), current_file, credential_data

                # 处理错误
                error_body = await response.aread()
                error_text = error_body.decode('utf-8', errors='ignore')
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}): {error_text[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    try:
                        error_data = json.loads(error_text)
                        cooldown_until = parse_quota_reset_timestamp(error_data)
                        if cooldown_until:
                            log.info(
                                f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                            )
                    except Exception as parse_err:
                        log.debug(f"[ANTIGRAVITY] Failed to parse cooldown time: {parse_err}")

                await credential_manager.record_api_call_result(
                    current_file,
                    False,
                    response.status_code,
                    cooldown_until=cooldown_until,
                    is_antigravity=True,
                    model_key=model_name  # 传递模型名称用于模型级 CD
                )

                # 检查自动封禁
                if await _check_should_auto_ban(response.status_code):
                    await _handle_auto_ban(credential_manager, response.status_code, current_file)

                # 清理资源
                try:
                    await stream_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                await client.aclose()

                # 重试逻辑
                if retry_enabled and attempt < max_retries:
                    log.warning(f"[ANTIGRAVITY RETRY] Retrying ({attempt + 1}/{max_retries})")
                    await asyncio.sleep(retry_interval)
                    continue

                raise Exception(f"Antigravity API error ({response.status_code}): {error_text[:200]}")

            except Exception as stream_error:
                # 确保在异常情况下也清理资源
                try:
                    await client.aclose()
                except Exception:
                    pass
                raise stream_error

        except Exception as e:
            log.error(f"[ANTIGRAVITY] Request failed with credential {current_file}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(retry_interval)
                continue
            raise

    raise Exception("All antigravity retry attempts failed")


async def send_antigravity_request_no_stream(
    request_body: Dict[str, Any],
    credential_manager: CredentialManager,
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    发送 Antigravity 非流式请求

    Returns:
        (response_data, credential_name, credential_data)
    """
    retry_enabled = await get_retry_429_enabled()
    max_retries = await get_retry_429_max_retries()
    retry_interval = await get_retry_429_interval()

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            is_antigravity=True, model_key=model_name
        )
        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available")
            raise Exception("No valid antigravity credentials available")

        current_file, credential_data = cred_result
        access_token = credential_data.get("access_token") or credential_data.get("token")

        if not access_token:
            log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
            continue

        log.info(f"[ANTIGRAVITY] Using credential: {current_file} (model={model_name}, attempt {attempt + 1}/{max_retries + 1})")

        # 构建请求头
        headers = build_antigravity_headers(access_token)

        try:
            # 发送非流式请求
            response = await http_client.post(
                f"{ANTIGRAVITY_URL}/v1internal:generateContent",
                json=request_body,
                headers=headers,
                timeout=300.0,
            )

            # 检查响应状态
            if response.status_code == 200:
                log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                await credential_manager.record_api_call_result(
                    current_file, True, is_antigravity=True, model_key=model_name
                )
                response_data = response.json()
                return response_data, current_file, credential_data

            # 处理错误
            error_body = response.text
            log.error(f"[ANTIGRAVITY] API error ({response.status_code}): {error_body[:500]}")

            # 记录错误（使用模型级 CD）
            cooldown_until = None
            if response.status_code == 429:
                try:
                    error_data = json.loads(error_body)
                    cooldown_until = parse_quota_reset_timestamp(error_data)
                    if cooldown_until:
                        log.info(
                            f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}"
                        )
                except Exception as parse_err:
                    log.debug(f"[ANTIGRAVITY] Failed to parse cooldown time: {parse_err}")

            await credential_manager.record_api_call_result(
                current_file,
                False,
                response.status_code,
                cooldown_until=cooldown_until,
                is_antigravity=True,
                model_key=model_name  # 传递模型名称用于模型级 CD
            )

            # 检查自动封禁
            if await _check_should_auto_ban(response.status_code):
                await _handle_auto_ban(credential_manager, response.status_code, current_file)

            # 重试逻辑
            if retry_enabled and attempt < max_retries:
                log.warning(f"[ANTIGRAVITY RETRY] Retrying ({attempt + 1}/{max_retries})")
                await asyncio.sleep(retry_interval)
                continue

            raise Exception(f"Antigravity API error ({response.status_code}): {error_body[:200]}")

        except Exception as e:
            log.error(f"[ANTIGRAVITY] Request failed with credential {current_file}: {e}")
            if attempt < max_retries:
                await asyncio.sleep(retry_interval)
                continue
            raise

    raise Exception("All antigravity retry attempts failed")


async def fetch_available_models(
    credential_manager: CredentialManager,
) -> List[str]:
    """
    获取可用模型列表

    Returns:
        模型名称列表
    """
    # 获取可用凭证
    cred_result = await credential_manager.get_valid_credential(is_antigravity=True)
    if not cred_result:
        log.error("[ANTIGRAVITY] No valid credentials available for fetching models")
        return []

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")

    if not access_token:
        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
        return []

    # 构建请求头
    headers = build_antigravity_headers(access_token)

    try:
        # 发送请求
        response = await http_client.get(
            f"{ANTIGRAVITY_URL}/v1internal:fetchAvailableModels",
            headers=headers,
            timeout=30.0,
        )

        if response.status_code == 200:
            data = response.json()
            # 提取模型名称
            models = data.get("models", [])
            model_names = [model.get("name", "") for model in models if model.get("name")]
            log.info(f"[ANTIGRAVITY] Fetched {len(model_names)} available models")
            return model_names
        else:
            log.error(f"[ANTIGRAVITY] Failed to fetch models ({response.status_code}): {response.text[:500]}")
            return []

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Failed to fetch models: {e}")
        return []
