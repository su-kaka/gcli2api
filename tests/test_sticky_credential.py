"""
会话粘性选号功能测试
- sqlite 后端 get_next_available_credential 的 preferred_filename 优先排序
- StickySessionStore 的绑定 / TTL 过期 / 容量淘汰

运行: python tests/test_sticky_credential.py
"""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aiosqlite

from src.storage.sqlite_manager import SQLiteManager
from src.credential_manager import StickySessionStore

MODEL = "gemini-3-pro-preview"


async def test_sqlite_sticky():
    tmpdir = tempfile.mkdtemp()
    os.environ["CREDENTIALS_DIR"] = tmpdir
    db = SQLiteManager()
    await db.initialize()

    for i in range(3):
        await db.store_credential(
            f"cred{i}.json",
            {"access_token": f"tok{i}", "project_id": f"p{i}"},
            mode="antigravity",
        )
    await db.store_credential(
        "gcli0.json",
        {"access_token": "gtok0", "project_id": "gp0"},
        mode="geminicli",
    )

    # 1) 无偏好：随机返回可用凭证（多次应覆盖全部）
    picks = set()
    for _ in range(40):
        name, _data = await db.get_next_available_credential(mode="antigravity", model_name=MODEL)
        picks.add(name)
    assert picks == {"cred0.json", "cred1.json", "cred2.json"}, picks
    print("PASS 无偏好随机覆盖全部可用凭证:", sorted(picks))

    # 2) 有偏好：优先返回 preferred
    for _ in range(20):
        name, _data = await db.get_next_available_credential(
            mode="antigravity", model_name=MODEL, preferred_filename="cred1.json"
        )
        assert name == "cred1.json", name
    print("PASS 粘性优先命中")

    # 3) preferred 被禁用：回退到其他凭证（不绕过过滤）
    async with aiosqlite.connect(db._db_path) as raw:
        await raw.execute("UPDATE antigravity_credentials SET disabled=1 WHERE filename='cred1.json'")
        await raw.commit()
    name, _data = await db.get_next_available_credential(
        mode="antigravity", model_name=MODEL, preferred_filename="cred1.json"
    )
    assert name in {"cred0.json", "cred2.json"}, name
    print("PASS 粘性号被禁用时回退:", name)

    # 4) preferred 模型冷却中：回退
    async with aiosqlite.connect(db._db_path) as raw:
        await raw.execute("UPDATE antigravity_credentials SET disabled=0 WHERE filename='cred1.json'")
        await raw.execute(
            "UPDATE antigravity_credentials SET model_cooldowns=? WHERE filename='cred1.json'",
            ('{"%s": 9999999999}' % MODEL,),
        )
        await raw.commit()
    name, _data = await db.get_next_available_credential(
        mode="antigravity", model_name=MODEL, preferred_filename="cred1.json"
    )
    assert name in {"cred0.json", "cred2.json"}, name
    print("PASS 粘性号冷却中回退:", name)

    # 5) 不带 model_name 的查询同样支持粘性（antigravity 与 geminicli 分支）
    name, _data = await db.get_next_available_credential(
        mode="antigravity", preferred_filename="cred0.json"
    )
    assert name == "cred0.json", name
    name, _data = await db.get_next_available_credential(
        mode="geminicli", preferred_filename="gcli0.json"
    )
    assert name == "gcli0.json", name
    print("PASS 无模型名查询粘性优先 (antigravity/geminicli)")


async def test_sticky_store():
    store = StickySessionStore(ttl_seconds=0.1, max_entries=3)

    await store.bind("s1", "a.json")
    assert await store.get("s1") == "a.json"

    await asyncio.sleep(0.15)
    assert await store.get("s1") is None
    print("PASS TTL 过期")

    for i in range(5):
        await store.bind(f"k{i}", f"c{i}.json")
    assert len(store._bindings) <= 3
    print("PASS 容量上限淘汰")

    # 空 key 不产生绑定
    await store.bind("", "x.json")
    assert await store.get("") is None
    print("PASS StickySessionStore")


async def main():
    await test_sqlite_sticky()
    await test_sticky_store()
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(main())
