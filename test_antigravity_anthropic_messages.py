import json

import pytest

from src.anthropic_converter import (
    clean_json_schema,
    convert_anthropic_request_to_antigravity_components,
    convert_messages_to_contents,
    map_claude_model_to_gemini,
    reorganize_tool_messages,
)
from src.anthropic_streaming import antigravity_sse_to_anthropic_sse
from src.antigravity_anthropic_router import _convert_antigravity_response_to_anthropic_message


def test_clean_json_schema_会追加校验信息到描述():
    schema = {
        "type": "object",
        "properties": {
            "q": {
                "type": "string",
                "description": "查询词",
                "minLength": 2,
                "maxLength": 5,
            }
        },
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "additionalProperties": False,
    }

    cleaned = clean_json_schema(schema)
    assert "$schema" not in cleaned
    assert "additionalProperties" not in cleaned

    desc = cleaned["properties"]["q"]["description"]
    assert "minLength: 2" in desc
    assert "maxLength: 5" in desc
    assert "minLength" not in cleaned["properties"]["q"]
    assert "maxLength" not in cleaned["properties"]["q"]


def test_convert_messages_to_contents_支持多种内容块():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "你好"},
                {"type": "thinking", "thinking": "思考中", "signature": "sig1"},
                # 缺少 signature 的 thinking 应被丢弃（否则下游可能报 thinking.signature 必填）
                {"type": "thinking", "thinking": "无签名思考"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": "AAAA",
                    },
                },
                {"type": "tool_use", "id": "t1", "name": "search", "input": {"q": "a"}},
                {"type": "tool_result", "tool_use_id": "t1", "content": [{"type": "text", "text": "ok"}]},
            ],
        },
        {"role": "assistant", "content": "收到"},
    ]

    contents = convert_messages_to_contents(messages)
    assert contents[0]["role"] == "user"
    parts = contents[0]["parts"]

    assert parts[0] == {"text": "你好"}
    assert parts[1]["thought"] is True
    assert parts[1]["text"] == "思考中"
    assert parts[1]["thoughtSignature"] == "sig1"
    assert parts[2]["inlineData"]["mimeType"] == "image/png"
    assert parts[2]["inlineData"]["data"] == "AAAA"
    assert parts[3]["functionCall"]["id"] == "t1"
    assert parts[3]["functionCall"]["name"] == "search"
    assert parts[3]["functionCall"]["args"] == {"q": "a"}
    assert parts[4]["functionResponse"]["id"] == "t1"
    assert parts[4]["functionResponse"]["response"]["output"] == "ok"

    assert contents[1]["role"] == "model"
    assert contents[1]["parts"] == [{"text": "收到"}]


def test_convert_request_components_模型映射对齐_converter_py():
    payload = {"model": "claude-3-5-sonnet-20241022", "max_tokens": 8, "messages": []}
    components = convert_anthropic_request_to_antigravity_components(payload)
    assert components["model"] == "claude-sonnet-4-5"
    assert "thinkingConfig" not in components["generation_config"]


def test_reorganize_tool_messages_会把_tool_result_移动到_tool_use_之后():
    contents = [
        {"role": "user", "parts": [{"text": "hi"}]},
        {"role": "model", "parts": [{"functionCall": {"id": "t1", "name": "tool", "args": {"x": 1}}}]},
        {"role": "model", "parts": [{"text": "（中间插入的assistant文本）"}]},
        {"role": "user", "parts": [{"functionResponse": {"id": "t1", "name": "tool", "response": {"output": "ok"}}}]},
    ]

    new_contents = reorganize_tool_messages(contents)
    # 期望 tool_result 紧跟 tool_use
    assert new_contents[1]["parts"][0].get("functionCall", {}).get("id") == "t1"
    assert new_contents[2]["parts"][0].get("functionResponse", {}).get("id") == "t1"


def test_model_mapping_支持_claude_cli_版本化模型名():
    assert map_claude_model_to_gemini("claude-opus-4-5-20251101") == "claude-opus-4-5-thinking"
    assert map_claude_model_to_gemini("claude-sonnet-4-5-20251001") == "claude-sonnet-4-5"
    assert map_claude_model_to_gemini("claude-haiku-4-5-20251001") == "gemini-2.5-flash"
    assert map_claude_model_to_gemini(" claude-opus-4-5-20251101 ") == "claude-opus-4-5-thinking"


def test_thinking_null_不会启用_thinkingConfig():
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 128,
        "thinking": None,
        "messages": [{"role": "user", "content": "hi"}],
    }
    components = convert_anthropic_request_to_antigravity_components(payload)
    assert "thinkingConfig" not in components["generation_config"]


def test_thinking_enabled_但无历史_thinking_blocks_不会下发_thinkingConfig():
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 128,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "messages": [{"role": "user", "content": "hi"}],
    }
    components = convert_anthropic_request_to_antigravity_components(payload)
    assert "thinkingConfig" in components["generation_config"]


def test_thinking_enabled_但最后一条_assistant_不以_thinking_开头_会跳过_thinkingConfig():
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 128,
        "thinking": {"type": "enabled", "budget_tokens": 1024},
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "hello"}]},
        ],
    }
    components = convert_anthropic_request_to_antigravity_components(payload)
    assert "thinkingConfig" not in components["generation_config"]


def test_antigravity_response_to_anthropic_message_映射_stop_reason_usage():
    response_data = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"thought": True, "text": "t", "thoughtSignature": "s"},
                            {"text": "x"},
                            {"functionCall": {"id": "c1", "name": "tool", "args": {"a": 1}}},
                            {"inlineData": {"mimeType": "image/png", "data": "BBBB"}},
                        ]
                    },
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 5},
        }
    }

    msg = _convert_antigravity_response_to_anthropic_message(
        response_data, model="claude-3-5-sonnet-20241022", message_id="msg_test"
    )
    assert msg["id"] == "msg_test"
    assert msg["type"] == "message"
    assert msg["stop_reason"] == "tool_use"
    assert msg["usage"] == {"input_tokens": 3, "output_tokens": 5}
    assert msg["content"][0]["type"] == "thinking"
    assert msg["content"][0]["signature"] == "s"
    assert msg["content"][2]["type"] == "tool_use"
    assert msg["content"][3]["type"] == "image"


@pytest.mark.asyncio
async def test_streaming_事件序列包含必要事件():
    antigravity_lines = [
        'data: {"response":{"candidates":[{"content":{"parts":[{"thought":true,"text":"A"}]}}]}}',
        'data: {"response":{"candidates":[{"content":{"parts":[{"text":"B"}]}}]}}',
        'data: {"response":{"candidates":[{"content":{"parts":[{"functionCall":{"id":"c1","name":"tool","args":{"x":1}}}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":2}}}',
    ]

    async def gen():
        for l in antigravity_lines:
            yield l

    chunks = []
    async for chunk in antigravity_sse_to_anthropic_sse(gen(), model="m", message_id="msg1"):
        chunks.append(chunk.decode("utf-8"))

    def parse_event(chunk_str: str):
        lines = [l for l in chunk_str.splitlines() if l.strip()]
        assert lines[0].startswith("event: ")
        assert lines[1].startswith("data: ")
        event = lines[0].split("event: ", 1)[1].strip()
        data = json.loads(lines[1].split("data: ", 1)[1])
        return event, data

    events = [parse_event(c)[0] for c in chunks]
    assert events[0] == "message_start"
    assert "content_block_start" in events
    assert "content_block_delta" in events
    assert "content_block_stop" in events
    assert events[-2] == "message_delta"
    assert events[-1] == "message_stop"
