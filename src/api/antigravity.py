"""
Antigravity API Client - Handles communication with Google's Antigravity API
处理与 Google Antigravity API 的通信
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Response
from config import (
    get_antigravity_api_url,
    get_antigravity_stream2nostream,
    get_auto_ban_error_codes,
)
from log import log

from src.credential_manager import CredentialManager
from src.httpx_client import stream_post_async, post_async
from src.models import Model, model_to_dict
from src.utils import ANTIGRAVITY_USER_AGENT

# 导入共同的基础功能
from src.api.utils import (
    handle_error_with_retry,
    get_retry_config,
    record_api_call_success,
    record_api_call_error,
    parse_and_log_cooldown,
    collect_streaming_response,
)

# ==================== 全局凭证管理器 ====================

# 全局凭证管理器实例（单例模式）
_credential_manager: Optional[CredentialManager] = None


async def _get_credential_manager() -> CredentialManager:
    """
    获取全局凭证管理器实例
    
    Returns:
        CredentialManager实例
    """
    global _credential_manager
    if not _credential_manager:
        _credential_manager = CredentialManager()
        await _credential_manager.initialize()
    return _credential_manager


# ==================== 辅助函数 ====================

def build_antigravity_headers(access_token: str, model_name: str = "") -> Dict[str, str]:
    """
    构建 Antigravity API 请求头

    Args:
        access_token: 访问令牌
        model_name: 模型名称，用于判断 request_type

    Returns:
        请求头字典
    """
    headers = {
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip',
        'requestId': f"req-{uuid.uuid4()}"
    }

    # 根据模型名称判断 request_type
    if model_name:
        request_type = "image_gen" if "image" in model_name.lower() else "agent"
        headers['requestType'] = request_type

    return headers


# ==================== 新的流式和非流式请求函数 ====================

async def stream_request(
    body: Dict[str, Any],
    native: bool = False,
    headers: Optional[Dict[str, str]] = None,
):
    """
    流式请求函数

    Args:
        body: 请求体
        native: 是否返回原生bytes流，False则返回str流
        headers: 额外的请求头

    Yields:
        Response对象（错误时）或 bytes流/str流（成功时）
    """
    # 获取凭证管理器
    credential_manager = await _get_credential_manager()

    model_name = body.get("model", "")

    # 1. 获取有效凭证
    cred_result = await credential_manager.get_valid_credential(
        mode="antigravity", model_key=model_name
    )

    if not cred_result:
        # 如果返回值是None，直接返回错误500
        log.error("[ANTIGRAVITY STREAM] 当前无可用凭证")
        yield Response(
            content=json.dumps({"error": "当前无可用凭证"}),
            status_code=500,
            media_type="application/json"
        )
        return

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")

    if not access_token:
        log.error(f"[ANTIGRAVITY STREAM] No access token in credential: {current_file}")
        yield Response(
            content=json.dumps({"error": "凭证中没有访问令牌"}),
            status_code=500,
            media_type="application/json"
        )
        return

    # 2. 构建URL和请求头
    antigravity_url = await get_antigravity_api_url()
    target_url = f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse"

    auth_headers = build_antigravity_headers(access_token, model_name)

    # 合并自定义headers
    if headers:
        auth_headers.update(headers)

    # 3. 调用stream_post_async进行请求
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # 禁用凭证的错误码
    last_error_response = None  # 记录最后一次的错误响应

    for attempt in range(max_retries + 1):
        success_recorded = False  # 标记是否已记录成功

        try:
            async for chunk in stream_post_async(
                url=target_url,
                body=body,
                native=native,
                headers=auth_headers
            ):
                # 判断是否是Response对象
                if isinstance(chunk, Response):
                    status_code = chunk.status_code
                    last_error_response = chunk  # 记录最后一次错误

                    # 如果错误码是429或者不在禁用码当中，做好记录后进行重试
                    if status_code == 429 or status_code not in DISABLE_ERROR_CODES:
                        log.warning(f"[ANTIGRAVITY STREAM] 流式请求失败 (status={status_code}), 凭证: {current_file}")

                        # 记录错误
                        cooldown_until = None
                        if status_code == 429:
                            # 尝试解析冷却时间
                            try:
                                error_body = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                                cooldown_until = await parse_and_log_cooldown(error_body, mode="antigravity")
                            except Exception:
                                pass

                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            cooldown_until, mode="antigravity", model_key=model_name
                        )

                        # 检查是否应该重试
                        should_retry = await handle_error_with_retry(
                            credential_manager, status_code, current_file,
                            retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                            mode="antigravity"
                        )

                        if should_retry and attempt < max_retries:
                            # 重新获取凭证并重试
                            log.info(f"[ANTIGRAVITY STREAM] 重试请求 (attempt {attempt + 2}/{max_retries + 1})...")
                            await asyncio.sleep(retry_interval)

                            # 获取新凭证
                            cred_result = await credential_manager.get_valid_credential(
                                mode="antigravity", model_key=model_name
                            )
                            if not cred_result:
                                log.error("[ANTIGRAVITY STREAM] 重试时无可用凭证")
                                yield Response(
                                    content=json.dumps({"error": "当前无可用凭证"}),
                                    status_code=500,
                                    media_type="application/json"
                                )
                                return

                            current_file, credential_data = cred_result
                            access_token = credential_data.get("access_token") or credential_data.get("token")

                            if not access_token:
                                log.error(f"[ANTIGRAVITY STREAM] No access token in credential: {current_file}")
                                yield Response(
                                    content=json.dumps({"error": "凭证中没有访问令牌"}),
                                    status_code=500,
                                    media_type="application/json"
                                )
                                return

                            auth_headers = build_antigravity_headers(access_token, model_name)
                            if headers:
                                auth_headers.update(headers)
                            break  # 跳出内层循环，重新请求
                        else:
                            # 不重试，直接返回原始错误
                            log.error(f"[ANTIGRAVITY STREAM] 达到最大重试次数或不应重试，返回原始错误")
                            yield chunk
                            return
                    else:
                        # 错误码在禁用码当中，直接返回，无需重试
                        log.error(f"[ANTIGRAVITY STREAM] 流式请求失败，禁用错误码 (status={status_code}), 凭证: {current_file}")
                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            None, mode="antigravity", model_key=model_name
                        )
                        yield chunk
                        return
                else:
                    # 不是Response，说明是真流，直接yield返回
                    # 只在第一个chunk时记录成功
                    if not success_recorded:
                        await record_api_call_success(
                            credential_manager, current_file, mode="antigravity", model_key=model_name
                        )
                        success_recorded = True

                    yield chunk

            # 流式请求成功完成，退出重试循环
            return

        except Exception as e:
            log.error(f"[ANTIGRAVITY STREAM] 流式请求异常: {e}, 凭证: {current_file}")
            if attempt < max_retries:
                log.info(f"[ANTIGRAVITY STREAM] 异常后重试 (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # 所有重试都失败，返回最后一次的错误（如果有）
                log.error(f"[ANTIGRAVITY STREAM] 所有重试均失败，最后异常: {e}")
                yield last_error_response


async def non_stream_request(
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Response:
    """
    非流式请求函数

    Args:
        body: 请求体
        headers: 额外的请求头

    Returns:
        Response对象
    """
    # 检查是否启用流式收集模式
    if await get_antigravity_stream2nostream():
        log.info("[ANTIGRAVITY] 使用流式收集模式实现非流式请求")

        # 调用stream_request获取流
        stream = stream_request(body=body, native=False, headers=headers)

        # 收集流式响应
        # stream_request是一个异步生成器，可能yield Response（错误）或流数据
        # collect_streaming_response会自动处理这两种情况
        return await collect_streaming_response(stream)

    # 否则使用传统非流式模式
    log.info("[ANTIGRAVITY] 使用传统非流式模式")

    # 获取凭证管理器
    credential_manager = await _get_credential_manager()

    model_name = body.get("model", "")

    # 1. 获取有效凭证
    cred_result = await credential_manager.get_valid_credential(
        mode="antigravity", model_key=model_name
    )

    if not cred_result:
        # 如果返回值是None，直接返回错误500
        log.error("[ANTIGRAVITY] 当前无可用凭证")
        return Response(
            content=json.dumps({"error": "当前无可用凭证"}),
            status_code=500,
            media_type="application/json"
        )

    current_file, credential_data = cred_result
    access_token = credential_data.get("access_token") or credential_data.get("token")

    if not access_token:
        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
        return Response(
            content=json.dumps({"error": "凭证中没有访问令牌"}),
            status_code=500,
            media_type="application/json"
        )

    # 2. 构建URL和请求头
    antigravity_url = await get_antigravity_api_url()
    target_url = f"{antigravity_url}/v1internal:generateContent"

    auth_headers = build_antigravity_headers(access_token, model_name)

    # 合并自定义headers
    if headers:
        auth_headers.update(headers)

    # 3. 调用post_async进行请求
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # 禁用凭证的错误码
    last_error_response = None  # 记录最后一次的错误响应

    for attempt in range(max_retries + 1):
        try:
            response = await post_async(
                url=target_url,
                json=body,
                headers=auth_headers,
                timeout=300.0
            )

            status_code = response.status_code

            # 成功
            if status_code == 200:
                await record_api_call_success(
                    credential_manager, current_file, mode="antigravity", model_key=model_name
                )
                return Response(
                    content=response.content,
                    status_code=200,
                    headers=dict(response.headers)
                )

            # 失败 - 记录最后一次错误
            last_error_response = Response(
                content=response.content,
                status_code=status_code,
                headers=dict(response.headers)
            )

            # 判断是否需要重试
            if status_code == 429 or status_code not in DISABLE_ERROR_CODES:
                log.warning(f"[ANTIGRAVITY] 非流式请求失败 (status={status_code}), 凭证: {current_file}")

                # 记录错误
                cooldown_until = None
                if status_code == 429:
                    # 尝试解析冷却时间
                    try:
                        error_text = response.text
                        cooldown_until = await parse_and_log_cooldown(error_text, mode="antigravity")
                    except Exception:
                        pass

                await record_api_call_error(
                    credential_manager, current_file, status_code,
                    cooldown_until, mode="antigravity", model_key=model_name
                )

                # 检查是否应该重试
                should_retry = await handle_error_with_retry(
                    credential_manager, status_code, current_file,
                    retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                    mode="antigravity"
                )

                if should_retry and attempt < max_retries:
                    # 重新获取凭证并重试
                    log.info(f"[ANTIGRAVITY] 重试请求 (attempt {attempt + 2}/{max_retries + 1})...")
                    await asyncio.sleep(retry_interval)

                    # 获取新凭证
                    cred_result = await credential_manager.get_valid_credential(
                        mode="antigravity", model_key=model_name
                    )
                    if not cred_result:
                        log.error("[ANTIGRAVITY] 重试时无可用凭证")
                        return Response(
                            content=json.dumps({"error": "当前无可用凭证"}),
                            status_code=500,
                            media_type="application/json"
                        )

                    current_file, credential_data = cred_result
                    access_token = credential_data.get("access_token") or credential_data.get("token")

                    if not access_token:
                        log.error(f"[ANTIGRAVITY] No access token in credential: {current_file}")
                        return Response(
                            content=json.dumps({"error": "凭证中没有访问令牌"}),
                            status_code=500,
                            media_type="application/json"
                        )

                    auth_headers = build_antigravity_headers(access_token, model_name)
                    if headers:
                        auth_headers.update(headers)
                    continue  # 重试
                else:
                    # 不重试，直接返回原始错误
                    log.error(f"[ANTIGRAVITY] 达到最大重试次数或不应重试，返回原始错误")
                    return last_error_response
            else:
                # 错误码在禁用码当中，直接返回，无需重试
                log.error(f"[ANTIGRAVITY] 非流式请求失败，禁用错误码 (status={status_code}), 凭证: {current_file}")
                await record_api_call_error(
                    credential_manager, current_file, status_code,
                    None, mode="antigravity", model_key=model_name
                )
                return last_error_response

        except Exception as e:
            log.error(f"[ANTIGRAVITY] 非流式请求异常: {e}, 凭证: {current_file}")
            if attempt < max_retries:
                log.info(f"[ANTIGRAVITY] 异常后重试 (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # 所有重试都失败，返回最后一次的错误（如果有）
                log.error(f"[ANTIGRAVITY] 所有重试均失败，最后异常: {e}")
                return last_error_response

    # 所有重试都失败，返回最后一次的原始错误
    log.error("[ANTIGRAVITY] 所有重试均失败")
    return last_error_response


# ==================== 模型和配额查询 ====================

async def fetch_available_models() -> List[Dict[str, Any]]:
    """
    获取可用模型列表，返回符合 OpenAI API 规范的格式
    
    Returns:
        模型列表，格式为字典列表（用于兼容现有代码）
        
    Raises:
        返回空列表如果获取失败
    """
    # 获取凭证管理器和可用凭证
    credential_manager = await _get_credential_manager()
    cred_result = await credential_manager.get_valid_credential(mode="antigravity")
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
        # 使用 POST 请求获取模型列表
        antigravity_url = await get_antigravity_api_url()

        response = await post_async(
            url=f"{antigravity_url}/v1internal:fetchAvailableModels",
            json={},  # 空的请求体
            headers=headers
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
            "success": True/False,
            "models": {
                "model_name": {
                    "remaining": 0.95,
                    "resetTime": "12-20 10:30",
                    "resetTimeRaw": "2025-12-20T02:30:00Z"
                }
            },
            "error": "错误信息" (仅在失败时)
        }
    """

    headers = build_antigravity_headers(access_token)

    try:
        antigravity_url = await get_antigravity_api_url()

        response = await post_async(
            url=f"{antigravity_url}/v1internal:fetchAvailableModels",
            json={},
            headers=headers,
            timeout=30.0
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