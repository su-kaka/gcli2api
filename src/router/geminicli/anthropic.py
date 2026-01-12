"""
Anthropic Router - Handles Anthropic/Claude format API requests via GeminiCLI
通过GeminiCLI处理Anthropic/Claude格式请求的路由模块
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 标准库
import asyncio
import json

# 第三方库
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    get_base_model_from_feature_model,
    is_anti_truncation_model,
    is_fake_streaming_model,
    authenticate_bearer,
)

# 本地模块 - 转换器（假流式需要）
from src.converter.fake_stream import (
    parse_response_for_fake_stream,
    build_anthropic_fake_stream_chunks,
    create_anthropic_heartbeat_chunk,
)

# 本地模块 - 基础路由工具
from src.router.hi_check import is_health_check_request, create_health_check_response

# 本地模块 - 数据模型
from src.models import ClaudeRequest, model_to_dict

# 本地模块 - 任务管理
from src.task_manager import create_managed_task

# 本地模块 - Token估算
from src.token_estimator import estimate_input_tokens


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.post("/v1/messages")
async def messages(
    claude_request: ClaudeRequest,
    token: str = Depends(authenticate_bearer)
):
    """
    处理Anthropic/Claude格式的消息请求（流式和非流式）

    Args:
        claude_request: Anthropic/Claude格式的请求体
        token: Bearer认证令牌
    """
    log.debug(f"[GEMINICLI-ANTHROPIC] Request for model: {claude_request.model}")

    # 转换为字典
    normalized_dict = model_to_dict(claude_request)

    # 健康检查
    if is_health_check_request(normalized_dict, format="anthropic"):
        response = create_health_check_response(format="anthropic")
        return JSONResponse(content=response)

    # 处理模型名称和功能检测
    use_fake_streaming = is_fake_streaming_model(claude_request.model)
    use_anti_truncation = is_anti_truncation_model(claude_request.model)
    real_model = get_base_model_from_feature_model(claude_request.model)

    # 获取流式标志
    is_streaming = claude_request.stream

    # 对于抗截断模型的非流式请求，给出警告
    if use_anti_truncation and not is_streaming:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")

    # 更新模型名为真实模型名
    normalized_dict["model"] = real_model

    # 转换为 Gemini 格式 (使用 converter)
    from src.converter.anthropic2gemini import anthropic_to_gemini_request
    gemini_dict = await anthropic_to_gemini_request(normalized_dict)

    # anthropic_to_gemini_request 不包含 model 字段，需要手动添加
    gemini_dict["model"] = real_model

    # 规范化 Gemini 请求 (使用 geminicli 模式)
    from src.converter.gemini_fix import normalize_gemini_request
    gemini_dict = await normalize_gemini_request(gemini_dict, mode="geminicli")

    # 准备API请求格式 - 提取model并将其他字段放入request中
    api_request = {
        "model": gemini_dict.pop("model"),
        "request": gemini_dict
    }

    # ========== 非流式请求 ==========
    if not is_streaming:
        # 调用 API 层的非流式请求
        from src.api.geminicli import non_stream_request
        response = await non_stream_request(body=api_request)

        # 检查响应状态码
        status_code = getattr(response, "status_code", 200)

        # 提取响应体
        if hasattr(response, "body"):
            response_body = response.body.decode() if isinstance(response.body, bytes) else response.body
        elif hasattr(response, "content"):
            response_body = response.content.decode() if isinstance(response.content, bytes) else response.content
        else:
            response_body = str(response)

        try:
            gemini_response = json.loads(response_body)
        except Exception as e:
            log.error(f"Failed to parse Gemini response: {e}")
            raise HTTPException(status_code=500, detail="Response parsing failed")

        # 转换为 Anthropic 格式
        from src.converter.anthropic2gemini import gemini_to_anthropic_response
        anthropic_response = gemini_to_anthropic_response(
            gemini_response,
            real_model,
            status_code
        )

        return JSONResponse(content=anthropic_response, status_code=status_code)

    # ========== 流式请求 ==========

    # ========== 假流式生成器 ==========
    async def fake_stream_generator():
        # 发送心跳
        heartbeat = create_anthropic_heartbeat_chunk()
        yield f"data: {json.dumps(heartbeat)}\n\n".encode()

        # 异步发送实际请求
        async def get_response():
            from src.api.geminicli import non_stream_request
            response = await non_stream_request(body=api_request)
            return response

        # 创建请求任务
        response_task = create_managed_task(get_response(), name="anthropic_fake_stream_request")

        try:
            # 每3秒发送一次心跳，直到收到响应
            while not response_task.done():
                await asyncio.sleep(3.0)
                if not response_task.done():
                    yield f"data: {json.dumps(heartbeat)}\n\n".encode()

            # 获取响应结果
            response = await response_task

        except asyncio.CancelledError:
            response_task.cancel()
            try:
                await response_task
            except asyncio.CancelledError:
                pass
            raise
        except Exception as e:
            response_task.cancel()
            try:
                await response_task
            except asyncio.CancelledError:
                pass
            log.error(f"Fake streaming request failed: {e}")
            raise

        # 检查响应状态码
        if hasattr(response, "status_code") and response.status_code != 200:
            # 错误响应 - 提取错误信息并以SSE格式返回
            log.error(f"Fake streaming got error response: status={response.status_code}")

            if hasattr(response, "body"):
                error_body = response.body.decode() if isinstance(response.body, bytes) else response.body
            elif hasattr(response, "content"):
                error_body = response.content.decode() if isinstance(response.content, bytes) else response.content
            else:
                error_body = str(response)

            try:
                error_data = json.loads(error_body)
                # 转换错误为 Anthropic 格式
                from src.converter.anthropic2gemini import gemini_to_anthropic_response
                anthropic_error = gemini_to_anthropic_response(
                    error_data,
                    real_model,
                    response.status_code
                )
                yield f"data: {json.dumps(anthropic_error)}\n\n".encode()
            except Exception:
                # 如果无法解析为JSON，包装成错误对象
                yield f"data: {json.dumps({'error': error_body})}\n\n".encode()

            yield "data: [DONE]\n\n".encode()
            return

        # 处理成功响应 - 提取响应内容
        if hasattr(response, "body"):
            response_body = response.body.decode() if isinstance(response.body, bytes) else response.body
        elif hasattr(response, "content"):
            response_body = response.content.decode() if isinstance(response.content, bytes) else response.content
        else:
            response_body = str(response)

        try:
            gemini_response = json.loads(response_body)
            log.debug(f"Anthropic fake stream Gemini response: {gemini_response}")

            # 检查是否是错误响应（有些错误可能status_code是200但包含error字段）
            if "error" in gemini_response:
                log.error(f"Fake streaming got error in response body: {gemini_response['error']}")
                # 转换错误为 Anthropic 格式
                from src.converter.anthropic2gemini import gemini_to_anthropic_response
                anthropic_error = gemini_to_anthropic_response(
                    gemini_response,
                    real_model,
                    200
                )
                yield f"data: {json.dumps(anthropic_error)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return

            # 使用统一的解析函数
            content, reasoning_content, finish_reason, images = parse_response_for_fake_stream(gemini_response)

            log.debug(f"Anthropic extracted content: {content}")
            log.debug(f"Anthropic extracted reasoning: {reasoning_content[:100] if reasoning_content else 'None'}...")
            log.debug(f"Anthropic extracted images count: {len(images)}")

            # 构建响应块
            chunks = build_anthropic_fake_stream_chunks(content, reasoning_content, finish_reason, real_model, images)
            for idx, chunk in enumerate(chunks):
                chunk_json = json.dumps(chunk)
                log.debug(f"[FAKE_STREAM] Yielding chunk #{idx+1}: {chunk_json[:200]}")
                yield f"data: {chunk_json}\n\n".encode()

        except Exception as e:
            log.error(f"Response parsing failed: {e}, directly yield error")
            # 构建错误响应
            error_chunk = {
                "type": "error",
                "error": {
                    "type": "api_error",
                    "message": str(e)
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n".encode()

        yield "data: [DONE]\n\n".encode()

    # ========== 流式抗截断生成器 ==========
    async def anti_truncation_generator():
        from src.converter.anti_truncation import AntiTruncationStreamProcessor
        from src.api.geminicli import stream_request
        from src.converter.anti_truncation import apply_anti_truncation
        from src.converter.anthropic2gemini import gemini_stream_to_anthropic_stream

        max_attempts = await get_anti_truncation_max_attempts()

        # 首先对payload应用反截断指令
        anti_truncation_payload = apply_anti_truncation(api_request)

        # 定义流式请求函数（返回 StreamingResponse）
        async def stream_request_wrapper(payload):
            # stream_request 返回异步生成器，需要包装成 StreamingResponse
            stream_gen = stream_request(body=payload, native=False)
            return StreamingResponse(stream_gen, media_type="text/event-stream")

        # 创建反截断处理器
        processor = AntiTruncationStreamProcessor(
            stream_request_wrapper,
            anti_truncation_payload,
            max_attempts
        )

        # 包装以确保是bytes流
        async def bytes_wrapper():
            async for chunk in processor.process_stream():
                if isinstance(chunk, str):
                    yield chunk.encode('utf-8')
                else:
                    yield chunk

        # 直接将整个流传递给转换器
        async for anthropic_chunk in gemini_stream_to_anthropic_stream(
            bytes_wrapper(),
            real_model,
            200
        ):
            if anthropic_chunk:
                yield anthropic_chunk

    # ========== 普通流式生成器 ==========
    async def normal_stream_generator():
        from src.api.geminicli import stream_request
        from fastapi import Response
        from src.converter.anthropic2gemini import gemini_stream_to_anthropic_stream

        # 调用 API 层的流式请求（不使用 native 模式）
        stream_gen = stream_request(body=api_request, native=False)

        # 包装流式生成器以处理错误响应
        async def gemini_chunk_wrapper():
            async for chunk in stream_gen:
                # 检查是否是Response对象（错误情况）
                if isinstance(chunk, Response):
                    # 错误响应，不进行转换，直接传递
                    error_content = chunk.body if isinstance(chunk.body, bytes) else chunk.body.encode('utf-8')
                    try:
                        gemini_error = json.loads(error_content.decode('utf-8'))
                        from src.converter.anthropic2gemini import gemini_to_anthropic_response
                        anthropic_error = gemini_to_anthropic_response(
                            gemini_error,
                            real_model,
                            chunk.status_code
                        )
                        yield f"data: {json.dumps(anthropic_error)}\n\n".encode('utf-8')
                    except Exception:
                        yield f"data: {json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': 'Stream error'}})}\n\n".encode('utf-8')
                    return
                else:
                    # 确保是bytes类型
                    if isinstance(chunk, str):
                        yield chunk.encode('utf-8')
                    else:
                        yield chunk

        # 使用转换器处理整个流
        async for anthropic_chunk in gemini_stream_to_anthropic_stream(
            gemini_chunk_wrapper(),
            real_model,
            200
        ):
            if anthropic_chunk:
                yield anthropic_chunk

    # ========== 根据模式选择生成器 ==========
    if use_fake_streaming:
        return StreamingResponse(fake_stream_generator(), media_type="text/event-stream")
    elif use_anti_truncation:
        log.info("启用流式抗截断功能")
        return StreamingResponse(anti_truncation_generator(), media_type="text/event-stream")
    else:
        return StreamingResponse(normal_stream_generator(), media_type="text/event-stream")


@router.post("/v1/messages/count_tokens")
async def count_tokens(
    request: Request,
    _token: str = Depends(authenticate_bearer)
):
    """
    处理Anthropic格式的token计数请求
    
    Args:
        request: FastAPI请求对象
        _token: Bearer认证令牌（由Depends验证）
    
    Returns:
        JSONResponse: 包含input_tokens的响应
    """
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": f"JSON 解析失败: {str(e)}"}}
        )

    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "请求体必须为 JSON object"}}
        )

    if not payload.get("model") or not isinstance(payload.get("messages"), list):
        return JSONResponse(
            status_code=400,
            content={"type": "error", "error": {"type": "invalid_request_error", "message": "缺少必填字段：model / messages"}}
        )

    try:
        client_host = request.client.host if request.client else "unknown"
        client_port = request.client.port if request.client else "unknown"
    except Exception:
        client_host = "unknown"
        client_port = "unknown"

    thinking_present = "thinking" in payload
    thinking_value = payload.get("thinking")
    thinking_summary = None
    if thinking_present:
        if isinstance(thinking_value, dict):
            thinking_summary = {
                "type": thinking_value.get("type"),
                "budget_tokens": thinking_value.get("budget_tokens"),
            }
        else:
            thinking_summary = thinking_value

    user_agent = request.headers.get("user-agent", "")
    log.info(
        f"[GEMINICLI-ANTHROPIC] /messages/count_tokens 收到请求: client={client_host}:{client_port}, "
        f"model={payload.get('model')}, messages={len(payload.get('messages') or [])}, "
        f"thinking_present={thinking_present}, thinking={thinking_summary}, ua={user_agent}"
    )

    # 简单估算
    input_tokens = 0
    try:
        input_tokens = estimate_input_tokens(payload)
    except Exception as e:
        log.error(f"[GEMINICLI-ANTHROPIC] token 估算失败: {e}")

    return JSONResponse(content={"input_tokens": input_tokens})


# ==================== 测试代码 ====================

if __name__ == "__main__":
    """
    测试代码：演示Anthropic路由的流式和非流式响应
    运行方式: python src/router/geminicli/anthropic.py
    """

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    print("=" * 80)
    print("Anthropic Router 测试")
    print("=" * 80)

    # 创建测试应用
    app = FastAPI()
    app.include_router(router)

    # 测试客户端
    client = TestClient(app)

    # 测试请求体 (Anthropic格式)
    test_request_body = {
        "model": "gemini-2.5-flash",
        "max_tokens": 1024,
        "messages": [
            {"role": "user", "content": "Hello, tell me a joke in one sentence."}
        ]
    }

    # 测试Bearer令牌（模拟）
    test_token = "Bearer pwd"

    def test_non_stream_request():
        """测试非流式请求"""
        print("\n" + "=" * 80)
        print("【测试1】非流式请求 (POST /v1/messages)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        response = client.post(
            "/v1/messages",
            json=test_request_body,
            headers={"Authorization": test_token}
        )

        print("非流式响应数据:")
        print("-" * 80)
        print(f"状态码: {response.status_code}")
        print(f"Content-Type: {response.headers.get('content-type', 'N/A')}")

        try:
            content = response.text
            print(f"\n响应内容 (原始):\n{content}\n")

            # 尝试解析JSON
            try:
                json_data = response.json()
                print(f"响应内容 (格式化JSON):")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
            except json.JSONDecodeError:
                print("(非JSON格式)")
        except Exception as e:
            print(f"内容解析失败: {e}")

    def test_stream_request():
        """测试流式请求"""
        print("\n" + "=" * 80)
        print("【测试2】流式请求 (POST /v1/messages)")
        print("=" * 80)

        stream_request_body = test_request_body.copy()
        stream_request_body["stream"] = True

        print(f"请求体: {json.dumps(stream_request_body, indent=2, ensure_ascii=False)}\n")

        print("流式响应数据 (每个chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/v1/messages",
            json=stream_request_body,
            headers={"Authorization": test_token}
        ) as response:
            print(f"状态码: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    print(f"\nChunk #{chunk_count}:")
                    print(f"  类型: {type(chunk).__name__}")
                    print(f"  长度: {len(chunk)}")

                    # 解码chunk
                    try:
                        chunk_str = chunk.decode('utf-8')
                        print(f"  内容预览: {repr(chunk_str[:200] if len(chunk_str) > 200 else chunk_str)}")

                        # 如果是SSE格式，尝试解析每一行
                        if chunk_str.startswith("event: ") or chunk_str.startswith("data: "):
                            # 按行分割，处理每个SSE事件
                            for line in chunk_str.strip().split('\n'):
                                line = line.strip()
                                if not line:
                                    continue

                                if line == "data: [DONE]":
                                    print(f"  => 流结束标记")
                                elif line.startswith("data: "):
                                    try:
                                        json_str = line[6:]  # 去掉 "data: " 前缀
                                        json_data = json.loads(json_str)
                                        print(f"  解析后的JSON: {json.dumps(json_data, indent=4, ensure_ascii=False)}")
                                    except Exception as e:
                                        print(f"  SSE解析失败: {e}")
                    except Exception as e:
                        print(f"  解码失败: {e}")

            print(f"\n总共收到 {chunk_count} 个chunk")

    def test_fake_stream_request():
        """测试假流式请求"""
        print("\n" + "=" * 80)
        print("【测试3】假流式请求 (POST /v1/messages with 假流式 prefix)")
        print("=" * 80)

        fake_stream_request_body = test_request_body.copy()
        fake_stream_request_body["model"] = "假流式/gemini-2.5-flash"
        fake_stream_request_body["stream"] = True

        print(f"请求体: {json.dumps(fake_stream_request_body, indent=2, ensure_ascii=False)}\n")

        print("假流式响应数据 (每个chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/v1/messages",
            json=fake_stream_request_body,
            headers={"Authorization": test_token}
        ) as response:
            print(f"状态码: {response.status_code}")
            print(f"Content-Type: {response.headers.get('content-type', 'N/A')}\n")

            chunk_count = 0
            for chunk in response.iter_bytes():
                if chunk:
                    chunk_count += 1
                    chunk_str = chunk.decode('utf-8')

                    print(f"\nChunk #{chunk_count}:")
                    print(f"  长度: {len(chunk_str)} 字节")

                    # 解析chunk中的所有SSE事件
                    events = []
                    for line in chunk_str.split('\n'):
                        line = line.strip()
                        if line.startswith("data: ") or line.startswith("event: "):
                            events.append(line)

                    print(f"  包含 {len(events)} 个SSE事件")

                    # 显示每个事件
                    for event_idx, event_line in enumerate(events, 1):
                        if event_line == "data: [DONE]":
                            print(f"  事件 #{event_idx}: [DONE]")
                        elif event_line.startswith("data: "):
                            try:
                                json_str = event_line[6:]  # 去掉 "data: " 前缀
                                json_data = json.loads(json_str)
                                event_type = json_data.get("type", "unknown")
                                print(f"  事件 #{event_idx}: type={event_type}")
                            except Exception as e:
                                print(f"  事件 #{event_idx}: 解析失败 - {e}")

            print(f"\n总共收到 {chunk_count} 个HTTP chunk")

    # 运行测试
    try:
        # 测试非流式请求
        test_non_stream_request()

        # 测试流式请求
        test_stream_request()

        # 测试假流式请求
        test_fake_stream_request()

        print("\n" + "=" * 80)
        print("测试完成")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ 测试过程中出现异常: {e}")
        import traceback
        traceback.print_exc()
