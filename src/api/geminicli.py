"""
GeminiCli API Client - Handles all communication with GeminiCli API.
This module is used by both OpenAI compatibility layer and native Gemini endpoints.
GeminiCli API 客户端 - 处理与 GeminiCli API 的所有通信
"""

import asyncio
import gc
import json
from datetime import datetime, timezone

from fastapi import Response
from fastapi.responses import StreamingResponse

from config import (
    get_code_assist_endpoint,
    get_return_thoughts_to_frontend,
)
from src.utils import (
    get_base_model_name,
    get_model_group,
)
from log import log

from src.credential_manager import CredentialManager
from src.httpx_client import create_streaming_client_with_kwargs, http_client
from src.utils import get_user_agent, parse_quota_reset_timestamp
from src.converter.gemini_fix import (
    build_gemini_request_payload,
    parse_google_api_response,
    parse_streaming_chunk,
)

# 导入共同的基础功能
from src.api.base_api_client import (
    check_should_auto_ban,
    handle_auto_ban,
    handle_error_with_retry,
    get_retry_config,
    record_api_call_success,
    record_api_call_error,
    parse_and_log_cooldown,
)


# ==================== 错误响应 ====================

def create_error_response(message: str, status_code: int = 500) -> Response:
    """
    创建标准化错误响应
    
    Args:
        message: 错误消息
        status_code: HTTP状态码
        
    Returns:
        FastAPI Response对象
    """
    return Response(
        content=json.dumps(
            {"error": {"message": message, "type": "api_error", "code": status_code}}
        ),
        status_code=status_code,
        media_type="application/json",
    )


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


# ==================== 主请求函数 ====================

async def send_gemini_request(
    payload: dict, is_streaming: bool = False, credential_manager: CredentialManager = None
) -> Response:
    """
    发送请求到 Google's Gemini API
    
    使用统一的重试和错误处理逻辑。

    Args:
        payload: Gemini格式的请求payload
        is_streaming: 是否是流式请求
        credential_manager: CredentialManager实例

    Returns:
        FastAPI Response对象
    """
    # 获取429重试配置
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_429_enabled = retry_config["retry_enabled"]
    retry_interval = retry_config["retry_interval"]

    # 动态确定API端点和payload格式
    model_name = payload.get("model", "")
    base_model_name = get_base_model_name(model_name)
    action = "streamGenerateContent" if is_streaming else "generateContent"
    target_url = f"{await get_code_assist_endpoint()}/v1internal:{action}"
    if is_streaming:
        target_url += "?alt=sse"

    # 确保有credential_manager
    if not credential_manager:
        return create_error_response("Credential manager not provided", 500)

    # 获取模型组（用于分组 CD）
    model_group = get_model_group(model_name)

    for attempt in range(max_retries + 1):
        # 每次请求都获取新的凭证（传递模型组）
        try:
            credential_result = await credential_manager.get_valid_credential(
                mode="geminicli", model_key=model_group
            )
            if not credential_result:
                return create_error_response("No valid credentials available", 500)

            current_file, credential_data = credential_result
            headers, final_payload, target_url = await prepare_request_headers_and_payload(
                payload, credential_data, target_url
            )
            # 预序列化payload
            final_post_data = json.dumps(final_payload)
        except Exception as e:
            return create_error_response(str(e), 500)
        try:
            if is_streaming:
                # 流式请求处理 - 使用httpx_client模块的统一配置
                client = None
                stream_ctx = None
                resp = None

                try:
                    client = await create_streaming_client_with_kwargs()

                    # 使用stream方法但不在async with块中消费数据
                    stream_ctx = client.stream(
                        "POST", target_url, content=final_post_data, headers=headers
                    )
                    resp = await stream_ctx.__aenter__()

                    if resp.status_code != 200:
                        # 处理其他非200状态码的错误
                        response_content = ""
                        cooldown_until = None
                        try:
                            content_bytes = await resp.aread()
                            if isinstance(content_bytes, bytes):
                                response_content = content_bytes.decode("utf-8", errors="ignore")
                                # 如果是429错误，尝试解析冷却时间
                                if resp.status_code == 429:
                                    cooldown_until = await parse_and_log_cooldown(
                                        response_content, mode="geminicli"
                                    )
                        except Exception as e:
                            log.debug(f"[STREAMING] Failed to read error response content: {e}")

                        # 显示详细的错误信息
                        if response_content:
                            log.error(
                                f"Google API returned status {resp.status_code} (STREAMING). Response details: {response_content[:500]}"
                            )
                        else:
                            log.error(
                                f"Google API returned status {resp.status_code} (STREAMING) - no response details available"
                            )

                        # 记录API调用错误
                        if credential_manager and current_file:
                            await record_api_call_error(
                                credential_manager,
                                current_file,
                                resp.status_code,
                                cooldown_until,
                                mode="geminicli",
                                model_key=model_group
                            )

                        # 清理资源 - 确保按正确顺序清理
                        try:
                            if stream_ctx:
                                await stream_ctx.__aexit__(None, None, None)
                        except Exception as cleanup_err:
                            log.debug(f"Error cleaning up stream_ctx: {cleanup_err}")
                        finally:
                            try:
                                if client:
                                    await client.aclose()
                            except Exception as cleanup_err:
                                log.debug(f"Error closing client: {cleanup_err}")

                        # 使用统一的错误处理和重试逻辑
                        should_retry = await handle_error_with_retry(
                            credential_manager,
                            resp.status_code,
                            current_file,
                            retry_429_enabled,
                            attempt,
                            max_retries,
                            retry_interval,
                            mode="geminicli"
                        )

                        if should_retry:
                            # 继续重试（会在下次循环中自动获取新凭证）
                            continue

                        # 不需要重试，返回错误流
                        error_msg = f"API error: {resp.status_code}"
                        if await check_should_auto_ban(resp.status_code):
                            error_msg += " (credential auto-banned)"

                        async def error_stream():
                            error_response = {
                                "error": {
                                    "message": error_msg,
                                    "type": "api_error",
                                    "code": resp.status_code,
                                }
                            }
                            yield f"data: {json.dumps(error_response)}\n\n"

                        return StreamingResponse(
                            error_stream(),
                            media_type="text/event-stream",
                            status_code=resp.status_code,
                        )
                    else:
                        # 成功响应，传递所有资源给流式处理函数管理
                        return handle_streaming_response(
                            resp,
                            stream_ctx,
                            client,
                            credential_manager,
                            current_file,
                            model_group,
                        )

                except Exception as e:
                    # 清理资源 - 确保按正确顺序清理
                    try:
                        if stream_ctx:
                            await stream_ctx.__aexit__(None, None, None)
                    except Exception as cleanup_err:
                        log.debug(f"Error cleaning up stream_ctx in exception handler: {cleanup_err}")
                    finally:
                        try:
                            if client:
                                await client.aclose()
                        except Exception as cleanup_err:
                            log.debug(f"Error closing client in exception handler: {cleanup_err}")
                    raise e

            else:
                # 非流式请求处理 - 使用httpx_client模块
                async with http_client.get_client(timeout=None) as client:
                    resp = await client.post(target_url, content=final_post_data, headers=headers)

                    # === 修改：统一处理所有非200状态码，沿用429行为 ===
                    if resp.status_code == 200:
                        return await handle_non_streaming_response(
                            resp, credential_manager, current_file, model_group
                        )

                    # 记录错误
                    status = resp.status_code
                    cooldown_until = None

                    # 如果是429错误，尝试获取冷却时间
                    if status == 429:
                        try:
                            content_bytes = resp.content if hasattr(resp, "content") else await resp.aread()
                            if isinstance(content_bytes, bytes):
                                response_content = content_bytes.decode("utf-8", errors="ignore")
                                error_data = json.loads(response_content)
                                cooldown_until = parse_quota_reset_timestamp(error_data)
                                if cooldown_until:
                                    log.info(f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}")
                        except Exception as parse_err:
                            log.debug(f"[NON-STREAMING] Failed to parse cooldown time: {parse_err}")

                    if credential_manager and current_file:
                        # 保留 429 的统计码不变
                        await record_api_call_error(
                            credential_manager,
                            current_file,
                            429 if status == 429 else status,
                            cooldown_until,
                            mode="geminicli",
                            model_key=model_group
                        )

                    # 使用统一的错误处理和重试逻辑
                    should_retry = await handle_error_with_retry(
                        credential_manager,
                        status,
                        current_file,
                        retry_429_enabled,
                        attempt,
                        max_retries,
                        retry_interval,
                        mode="geminicli"
                    )

                    if should_retry:
                        # 继续重试（会在下次循环中自动获取新凭证）
                        continue

                    # 不需要重试，返回错误
                    error_msg = f"{status} error, max retries reached"
                    if await check_should_auto_ban(status):
                        error_msg = f"{status} error (credential auto-banned), max retries reached"
                        log.error(f"[AUTO_BAN] {error_msg}")
                    elif status == 429:
                        error_msg = "429 rate limit exceeded, max retries reached"
                        log.error("[RETRY] Max retries exceeded for 429 error")
                    else:
                        log.error(f"[RETRY] Max retries exceeded for error status {status}")

                    return create_error_response(error_msg, status)

        except Exception as e:
            if attempt < max_retries:
                log.warning(
                    f"[RETRY] Request failed with exception, retrying ({attempt + 1}/{max_retries}): {str(e)}"
                )
                await asyncio.sleep(retry_interval)
                continue
            else:
                log.error(f"Request to Google API failed: {str(e)}")
                return create_error_response(f"Request failed: {str(e)}")

    # 如果循环结束仍未成功，返回错误
    return create_error_response("Max retries exceeded", 429)


# ==================== 流式响应处理 ====================

def handle_streaming_response(
    resp,
    stream_ctx,
    client,
    credential_manager: CredentialManager = None,
    credential_name: str = None,
    model_key: str = None,
) -> StreamingResponse:
    """
    处理 Gemini 流式响应，包装为可管理的生成器
    
    Args:
        resp: HTTP响应对象
        stream_ctx: 流上下文管理器
        client: HTTP客户端
        credential_manager: 凭证管理器
        credential_name: 凭证名称
        model_key: 模型键（用于模型级CD）
        
    Returns:
        StreamingResponse对象
    """
    # 检查HTTP错误
    if resp.status_code != 200:
        # 立即清理资源并返回错误
        async def cleanup_and_error():
            # 清理资源 - 按正确顺序：先关闭stream，再关闭client
            try:
                if stream_ctx:
                    await stream_ctx.__aexit__(None, None, None)
            except Exception as cleanup_err:
                log.debug(f"Error cleaning up stream_ctx: {cleanup_err}")
            finally:
                try:
                    if client:
                        await client.aclose()
                except Exception as cleanup_err:
                    log.debug(f"Error closing client: {cleanup_err}")

            # 获取响应内容用于详细错误显示
            response_content = ""
            cooldown_until = None
            try:
                content_bytes = await resp.aread()
                if isinstance(content_bytes, bytes):
                    response_content = content_bytes.decode("utf-8", errors="ignore")
                    # 如果是429错误，尝试解析冷却时间
                    if resp.status_code == 429:
                        try:
                            error_data = json.loads(response_content)
                            cooldown_until = parse_quota_reset_timestamp(error_data)
                            if cooldown_until:
                                log.info(f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}")
                        except Exception as parse_err:
                            log.debug(f"[STREAMING] Failed to parse cooldown time for error analysis: {parse_err}")
            except Exception as e:
                log.debug(f"[STREAMING] Failed to read response content for error analysis: {e}")
                response_content = ""

            # 显示详细错误信息
            if resp.status_code == 429:
                if response_content:
                    log.error(
                        f"Google API returned status 429 (STREAMING). Response details: {response_content[:500]}"
                    )
                else:
                    log.error("Google API returned status 429 (STREAMING)")
            else:
                if response_content:
                    log.error(
                        f"Google API returned status {resp.status_code} (STREAMING). Response details: {response_content[:500]}"
                    )
                else:
                    log.error(f"Google API returned status {resp.status_code} (STREAMING)")

            # 记录API调用错误
            if credential_manager and credential_name:
                await record_api_call_error(
                    credential_manager,
                    credential_name,
                    resp.status_code,
                    cooldown_until,
                    mode="geminicli",
                    model_key=model_key
                )

            # 处理429和自动封禁
            if resp.status_code == 429:
                log.warning(f"429 error encountered for credential: {credential_name}")
            elif await check_should_auto_ban(resp.status_code):
                await handle_auto_ban(credential_manager, resp.status_code, credential_name, mode="geminicli")

            error_response = {
                "error": {
                    "message": f"API error: {resp.status_code}",
                    "type": "api_error",
                    "code": resp.status_code,
                }
            }
            yield f"data: {json.dumps(error_response)}\n\n".encode("utf-8")

        return StreamingResponse(
            cleanup_and_error(), media_type="text/event-stream", status_code=resp.status_code
        )

    # 正常流式响应处理，确保资源在流结束时被清理
    async def managed_stream_generator():
        success_recorded = False
        chunk_count = 0
        bytes_transferred = 0
        return_thoughts = await get_return_thoughts_to_frontend()
        try:
            async for chunk in resp.aiter_lines():
                # 使用统一的解析函数
                parsed_data = parse_streaming_chunk(chunk, return_thoughts)
                if parsed_data is None:
                    continue
                
                # 记录第一次成功响应
                if not success_recorded:
                    if credential_name and credential_manager:
                        await record_api_call_success(
                            credential_manager, credential_name, mode="geminicli", model_key=model_key
                        )
                    success_recorded = True

                chunk_data = f"data: {json.dumps(parsed_data, separators=(',', ':'))}\n\n".encode()
                yield chunk_data
                await asyncio.sleep(0)  # 让其他协程有机会运行

                # 基于传输字节数触发GC，而不是chunk数量
                # 每传输约10MB数据时触发一次GC
                chunk_count += 1
                bytes_transferred += len(chunk_data)
                if bytes_transferred > 10 * 1024 * 1024:  # 10MB
                    gc.collect()
                    bytes_transferred = 0
                    log.debug(f"Triggered GC after {chunk_count} chunks (~10MB transferred)")

        except Exception as e:
            log.error(f"Streaming error: {e}")
            err = {"error": {"message": str(e), "type": "api_error", "code": 500}}
            yield f"data: {json.dumps(err)}\n\n".encode()
        finally:
            # 确保清理所有资源 - 按正确顺序：先关闭stream，再关闭client
            try:
                if stream_ctx:
                    await stream_ctx.__aexit__(None, None, None)
            except Exception as e:
                log.debug(f"Error closing stream context: {e}")
            finally:
                try:
                    if client:
                        await client.aclose()
                except Exception as e:
                    log.debug(f"Error closing client: {e}")

    return StreamingResponse(managed_stream_generator(), media_type="text/event-stream")


# ==================== 非流式响应处理 ====================

async def handle_non_streaming_response(
    resp,
    credential_manager: CredentialManager = None,
    credential_name: str = None,
    model_key: str = None,
) -> Response:
    """
    处理 Gemini 非流式响应
    
    Args:
        resp: HTTP响应对象
        credential_manager: 凭证管理器
        credential_name: 凭证名称
        model_key: 模型键（用于模型级CD）
        
    Returns:
        FastAPI Response对象
    """
    if resp.status_code == 200:
        try:
            # 记录成功响应
            if credential_name and credential_manager:
                await record_api_call_success(
                    credential_manager, credential_name, mode="geminicli", model_key=model_key
                )

            raw = await resp.aread()
            return_thoughts = await get_return_thoughts_to_frontend()
            
            # 使用统一的解析函数
            standard_gemini_response = parse_google_api_response(raw, return_thoughts)
            
            log.debug(
                f"提取的response字段: {json.dumps(standard_gemini_response, ensure_ascii=False)[:500]}..."
            )
            return Response(
                content=json.dumps(standard_gemini_response),
                status_code=200,
                media_type="application/json; charset=utf-8",
            )
        except Exception as e:
            log.error(f"Failed to parse Google API response: {str(e)}")
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("Content-Type"),
            )
    else:
        # 获取响应内容用于详细错误显示
        response_content = ""
        cooldown_until = None
        try:
            if hasattr(resp, "content"):
                content = resp.content
                if isinstance(content, bytes):
                    response_content = content.decode("utf-8", errors="ignore")
            else:
                content_bytes = await resp.aread()
                if isinstance(content_bytes, bytes):
                    response_content = content_bytes.decode("utf-8", errors="ignore")

            # 如果是429错误，尝试解析冷却时间
            if resp.status_code == 429 and response_content:
                try:
                    error_data = json.loads(response_content)
                    cooldown_until = parse_quota_reset_timestamp(error_data)
                    if cooldown_until:
                        log.info(f"检测到quota冷却时间: {datetime.fromtimestamp(cooldown_until, timezone.utc).isoformat()}")
                except Exception as parse_err:
                    log.debug(f"[NON-STREAMING] Failed to parse cooldown time for error analysis: {parse_err}")
        except Exception as e:
            log.debug(f"[NON-STREAMING] Failed to read response content for error analysis: {e}")
            response_content = ""

        # 显示详细错误信息
        if resp.status_code == 429:
            if response_content:
                log.error(
                    f"Google API returned status 429 (NON-STREAMING). Response details: {response_content[:500]}"
                )
            else:
                log.error("Google API returned status 429 (NON-STREAMING)")
        else:
            if response_content:
                log.error(
                    f"Google API returned status {resp.status_code} (NON-STREAMING). Response details: {response_content[:500]}"
                )
            else:
                log.error(f"Google API returned status {resp.status_code} (NON-STREAMING)")

        # 记录API调用错误
        if credential_manager and credential_name:
            await record_api_call_error(
                credential_manager,
                credential_name,
                resp.status_code,
                cooldown_until,
                mode="geminicli",
                model_key=model_key
            )

        # 处理429和自动封禁
        if resp.status_code == 429:
            log.warning(f"429 error encountered for credential: {credential_name}")
        elif await check_should_auto_ban(resp.status_code):
            await handle_auto_ban(credential_manager, resp.status_code, credential_name, mode="geminicli")

        return create_error_response(f"API error: {resp.status_code}", resp.status_code)


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
    
    return build_gemini_request_payload(
        native_request,
        model_from_path,
        get_base_model_name,
        get_thinking_budget,
        should_include_thoughts,
        is_search_model,
        DEFAULT_SAFETY_SETTINGS
    )
