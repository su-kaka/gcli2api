"""
抗截断模型日志记录验证
- 每次 stream_request（首次 + 续传尝试）各记一条 usage_log（这是正确行为）
- enable_prefill_mode 残留参数已修复，AntiTruncationStreamProcessor 正常实例化
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


def _sse(payload: dict) -> bytes:
    import json

    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")


async def test_anti_truncation_logging():
    os.environ.pop("USAGE_LOG_ENABLED", None)
    config._config_cache.clear()
    config._config_cache["usage_log_enabled"] = True

    from src.storage.database import get_database
    from src.converter.anti_truncation import (
        AntiTruncationStreamProcessor,
        apply_anti_truncation,
    )
    from src.usage_log import record_usage_log

    database = await get_database()

    # ========== 1. 构造函数签名（enable_prefill_mode 已移除）==========
    import inspect

    sig = inspect.signature(AntiTruncationStreamProcessor.__init__)
    assert "enable_prefill_mode" not in sig.parameters, "enable_prefill_mode 应已移除"
    print("PASS: __init__ signature =", list(sig.parameters))

    # ========== 2. 模拟抗截断流程：3 次上游调用（1 首次 + 2 续传）==========
    # 模拟 stream_request: 每次被调用时记录一条日志并返回 SSE 流
    upstream_calls = []

    async def mock_stream_request(body, native=False, log_ctx=None):
        upstream_calls.append({"body": body, "log_ctx": log_ctx})
        # 模拟 API 层的日志记录（真实代码中 stream_request 内部完成）
        from src.usage_log import new_request_id

        entry = {
            "request_id": log_ctx.get("request_id") or new_request_id(),
            "api_format": log_ctx.get("api_format", ""),
            "client_ip": log_ctx.get("client_ip", ""),
            "model_name": log_ctx.get("client_model") or body.get("model", ""),
            "upstream_model_name": body.get("model", ""),
            "credential_email": "test@example.com",
            "is_stream": True,
            "status_code": 200,
            "success": True,
            "use_time": 1.0,
            "prompt_tokens": 50,
            "completion_tokens": 30,
            "total_tokens": 80,
        }
        await record_usage_log(entry)

        attempt = len(upstream_calls)

        async def gen():
            if attempt == 1:
                # 首次：合成工具调用命中，但 MAX_TOKENS 截断 -> 触发续传
                yield _sse({"candidates": [{"content": {"parts": [
                    {"functionCall": {"name": "emit_answer", "args": {"content": "第一段回答"}}}
                ]}, "finishReason": "MAX_TOKENS"}]})
                yield b"data: [DONE]\n\n"
            elif attempt == 2:
                # 续传 1：再次截断 -> 再续传
                yield _sse({"candidates": [{"content": {"parts": [
                    {"functionCall": {"name": "emit_answer", "args": {"content": "第二段回答"}}}
                ]}, "finishReason": "MAX_TOKENS"}]})
                yield b"data: [DONE]\n\n"
            else:
                # 续传 2：自然收尾
                yield _sse({"candidates": [{"content": {"parts": [
                    {"functionCall": {"name": "emit_answer", "args": {"content": "最后一段"}}}
                ]}, "finishReason": "STOP"}]})
                yield b"data: [DONE]\n\n"

        async for chunk in gen():
            yield chunk

    # 构造基础请求（模拟 router 的 api_request）
    api_request = {
        "model": "gemini-2.5-pro",
        "request": {
            "contents": [{"role": "user", "parts": [{"text": "写一篇长文"}]}],
            "generationConfig": {},
            "tools": [],
        },
    }
    anti_truncation_payload = apply_anti_truncation(api_request)
    log_ctx = {"api_format": "openai", "client_ip": "127.0.0.1", "client_model": "抗截断-gemini-2.5-pro"}

    # 清空日志表
    await database.delete_usage_logs_before(99999999999)

    async def stream_request_wrapper(payload):
        from fastapi.responses import StreamingResponse

        return StreamingResponse(mock_stream_request(payload, native=False, log_ctx=log_ctx), media_type="text/event-stream")

    processor = AntiTruncationStreamProcessor(
        stream_request_wrapper,
        anti_truncation_payload,
        max_attempts=5,
    )

    output_chunks = []
    async for chunk in processor.process_stream():
        output_chunks.append(chunk)

    print(f"upstream calls: {len(upstream_calls)}, output chunks: {len(output_chunks)}")
    assert len(upstream_calls) == 3, f"应有 3 次上游调用（1+2续传）, 实际 {len(upstream_calls)}"
    full_output = b"".join(output_chunks).decode("utf-8", errors="ignore")
    assert "第一段回答" in full_output and "第二段回答" in full_output and "最后一段" in full_output
    print("PASS: anti-truncation flow works, all 3 attempts' content delivered")

    # ========== 3. 日志验证：3 次上游调用 -> 3 条日志 ==========
    items, total = await database.query_usage_logs(limit=10)
    print(f"logged entries: {total}")
    assert total == 3, f"3 次上游调用应各记 1 条, 实际 {total}"
    for it in items:
        assert it["success"] is True
        assert it["model_name"] == "抗截断-gemini-2.5-pro"
        assert it["upstream_model_name"] == "gemini-2.5-pro"
        assert it["credential_email"] == "test@example.com"
        assert it["prompt_tokens"] == 50 and it["total_tokens"] == 80
    # 每个 attempt 有独立的 request_id（log_ctx 未传 request_id 时自动生成）
    request_ids = {it["request_id"] for it in items}
    assert len(request_ids) == 3, "每次上游调用应有独立 request_id"
    print("PASS: 3 log entries, one per upstream attempt, model names correct")

    # 清理
    await database.delete_usage_logs_before(99999999999)
    config._config_cache.clear()
    print("ALL ANTI-TRUNCATION LOGGING TESTS PASSED")


def test_anti_truncation_logging_module():
    asyncio.run(test_anti_truncation_logging())


if __name__ == "__main__":
    asyncio.run(test_anti_truncation_logging())
