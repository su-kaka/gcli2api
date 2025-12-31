"""
测试工具函数
"""

import pytest
from src.utils import (
    get_base_model_name,
    get_base_model_from_feature_model,
    is_search_model,
    is_nothinking_model,
    is_maxthinking_model,
    is_fake_streaming_model,
    is_anti_truncation_model,
    get_thinking_budget,
    should_include_thoughts,
    get_available_models,
    get_model_group,
    get_user_agent,
    parse_quota_reset_timestamp,
)


class TestModelNameParsing:
    """模型名称解析测试"""

    def test_get_base_model_name_simple(self):
        """测试简单模型名"""
        assert get_base_model_name("gemini-2.5-pro") == "gemini-2.5-pro"
        assert get_base_model_name("gemini-2.5-flash") == "gemini-2.5-flash"

    def test_get_base_model_name_with_suffix(self):
        """测试带后缀的模型名"""
        assert get_base_model_name("gemini-2.5-pro-maxthinking") == "gemini-2.5-pro"
        assert get_base_model_name("gemini-2.5-pro-nothinking") == "gemini-2.5-pro"
        assert get_base_model_name("gemini-2.5-pro-search") == "gemini-2.5-pro"

    def test_get_base_model_name_with_multiple_suffixes(self):
        """测试多后缀组合"""
        assert get_base_model_name("gemini-2.5-pro-maxthinking-search") == "gemini-2.5-pro"
        assert get_base_model_name("gemini-2.5-pro-nothinking-search") == "gemini-2.5-pro"

    def test_get_base_model_from_feature_model_no_prefix(self):
        """测试无前缀模型名"""
        assert get_base_model_from_feature_model("gemini-2.5-pro") == "gemini-2.5-pro"

    def test_get_base_model_from_feature_model_with_prefix(self):
        """测试带功能前缀的模型名"""
        assert get_base_model_from_feature_model("假流式/gemini-2.5-pro") == "gemini-2.5-pro"
        assert get_base_model_from_feature_model("流式抗截断/gemini-2.5-pro") == "gemini-2.5-pro"


class TestModelFeatureDetection:
    """模型功能检测测试"""

    def test_is_search_model(self):
        """测试搜索模型检测"""
        assert is_search_model("gemini-2.5-pro-search") is True
        assert is_search_model("gemini-2.5-pro-maxthinking-search") is True
        assert is_search_model("gemini-2.5-pro") is False

    def test_is_nothinking_model(self):
        """测试无思考模型检测"""
        assert is_nothinking_model("gemini-2.5-pro-nothinking") is True
        assert is_nothinking_model("gemini-2.5-pro") is False
        assert is_nothinking_model("gemini-2.5-pro-maxthinking") is False

    def test_is_maxthinking_model(self):
        """测试最大思考模型检测"""
        assert is_maxthinking_model("gemini-2.5-pro-maxthinking") is True
        assert is_maxthinking_model("gemini-2.5-pro") is False
        assert is_maxthinking_model("gemini-2.5-pro-nothinking") is False

    def test_is_fake_streaming_model(self):
        """测试假流式模型检测"""
        assert is_fake_streaming_model("假流式/gemini-2.5-pro") is True
        assert is_fake_streaming_model("gemini-2.5-pro") is False
        assert is_fake_streaming_model("流式抗截断/gemini-2.5-pro") is False

    def test_is_anti_truncation_model(self):
        """测试抗截断模型检测"""
        assert is_anti_truncation_model("流式抗截断/gemini-2.5-pro") is True
        assert is_anti_truncation_model("gemini-2.5-pro") is False
        assert is_anti_truncation_model("假流式/gemini-2.5-pro") is False


class TestThinkingBudget:
    """思考预算测试"""

    def test_get_thinking_budget_nothinking(self):
        """测试无思考模式预算"""
        assert get_thinking_budget("gemini-2.5-pro-nothinking") == 128

    def test_get_thinking_budget_maxthinking_pro(self):
        """测试 Pro 模型最大思考预算"""
        assert get_thinking_budget("gemini-2.5-pro-maxthinking") == 32768

    def test_get_thinking_budget_maxthinking_flash(self):
        """测试 Flash 模型最大思考预算"""
        assert get_thinking_budget("gemini-2.5-flash-maxthinking") == 24576

    def test_get_thinking_budget_default(self):
        """测试默认思考预算"""
        assert get_thinking_budget("gemini-2.5-pro") is None

    def test_should_include_thoughts(self):
        """测试是否包含思考内容"""
        assert should_include_thoughts("gemini-2.5-pro") is True
        assert should_include_thoughts("gemini-2.5-pro-maxthinking") is True
        # nothinking 模式下，pro 模型仍然包含思考
        assert should_include_thoughts("gemini-2.5-pro-nothinking") is True
        # nothinking 模式下，flash 模型不包含思考
        assert should_include_thoughts("gemini-2.5-flash-nothinking") is False


class TestModelGroup:
    """模型分组测试"""

    def test_get_model_group_pro(self):
        """测试 Pro 模型分组"""
        assert get_model_group("gemini-2.5-pro") == "pro"
        assert get_model_group("gemini-3-pro-preview") == "pro"
        assert get_model_group("gemini-2.5-pro-maxthinking") == "pro"

    def test_get_model_group_flash(self):
        """测试 Flash 模型分组"""
        assert get_model_group("gemini-2.5-flash") == "flash"
        assert get_model_group("gemini-3-flash-preview") == "flash"
        assert get_model_group("假流式/gemini-2.5-flash") == "flash"


class TestAvailableModels:
    """可用模型列表测试"""

    def test_get_available_models_contains_base(self):
        """测试包含基础模型"""
        models = get_available_models()
        assert "gemini-2.5-pro" in models
        assert "gemini-2.5-flash" in models

    def test_get_available_models_contains_variants(self):
        """测试包含变体模型"""
        models = get_available_models()
        assert "gemini-2.5-pro-maxthinking" in models
        assert "gemini-2.5-pro-nothinking" in models
        assert "gemini-2.5-pro-search" in models

    def test_get_available_models_contains_prefixed(self):
        """测试包含前缀模型"""
        models = get_available_models()
        assert "假流式/gemini-2.5-pro" in models
        assert "流式抗截断/gemini-2.5-pro" in models


class TestUserAgent:
    """User-Agent 测试"""

    def test_get_user_agent_format(self):
        """测试 User-Agent 格式"""
        ua = get_user_agent()
        assert ua.startswith("GeminiCLI/")
        assert "(" in ua and ")" in ua


class TestQuotaResetParsing:
    """配额重置时间解析测试"""

    def test_parse_quota_reset_timestamp_valid(self):
        """测试有效的配额重置响应"""
        error_response = {
            "error": {
                "code": 429,
                "message": "You have exhausted your capacity...",
                "status": "RESOURCE_EXHAUSTED",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
                        "reason": "QUOTA_EXHAUSTED",
                        "metadata": {
                            "quotaResetTimeStamp": "2025-11-30T14:57:24Z",
                            "quotaResetDelay": "13h19m1.20964964s"
                        }
                    }
                ]
            }
        }
        result = parse_quota_reset_timestamp(error_response)
        assert result is not None
        assert isinstance(result, float)

    def test_parse_quota_reset_timestamp_missing_details(self):
        """测试缺少 details 的响应"""
        error_response = {
            "error": {
                "code": 429,
                "message": "Rate limited"
            }
        }
        result = parse_quota_reset_timestamp(error_response)
        assert result is None

    def test_parse_quota_reset_timestamp_empty(self):
        """测试空响应"""
        result = parse_quota_reset_timestamp({})
        assert result is None

    def test_parse_quota_reset_timestamp_wrong_type(self):
        """测试错误的 detail 类型"""
        error_response = {
            "error": {
                "details": [
                    {
                        "@type": "some.other.type",
                        "metadata": {}
                    }
                ]
            }
        }
        result = parse_quota_reset_timestamp(error_response)
        assert result is None
