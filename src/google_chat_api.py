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
        await credential_manager.force_rotate_credential()
    
    # 处理自动封禁的错误码
    elif await get_auto_ban_enabled() and status_code in await get_auto_ban_error_codes() and credential_manager:
        if response_content:
            log.error(f"Google API returned status {status_code} - auto ban triggered. Response details: {response_content[:500]}")
        else:
            log.warning(f"Google API returned status {status_code} - auto ban triggered, rotating credentials")
        await credential_manager.force_rotate_credential()

async def _prepare_request_headers_and_payload(payload: dict, credential_data: dict):
    """Prepare request headers and final payload from credential data."""
    # 尝试获取token，支持多种字段名
    token = credential_data.get('token') or credential_data.get('access_token', '')
    
    if not token:
        raise Exception("凭证中没有找到有效的访问令牌（token或access_token字段）")
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": get_user_agent(),
    }

    # 直接使用凭证数据中的项目ID
    project_id = credential_data.get("project_id", "")
    if not project_id:
        raise Exception("项目ID不存在于凭证数据中")

    final_payload = {
        "model": payload.get("model"),
        "project": project_id,
        "request": payload.get("request", {})
    }
    
    return headers, final_payload

async def send_gemini_request(payload: dict, is_streaming: bool = False, credential_manager: CredentialManager = None) -> Response:
    """
    Send a request to Google's Gemini API.
    
    Args:
        payload: The request payload in Gemini format
        is_streaming: Whether this is a streaming request
        credential_manager: CredentialManager instance
        
    Returns:
        FastAPI Response object
    """
    # 获取429重试配置
    max_retries = await get_retry_429_max_retries()
    retry_429_enabled = await get_retry_429_enabled()
    retry_interval = await get_retry_429_interval()
    
    # 确定API端点
    action = "streamGenerateContent" if is_streaming else "generateContent"
    target_url = f"{await get_code_assist_endpoint()}/v1internal:{action}"
    if is_streaming:
        target_url += "?alt=sse"

    # 确保有credential_manager
    if not credential_manager:
        return _create_error_response("Credential manager not provided", 500)
    
    # 获取当前凭证
    try:
        credential_result = await credential_manager.get_valid_credential()
        if not credential_result:
            return _create_error_response("No valid credentials available", 500)
        
        current_file, credential_data = credential_result
        headers, final_payload = await _prepare_request_headers_and_payload(payload, credential_data)
    except Exception as e:
        return _create_error_response(str(e), 500)

    # 预序列化payload，避免重试时重复序列化
    final_post_data = json.dumps(final_payload)
    
    # Debug日志：打印请求体结构
    log.debug(f"Final request payload structure: {json.dumps(final_payload, ensure_ascii=False, indent=2)}")

    for attempt in range(max_retries + 1):
        try:
            if is_streaming:
                # 流式请求处理 - 使用httpx_client模块的统一配置
                client = await create_streaming_client_with_kwargs()
                
                try:
                    # 使用stream方法但不在async with块中消费数据
                    stream_ctx = client.stream("POST", target_url, content=final_post_data, headers=headers)
                    resp = await stream_ctx.__aenter__()
                    
                    if resp.status_code == 429:
                        # 记录429错误并获取响应内容
                        response_content = ""
                        try:
                            content_bytes = await resp.aread()
                            if isinstance(content_bytes, bytes):
                                response_content = content_bytes.decode('utf-8', errors='ignore')
                        except Exception as e:
                            log.debug(f"[STREAMING] Failed to read 429 response content: {e}")
                        
                        # 显示详细的429错误信息
                        if response_content:
                            log.error(f"Google API returned status 429 (STREAMING). Response details: {response_content[:500]}")
                        else:
                            log.error("Google API returned status 429 (STREAMING) - quota exhausted, no response details available")
                        
                        if credential_manager and current_file:
                            await credential_manager.record_api_call_result(current_file, False, 429)
                        
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
                                await credential_manager.force_rotate_credential()
                                # 重新获取凭证和headers（凭证可能已轮换）
                                new_credential_result = await credential_manager.get_valid_credential()
                                if new_credential_result:
                                    current_file, credential_data = new_credential_result
                                    headers, updated_payload = await _prepare_request_headers_and_payload(payload, credential_data)
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
                    elif resp.status_code != 200:
                        # 处理其他非200状态码的错误
                        response_content = ""
                        try:
                            content_bytes = await resp.aread()
                            if isinstance(content_bytes, bytes):
                                response_content = content_bytes.decode('utf-8', errors='ignore')
                        except Exception as e:
                            log.debug(f"[STREAMING] Failed to read error response content: {e}")
                        
                        # 显示详细的错误信息
                        if response_content:
                            log.error(f"Google API returned status {resp.status_code} (STREAMING). Response details: {response_content[:500]}")
                        else:
                            log.error(f"Google API returned status {resp.status_code} (STREAMING) - no response details available")
                        
                        # 记录API调用错误
                        if credential_manager and current_file:
                            await credential_manager.record_api_call_result(current_file, False, resp.status_code)
                        
                        # 清理资源
                        try:
                            await stream_ctx.__aexit__(None, None, None)
                        except:
                            pass
                        await client.aclose()
                        
                        # 处理凭证轮换
                        await _handle_api_error(credential_manager, resp.status_code, response_content)
                        
                        # 返回错误流
                        async def error_stream():
                            error_response = {
                                "error": {
                                    "message": f"API error: {resp.status_code}",
                                    "type": "api_error", 
                                    "code": resp.status_code
                                }
                            }
                            yield f"data: {json.dumps(error_response)}\n\n"
                        return StreamingResponse(error_stream(), media_type="text/event-stream", status_code=resp.status_code)
                    else:
                        # 成功响应，传递所有资源给流式处理函数管理
                        return _handle_streaming_response_managed(resp, stream_ctx, client, credential_manager, payload.get("model", ""), current_file)
                        
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
                        if credential_manager and current_file:
                            await credential_manager.record_api_call_result(current_file, False, 429)
                        
                        # 如果重试可用且未达到最大次数，继续重试
                        if retry_429_enabled and attempt < max_retries:
                            log.warning(f"[RETRY] 429 error encountered, retrying ({attempt + 1}/{max_retries})")
                            if credential_manager:
                                # 429错误时强制轮换凭证，不增加调用计数
                                await credential_manager.force_rotate_credential()
                                # 重新获取凭证和headers（凭证可能已轮换）
                                new_credential_result = await credential_manager.get_valid_credential()
                                if new_credential_result:
                                    current_file, credential_data = new_credential_result
                                    headers, updated_payload = await _prepare_request_headers_and_payload(payload, credential_data)
                                    final_post_data = json.dumps(updated_payload)
                            await asyncio.sleep(retry_interval)
                            continue
                        else:
                            log.error(f"[RETRY] Max retries exceeded for 429 error")
                            return _create_error_response("429 rate limit exceeded, max retries reached", 429)
                    else:
                        # 非429错误或成功响应，正常处理
                        return await _handle_non_streaming_response(resp, credential_manager, payload.get("model", ""), current_file)
                    
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


def _handle_streaming_response_managed(resp, stream_ctx, client, credential_manager: CredentialManager = None, model_name: str = "", current_file: str = None) -> StreamingResponse:
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
            
            # 记录API调用错误
            if credential_manager and current_file:
                await credential_manager.record_api_call_result(current_file, False, resp.status_code)
            
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
                    if current_file and credential_manager:
                        await credential_manager.record_api_call_result(current_file, True)
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

async def _handle_non_streaming_response(resp, credential_manager: CredentialManager = None, model_name: str = "", current_file: str = None) -> Response:
    """Handle non-streaming response from Google API."""
    if resp.status_code == 200:
        try:
            # 记录成功响应
            if current_file and credential_manager:
                await credential_manager.record_api_call_result(current_file, True)
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
        
        # 记录API调用错误
        if credential_manager and current_file:
            await credential_manager.record_api_call_result(current_file, False, resp.status_code)
        
        await _handle_api_error(credential_manager, resp.status_code, response_content)
        
        return _create_error_response(f"API error: {resp.status_code}", resp.status_code)

def build_gemini_payload_from_native(native_request: dict, model_from_path: str) -> dict:
    """
    Build a Gemini API payload from a native Gemini request with full pass-through support.
    """
    # 创建请求副本以避免修改原始数据
    request_data = native_request.copy()
    
    # 应用默认安全设置（如果未指定）
    if "safetySettings" not in request_data:
        request_data["safetySettings"] = DEFAULT_SAFETY_SETTINGS
    
    # 确保generationConfig存在
    if "generationConfig" not in request_data:
        request_data["generationConfig"] = {}
    
    generation_config = request_data["generationConfig"]
    
    # 配置thinking（如果未指定thinkingConfig）
    if "thinkingConfig" not in generation_config:
        generation_config["thinkingConfig"] = {}
    
    thinking_config = generation_config["thinkingConfig"]
    
    # 只有在未明确设置时才应用默认thinking配置
    if "includeThoughts" not in thinking_config:
        thinking_config["includeThoughts"] = should_include_thoughts(model_from_path)
    if "thinkingBudget" not in thinking_config:
        thinking_config["thinkingBudget"] = get_thinking_budget(model_from_path)
    
    # 为搜索模型添加Google Search工具（如果未指定且没有functionDeclarations）
    if is_search_model(model_from_path):
        if "tools" not in request_data:
            request_data["tools"] = []
        # 检查是否已有functionDeclarations或googleSearch工具
        has_function_declarations = any(tool.get("functionDeclarations") for tool in request_data["tools"])
        has_google_search = any(tool.get("googleSearch") for tool in request_data["tools"])
        
        # 只有在没有任何工具时才添加googleSearch，或者只有googleSearch工具时可以添加更多googleSearch
        if not has_function_declarations and not has_google_search:
            request_data["tools"].append({"googleSearch": {}})
    
    # 透传所有其他Gemini原生字段:
    # - contents (必需)
    # - systemInstruction (可选)
    # - generationConfig (已处理)
    # - safetySettings (已处理)  
    # - tools (已处理)
    # - toolConfig (透传)
    # - cachedContent (透传)
    # - 以及任何其他未知字段都会被透传
    
    return {
        "model": get_base_model_name(model_from_path),
        "request": request_data
    }