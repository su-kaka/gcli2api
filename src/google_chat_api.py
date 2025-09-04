"""
Google API Client - Handles all communication with Google's Gemini API.
This module is used by both OpenAI compatibility layer and native Gemini endpoints.
"""
import asyncio
import gc
import json

from fastapi import Response
from fastapi.responses import StreamingResponse

from config import (
    get_code_assist_endpoint,
    DEFAULT_SAFETY_SETTINGS,
    get_base_model_name,
    get_thinking_budget,
    should_include_thoughts,
    is_search_model,
    get_auto_ban_enabled,
    get_auto_ban_error_codes,
    get_retry_429_max_retries,
    get_retry_429_enabled,
    get_retry_429_interval
)
from .httpx_client import http_client, create_streaming_client_with_kwargs
from log import log
from .credential_manager import CredentialManager
from .usage_stats import record_successful_call
from .utils import get_user_agent

def _create_error_response(message: str, status_code: int = 500) -> Response:
    """Create standardized error response."""
    return Response(
        content=json.dumps({
            "error": {
                "message": message,
                "type": "api_error",
                "code": status_code
            }
        }),
        status_code=status_code,
        media_type="application/json"
    )

async def _handle_api_error(credential_manager: CredentialManager, status_code: int, response_content: str = ""):
    """Handle API errors by rotating credentials when needed. Error recording should be done before calling this function."""
    if status_code == 429 and credential_manager:
        if response_content:
            log.error(f"Google API returned status 429 - quota exhausted. Response details: {response_content[:500]}")
        else:
            log.error("Google API returned status 429 - quota exhausted, switching credentials")
        await credential_manager.rotate_to_next_credential()
    
    # 处理自动封禁的错误码
    elif get_auto_ban_enabled() and status_code in get_auto_ban_error_codes() and credential_manager:
        if response_content:
            log.error(f"Google API returned status {status_code} - auto ban triggered. Response details: {response_content[:500]}")
        else:
            log.warning(f"Google API returned status {status_code} - auto ban triggered, rotating credentials")
        await credential_manager.rotate_to_next_credential()

async def _prepare_request_headers_and_payload(payload: dict, creds, credential_manager: CredentialManager):
    """Prepare request headers and final payload."""
    headers = {
        "Authorization": f"Bearer {creds.access_token}",
        "Content-Type": "application/json",
        "User-Agent": get_user_agent(),
    }

    # 获取项目ID (使用线程安全的方法)
    try:
        proj_id = credential_manager.get_user_project_id(creds)
    except Exception as e:
        raise Exception(f"Failed to get user project ID: {str(e)}")
    
    # 上线用户
    try:
        await credential_manager.onboard_user(creds, proj_id)
    except Exception as e:
        raise Exception(f"Failed to onboard user: {str(e)}")

    final_payload = {
        "model": payload.get("model"),
        "project": proj_id,
        "request": payload.get("request", {})
    }
    
    return headers, final_payload

async def send_gemini_request(payload: dict, is_streaming: bool = False, creds = None, credential_manager: CredentialManager = None) -> Response:
    """
    Send a request to Google's Gemini API.
    
    Args:
        payload: The request payload in Gemini format
        is_streaming: Whether this is a streaming request
        creds: Credentials object (legacy compatibility)
        credential_manager: CredentialManager instance for high-performance operations
        
    Returns:
        FastAPI Response object
    """
    # 获取429重试配置
    max_retries = get_retry_429_max_retries()
    retry_429_enabled = get_retry_429_enabled()
    retry_interval = get_retry_429_interval()
    
    # 确定API端点
    action = "streamGenerateContent" if is_streaming else "generateContent"
    target_url = f"{get_code_assist_endpoint()}/v1internal:{action}"
    if is_streaming:
        target_url += "?alt=sse"

    try:
        headers, final_payload = await _prepare_request_headers_and_payload(payload, creds, credential_manager)
    except Exception as e:
        return _create_error_response(str(e), 500)

    # 预序列化payload，避免重试时重复序列化
    final_post_data = json.dumps(final_payload)

    for attempt in range(max_retries + 1):
        try:
            if is_streaming:
                # 流式请求处理 - 使用httpx_client模块的统一配置
                client = create_streaming_client_with_kwargs()
                
                try:
                    # 使用stream方法但不在async with块中消费数据
                    stream_ctx = client.stream("POST", target_url, content=final_post_data, headers=headers)
                    resp = await stream_ctx.__aenter__()
                    
                    if resp.status_code == 429:
                        # 记录429错误
                        current_file = credential_manager.get_current_file_path() if credential_manager else None
                        if current_file and credential_manager:
                            response_content = ""
                            try:
                                content_bytes = await resp.aread()
                                if isinstance(content_bytes, bytes):
                                    response_content = content_bytes.decode('utf-8', errors='ignore')
                            except Exception:
                                pass
                            await credential_manager.record_error(current_file, 429, response_content)
                        
                        # 清理资源
                        try:
                            await stream_ctx.__aexit__(None, None, None)
                        except:
                            pass
                        await client.aclose()
                        
                        # 如果重试可用且未达到最大次数，进行重试
                        if retry_429_enabled and attempt < max_retries:
                            log.warning(f"[RETRY] 429 error encountered, retrying ({attempt + 1}/{max_retries})")
                            if credential_manager:
                                # 429错误时强制轮换凭证，不增加调用计数
                                await credential_manager._force_rotate_credential()
                                # 重新获取凭证和headers（凭证可能已轮换），复用原始payload
                                new_creds, _ = await credential_manager.get_credentials_and_project()
                                new_headers, updated_payload = await _prepare_request_headers_and_payload(payload, new_creds, credential_manager)
                                headers = new_headers
                                final_post_data = json.dumps(updated_payload)
                            await asyncio.sleep(retry_interval)
                            continue  # 跳出内层处理，继续外层循环重试
                        else:
                            # 返回429错误流
                            async def error_stream():
                                error_response = {
                                    "error": {
                                        "message": "429 rate limit exceeded, max retries reached",
                                        "type": "api_error",
                                        "code": 429
                                    }
                                }
                                yield f"data: {json.dumps(error_response)}\n\n"
                            return StreamingResponse(error_stream(), media_type="text/event-stream", status_code=429)
                    else:
                        # 成功响应，传递所有资源给流式处理函数管理
                        return _handle_streaming_response_managed(resp, stream_ctx, client, credential_manager, payload.get("model", ""))
                        
                except Exception as e:
                    # 清理资源
                    try:
                        await client.aclose()
                    except:
                        pass
                    raise e

            else:
                # 非流式请求处理 - 使用httpx_client模块
                async with http_client.get_client(timeout=None) as client:
                    resp = await client.post(
                        target_url, content=final_post_data, headers=headers
                    )
                    
                    if resp.status_code == 429:
                        # 记录429错误
                        current_file = credential_manager.get_current_file_path() if credential_manager else None
                        if current_file and credential_manager:
                            response_content = ""
                            try:
                                if hasattr(resp, 'content'):
                                    content = resp.content
                                    if isinstance(content, bytes):
                                        response_content = content.decode('utf-8', errors='ignore')
                                else:
                                    content_bytes = await resp.aread()
                                    if isinstance(content_bytes, bytes):
                                        response_content = content_bytes.decode('utf-8', errors='ignore')
                            except Exception:
                                pass
                            await credential_manager.record_error(current_file, 429, response_content)
                        
                        # 如果重试可用且未达到最大次数，继续重试
                        if retry_429_enabled and attempt < max_retries:
                            log.warning(f"[RETRY] 429 error encountered, retrying ({attempt + 1}/{max_retries})")
                            if credential_manager:
                                # 429错误时强制轮换凭证，不增加调用计数
                                await credential_manager._force_rotate_credential()
                                # 重新获取凭证和headers（凭证可能已轮换），复用原始payload
                                new_creds, _ = await credential_manager.get_credentials_and_project()
                                new_headers, updated_payload = await _prepare_request_headers_and_payload(payload, new_creds, credential_manager)
                                headers = new_headers
                                final_post_data = json.dumps(updated_payload)
                            await asyncio.sleep(retry_interval)
                            continue
                        else:
                            log.error(f"[RETRY] Max retries exceeded for 429 error")
                            return _create_error_response("429 rate limit exceeded, max retries reached", 429)
                    else:
                        # 非429错误或成功响应，正常处理
                        return await _handle_non_streaming_response(resp, credential_manager, payload.get("model", ""))
                    
        except Exception as e:
            if attempt < max_retries:
                log.warning(f"[RETRY] Request failed with exception, retrying ({attempt + 1}/{max_retries}): {str(e)}")
                await asyncio.sleep(retry_interval)
                continue
            else:
                log.error(f"Request to Google API failed: {str(e)}")
                return _create_error_response(f"Request failed: {str(e)}")
    
    # 如果循环结束仍未成功，返回错误
    return _create_error_response("Max retries exceeded", 429)


def _handle_streaming_response_managed(resp, stream_ctx, client, credential_manager: CredentialManager = None, model_name: str = "") -> StreamingResponse:
    """Handle streaming response with complete resource lifecycle management."""
    
    # 检查HTTP错误
    if resp.status_code != 200:
        # 立即清理资源并返回错误
        async def cleanup_and_error():
            try:
                await stream_ctx.__aexit__(None, None, None)
            except:
                pass
            try:
                await client.aclose()
            except:
                pass
            
            # 记录错误
            current_file = credential_manager.get_current_file_path() if credential_manager else None
            log.debug(f"[STREAMING] Error handling: status_code={resp.status_code}, current_file={current_file}")
            
            # 获取响应内容用于详细错误显示
            response_content = ""
            try:
                content_bytes = await resp.aread()
                if isinstance(content_bytes, bytes):
                    response_content = content_bytes.decode('utf-8', errors='ignore')
            except Exception as e:
                log.debug(f"[STREAMING] Failed to read response content for error analysis: {e}")
                response_content = ""
            
            # 显示详细错误信息
            if resp.status_code == 429:
                if response_content:
                    log.error(f"Google API returned status 429 (STREAMING). Response details: {response_content[:500]}")
                else:
                    log.error(f"Google API returned status 429 (STREAMING)")
            else:
                if response_content:
                    log.error(f"Google API returned status {resp.status_code} (STREAMING). Response details: {response_content[:500]}")
                else:
                    log.error(f"Google API returned status {resp.status_code} (STREAMING)")
            
            if credential_manager:
                if current_file:
                    log.debug(f"[STREAMING] Calling record_error for file {current_file} with status_code {resp.status_code}")
                    log.debug(f"[STREAMING] Response content snippet: {response_content[:200]}...")
                    await credential_manager.record_error(current_file, resp.status_code, response_content)
                else:
                    log.warning(f"[STREAMING] No current file path available for recording error {resp.status_code}")
            
            await _handle_api_error(credential_manager, resp.status_code, response_content)
            
            error_response = {
                "error": {
                    "message": f"API error: {resp.status_code}",
                    "type": "api_error",
                    "code": resp.status_code
                }
            }
            yield f'data: {json.dumps(error_response)}\n\n'.encode('utf-8')
        
        return StreamingResponse(
            cleanup_and_error(),
            media_type="text/event-stream",
            status_code=resp.status_code
        )
    
    # 正常流式响应处理，确保资源在流结束时被清理
    async def managed_stream_generator():
        success_recorded = False
        managed_stream_generator._chunk_count = 0  # 初始化chunk计数器
        try:
            async for chunk in resp.aiter_lines():
                if not chunk or not chunk.startswith('data: '):
                    continue
                    
                # 记录第一次成功响应
                if not success_recorded:
                    current_file = credential_manager.get_current_file_path() if credential_manager else None
                    if current_file and credential_manager:
                        await credential_manager.record_success(current_file, "chat_content")
                        # 记录到使用统计
                        try:
                            await record_successful_call(current_file, model_name)
                        except Exception as e:
                            log.debug(f"Failed to record usage statistics: {e}")
                    success_recorded = True
                
                payload = chunk[len('data: '):]
                try:
                    obj = json.loads(payload)
                    if "response" in obj:
                        data = obj["response"]
                        yield f"data: {json.dumps(data, separators=(',',':'))}\n\n".encode()
                        await asyncio.sleep(0)  # 让其他协程有机会运行
                        
                        # 定期释放内存（每100个chunk）
                        if hasattr(managed_stream_generator, '_chunk_count'):
                            managed_stream_generator._chunk_count += 1
                            if managed_stream_generator._chunk_count % 100 == 0:
                                gc.collect()
                    else:
                        yield f"data: {json.dumps(obj, separators=(',',':'))}\n\n".encode()
                except json.JSONDecodeError:
                    continue
                    
        except Exception as e:
            log.error(f"Streaming error: {e}")
            err = {"error": {"message": str(e), "type": "api_error", "code": 500}}
            yield f"data: {json.dumps(err)}\n\n".encode()
        finally:
            # 确保清理所有资源
            try:
                await stream_ctx.__aexit__(None, None, None)
            except Exception as e:
                log.debug(f"Error closing stream context: {e}")
            try:
                await client.aclose()
            except Exception as e:
                log.debug(f"Error closing client: {e}")

    return StreamingResponse(
        managed_stream_generator(),
        media_type="text/event-stream"
    )

async def _handle_non_streaming_response(resp, credential_manager: CredentialManager = None, model_name: str = "") -> Response:
    """Handle non-streaming response from Google API."""
    if resp.status_code == 200:
        try:
            # 记录成功响应
            current_file = credential_manager.get_current_file_path() if credential_manager else None
            if current_file and credential_manager:
                await credential_manager.record_success(current_file, "chat_content")
                # 记录到使用统计
                try:
                    await record_successful_call(current_file, model_name)
                except Exception as e:
                    log.debug(f"Failed to record usage statistics: {e}")
            
            raw = await resp.aread()
            google_api_response = raw.decode('utf-8')
            if google_api_response.startswith('data: '):
                google_api_response = google_api_response[len('data: '):]
            google_api_response = json.loads(google_api_response)
            log.debug(f"Google API原始响应: {json.dumps(google_api_response, ensure_ascii=False)[:500]}...")
            standard_gemini_response = google_api_response.get("response")
            log.debug(f"提取的response字段: {json.dumps(standard_gemini_response, ensure_ascii=False)[:500]}...")
            return Response(
                content=json.dumps(standard_gemini_response),
                status_code=200,
                media_type="application/json; charset=utf-8"
            )
        except Exception as e:
            log.error(f"Failed to parse Google API response: {str(e)}")
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                media_type=resp.headers.get("Content-Type")
            )
    else:
        # 记录错误并检查是否是 429，立即切换凭据
        current_file = credential_manager.get_current_file_path() if credential_manager else None
        log.debug(f"[NON-STREAMING] Error handling: status_code={resp.status_code}, current_file={current_file}")
        
        # 获取响应内容用于详细错误显示
        response_content = ""
        try:
            if hasattr(resp, 'content'):
                content = resp.content
                if isinstance(content, bytes):
                    response_content = content.decode('utf-8', errors='ignore')
            else:
                content_bytes = await resp.aread()
                if isinstance(content_bytes, bytes):
                    response_content = content_bytes.decode('utf-8', errors='ignore')
        except Exception as e:
            log.debug(f"[NON-STREAMING] Failed to read response content for error analysis: {e}")
            response_content = ""
        
        # 显示详细错误信息
        if resp.status_code == 429:
            if response_content:
                log.error(f"Google API returned status 429 (NON-STREAMING). Response details: {response_content[:500]}")
            else:
                log.error(f"Google API returned status 429 (NON-STREAMING)")
        else:
            if response_content:
                log.error(f"Google API returned status {resp.status_code} (NON-STREAMING). Response details: {response_content[:500]}")
            else:
                log.error(f"Google API returned status {resp.status_code} (NON-STREAMING)")
        
        if credential_manager:
            if current_file:
                log.debug(f"[NON-STREAMING] Calling record_error for file {current_file} with status_code {resp.status_code}")
                log.debug(f"[NON-STREAMING] Response content snippet: {response_content[:200]}...")
                await credential_manager.record_error(current_file, resp.status_code, response_content)
            else:
                log.warning(f"[NON-STREAMING] No current file path available for recording error {resp.status_code}")
        
        await _handle_api_error(credential_manager, resp.status_code, response_content)
        
        return _create_error_response(f"API error: {resp.status_code}", resp.status_code)

def build_gemini_payload_from_openai(openai_payload: dict) -> dict:
    """
    Build a Gemini API payload from an OpenAI-transformed request.
    """
    model = openai_payload.get("model")
    safety_settings = openai_payload.get("safetySettings", DEFAULT_SAFETY_SETTINGS)
    
    # 构建请求数据，直接使用扁平化结构
    request_data = {
        "contents": openai_payload.get("contents"),
        "safetySettings": safety_settings,
        "generationConfig": openai_payload.get("generationConfig", {}),
    }
    
    # 添加系统指令（如果存在）
    system_instruction = openai_payload.get("system_instruction")
    if system_instruction:
        if isinstance(system_instruction, str):
            request_data["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        else:
            request_data["systemInstruction"] = system_instruction
    
    # 添加其他可选字段
    optional_fields = ["cachedContent", "tools", "toolConfig"]
    for field in optional_fields:
        value = openai_payload.get(field)
        if value is not None:
            request_data[field] = value
    
    # 移除None值
    request_data = {k: v for k, v in request_data.items() if v is not None}
    
    return {
        "model": model,
        "request": request_data
    }


def build_gemini_payload_from_native(native_request: dict, model_from_path: str) -> dict:
    """
    Build a Gemini API payload from a native Gemini request.
    """
    native_request["safetySettings"] = DEFAULT_SAFETY_SETTINGS
    
    if "generationConfig" not in native_request:
        native_request["generationConfig"] = {}
    
    if "thinkingConfig" not in native_request["generationConfig"]:
        native_request["generationConfig"]["thinkingConfig"] = {}
    
    # 配置thinking
    thinking_budget = get_thinking_budget(model_from_path)
    include_thoughts = should_include_thoughts(model_from_path)
    
    native_request["generationConfig"]["thinkingConfig"]["includeThoughts"] = include_thoughts
    native_request["generationConfig"]["thinkingConfig"]["thinkingBudget"] = thinking_budget
    
    # Add Google Search grounding for search models
    if is_search_model(model_from_path):
        if "tools" not in native_request:
            native_request["tools"] = []
        # Add googleSearch tool if not already present
        if not any(tool.get("googleSearch") for tool in native_request["tools"]):
            native_request["tools"].append({"googleSearch": {}})
    
    return {
        "model": get_base_model_name(model_from_path),
        "request": native_request
    }