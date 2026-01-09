"""
GeminiCli API Client - Handles all communication with GeminiCli API.
This module is used by both OpenAI compatibility layer and native Gemini endpoints.
GeminiCli API 客户端 - 处理与 GeminiCli API 的所有通信
"""

import asyncio
import json
from typing import Tuple, Any, Dict, Optional

from fastapi import HTTPException
from config import get_code_assist_endpoint
from src.utils import get_model_group
from log import log

from src.credential_manager import CredentialManager
from src.httpx_client import create_streaming_client_with_kwargs, http_client
from src.utils import get_user_agent

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


# ==================== 请求准备 ====================

async def prepare_request_headers_and_payload(
    payload: dict, credential_data: dict, target_url: str
):
    """
    从凭证数据准备请求头和最终payload
    
    Args:
        payload: 原始请求payload
        credential_data: 凭证数据字典
        target_url: 目标URL
        
    Returns:
        元组: (headers, final_payload, target_url)
        
    Raises:
        Exception: 如果凭证中缺少必要字段
    """
    token = credential_data.get("token") or credential_data.get("access_token", "")
    if not token:
        raise Exception("凭证中没有找到有效的访问令牌（token或access_token字段）")

    source_request = payload.get("request", {})

    # 内部API使用Bearer Token和项目ID
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": get_user_agent(),
    }
    project_id = credential_data.get("project_id", "")
    if not project_id:
        raise Exception("项目ID不存在于凭证数据中")
    final_payload = {
        "model": payload.get("model"),
        "project": project_id,
        "request": source_request,
    }

    return headers, final_payload, target_url


# ==================== Anthropic 兼容层专用函数 ====================

from typing import Tuple, Any, Dict


def build_gemini_payload_from_native(native_request: dict, model_from_path: str) -> dict:
    """
    Build a Gemini API payload from a native Gemini request with full pass-through support.
    现在使用 gemini_fix.py 中的统一函数
    """
    from src.utils import (
        DEFAULT_SAFETY_SETTINGS,
        get_base_model_name,
        get_thinking_budget,
        is_search_model,
        should_include_thoughts,
    )
    from src.converter.gemini_fix import build_gemini_request_payload
    
    return build_gemini_request_payload(
        native_request,
        model_from_path,
        get_base_model_name,
        get_thinking_budget,
        should_include_thoughts,
        is_search_model,
        DEFAULT_SAFETY_SETTINGS
    )


def handle_geminicli_streaming_response(
    response,
    stream_ctx,
    client,
):
    """
    处理 GeminiCli 流式响应，返回原始行迭代器
    同时去掉 GeminiCLI 的 response 包装
    """
    import json

    async def filtered_lines():
        line_count = 0
        try:
            log.debug(f"[GEMINICLI-STREAM] Starting to iterate response lines")
            async for line in response.aiter_lines():
                # 处理 bytes 类型
                if isinstance(line, bytes):
                    if not line or not line.startswith(b"data: "):
                        continue
                    # 解码 bytes
                    line_str = line.decode('utf-8', errors='ignore')
                else:
                    if not line or not line.startswith("data: "):
                        continue
                    line_str = line

                line_count += 1
                log.debug(f"[GEMINICLI-STREAM] Received line #{line_count}: {line_str[:100] if line_str else '(empty)'}")

                try:
                    payload_str = line_str[6:]  # 去掉 "data: " 前缀
                    if payload_str.strip() == "[DONE]":
                        log.debug(f"[GEMINICLI-STREAM] Yielding [DONE] marker")
                        yield b"data: [DONE]\n\n"
                        continue

                    # 解析 JSON 并去掉包装
                    data = json.loads(payload_str)
                    data = unwrap_geminicli_response(data)

                    # 重新编码为 SSE 行（必须以 \n\n 结尾），返回 bytes
                    output_line = f"data: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n".encode()
                    log.debug(f"[GEMINICLI-STREAM] Yielding processed line")
                    yield output_line
                    await asyncio.sleep(0)  # 关键：让出执行权，立即推送数据
                except (json.JSONDecodeError, KeyError) as e:
                    # 解析失败，直接传递原始行（确保格式正确）
                    log.warning(f"[GEMINICLI-STREAM] Failed to parse line, passing through: {e}")
                    yield f"{line_str}\n\n".encode()
                    await asyncio.sleep(0)

            log.info(f"[GEMINICLI-STREAM] Finished iterating. Total lines: {line_count}")
        except Exception as e:
            log.error(f"[GEMINICLI-STREAM] Error during streaming: {e}")
            raise

    return (filtered_lines(), stream_ctx, client)


async def send_geminicli_request_stream(
    request_body: Dict[str, Any],
) -> Tuple[Any, str, Dict[str, Any]]:
    """
    发送 GeminiCli 流式请求（Anthropic 兼容层专用）
    
    Args:
        request_body: GeminiCli格式的请求体
        
    Returns:
        元组: (response_iterator, credential_name, credential_data)
        其中 response_iterator 是 (filtered_lines, stream_ctx, client) 元组
    """
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    model_name = request_body.get("model", "")
    model_group = get_model_group(model_name)

    # 获取凭证管理器
    credential_manager = await _get_credential_manager()

    for attempt in range(max_retries + 1):
        cred_result = await credential_manager.get_valid_credential(
            mode="geminicli", model_key=model_group
        )
        if not cred_result:
            raise HTTPException(status_code=503, detail="No valid geminicli credentials available")

        current_file, credential_data = cred_result
        
        try:
            headers, final_payload, target_url = await prepare_request_headers_and_payload(
                request_body, credential_data, 
                f"{await get_code_assist_endpoint()}/v1internal:streamGenerateContent?alt=sse"
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to prepare request: {e}")

        try:
            # 预序列化 payload 以避免额外的序列化开销
            final_post_data = json.dumps(final_payload)

            client = await create_streaming_client_with_kwargs()
            stream_ctx = client.stream("POST", target_url, content=final_post_data, headers=headers)
            response = await stream_ctx.__aenter__()

            if response.status_code == 200:
                log.info(f"[GEMINICLI-STREAM] Request successful with credential: {current_file}")
                # 记录API调用成功
                await record_api_call_success(
                    credential_manager, current_file, mode="geminicli", model_key=model_group
                )
                response_iterator = handle_geminicli_streaming_response(
                    response, stream_ctx, client
                )
                return response_iterator, current_file, credential_data

            # 处理错误
            error_body = await response.aread()
            error_text = error_body.decode('utf-8', errors='ignore')
            log.error(f"[GEMINICLI-STREAM] API error ({response.status_code}) with credential {current_file}: {error_text[:500]}")

            cooldown_until = None
            if response.status_code == 429:
                cooldown_until = await parse_and_log_cooldown(error_text, mode="geminicli")

            await record_api_call_error(
                credential_manager, current_file, response.status_code, 
                cooldown_until, mode="geminicli", model_key=model_group
            )

            try:
                await stream_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            await client.aclose()

            should_retry = await handle_error_with_retry(
                credential_manager, response.status_code, current_file,
                retry_config["retry_enabled"], attempt, max_retries, retry_interval, mode="geminicli"
            )

            if should_retry:
                log.info(f"[GEMINICLI-STREAM] Retrying request (attempt {attempt + 2}/{max_retries + 1})...")
                continue

            raise HTTPException(
                status_code=response.status_code if response.status_code < 600 else 502,
                detail=f"GeminiCli API error: {error_text[:200]}"
            )

        except HTTPException:
            # HTTPException 已经被正确处理，直接向上抛出，不进行重试
            raise
        except Exception as e:
            # 只对网络错误等非预期异常进行重试
            log.error(f"[GEMINICLI-STREAM] Unexpected request exception with credential {current_file}: {str(e)}")
            try:
                await client.aclose()
            except Exception:
                pass

            if attempt < max_retries:
                log.info(f"[GEMINICLI-STREAM] Retrying after unexpected exception (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            raise

    raise HTTPException(status_code=503, detail="All retry attempts failed for GeminiCli streaming request")


async def send_geminicli_request_no_stream(
    request_body: Dict[str, Any],
) -> Tuple[Dict[str, Any], str, Dict[str, Any]]:
    """
    发送 GeminiCli 非流式请求
    Args:
        request_body: GeminiCli格式的请求体
        
    Returns:
        元组: (response_data, credential_name, credential_data)
    """
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    model_name = request_body.get("model", "")
    model_group = get_model_group(model_name)

    # 获取凭证管理器
    credential_manager = await _get_credential_manager()

    for attempt in range(max_retries + 1):
        cred_result = await credential_manager.get_valid_credential(
            mode="geminicli", model_key=model_group
        )
        if not cred_result:
            raise HTTPException(status_code=503, detail="No valid geminicli credentials available")

        current_file, credential_data = cred_result
        
        try:
            headers, final_payload, target_url = await prepare_request_headers_and_payload(
                request_body, credential_data,
                f"{await get_code_assist_endpoint()}/v1internal:generateContent"
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to prepare request: {e}")

        try:
            async with http_client.get_client(timeout=300.0) as client:
                response = await client.post(target_url, json=final_payload, headers=headers)

                if response.status_code == 200:
                    await record_api_call_success(
                        credential_manager, current_file, mode="geminicli", model_key=model_group
                    )
                    response_data = response.json()
                    response_data = unwrap_geminicli_response(response_data)
                    return response_data, current_file, credential_data

                # 处理错误
                error_text = response.text
                log.error(f"[GEMINICLI] API error ({response.status_code}) with credential {current_file}: {error_text[:500]}")

                cooldown_until = None
                if response.status_code == 429:
                    cooldown_until = await parse_and_log_cooldown(error_text, mode="geminicli")

                await record_api_call_error(
                    credential_manager, current_file, response.status_code,
                    cooldown_until, mode="geminicli", model_key=model_group
                )

                should_retry = await handle_error_with_retry(
                    credential_manager, response.status_code, current_file,
                    retry_config["retry_enabled"], attempt, max_retries, retry_interval, mode="geminicli"
                )

                if should_retry:
                    log.info(f"[GEMINICLI] Retrying request (attempt {attempt + 2}/{max_retries + 1})...")
                    continue

                raise HTTPException(
                    status_code=response.status_code if response.status_code < 600 else 502,
                    detail=f"GeminiCli API error: {error_text[:200]}"
                )

        except HTTPException:
            # HTTPException 已经被正确处理，直接向上抛出，不进行重试
            raise
        except Exception as e:
            # 只对网络错误等非预期异常进行重试
            log.error(f"[GEMINICLI] Unexpected request exception with credential {current_file}: {str(e)}")
            if attempt < max_retries:
                log.info(f"[GEMINICLI] Retrying after unexpected exception (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            raise

    raise HTTPException(status_code=503, detail="All retry attempts failed for GeminiCli request")
