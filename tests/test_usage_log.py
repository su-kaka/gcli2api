"""
请求日志（usage_log）端到端测试
- 表创建与写入
- extract_usage_tokens 提取（非流式 / 流式 / 包装）
- 查询 / 统计 / 清理
- 开关配置
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


async def test_usage_log_flow():
    # 环境干净
    os.environ.pop("USAGE_LOG_ENABLED", None)
    os.environ.pop("USAGE_LOG_RETENTION_DAYS", None)
    config._config_cache.clear()

    from src.storage.database import get_database
    from src.usage_log import record_usage_log, extract_usage_tokens, clear_usage_logs

    database = await get_database()

    # 1. 写入测试日志
    now = time.time()
    ok = await database.insert_usage_log({
        "created_at": now,
        "api_format": "openai",
        "client_ip": "127.0.0.1",
        "request_id": "test001",
        "model_name": "抗截断/claude-sonnet-4-5",
        "upstream_model_name": "claude-sonnet-4-5",
        "credential_email": "user1@example.com",
        "is_stream": True,
        "status_code": 200,
        "success": True,
        "use_time": 2.5,
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "error_message": "",
    })
    assert ok is True
    ok = await database.insert_usage_log({
        "api_format": "gemini",
        "model_name": "gemini-2.5-pro",
        "credential_email": "user2@example.com",
        "status_code": 429,
        "success": False,
        "use_time": 0.5,
        "error_message": "rate limited",
    })
    assert ok is True
    print("PASS insert")

    # 2. record_usage_log 走配置开关
    await record_usage_log({
        "api_format": "anthropic",
        "model_name": "claude-opus-4-5",
        "credential_email": "user3@example.com",
        "success": True,
        "status_code": 200,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    })
    # 关闭开关后不写入
    config._config_cache["usage_log_enabled"] = False
    before = len((await database.query_usage_logs(limit=1000))[0])
    await record_usage_log({
        "api_format": "openai", "model_name": "blocked-model", "success": True,
    })
    after = len((await database.query_usage_logs(limit=1000))[0])
    assert before == after, "关闭开关后不应写入"
    config._config_cache["usage_log_enabled"] = True
    print("PASS record_usage_log with toggle")

    # 3. 查询
    items, total = await database.query_usage_logs(limit=10)
    assert total >= 3
    assert items[0]["created_at"] >= items[-1]["created_at"], "应按新到旧排序"

    items, _ = await database.query_usage_logs(limit=10, status="error")
    assert all(i["success"] is False for i in items) and len(items) >= 1

    items, _ = await database.query_usage_logs(limit=10, model_name="抗截断/claude-sonnet-4-5")
    assert len(items) == 1 and items[0]["model_name"] == "抗截断/claude-sonnet-4-5"
    assert items[0]["upstream_model_name"] == "claude-sonnet-4-5"

    items, _ = await database.query_usage_logs(limit=10, api_format="anthropic")
    assert len(items) == 1 and items[0]["credential_email"] == "user3@example.com"

    items, _ = await database.query_usage_logs(limit=10, credential_email="user1@example.com")
    assert len(items) == 1 and items[0]["total_tokens"] == 150
    print("PASS query with filters")

    # 4. 统计
    stats = await database.usage_log_stats()
    assert stats["total_requests"] >= 3
    assert stats["success_count"] >= 2
    assert stats["error_count"] >= 1
    assert stats["total_tokens"] >= 165
    assert stats["avg_use_time"] > 0
    assert any(m["model_name"] == "抗截断/claude-sonnet-4-5" for m in stats["by_model"])
    print("PASS stats:", {k: v for k, v in stats.items() if k != "by_model"})

    # 5. extract_usage_tokens
    # 非流式
    t = extract_usage_tokens({"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 40, "thoughtsTokenCount": 10, "totalTokenCount": 150}})
    assert t == {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}, t
    # 包装 response
    t = extract_usage_tokens({"response": {"usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 5}}})
    assert t["total_tokens"] == 25, t
    # 流式 SSE 块
    t = extract_usage_tokens('data: {"usageMetadata": {"promptTokenCount": 30, "candidatesTokenCount": 7}}')
    assert t["total_tokens"] == 37, t
    # 无 usage
    assert extract_usage_tokens({"candidates": []}) == {}
    assert extract_usage_tokens("data: [DONE]") == {}
    assert extract_usage_tokens("not json") == {}
    print("PASS extract_usage_tokens")

    # 6. 清理（删除 1 天前）
    # 写一条旧日志
    await database.insert_usage_log({
        "created_at": now - 86400 * 10,
        "api_format": "openai", "model_name": "old-model", "success": True,
    })
    deleted = await clear_usage_logs(1)
    assert deleted >= 1
    items, _ = await database.query_usage_logs(limit=100, model_name="old-model")
    assert len(items) == 0
    print("PASS clear:", deleted)

    # 7. 清空测试数据
    deleted = await clear_usage_logs(0)
    items, total = await database.query_usage_logs(limit=100)
    assert total == 0, f"应已清空, 剩余 {total}"
    print("PASS cleanup all:", deleted)

    # 8. 配置默认值
    assert await config.get_usage_log_enabled() is True
    assert await config.get_usage_log_retention_days() == 30
    config._config_cache["usage_log_retention_days"] = 0
    assert await config.get_usage_log_retention_days() == 0
    config._config_cache.clear()
    print("PASS config defaults")

    print("ALL USAGE LOG TESTS PASSED")


def test_usage_log_module():
    asyncio.run(test_usage_log_flow())


if __name__ == "__main__":
    asyncio.run(test_usage_log_flow())
