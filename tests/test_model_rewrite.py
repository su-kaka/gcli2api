"""
模型名复写测试
- 规则解析与编译: [pattern, replacement], 正则校验, 无效规则跳过
- rewrite_model_name: 首条命中即替换, 全字符串搜索, 反向引用
- 配置读取: 环境变量 > 数据库配置 > 默认空; 开关默认关闭
- normalize_antigravity_request 端到端: 复写后走对应 profile
- 面板保存校验: 非法规则 400
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config


async def test_compile_and_rewrite():
    # ===== 1. 编译 =====
    compiled, invalid = config.compile_model_rewrite_rules([
        ["^gpt-4$", "gemini-2.5-pro"],
        ["^claude-3-5-sonnet.*$", "claude-sonnet-4-5"],
        ["(bad", "never-matches"],  # 无效正则
        ["only-pattern"],  # 结构错误 -> 跳过
    ])
    assert len(compiled) == 2, compiled
    assert invalid == [["(bad", "never-matches"]]

    # ===== 2. 复写 =====
    # 精确匹配
    assert config.rewrite_model_name("gpt-4", compiled) == "gemini-2.5-pro"
    # 正则模糊匹配
    assert config.rewrite_model_name("claude-3-5-sonnet-20241022", compiled) == "claude-sonnet-4-5"
    # 未命中 -> 原样返回
    assert config.rewrite_model_name("gemini-2.5-pro", compiled) == "gemini-2.5-pro"
    assert config.rewrite_model_name("", compiled) == ""

    # 反向引用: (fast|flash) 捕获组拼进替换
    compiled2, _ = config.compile_model_rewrite_rules([
        [r"^my-(.+)$", r"gemini-\1"],
    ])
    assert config.rewrite_model_name("my-2.5-pro", compiled2) == "gemini-2.5-pro"
    assert config.rewrite_model_name("my-3-flash", compiled2) == "gemini-3-flash"

    # 首条命中即停止（不继续应用后续规则）；re.sub 语义：只替换命中部分
    compiled3, _ = config.compile_model_rewrite_rules([
        ["^a", "first"],
        ["^ab$", "second"],
    ])
    out = config.rewrite_model_name("ab", compiled3)
    assert out == "firstb", out  # ^a 命中替换 "a" -> "first"，剩余 "b" 保留，第二条不再应用
    print("PASS compile & rewrite_model_name")

    # ===== 3. 配置读取与开关 =====
    os.environ.pop("MODEL_REWRITE_ENABLED", None)
    os.environ.pop("MODEL_REWRITE_RULES", None)
    config._config_cache.clear()

    # 默认关闭
    assert await config.get_model_rewrite_enabled() is False
    enabled, rules = await config.get_model_rewrite_filter()
    assert enabled is False and rules == []

    # 开关关闭 + 有规则 -> 不启用
    config._config_cache["model_rewrite_rules"] = [["^gpt-4$", "gemini-2.5-pro"]]
    enabled, rules = await config.get_model_rewrite_filter()
    assert enabled is False

    # 开启 + 有规则 -> 生效
    config._config_cache["model_rewrite_enabled"] = True
    enabled, rules = await config.get_model_rewrite_filter()
    assert enabled is True and len(rules) == 1

    # 开启 + 空规则 -> 不启用
    config._config_cache["model_rewrite_rules"] = []
    enabled, rules = await config.get_model_rewrite_filter()
    assert enabled is False

    # 全部规则无效 -> 不启用
    config._config_cache["model_rewrite_rules"] = [["(bad", "x"]]
    enabled, rules = await config.get_model_rewrite_filter()
    assert enabled is False

    # 环境变量 JSON 数组格式
    os.environ["MODEL_REWRITE_ENABLED"] = "true"
    os.environ["MODEL_REWRITE_RULES"] = '[["^gpt-4$","gemini-2.5-pro"]]'
    config._config_cache["model_rewrite_enabled"] = False
    entries = await config.get_model_rewrite_rules_config()
    assert entries == [["^gpt-4$", "gemini-2.5-pro"]], entries
    enabled, rules = await config.get_model_rewrite_filter()
    assert enabled is True and rules[0][1] == "gemini-2.5-pro"

    # 环境变量简化格式 "pat->rep,pat->rep"
    os.environ["MODEL_REWRITE_RULES"] = "^gpt-4$->gemini-2.5-pro, ^o1$->gemini-2.5-flash"
    entries = await config.get_model_rewrite_rules_config()
    assert entries == [["^gpt-4$", "gemini-2.5-pro"], ["^o1$", "gemini-2.5-flash"]], entries

    # 数据库 dict 格式兼容
    os.environ.pop("MODEL_REWRITE_ENABLED", None)
    os.environ.pop("MODEL_REWRITE_RULES", None)
    config._config_cache["model_rewrite_rules"] = [
        {"pattern": "^gpt-4$", "replacement": "gemini-2.5-pro"}
    ]
    entries = await config.get_model_rewrite_rules_config()
    assert entries == [["^gpt-4$", "gemini-2.5-pro"]], entries
    config._config_cache.clear()
    print("PASS config priority & toggles")


async def test_normalize_request_rewrite():
    """复写在 normalize_antigravity_request 中端到端生效"""
    from src.converter.antigravity_fix import normalize_antigravity_request

    os.environ.pop("MODEL_REWRITE_ENABLED", None)
    os.environ.pop("MODEL_REWRITE_RULES", None)

    # 基础请求体（OpenAI -> Gemini 转换后的典型形态）
    base_request = {
        "model": "gpt-4o",
        "contents": [{"role": "user", "parts": [{"text": "hi"}]}],
    }

    # 未启用 -> 模型名原样
    config._config_cache.clear()
    result = await normalize_antigravity_request(dict(base_request))
    assert result["model"] == "gpt-4o"

    # 启用复写: gpt-4o -> gemini-2.5-pro
    config._config_cache["model_rewrite_enabled"] = True
    config._config_cache["model_rewrite_rules"] = [["^gpt-4o$", "gemini-2.5-pro"]]
    result = await normalize_antigravity_request(dict(base_request))
    assert result["model"] == "gemini-2.5-pro", result["model"]

    # 未命中 -> 原样
    result = await normalize_antigravity_request({**base_request, "model": "claude-sonnet-4-6"})
    assert result["model"] == "claude-sonnet-4-6"

    # 复写后走对应 profile: 映射到 claude 模型 -> upstream 保持
    config._config_cache["model_rewrite_rules"] = [["^my-claude$", "claude-sonnet-4-6"]]
    result = await normalize_antigravity_request({**base_request, "model": "my-claude"})
    assert result["model"] == "claude-sonnet-4-6"

    # 复写后的模型进入 image 家族路径
    config._config_cache["model_rewrite_rules"] = [["^my-image$", "gemini-3.1-flash-image"]]
    result = await normalize_antigravity_request({
        **base_request,
        "model": "my-image",
        "size": "1024x1024",
    })
    # 图片路径统一使用 IMAGE_BASE_MODEL
    assert result["model"] == "gemini-3.1-flash-image"

    config._config_cache.clear()
    print("PASS normalize_antigravity_request rewrite")


async def test_save_config_validation():
    """面板保存配置对 model_rewrite_rules 的校验（真实数据库，结束后清理）"""
    from fastapi import HTTPException

    from src.models import ConfigSaveRequest
    from src.panel.config_routes import save_config
    from src.storage.database import get_database

    # 有效规则（含空串条目 -> 校验时无空白，此处应保存成功）
    req = ConfigSaveRequest(config={
        "model_rewrite_enabled": True,
        "model_rewrite_rules": [["^gpt-4$", "gemini-2.5-pro"]],
    })
    resp = await save_config(req)
    assert resp.status_code == 200
    saved = await config.get_model_rewrite_rules_config()
    assert saved == [["^gpt-4$", "gemini-2.5-pro"]], saved
    assert await config.get_model_rewrite_enabled() is True

    # 无效正则 -> 400
    try:
        bad = ConfigSaveRequest(config={"model_rewrite_rules": [["(bad", "x"]]})
        await save_config(bad)
        raise AssertionError("无效正则应返回 400")
    except HTTPException as e:
        assert e.status_code == 400

    # 规则结构错误 -> 400
    try:
        bad = ConfigSaveRequest(config={"model_rewrite_rules": [["only-pattern"]]})
        await save_config(bad)
        raise AssertionError("结构错误应返回 400")
    except HTTPException as e:
        assert e.status_code == 400

    # 清理测试数据
    database = await get_database()
    await database.delete_config("model_rewrite_rules")
    await database.delete_config("model_rewrite_enabled")
    config._config_cache.pop("model_rewrite_rules", None)
    config._config_cache.pop("model_rewrite_enabled", None)
    print("PASS save_config validation")


async def test_model_rewrite_flow():
    await test_compile_and_rewrite()
    await test_normalize_request_rewrite()
    await test_save_config_validation()
    print("ALL MODEL REWRITE TESTS PASSED")


def test_model_rewrite_module():
    asyncio.run(test_model_rewrite_flow())


if __name__ == "__main__":
    asyncio.run(test_model_rewrite_flow())
