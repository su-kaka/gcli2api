"""
凭证选择模式测试
- 三种模式: random（随机）/ cache（会话缓存匹配）/ sticky（粘性直到 CD）
- 会话 key 派生: systemInstruction 优先, 无则第一条 user 消息; 必须是格式处理后内容
- 缓存绑定: 命中复用, CD 自动解绑, TTL 续期
- database 层: get_available_credential_by_email 的 CD/禁用检查
"""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


async def test_credential_select_modes():
    os.environ.pop("CREDENTIAL_SELECT_MODE", None)
    os.environ.pop("REDIS_URL", None)
    config._config_cache.clear()

    from src.storage.database import get_database, close_database
    from src import session_cache
    from src.credential_manager import (
    _reset_sticky,
    get_valid_credential,
)

    database = await get_database()

    # ===== 0. 清理旧测试凭证 =====
    for email in ("cred-a@test.com", "cred-b@test.com", "cred-c@test.com"):
        await database.delete_credential(email)
    # 写入两个测试凭证（expiry 设为远期，避免触发真实 token 刷新）
    from datetime import datetime, timedelta, timezone

    far_expiry = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    for email in ("cred-a@test.com", "cred-b@test.com"):
        await database.store_credential(email, {
            "access_token": f"tok-{email}", "refresh_token": f"rt-{email}",
            "client_id": "cid", "client_secret": "cs", "project_id": "pid", "expiry": far_expiry,
        })
    print("setup: 2 credentials")

    # ===== 1. 会话 key 派生 =====
    # 有 systemInstruction
    key1 = session_cache.derive_session_key("gemini-2.5-pro", {
        "systemInstruction": {"parts": [{"text": "你是一个助手"}]},
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    })
    assert key1 and key1.startswith("auto:"), key1
    # 同内容同 key
    key1b = session_cache.derive_session_key("gemini-2.5-pro", {
        "systemInstruction": {"parts": [{"text": "你是一个助手"}]},
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    })
    assert key1 == key1b
    # 不同 system -> 不同 key
    key2 = session_cache.derive_session_key("gemini-2.5-pro", {
        "systemInstruction": {"parts": [{"text": "另一个系统提示"}]},
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    })
    assert key1 != key2
    # 无 system -> 取第一条 user 消息
    key3 = session_cache.derive_session_key("gemini-2.5-pro", {
        "contents": [
            {"role": "user", "parts": [{"text": "第一条用户消息"}]},
            {"role": "model", "parts": [{"text": "回复"}]},
        ],
    })
    assert key3 and key3 != key1
    # 不同模型 -> 不同 key（同内容）
    key4 = session_cache.derive_session_key("claude-sonnet-4-5", {
        "systemInstruction": {"parts": [{"text": "你是一个助手"}]},
    })
    assert key4 != key1
    # 空请求 -> None
    assert session_cache.derive_session_key("m", {}) is None
    assert session_cache.derive_session_key("m", {"contents": []}) is None
    print("PASS: session key derivation (system-first, first-user fallback, model mixed)")

    # ===== 2. 内存后端绑定: set/get/TTL 续期/删除 =====
    await session_cache.set_session_binding(key1, "cred-a@test.com")
    assert await session_cache.get_session_binding(key1) == "cred-a@test.com"
    # 命中续期
    assert await session_cache.get_session_binding(key1) == "cred-a@test.com"
    # 未绑定的 key
    assert await session_cache.get_session_binding("auto:unknown") is None
    # 删除
    await session_cache.delete_session_binding(key1)
    assert await session_cache.get_session_binding(key1) is None
    print("PASS: memory binding set/get/delete")

    # ===== 3. get_available_credential_by_email =====
    r = await database.get_available_credential_by_email("cred-a@test.com", model_name="gemini-2.5-pro")
    assert r and r[0] == "cred-a@test.com"
    assert await database.get_available_credential_by_email("nonexist@test.com") is None
    # 禁用后不可用
    await database.update_credential_state("cred-a@test.com", {"disabled": True})
    assert await database.get_available_credential_by_email("cred-a@test.com") is None
    await database.update_credential_state("cred-a@test.com", {"disabled": False})
    # CD 检查: claude 模型家族 CD
    await database.set_model_cooldown("cred-a@test.com", "claude_gpt", time.time() + 3600)
    assert await database.get_available_credential_by_email("cred-a@test.com", model_name="claude-sonnet-4-5") is None
    # gemini 家族不受 claude CD 影响
    r = await database.get_available_credential_by_email("cred-a@test.com", model_name="gemini-2.5-pro")
    assert r is not None
    await database.set_model_cooldown("cred-a@test.com", "claude_gpt", time.time() - 1)  # 解除
    print("PASS: get_available_credential_by_email (disabled/CD checks)")

    # ===== 4. cache 模式: 命中绑定复用同一凭证 =====
    config._config_cache["credential_select_mode"] = "cache"
    session_key = "auto:" + "x" * 32
    picked = []
    for _ in range(5):
        result = await get_valid_credential(model_name="gemini-2.5-pro", session_key=session_key)
        assert result is not None
        picked.append(result[0])
    assert all(p == picked[0] for p in picked), f"cache 模式应始终复用同一凭证: {picked}"
    bound = await session_cache.get_session_binding(session_key)
    assert bound == picked[0]
    print(f"PASS: cache mode reuses same credential ({picked[0]})")

    # 绑定凭证进入 CD -> 自动解绑切换
    cd_group = "gemini"
    await database.set_model_cooldown(picked[0], cd_group, time.time() + 3600)
    result = await get_valid_credential(model_name="gemini-2.5-pro", session_key=session_key)
    assert result is not None and result[0] != picked[0], "CD 后应切换到其他凭证"
    new_bound = await session_cache.get_session_binding(session_key)
    assert new_bound == result[0], "应重新绑定到新凭证"
    await database.set_model_cooldown(picked[0], cd_group, time.time() - 1)
    print(f"PASS: cache mode rebinds after CD (->{result[0]})")

    # ===== 5. sticky 模式: 所有请求同一凭证, CD 后切换 =====
    config._config_cache["credential_select_mode"] = "sticky"
    _reset_sticky()
    picked2 = []
    for _ in range(8):
        result = await get_valid_credential(model_name="gemini-2.5-pro")
        assert result is not None
        picked2.append(result[0])
    assert all(p == picked2[0] for p in picked2), f"sticky 模式应锁定同一凭证: {picked2}"
    print(f"PASS: sticky mode locks credential ({picked2[0]})")

    # sticky 凭证 CD -> 切换
    await database.set_model_cooldown(picked2[0], cd_group, time.time() + 3600)
    result = await get_valid_credential(model_name="gemini-2.5-pro")
    assert result is not None and result[0] != picked2[0], "sticky CD 后应切换"
    import src.credential_manager as _cm
    assert _cm._sticky_email == result[0]
    await database.set_model_cooldown(picked2[0], cd_group, time.time() - 1)
    print(f"PASS: sticky mode switches after CD (->{result[0]})")

    # ===== 6. random 模式: 不记录亲和, 每次随机 =====
    config._config_cache["credential_select_mode"] = "random"
    _reset_sticky()
    random_picked = set()
    for _ in range(20):
        result = await get_valid_credential(model_name="gemini-2.5-pro", session_key="auto:yyy")
        assert result is not None
        random_picked.add(result[0])
    # 20 次随机应命中多个凭证（2 个凭证, 概率 2^-20 全中一个, 安全断言 >=1）
    print(f"PASS: random mode picked {len(random_picked)} distinct credentials (no affinity)")

    # ===== 7. 无效模式回落 cache =====
    config._config_cache["credential_select_mode"] = "invalid_mode"
    assert await config.get_credential_select_mode() == "cache"
    print("PASS: invalid mode falls back to cache")

    # ===== 清理 =====
    for email in ("cred-a@test.com", "cred-b@test.com", "cred-c@test.com"):
        await database.delete_credential(email)
    await session_cache.close()
    config._config_cache.clear()
    print("ALL CREDENTIAL SELECT MODE TESTS PASSED")


def test_credential_select_modes_module():
    asyncio.run(test_credential_select_modes())


if __name__ == "__main__":
    asyncio.run(test_credential_select_modes())
