"""
Gemini Router - Handles native Gemini format API requests
处理原生Gemini格式请求的路由模块
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
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    get_base_model_from_feature_model,
    is_anti_truncation_model,
    authenticate_gemini_flexible,
    is_fake_streaming_model
)

# 本地模块 - 转换器（假流式需要）
from src.converter.fake_stream import (
    parse_response_for_fake_stream,
    build_gemini_fake_stream_chunks,
    create_gemini_heartbeat_chunk,
)

# 本地模块 - 基础路由工具
from src.router.hi_check import is_health_check_request, create_health_check_response

# 本地模块 - 数据模型
from src.models import GeminiRequest, model_to_dict

# 本地模块 - 任务管理
from src.task_manager import create_managed_task


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.post("/v1beta/models/{model:path}:generateContent")
@router.post("/v1/models/{model:path}:generateContent")
async def generate_content(
    gemini_request: "GeminiRequest",
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    处理Gemini格式的内容生成请求（非流式）

    Args:
        gemini_request: Gemini格式的请求体
        model: 模型名称
        api_key: API 密钥
    """
    log.debug(f"[GEMINICLI] Non-streaming request for model: {model}")

    # 转换为字典
    normalized_dict = model_to_dict(gemini_request)

    # 健康检查
    if is_health_check_request(normalized_dict, format="gemini"):
        response = create_health_check_response(format="gemini")
        return JSONResponse(content=response)

    # 处理模型名称和功能检测
    use_anti_truncation = is_anti_truncation_model(model)
    real_model = get_base_model_from_feature_model(model)

    # 对于抗截断模型的非流式请求，给出警告
    if use_anti_truncation:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")

    # 更新模型名为真实模型名
    normalized_dict["model"] = real_model

    # 规范化 Gemini 请求 (使用 geminicli 模式)
    from src.converter.gemini_fix import normalize_gemini_request
    normalized_dict = await normalize_gemini_request(normalized_dict, mode="geminicli")

    # 准备API请求格式 - 提取model并将其他字段放入request中
    api_request = {
        "model": normalized_dict.pop("model"),
        "request": normalized_dict
    }

    # 调用 API 层的非流式请求
    from src.api.geminicli import non_stream_request
    response = await non_stream_request(body=api_request)

    # 解包装响应：GeminiCli API 返回的格式有额外的 response 包装层
    # 需要提取 response.response 并返回标准 Gemini 格式
    try:
        if response.status_code == 200:
            response_data = json.loads(response.body if hasattr(response, 'body') else response.content)
            # 如果有 response 包装，解包装它
            if "response" in response_data:
                unwrapped_data = response_data["response"]
                return JSONResponse(content=unwrapped_data)
        # 错误响应或没有 response 字段，直接返回
        return response
    except Exception as e:
        log.warning(f"Failed to unwrap response: {e}, returning original response")
        return response

@router.post("/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/v1/models/{model:path}:streamGenerateContent")
async def stream_generate_content(
    gemini_request: GeminiRequest,
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    处理Gemini格式的流式内容生成请求

    Args:
        gemini_request: Gemini格式的请求体
        model: 模型名称
        api_key: API 密钥
    """
    log.debug(f"[GEMINICLI] Streaming request for model: {model}")

    # 转换为字典
    normalized_dict = model_to_dict(gemini_request)

    # 处理模型名称和功能检测
    use_fake_streaming = is_fake_streaming_model(model)
    use_anti_truncation = is_anti_truncation_model(model)
    real_model = get_base_model_from_feature_model(model)

    # 更新模型名为真实模型名
    normalized_dict["model"] = real_model

    # ========== 假流式生成器 ==========
    async def fake_stream_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="geminicli")

        # 准备API请求格式 - 提取model并将其他字段放入request中
        api_request = {
            "model": normalized_req.pop("model"),
            "request": normalized_req
        }

        # 发送心跳
        heartbeat = create_gemini_heartbeat_chunk()
        yield f"data: {json.dumps(heartbeat)}\n\n".encode()

        # 异步发送实际请求
        async def get_response():
            from src.api.geminicli import non_stream_request
            response = await non_stream_request(body=api_request)
            return response

        # 创建请求任务
        response_task = create_managed_task(get_response(), name="gemini_fake_stream_request")

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
                # 以SSE格式返回错误
                yield f"data: {json.dumps(error_data)}\n\n".encode()
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
            response_data = json.loads(response_body)
            log.debug(f"Gemini fake stream response data: {response_data}")

            # 检查是否是错误响应（有些错误可能status_code是200但包含error字段）
            if "error" in response_data:
                log.error(f"Fake streaming got error in response body: {response_data['error']}")
                yield f"data: {json.dumps(response_data)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return

            # 使用统一的解析函数
            content, reasoning_content, finish_reason, images = parse_response_for_fake_stream(response_data)

            log.debug(f"Gemini extracted content: {content}")
            log.debug(f"Gemini extracted reasoning: {reasoning_content[:100] if reasoning_content else 'None'}...")
            log.debug(f"Gemini extracted images count: {len(images)}")

            # 构建响应块
            chunks = build_gemini_fake_stream_chunks(content, reasoning_content, finish_reason, images)
            for idx, chunk in enumerate(chunks):
                chunk_json = json.dumps(chunk)
                log.debug(f"[FAKE_STREAM] Yielding chunk #{idx+1}: {chunk_json[:200]}")
                yield f"data: {chunk_json}\n\n".encode()

        except Exception as e:
            log.error(f"Response parsing failed: {e}, directly yield original response")
            # 直接yield原始响应,不进行包装
            yield f"data: {response_body}\n\n".encode()

        yield "data: [DONE]\n\n".encode()

    # ========== 流式抗截断生成器 ==========
    async def anti_truncation_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        from src.converter.anti_truncation import AntiTruncationStreamProcessor
        from src.converter.anti_truncation import apply_anti_truncation
        from src.api.geminicli import stream_request

        # 先进行基础标准化
        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="geminicli")

        # 准备API请求格式 - 提取model并将其他字段放入request中
        api_request = {
            "model": normalized_req.pop("model") if "model" in normalized_req else real_model,
            "request": normalized_req
        }

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

        # 迭代 process_stream() 生成器，并展开 response 包装
        async for chunk in processor.process_stream():
            if isinstance(chunk, (str, bytes)):
                chunk_str = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk

                # 解析并展开 response 包装
                if chunk_str.startswith("data: "):
                    json_str = chunk_str[6:].strip()

                    # 跳过 [DONE] 标记
                    if json_str == "[DONE]":
                        yield chunk
                        continue

                    try:
                        # 解析JSON
                        data = json.loads(json_str)

                        # 展开 response 包装
                        if "response" in data and "candidates" not in data:
                            log.debug(f"[GEMINICLI-ANTI-TRUNCATION] 展开response包装")
                            unwrapped_data = data["response"]
                            # 重新构建SSE格式
                            yield f"data: {json.dumps(unwrapped_data, ensure_ascii=False)}\n\n".encode('utf-8')
                        else:
                            # 已经是展开的格式，直接返回
                            yield chunk
                    except json.JSONDecodeError:
                        # JSON解析失败，直接返回原始chunk
                        yield chunk
                else:
                    # 不是SSE格式，直接返回
                    yield chunk
            else:
                # 其他类型，直接返回
                yield chunk

    # ========== 普通流式生成器 ==========
    async def normal_stream_generator():
        from src.converter.gemini_fix import normalize_gemini_request
        from src.api.geminicli import stream_request
        from fastapi import Response

        normalized_req = await normalize_gemini_request(normalized_dict.copy(), mode="geminicli")

        # 准备API请求格式 - 提取model并将其他字段放入request中
        api_request = {
            "model": normalized_req.pop("model"),
            "request": normalized_req
        }

        # 所有流式请求都使用非 native 模式（SSE格式）并展开 response 包装
        log.debug(f"[GEMINICLI] 使用非native模式，将展开response包装")
        stream_gen = stream_request(body=api_request, native=False)

        # 展开 response 包装
        async for chunk in stream_gen:
            # 检查是否是Response对象（错误情况）
            if isinstance(chunk, Response):
                # 将Response转换为SSE格式的错误消息
                error_content = chunk.body if isinstance(chunk.body, bytes) else chunk.body.encode('utf-8')
                error_json = json.loads(error_content.decode('utf-8'))
                # 以SSE格式返回错误
                yield f"data: {json.dumps(error_json)}\n\n".encode('utf-8')
                return

            # 处理SSE格式的chunk
            if isinstance(chunk, (str, bytes)):
                chunk_str = chunk.decode('utf-8') if isinstance(chunk, bytes) else chunk

                # 解析并展开 response 包装
                if chunk_str.startswith("data: "):
                    json_str = chunk_str[6:].strip()

                    # 跳过 [DONE] 标记
                    if json_str == "[DONE]":
                        yield chunk
                        continue

                    try:
                        # 解析JSON
                        data = json.loads(json_str)

                        # 展开 response 包装
                        if "response" in data and "candidates" not in data:
                            log.debug(f"[GEMINICLI] 展开response包装")
                            unwrapped_data = data["response"]
                            # 重新构建SSE格式
                            yield f"data: {json.dumps(unwrapped_data, ensure_ascii=False)}\n\n".encode('utf-8')
                        else:
                            # 已经是展开的格式，直接返回
                            yield chunk
                    except json.JSONDecodeError:
                        # JSON解析失败，直接返回原始chunk
                        yield chunk
                else:
                    # 不是SSE格式，直接返回
                    yield chunk

    # ========== 根据模式选择生成器 ==========
    if use_fake_streaming:
        return StreamingResponse(fake_stream_generator(), media_type="text/event-stream")
    elif use_anti_truncation:
        log.info("启用流式抗截断功能")
        return StreamingResponse(anti_truncation_generator(), media_type="text/event-stream")
    else:
        return StreamingResponse(normal_stream_generator(), media_type="text/event-stream")

@router.post("/v1beta/models/{model:path}:countTokens")
@router.post("/v1/models/{model:path}:countTokens")
async def count_tokens(
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """
    模拟Gemini格式的token计数
    
    使用简单的启发式方法：大约4字符=1token
    """

    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 简单的token计数模拟 - 基于文本长度估算
    total_tokens = 0

    # 如果有contents字段
    if "contents" in request_data:
        for content in request_data["contents"]:
            if "parts" in content:
                for part in content["parts"]:
                    if "text" in part:
                        # 简单估算：大约4字符=1token
                        text_length = len(part["text"])
                        total_tokens += max(1, text_length // 4)

    # 如果有generateContentRequest字段
    elif "generateContentRequest" in request_data:
        gen_request = request_data["generateContentRequest"]
        if "contents" in gen_request:
            for content in gen_request["contents"]:
                if "parts" in content:
                    for part in content["parts"]:
                        if "text" in part:
                            text_length = len(part["text"])
                            total_tokens += max(1, text_length // 4)

    # 返回Gemini格式的响应
    return JSONResponse(content={"totalTokens": total_tokens})

# ==================== 测试代码 ====================

if __name__ == "__main__":
    """
    测试代码：演示Gemini路由的流式和非流式响应
    运行方式: python src/router/geminicli/gemini.py
    """

    from fastapi.testclient import TestClient
    from fastapi import FastAPI

    print("=" * 80)
    print("Gemini Router 测试")
    print("=" * 80)

    # 创建测试应用
    app = FastAPI()
    app.include_router(router)

    # 测试客户端
    client = TestClient(app)

    # 测试请求体 (Gemini格式)
    test_request_body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Hello, tell me a joke in one sentence."}]
            }
        ]
    }

    # 测试API密钥（模拟）
    test_api_key = "pwd"

    def test_non_stream_request():
        """测试非流式请求"""
        print("\n" + "=" * 80)
        print("【测试2】非流式请求 (POST /v1/models/gemini-2.5-flash:generateContent)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        response = client.post(
            "/v1/models/gemini-2.5-flash:generateContent",
            json=test_request_body,
            params={"key": test_api_key}
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
        print("【测试3】流式请求 (POST /v1/models/gemini-2.5-flash:streamGenerateContent)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        print("流式响应数据 (每个chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/v1/models/gemini-2.5-flash:streamGenerateContent",
            json=test_request_body,
            params={"key": test_api_key}
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
                        if chunk_str.startswith("data: "):
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
        print("【测试4】假流式请求 (POST /v1/models/假流式/gemini-2.5-flash:streamGenerateContent)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        print("假流式响应数据 (每个chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/v1/models/假流式/gemini-2.5-flash:streamGenerateContent",
            json=test_request_body,
            params={"key": test_api_key}
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
                        if line.startswith("data: "):
                            events.append(line)

                    print(f"  包含 {len(events)} 个SSE事件")

                    # 显示每个事件
                    for event_idx, event_line in enumerate(events, 1):
                        if event_line == "data: [DONE]":
                            print(f"  事件 #{event_idx}: [DONE]")
                        else:
                            try:
                                json_str = event_line[6:]  # 去掉 "data: " 前缀
                                json_data = json.loads(json_str)
                                # 提取text内容
                                text = json_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                                finish_reason = json_data.get("candidates", [{}])[0].get("finishReason")
                                print(f"  事件 #{event_idx}: text={repr(text[:50])}{'...' if len(text) > 50 else ''}, finishReason={finish_reason}")
                            except Exception as e:
                                print(f"  事件 #{event_idx}: 解析失败 - {e}")

            print(f"\n总共收到 {chunk_count} 个HTTP chunk")

    def test_anti_truncation_stream_request():
        """测试流式抗截断请求"""
        print("\n" + "=" * 80)
        print("【测试5】流式抗截断请求 (POST /v1/models/流式抗截断/gemini-2.5-flash:streamGenerateContent)")
        print("=" * 80)
        print(f"请求体: {json.dumps(test_request_body, indent=2, ensure_ascii=False)}\n")

        print("流式抗截断响应数据 (每个chunk):")
        print("-" * 80)

        with client.stream(
            "POST",
            "/v1/models/流式抗截断/gemini-2.5-flash:streamGenerateContent",
            json=test_request_body,
            params={"key": test_api_key}
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
                        if chunk_str.startswith("data: "):
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

    # 运行测试
    try:
        # 测试非流式请求
        test_non_stream_request()

        # 测试流式请求
        test_stream_request()

        # 测试假流式请求
        test_fake_stream_request()

        # 测试流式抗截断请求
        test_anti_truncation_stream_request()

        print("\n" + "=" * 80)
        print("测试完成")
        print("=" * 80)

    except Exception as e:
        print(f"\n❌ 测试过程中出现异常: {e}")
        import traceback
        traceback.print_exc()

