"""
Antigravity API Client - Handles communication with Google's Antigravity API
处理与 Google Antigravity API 的通信
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from config import (
    get_antigravity_api_url,
    get_return_thoughts_to_frontend,
    get_antigravity_stream2nostream,
)
from log import log

from src.credential_manager import CredentialManager
from src.httpx_client import create_streaming_client_with_kwargs, http_client
from src.models import Model, model_to_dict
from src.utils import ANTIGRAVITY_USER_AGENT
from src.converter.gemini_fix import (
    filter_thoughts_from_stream_chunk,
    collect_streaming_response
)

# 导入共同的基础功能
from src.api.base_api_client import (
    handle_error_with_retry,
    get_retry_config,
    record_api_call_success,
    record_api_call_error,
    parse_and_log_cooldown,
    unwrap_geminicli_response,
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

def build_antigravity_headers(access_token: str) -> Dict[str, str]:
    """
    构建 Antigravity API 请求头
    
    Args:
        access_token: 访问令牌
        
    Returns:
        请求头字典
    """
    return {
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip'
    }


# ==================== 流式响应处理 ====================

def handle_streaming_response(
    response,
    stream_ctx,
    client,
) -> Tuple[Any, Any, Any]:
    """
    处理 Antigravity 流式响应，包装为可管理的生成器
    
    Args:
        response: HTTP响应对象
        stream_ctx: 流上下文管理器
        client: HTTP客户端
        
    Returns:
        元组: (filtered_lines_generator, stream_ctx, client)
    """
    async def filter_stream_lines():
        """过滤流式响应行，移除思维链内容（如果配置要求）并去掉 response 包装"""
        return_thoughts = await get_return_thoughts_to_frontend()
        log.debug(f"[ANTIGRAVITY STREAM] Starting to filter stream lines, return_thoughts={return_thoughts}")
        line_count = 0

        try:
            async for line in response.aiter_lines():
                # 处理 bytes 类型
                if isinstance(line, bytes):
                    if not line or not line.startswith(b"data: "):
                        continue
                    line_count += 1
                    log.debug(f"[ANTIGRAVITY STREAM] Received line {line_count}: {line[:200] if line else b'empty'}")

                    raw = line[6:].strip()
                    if raw == b"[DONE]":
                        yield b"data: [DONE]\n\n"
                        continue

                    # 解码 bytes 后再解析 JSON
                    raw_str = raw.decode('utf-8', errors='ignore')
                else:
                    if not line or not line.startswith("data: "):
                        continue
                    line_count += 1
                    log.debug(f"[ANTIGRAVITY STREAM] Received line {line_count}: {line[:200] if line else 'empty'}")

                    raw = line[6:].strip()
                    if raw == "[DONE]":
                        yield b"data: [DONE]\n\n"
                        continue

                    raw_str = raw

                try:
                    log.debug(f"[ANTIGRAVITY STREAM] Parsing JSON: {raw_str[:200]}")
                    data = json.loads(raw_str)
                    log.debug(f"[ANTIGRAVITY STREAM] Parsed data keys: {data.keys() if isinstance(data, dict) else type(data)}")
                    # 去掉 Antigravity 的 response 包装
                    data = unwrap_geminicli_response(data)
                    log.debug(f"[ANTIGRAVITY STREAM] After unwrap, data keys: {data.keys() if isinstance(data, dict) else type(data)}")

                    # 如果需要过滤思维内容
                    if not return_thoughts:
                        filtered_data = filter_thoughts_from_stream_chunk(data)
                        # 如果过滤后为空，跳过这一行
                        if filtered_data is None:
                            log.debug(f"[ANTIGRAVITY STREAM] Filtered data is None, skipping")
                            continue
                        data = filtered_data

                    output_line = f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()
                    log.debug(f"[ANTIGRAVITY STREAM] Yielding filtered line")
                    yield output_line
                    await asyncio.sleep(0)  # 关键：让出执行权，立即推送数据
                except Exception as e:
                    # 解析失败，传递原始行
                    log.debug(f"[ANTIGRAVITY STREAM] JSON parse error: {e}, yielding raw line")
                    yield f"{line}\n\n".encode()
                    await asyncio.sleep(0)
        except Exception as e:
            log.error(f"[ANTIGRAVITY] Streaming error after {line_count} lines: {e}")
            raise

        log.info(f"[ANTIGRAVITY STREAM] Finished filtering, processed {line_count} lines total")
    
    filtered_lines = filter_stream_lines()
    return (filtered_lines, stream_ctx, client)


# ==================== 主请求函数 ====================

async def send_antigravity_request_stream(
    request_body: Dict[str, Any],
) -> Tuple[Any, str, Dict[str, Any]]:
    """
    发送 Antigravity 流式请求
    
    使用统一的重试和错误处理逻辑。
    
    Args:
        request_body: Antigravity格式的请求体
        
    Returns:
        元组: (response_iterator, credential_name, credential_data)
        其中 response_iterator 是 (filtered_lines, stream_ctx, client) 元组
        
    Raises:
        Exception: 如果所有重试都失败
    """
    retry_config = await get_retry_config()
    retry_enabled = retry_config["retry_enabled"]
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    # 获取凭证管理器
    credential_manager = await _get_credential_manager()

    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            mode="antigravity", model_key=model_name
        )
        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available")
            raise HTTPException(status_code=503, detail="No valid antigravity credentials available")

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
                # 预序列化 payload 以避免额外的序列化开销
                request_data = json.dumps(request_body)

                # 使用stream方法但不在async with块中消费数据
                stream_ctx = client.stream(
                    "POST",
                    f"{antigravity_url}/v1internal:streamGenerateContent?alt=sse",
                    content=request_data,
                    headers=headers,
                )
                response = await stream_ctx.__aenter__()

                # 检查响应状态
                if response.status_code == 200:
                    log.info(f"[ANTIGRAVITY] Streaming request successful with credential: {current_file}")
                    # 记录API调用成功
                    await record_api_call_success(
                        credential_manager, current_file, mode="antigravity", model_key=model_name
                    )
                    # 使用独立的响应处理函数
                    response_iterator = handle_streaming_response(
                        response,
                        stream_ctx,
                        client,
                    )
                    return response_iterator, current_file, credential_data

                # 处理错误
                error_body = await response.aread()
                error_text = error_body.decode('utf-8', errors='ignore')
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}) with credential {current_file}: {error_text[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    cooldown_until = await parse_and_log_cooldown(error_text, mode="antigravity")

                await record_api_call_error(
                    credential_manager,
                    current_file,
                    response.status_code,
                    cooldown_until,
                    mode="antigravity",
                    model_key=model_name
                )

                # 清理资源
                try:
                    await stream_ctx.__aexit__(None, None, None)
                except Exception:
                    pass
                await client.aclose()

                # 重试逻辑: 使用统一的错误处理
                should_retry = await handle_error_with_retry(
                    credential_manager,
                    response.status_code,
                    current_file,
                    retry_enabled,
                    attempt,
                    max_retries,
                    retry_interval,
                    mode="antigravity"
                )

                if should_retry:
                    log.info(f"[ANTIGRAVITY] Retrying request (attempt {attempt + 2}/{max_retries + 1})...")
                    continue

                raise HTTPException(
                    status_code=response.status_code if response.status_code < 600 else 502,
                    detail=f"Antigravity API error: {error_text[:200]}"
                )

            except Exception as stream_error:
                # 确保在异常情况下也清理资源
                try:
                    await client.aclose()
                except Exception:
                    pass
                raise stream_error

        except HTTPException:
            # HTTPException 已经被正确处理，直接向上抛出，不进行重试
            raise
        except Exception as e:
            # 只对网络错误等非预期异常进行重试
            log.error(f"[ANTIGRAVITY] Unexpected request exception with credential {current_file}: {str(e)}")
            if attempt < max_retries:
                log.info(f"[ANTIGRAVITY] Retrying after unexpected exception (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            raise

    raise HTTPException(status_code=503, detail="All antigravity retry attempts failed for streaming request")


async def send_antigravity_request_no_stream(
    request_body: Dict[str, Any],
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    发送 Antigravity 非流式请求
    
    使用统一的重试和错误处理逻辑。
    支持两种模式：
    1. 传统非流式请求（直接调用非流式API）
    2. 流式收集模式（调用流式API并收集为完整响应）
    
    Args:
        request_body: Antigravity格式的请求体
        
    Returns:
        元组: (response_data, credential_name, credential_data)
        
    Raises:
        Exception: 如果所有重试都失败
    """
    # 检查是否启用流式收集模式
    antigravity_stream2nostream = await get_antigravity_stream2nostream()
    
    if antigravity_stream2nostream:
        log.info("[ANTIGRAVITY] Using stream collection mode for non-stream request")
        return await _send_antigravity_request_no_stream_via_stream(request_body)
    else:
        log.info("[ANTIGRAVITY] Using traditional non-stream mode")
        return await _send_antigravity_request_no_stream_traditional(request_body)


async def _send_antigravity_request_no_stream_via_stream(
    request_body: Dict[str, Any],
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    通过流式API实现非流式请求（收集完整响应）
    
    Args:
        request_body: Antigravity格式的请求体
        
    Returns:
        元组: (response_data, credential_name, credential_data)
    """
    try:
        # 调用流式请求获取生成器
        stream_result = await send_antigravity_request_stream(request_body)
        
        if not stream_result:
            raise HTTPException(status_code=500, detail="Failed to get stream response")
        
        response_iterator, credential_name, credential_data = stream_result
        
        # 提取实际的生成器（response_iterator是一个元组）
        if isinstance(response_iterator, tuple):
            stream_generator = response_iterator[0]
        else:
            stream_generator = response_iterator
        
        # 收集流式响应
        log.info("[ANTIGRAVITY] Collecting streaming response...")
        collected_response = await collect_streaming_response(stream_generator)

        # collect_streaming_response 返回的格式是 {"response": {...}}
        # 需要去掉包装
        collected_response = unwrap_geminicli_response(collected_response)

        # 过滤思维链（如果需要）
        return_thoughts = await get_return_thoughts_to_frontend()
        if not return_thoughts:
            try:
                candidate = (collected_response.get("candidates", [{}])[0]) or {}
                parts = (candidate.get("content", {}) or {}).get("parts", []) or []
                filtered_parts = [part for part in parts if not (isinstance(part, dict) and part.get("thought") is True)]
                if filtered_parts != parts:
                    candidate["content"]["parts"] = filtered_parts
            except Exception as e:
                log.debug(f"[ANTIGRAVITY] Failed to filter thinking from collected response: {e}")

        log.info("[ANTIGRAVITY] Successfully collected complete response from stream")
        return collected_response, credential_name, credential_data
        
    except Exception as e:
        log.error(f"[ANTIGRAVITY] Stream collection failed: {e}")
        raise


async def _send_antigravity_request_no_stream_traditional(
    request_body: Dict[str, Any],
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    传统的非流式请求实现
    
    Args:
        request_body: Antigravity格式的请求体
        
    Returns:
        元组: (response_data, credential_name, credential_data)
    """
    retry_config = await get_retry_config()
    retry_enabled = retry_config["retry_enabled"]
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    # 提取模型名称用于模型级 CD
    model_name = request_body.get("model", "")

    # 获取凭证管理器
    credential_manager = await _get_credential_manager()

    for attempt in range(max_retries + 1):
        # 获取可用凭证（传递模型名称）
        cred_result = await credential_manager.get_valid_credential(
            mode="antigravity", model_key=model_name
        )
        if not cred_result:
            log.error("[ANTIGRAVITY] No valid credentials available")
            raise HTTPException(status_code=503, detail="No valid antigravity credentials available")

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
                    await record_api_call_success(
                        credential_manager, current_file, mode="antigravity", model_key=model_name
                    )
                    response_data = response.json()
                    response_data = unwrap_geminicli_response(response_data)

                    # 从源头过滤思维链
                    return_thoughts = await get_return_thoughts_to_frontend()
                    if not return_thoughts:
                        try:
                            candidate = (response_data.get("candidates", [{}])[0]) or {}
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
                log.error(f"[ANTIGRAVITY] API error ({response.status_code}) with credential {current_file}: {error_body[:500]}")

                # 记录错误（使用模型级 CD）
                cooldown_until = None
                if response.status_code == 429:
                    cooldown_until = await parse_and_log_cooldown(error_body, mode="antigravity")

                await record_api_call_error(
                    credential_manager,
                    current_file,
                    response.status_code,
                    cooldown_until,
                    mode="antigravity",
                    model_key=model_name
                )

                # 重试逻辑: 使用统一的错误处理
                should_retry = await handle_error_with_retry(
                    credential_manager,
                    response.status_code,
                    current_file,
                    retry_enabled,
                    attempt,
                    max_retries,
                    retry_interval,
                    mode="antigravity"
                )

                if should_retry:
                    log.info(f"[ANTIGRAVITY] Retrying request (attempt {attempt + 2}/{max_retries + 1})...")
                    continue

                raise HTTPException(
                    status_code=response.status_code if response.status_code < 600 else 502,
                    detail=f"Antigravity API error: {error_body[:200]}"
                )

        except HTTPException:
            # HTTPException 已经被正确处理，直接向上抛出，不进行重试
            raise
        except Exception as e:
            # 只对网络错误等非预期异常进行重试
            log.error(f"[ANTIGRAVITY] Unexpected request exception with credential {current_file}: {str(e)}")
            if attempt < max_retries:
                log.info(f"[ANTIGRAVITY] Retrying after unexpected exception (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            raise

    raise HTTPException(status_code=503, detail="All antigravity retry attempts failed for non-streaming request")


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