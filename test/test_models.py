"""
测试 Pydantic 数据模型
"""

import pytest
from src.models import (
    ChatCompletionRequest,
    Model,
    ModelList,
    OpenAIChatMessage,
    GeminiContent,
    GeminiPart,
    GeminiGenerationConfig,
    model_to_dict,
)


class TestOpenAIModels:
    """OpenAI 格式模型测试"""

    def test_chat_message_basic(self):
        """测试基本消息创建"""
        msg = OpenAIChatMessage(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"

    def test_chat_message_with_multimodal_content(self):
        """测试多模态内容"""
        content = [
            {"type": "text", "text": "What's in this image?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
        ]
        msg = OpenAIChatMessage(role="user", content=content)
        assert msg.role == "user"
        assert isinstance(msg.content, list)
        assert len(msg.content) == 2

    def test_chat_completion_request_minimal(self):
        """测试最小请求"""
        request = ChatCompletionRequest(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": "Hi"}],
        )
        assert request.model == "gemini-2.5-pro"
        assert len(request.messages) == 1
        assert request.stream is False

    def test_chat_completion_request_with_options(self):
        """测试带参数的请求"""
        request = ChatCompletionRequest(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": "Hi"}],
            stream=True,
            temperature=0.7,
            max_tokens=1000,
            top_p=0.9,
        )
        assert request.stream is True
        assert request.temperature == 0.7
        assert request.max_tokens == 1000
        assert request.top_p == 0.9

    def test_chat_completion_request_temperature_bounds(self):
        """测试温度参数边界"""
        # 有效值
        request = ChatCompletionRequest(
            model="test",
            messages=[{"role": "user", "content": "Hi"}],
            temperature=0.0,
        )
        assert request.temperature == 0.0

        request = ChatCompletionRequest(
            model="test",
            messages=[{"role": "user", "content": "Hi"}],
            temperature=2.0,
        )
        assert request.temperature == 2.0

    def test_model_list(self):
        """测试模型列表"""
        models = ModelList(
            data=[
                Model(id="gemini-2.5-pro"),
                Model(id="gemini-2.5-flash"),
            ]
        )
        assert len(models.data) == 2
        assert models.data[0].id == "gemini-2.5-pro"
        assert models.object == "list"


class TestGeminiModels:
    """Gemini 格式模型测试"""

    def test_gemini_part_text(self):
        """测试文本 Part"""
        part = GeminiPart(text="Hello world")
        assert part.text == "Hello world"
        assert part.thought is False

    def test_gemini_part_thought(self):
        """测试思考 Part"""
        part = GeminiPart(text="Let me think...", thought=True)
        assert part.thought is True

    def test_gemini_content(self):
        """测试 Content 结构"""
        content = GeminiContent(
            role="user",
            parts=[GeminiPart(text="Hello")],
        )
        assert content.role == "user"
        assert len(content.parts) == 1

    def test_gemini_generation_config(self):
        """测试生成配置"""
        config = GeminiGenerationConfig(
            temperature=0.8,
            topP=0.95,
            topK=40,
            maxOutputTokens=2048,
        )
        assert config.temperature == 0.8
        assert config.topP == 0.95
        assert config.topK == 40
        assert config.maxOutputTokens == 2048

    def test_gemini_generation_config_with_thinking(self):
        """测试带思考配置"""
        config = GeminiGenerationConfig(
            temperature=1.0,
            thinkingConfig={"thinkingBudget": 10000},
        )
        assert config.thinkingConfig is not None
        assert config.thinkingConfig["thinkingBudget"] == 10000


class TestModelToDict:
    """测试模型转字典兼容函数"""

    def test_model_to_dict_basic(self):
        """测试基本转换"""
        msg = OpenAIChatMessage(role="user", content="Hello")
        result = model_to_dict(msg)

        assert isinstance(result, dict)
        assert result["role"] == "user"
        assert result["content"] == "Hello"

    def test_model_to_dict_nested(self):
        """测试嵌套模型转换"""
        request = ChatCompletionRequest(
            model="test",
            messages=[{"role": "user", "content": "Hi"}],
        )
        result = model_to_dict(request)

        assert isinstance(result, dict)
        assert result["model"] == "test"
        assert isinstance(result["messages"], list)
