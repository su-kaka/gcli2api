"""
GeminiCli API Client - Handles all communication with GeminiCli API.
This module is used by both OpenAI compatibility layer and native Gemini endpoints.
GeminiCli API 客户端 - 处理与 GeminiCli API 的所有通信
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径（用于直接运行测试）
if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent.parent
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

import asyncio
import json
from typing import Any, Dict, Optional

from fastapi import Response
from config import get_code_assist_endpoint, get_auto_ban_error_codes
from src.api.utils import get_model_group
from log import log

from src.credential_manager import credential_manager
from src.httpx_client import stream_post_async, post_async

# 导入共同的基础功能
from src.api.utils import (
    handle_error_with_retry,
    get_retry_config,
    record_api_call_success,
    record_api_call_error,
    parse_and_log_cooldown,
)
from src.utils import GEMINICLI_USER_AGENT

# ==================== 全局凭证管理器 ====================

# 使用全局单例 credential_manager，自动初始化


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
        "User-Agent": GEMINICLI_USER_AGENT,
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
    # 获取有效凭证
    model_name = body.get("model", "")
    model_group = get_model_group(model_name)

    # 1. 获取有效凭证
    cred_result = await credential_manager.get_valid_credential(
        mode="geminicli", model_key=model_group
    )

    if not cred_result:
        # 如果返回值是None，直接返回错误500
        yield Response(
            content=json.dumps({"error": "当前无可用凭证"}),
            status_code=500,
            media_type="application/json"
        )
        return

    current_file, credential_data = cred_result

    # 2. 构建URL和请求头
    try:
        auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
            body, credential_data,
            f"{await get_code_assist_endpoint()}/v1internal:streamGenerateContent?alt=sse"
        )

        # 合并自定义headers
        if headers:
            auth_headers.update(headers)

    except Exception as e:
        log.error(f"准备请求失败: {e}")
        yield Response(
            content=json.dumps({"error": f"准备请求失败: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )
        return

    # 3. 调用stream_post_async进行请求
    retry_config = await get_retry_config()
    max_retries = retry_config["max_retries"]
    retry_interval = retry_config["retry_interval"]

    DISABLE_ERROR_CODES = await get_auto_ban_error_codes()  # 禁用凭证的错误码
    last_error_response = None  # 记录最后一次的错误响应
    
    # 内部函数：获取新凭证并更新headers
    async def refresh_credential():
        nonlocal current_file, credential_data, auth_headers, final_payload, target_url
        cred_result = await credential_manager.get_valid_credential(
            mode="geminicli", model_key=model_group
        )
        if not cred_result:
            return None
        current_file, credential_data = cred_result
        try:
            auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
                body, credential_data,
                f"{await get_code_assist_endpoint()}/v1internal:streamGenerateContent?alt=sse"
            )
            if headers:
                auth_headers.update(headers)
            return True
        except Exception:
            return None

    for attempt in range(max_retries + 1):
        success_recorded = False  # 标记是否已记录成功
        need_retry = False  # 标记是否需要重试

        try:
            async for chunk in stream_post_async(
                url=target_url,
                body=final_payload,
                native=native,
                headers=auth_headers
            ):
                # 判断是否是Response对象
                if isinstance(chunk, Response):
                    status_code = chunk.status_code
                    last_error_response = chunk  # 记录最后一次错误

                    # 如果错误码是429或者在禁用码当中，做好记录后进行重试
                    if status_code == 429 or status_code in DISABLE_ERROR_CODES:
                        # 解析错误响应内容
                        try:
                            error_body = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                            log.warning(f"[GEMINICLI STREAM] 流式请求失败 (status={status_code}), 凭证: {current_file}, 响应: {error_body[:500]}")
                        except Exception:
                            log.warning(f"[GEMINICLI STREAM] 流式请求失败 (status={status_code}), 凭证: {current_file}")

                        # 记录错误
                        cooldown_until = None
                        if status_code == 429:
                            # 尝试解析冷却时间
                            try:
                                error_body = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                                cooldown_until = await parse_and_log_cooldown(error_body, mode="geminicli")
                            except Exception:
                                pass

                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            cooldown_until, mode="geminicli", model_key=model_group
                        )

                        # 检查是否应该重试
                        should_retry = await handle_error_with_retry(
                            credential_manager, status_code, current_file,
                            retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                            mode="geminicli"
                        )

                        if should_retry and attempt < max_retries:
                            need_retry = True
                            break  # 跳出内层循环，准备重试
                        else:
                            # 不重试，直接返回原始错误
                            log.error(f"[GEMINICLI STREAM] 达到最大重试次数或不应重试，返回原始错误")
                            yield chunk
                            return
                    else:
                        # 错误码不在禁用码当中，直接返回，无需重试
                        try:
                            error_body = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                            log.error(f"[GEMINICLI STREAM] 流式请求失败，非重试错误码 (status={status_code}), 凭证: {current_file}, 响应: {error_body[:500]}")
                        except Exception:
                            log.error(f"[GEMINICLI STREAM] 流式请求失败，非重试错误码 (status={status_code}), 凭证: {current_file}")
                        await record_api_call_error(
                            credential_manager, current_file, status_code,
                            None, mode="geminicli", model_key=model_group
                        )
                        yield chunk
                        return
                else:
                    # 不是Response，说明是真流，直接yield返回
                    # 只在第一个chunk时记录成功
                    if not success_recorded:
                        await record_api_call_success(
                            credential_manager, current_file, mode="geminicli", model_key=model_group
                        )
                        success_recorded = True
                        log.debug(f"[GEMINICLI STREAM] 开始接收流式响应，模型: {model_name}")

                    yield chunk

            # 流式请求完成，检查结果
            if success_recorded:
                log.debug(f"[GEMINICLI STREAM] 流式响应完成，模型: {model_name}")
                return
            elif not need_retry:
                # 没有收到任何数据（空回复），需要重试
                log.warning(f"[GEMINICLI STREAM] 收到空回复，无任何内容，凭证: {current_file}")
                await record_api_call_error(
                    credential_manager, current_file, 200,
                    None, mode="geminicli", model_key=model_group
                )
                
                if attempt < max_retries:
                    need_retry = True
                else:
                    log.error(f"[GEMINICLI STREAM] 空回复达到最大重试次数")
                    yield Response(
                        content=json.dumps({"error": "服务返回空回复"}),
                        status_code=500,
                        media_type="application/json"
                    )
                    return
            
            # 统一处理重试
            if need_retry:
                log.info(f"[GEMINICLI STREAM] 重试请求 (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                
                if not await refresh_credential():
                    log.error("[GEMINICLI STREAM] 重试时无可用凭证或刷新失败")
                    yield Response(
                        content=json.dumps({"error": "当前无可用凭证"}),
                        status_code=500,
                        media_type="application/json"
                    )
                    return
                continue  # 重试

        except Exception as e:
            log.error(f"[GEMINICLI STREAM] 流式请求异常: {e}, 凭证: {current_file}")
            if attempt < max_retries:
                log.info(f"[GEMINICLI STREAM] 异常后重试 (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # 所有重试都失败，返回最后一次的错误（如果有）
                log.error(f"[GEMINICLI STREAM] 所有重试均失败，最后异常: {e}")
                yield last_error_response


async def non_stream_request(
    body: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Response:
    """
    非流式请求函数

    Args:
        body: 请求体
        native: 保留参数以保持接口一致性（实际未使用）
        headers: 额外的请求头

    Returns:
        Response对象
    """
    # 获取有效凭证
    model_name = body.get("model", "")
    model_group = get_model_group(model_name)

    # 1. 获取有效凭证
    cred_result = await credential_manager.get_valid_credential(
        mode="geminicli", model_key=model_group
    )

    if not cred_result:
        # 如果返回值是None，直接返回错误500
        return Response(
            content=json.dumps({"error": "当前无可用凭证"}),
            status_code=500,
            media_type="application/json"
        )

    current_file, credential_data = cred_result

    # 2. 构建URL和请求头
    try:
        auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
            body, credential_data,
            f"{await get_code_assist_endpoint()}/v1internal:generateContent"
        )

        # 合并自定义headers
        if headers:
            auth_headers.update(headers)

    except Exception as e:
        log.error(f"准备请求失败: {e}")
        return Response(
            content=json.dumps({"error": f"准备请求失败: {str(e)}"}),
            status_code=500,
            media_type="application/json"
        )

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
                json=final_payload,
                headers=auth_headers,
                timeout=300.0
            )

            status_code = response.status_code

            # 成功
            if status_code == 200:
                await record_api_call_success(
                    credential_manager, current_file, mode="geminicli", model_key=model_group
                )
                # 创建响应头,移除压缩相关的header避免重复解压
                response_headers = dict(response.headers)
                response_headers.pop('content-encoding', None)
                response_headers.pop('content-length', None)

                return Response(
                    content=response.content,
                    status_code=200,
                    headers=response_headers
                )

            # 失败 - 记录最后一次错误
            # 创建响应头,移除压缩相关的header避免重复解压
            error_headers = dict(response.headers)
            error_headers.pop('content-encoding', None)
            error_headers.pop('content-length', None)

            last_error_response = Response(
                content=response.content,
                status_code=status_code,
                headers=error_headers
            )

            # 判断是否需要重试
            # 获取错误文本
            try:
                error_text = response.text
                error_msg = f"非流式请求失败 (status={status_code}), 凭证: {current_file}, 响应: {error_text[:500]}"
            except Exception:
                error_text = ""
                error_msg = f"非流式请求失败 (status={status_code}), 凭证: {current_file}"

            # 如果错误码在禁用码当中，禁用该凭证
            if status_code in DISABLE_ERROR_CODES:
                log.error(f"非流式请求失败，禁用错误码 (status={status_code}), 凭证: {current_file}, 响应: {error_text[:500] if error_text else '无'}")
                # 记录错误并禁用凭证
                await record_api_call_error(
                    credential_manager, current_file, status_code,
                    None, mode="geminicli", model_key=model_group
                )
                # 尝试切换到新凭证并重试
                if attempt < max_retries:
                    log.info(f"[NON-STREAM] 切换凭证并重试 (attempt {attempt + 2}/{max_retries + 1})...")
                    await asyncio.sleep(retry_interval)

                    # 获取新凭证
                    cred_result = await credential_manager.get_valid_credential(
                        mode="geminicli", model_key=model_group
                    )
                    if not cred_result:
                        log.error("[NON-STREAM] 重试时无可用凭证")
                        return Response(
                            content=json.dumps({"error": "当前无可用凭证"}),
                            status_code=500,
                            media_type="application/json"
                        )

                    current_file, credential_data = cred_result
                    auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
                        body, credential_data,
                        f"{await get_code_assist_endpoint()}/v1internal:generateContent"
                    )
                    if headers:
                        auth_headers.update(headers)
                    continue  # 重试
                else:
                    # 达到最大重试次数
                    log.error(f"[NON-STREAM] 达到最大重试次数，返回原始错误")
                    return last_error_response
            else:
                # 错误码不在禁用码当中（如429等），做好记录后进行重试
                log.warning(error_msg)

                # 记录错误
                cooldown_until = None
                if status_code == 429:
                    # 尝试解析冷却时间
                    try:
                        if not error_text:
                            error_text = response.text
                        cooldown_until = await parse_and_log_cooldown(error_text, mode="geminicli")
                    except Exception:
                        pass

                await record_api_call_error(
                    credential_manager, current_file, status_code,
                    cooldown_until, mode="geminicli", model_key=model_group
                )

                # 检查是否应该重试
                should_retry = await handle_error_with_retry(
                    credential_manager, status_code, current_file,
                    retry_config["retry_enabled"], attempt, max_retries, retry_interval,
                    mode="geminicli"
                )

                if should_retry and attempt < max_retries:
                    # 重新获取凭证并重试
                    log.info(f"[NON-STREAM] 重试请求 (attempt {attempt + 2}/{max_retries + 1})...")
                    await asyncio.sleep(retry_interval)

                    # 获取新凭证
                    cred_result = await credential_manager.get_valid_credential(
                        mode="geminicli", model_key=model_group
                    )
                    if not cred_result:
                        log.error("[NON-STREAM] 重试时无可用凭证")
                        return Response(
                            content=json.dumps({"error": "当前无可用凭证"}),
                            status_code=500,
                            media_type="application/json"
                        )

                    current_file, credential_data = cred_result
                    auth_headers, final_payload, target_url = await prepare_request_headers_and_payload(
                        body, credential_data,
                        f"{await get_code_assist_endpoint()}/v1internal:generateContent"
                    )
                    if headers:
                        auth_headers.update(headers)
                    continue  # 重试
                else:
                    # 不重试，直接返回原始错误
                    log.error(f"[NON-STREAM] 达到最大重试次数或不应重试，返回原始错误")
                    return last_error_response

        except Exception as e:
            log.error(f"非流式请求异常: {e}, 凭证: {current_file}")
            if attempt < max_retries:
                log.info(f"[NON-STREAM] 异常后重试 (attempt {attempt + 2}/{max_retries + 1})...")
                await asyncio.sleep(retry_interval)
                continue
            else:
                # 所有重试都失败，返回最后一次的错误（如果有）或500错误
                log.error(f"[NON-STREAM] 所有重试均失败，最后异常: {e}")
                if last_error_response:
                    return last_error_response
                else:
                    return Response(
                        content=json.dumps({"error": f"请求异常: {str(e)}"}),
                        status_code=500,
                        media_type="application/json"
                    )

    # 所有重试都失败，返回最后一次的原始错误
    log.error("[NON-STREAM] 所有重试均失败")
    return last_error_response


# ==================== 测试代码 ====================

if __name__ == "__main__":
    """
    测试代码：演示API返回的流式和非流式数据格式
    运行方式: python src/api/geminicli.py
    """
    print("=" * 80)
    print("GeminiCli API 测试")
    print("=" * 80)

    # 测试请求体
    test_body = {
        "model": "gemini-2.5-flash",
        "request": {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": "Hello, tell me a joke in one sentence."}]
                }
            ]
        }
    }

    async def test_stream_request():
        """测试流式请求"""
        print("\n" + "=" * 80)
        print("【测试1】流式请求 (stream_request with native=False)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_body, indent=2, ensure_ascii=False)}\n")

        print("流式响应数据 (每个chunk):")
        print("-" * 80)

        chunk_count = 0
        async for chunk in stream_request(body=test_body, native=False):
            chunk_count += 1
            if isinstance(chunk, Response):
                # 错误响应
                print(f"\n❌ 错误响应:")
                print(f"  状态码: {chunk.status_code}")
                print(f"  Content-Type: {chunk.headers.get('content-type', 'N/A')}")
                try:
                    content = chunk.body.decode('utf-8') if isinstance(chunk.body, bytes) else str(chunk.body)
                    print(f"  内容: {content}")
                except Exception as e:
                    print(f"  内容解析失败: {e}")
            else:
                # 正常的流式数据块 (str类型)
                print(f"\nChunk #{chunk_count}:")
                print(f"  类型: {type(chunk).__name__}")
                print(f"  长度: {len(chunk) if hasattr(chunk, '__len__') else 'N/A'}")
                print(f"  内容预览: {repr(chunk[:200] if len(chunk) > 200 else chunk)}")

                # 如果是SSE格式，尝试解析
                if isinstance(chunk, str) and chunk.startswith("data: "):
                    try:
                        data_line = chunk.strip()
                        if data_line.startswith("data: "):
                            json_str = data_line[6:]  # 去掉 "data: " 前缀
                            json_data = json.loads(json_str)
                            print(f"  解析后的JSON: {json.dumps(json_data, indent=4, ensure_ascii=False)}")
                    except Exception as e:
                        print(f"  SSE解析尝试失败: {e}")

        print(f"\n总共收到 {chunk_count} 个chunk")

    async def test_non_stream_request():
        """测试非流式请求"""
        print("\n" + "=" * 80)
        print("【测试2】非流式请求 (non_stream_request)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_body, indent=2, ensure_ascii=False)}\n")

        response = await non_stream_request(body=test_body)

        print("非流式响应数据:")
        print("-" * 80)
        print(f"状态码: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type', 'N/A')}")
        print(f"\n响应头: {dict(response.headers)}\n")

        try:
            content = response.body.decode('utf-8') if isinstance(response.body, bytes) else str(response.body)
            print(f"响应内容 (原始):\n{content}\n")

            # 尝试解析JSON
            try:
                json_data = json.loads(content)
                print(f"响应内容 (格式化JSON):")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print("(非JSON格式)")
        except Exception as e:
            print(f"内容解析失败: {e}")

    async def main():
        """主测试函数"""
        try:
            # 测试流式请求
            await test_stream_request()

            # 测试非流式请求
            await test_non_stream_request()

            print("\n" + "=" * 80)
            print("测试完成")
            print("=" * 80)

        except Exception as e:
            print(f"\n❌ 测试过程中出现异常: {e}")
            import traceback
            traceback.print_exc()

    # 运行测试
    asyncio.run(main())
