"""
Antigravity API Client - Handles communication with Google's Antigravity API
处理与 Google Antigravity API 的通信
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config import (
    get_antigravity_api_url,
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_return_thoughts_to_frontend,
    get_retry_429_enabled,
    get_retry_429_interval,
    get_retry_429_max_retries,
)
from log import log

from .credential_manager import CredentialManager
from .httpx_client import create_streaming_client_with_kwargs, http_client
from .models import Model, model_to_dict
from .utils import ANTIGRAVITY_USER_AGENT, parse_quota_reset_timestamp

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
            "session_id": session_id,
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


async def _filter_thinking_from_stream(lines, return_thoughts: bool):
    """过滤流式响应中的思维链（如果配置禁用）"""
    async for line in lines:
        if not line or not line.startswith("data: "):
            yield line
            continue

        raw = line[6:].strip()
        if raw == "[DONE]":
            yield line
            continue

        if not return_thoughts:
            try:
                data = json.loads(raw)
                response = data.get("response", {}) or {}
                candidate = (response.get("candidates", []) or [{}])[0] or {}
                parts = (candidate.get("content", {}) or {}).get("parts", []) or []

                # 过滤掉思维链部分
                filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]

                # 如果过滤后为空，跳过这一行
                if not filtered_parts and parts:
                    continue

                # 更新parts
                if filtered_parts != parts:
                    candidate["content"]["parts"] = filtered_parts
                    yield f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n"
                    continue
            except Exception:
                pass

        yield line


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
            antigravity_url = await get_antigravity_api_url()

            try:
                # 使用stream方法但不在async with块中消费数据
                stream_ctx = client.stream(
                    "POST",
                    f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse",
                    json=request_body,
                    headers=headers,
                )
                response = await stream_ctx.__aenter__()

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                    # 注意: 不在这里记录成功,在流式生成器中第一次收到数据时记录
                    # 获取配置并包装响应流，在源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    filtered_lines = _filter_thinking_from_stream(response.aiter_lines(), return_thoughts)
                    # 返回过滤后的行生成器和资源管理对象,让调用者管理资源生命周期
                    return (filtered_lines, stream_ctx, client), current_file, credential_data

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
            antigravity_url = await get_antigravity_api_url()

            # 使用上下文管理器确保正确的资源管理
            async with http_client.get_client(timeout=300.0) as client:
                response = await client.post(
                    f"{antigravity_url}/v1internal:generateContent",
                    json=request_body,
                    headers=headers,
                )

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Request successful with credential: {current_file}")
                    await credential_manager.record_api_call_result(
                        current_file, True, is_antigravity=True, model_key=model_name
                    )
                    response_data = response.json()

                    # 从源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    if not return_thoughts:
                        try:
                            candidate = (response_data.get("response", {}) or {}).get("candidates", [{}])[0] or {}
                            parts = (candidate.get("content", {}) or {}).get("parts", []) or []
                            # 过滤掉思维链部分
                            filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]
                            if filtered_parts != parts:
                                candidate["content"]["parts"] = filtered_parts
                        except Exception as e:
                            log.debug(f"[ANTIGRAVITY] Failed to filter thinking from response: {e}")

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
) -> List[Dict[str, Any]]:
    """
    获取可用模型列表，返回符合 OpenAI API 规范的格式

    Returns:
        模型列表，格式为字典列表（用于兼容现有代码）
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
        # 使用 POST 请求获取模型列表（根据 buildAxiosConfig，method 是 POST）
        antigravity_url = await get_antigravity_api_url()

        # 使用上下文管理器确保正确的资源管理
        async with http_client.get_client(timeout=30.0) as client:
            response = await client.post(
                f"{antigravity_url}/v1internal:fetchAvailableModels",
                json={},  # 空的请求体
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY] Raw models response: {json.dumps(data, ensure_ascii=False)[:500]}")

                # 转换为 OpenAI 格式的模型列表，使用 Model 类
                model_list = []
                current_timestamp = int(datetime.now(timezone.utc).timestamp())

                if 'models' in data and isinstance(data['models'], dict):
                    # 遍历模型字典
                    for model_id in data['models'].keys():
                        model = Model(
                            id=model_id,
                            object='model',
                            created=current_timestamp,
                            owned_by='google'
                        )
                        model_list.append(model_to_dict(model))

                # 添加额外的 claude-opus-4-5 模型
                claude_opus_model = Model(
                    id='claude-opus-4-5',
                    object='model',
                    created=current_timestamp,
                    owned_by='google'
                )
                model_list.append(model_to_dict(claude_opus_model))

                log.info(f"[ANTIGRAVITY] Fetched {len(model_list)} available models")
                return model_list
            else:
                log.error(f"[ANTIGRAVITY] Failed to fetch models ({response.status_code}): {response.text[:500]}")
                return []

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY] Failed to fetch models: {e}")
        log.error(f"[ANTIGRAVITY] Traceback: {traceback.format_exc()}")
        return []


async def fetch_quota_info(access_token: str) -> Dict[str, Any]:
    """
    获取指定凭证的额度信息

    Args:
        access_token: Antigravity 访问令牌

    Returns:
        包含额度信息的字典，格式为：
        {
            "success": True,
            "models": {
                "gemini-2.0-flash-exp": {
                    "remaining": 0.95,
                    "resetTime": "12-20 10:30",
                    "resetTimeRaw": "2025-12-20T02:30:00Z"
                }
            }
        }
    """

    headers = build_antigravity_headers(access_token)

    try:
        antigravity_url = await get_antigravity_api_url()

        async with http_client.get_client(timeout=30.0) as client:
            response = await client.post(
                f"{antigravity_url}/v1internal:fetchAvailableModels",
                json={},
                headers=headers,
            )

            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY QUOTA] Raw response: {json.dumps(data, ensure_ascii=False)[:500]}")

                quota_info = {}

                if 'models' in data and isinstance(data['models'], dict):
                    for model_id, model_data in data['models'].items():
                        if isinstance(model_data, dict) and 'quotaInfo' in model_data:
                            quota = model_data['quotaInfo']
                            remaining = quota.get('remainingFraction', 0)
                            reset_time_raw = quota.get('resetTime', '')

                            # 转换为北京时间
                            reset_time_beijing = 'N/A'
                            if reset_time_raw:
                                try:
                                    utc_date = datetime.fromisoformat(reset_time_raw.replace('Z', '+00:00'))
                                    # 转换为北京时间 (UTC+8)
                                    from datetime import timedelta
                                    beijing_date = utc_date + timedelta(hours=8)
                                    reset_time_beijing = beijing_date.strftime('%m-%d %H:%M')
                                except Exception as e:
                                    log.warning(f"[ANTIGRAVITY QUOTA] Failed to parse reset time: {e}")

                            quota_info[model_id] = {
                                "remaining": remaining,
                                "resetTime": reset_time_beijing,
                                "resetTimeRaw": reset_time_raw
                            }

                return {
                    "success": True,
                    "models": quota_info
                }
            else:
                log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota ({response.status_code}): {response.text[:500]}")
                return {
                    "success": False,
                    "error": f"API返回错误: {response.status_code}"
                }

    except Exception as e:
        import traceback
        log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quota: {e}")
        log.error(f"[ANTIGRAVITY QUOTA] Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e)
        }