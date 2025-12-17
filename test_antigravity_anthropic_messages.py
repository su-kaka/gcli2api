import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.antigravity_api import build_antigravity_request_body, send_antigravity_request_no_stream
from src.anthropic_converter import (
    clean_json_schema,
    convert_anthropic_request_to_antigravity_components,
    convert_messages_to_contents,
    map_claude_model_to_gemini,
    reorganize_tool_messages,
)
from src.anthropic_streaming import antigravity_sse_to_anthropic_sse
from src.antigravity_anthropic_router import (
    _convert_antigravity_response_to_anthropic_message,
    _estimate_input_tokens_from_components,
)


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


def test_clean_json_schema_type_数组包含null_会降级为单值():
    schema = {
        "type": "object",
        "properties": {
            "mode": {
                "type": ["string", "null"],
                "description": "可空字符串",
            },
            "kind": {
                "type": ["null", "string"],
                "description": "顺序可能为 null 在前",
            },
        },
        "additionalProperties": False,
    }

    cleaned = clean_json_schema(schema)
    assert cleaned["properties"]["mode"]["type"] == "string"
    assert cleaned["properties"]["mode"]["nullable"] is True
    assert cleaned["properties"]["kind"]["type"] == "string"
    assert cleaned["properties"]["kind"]["nullable"] is True


def test_clean_json_schema_type_数组在深层items_properties_也会被处理():
    schema = {
        "type": "object",
        "properties": {
            "rows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "count": {"type": ["null", "integer"]},
                    },
                },
            }
        },
    }

    cleaned = clean_json_schema(schema)
    row_props = cleaned["properties"]["rows"]["items"]["properties"]
    assert row_props["name"]["type"] == "string"
    assert row_props["name"]["nullable"] is True
    assert row_props["count"]["type"] == "integer"
    assert row_props["count"]["nullable"] is True


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


def test_convert_request_components_tools_schema_不会包含type数组_避免下游400():
    def assert_no_type_array(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "type":
                    assert not isinstance(v, list)
                assert_no_type_array(v)
        elif isinstance(obj, list):
            for item in obj:
                assert_no_type_array(item)

    payload = {
        "model": "claude-opus-4-5-20251101",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "name": "ask_followup_question",
                "description": "test",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": ["string", "null"]},
                        "follow_up": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "mode": {"type": ["null", "string"]},
                                },
                            },
                        },
                    },
                },
            }
        ],
    }

    components = convert_anthropic_request_to_antigravity_components(payload)
    assert components["tools"]
    params = components["tools"][0]["functionDeclarations"][0]["parameters"]
    assert_no_type_array(params)
    assert params["properties"]["mode"]["nullable"] is True
    assert params["properties"]["follow_up"]["items"]["properties"]["mode"]["nullable"] is True


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


def test_model_mapping_透传_antigravity_实际支持的_gemini_3_pro_image():
    assert map_claude_model_to_gemini("gemini-3-pro-image") == "gemini-3-pro-image"


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


@pytest.mark.asyncio
async def test_streaming_空文本part_不会产生空text块_避免污染thinking索引():
    antigravity_lines = [
        'data: {"response":{"candidates":[{"content":{"parts":[{"text":""},{"thought":true,"text":"A"}]}}]}}',
        'data: {"response":{"candidates":[{"content":{"parts":[{"text":"B"}]},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":1,"candidatesTokenCount":2}}}',
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

    parsed = [parse_event(c) for c in chunks]

    assert not any(
        event == "content_block_start"
        and data.get("index") == 0
        and (data.get("content_block") or {}).get("type") == "text"
        for event, data in parsed
    )

    starts = [(event, data) for event, data in parsed if event == "content_block_start"]
    assert starts[0][1]["index"] == 0
    assert starts[0][1]["content_block"]["type"] == "thinking"
    assert starts[1][1]["index"] == 1
    assert starts[1][1]["content_block"]["type"] == "text"


@pytest.mark.asyncio
async def test_streaming_message_start_会注入估算_input_tokens():
    payload = {
        "model": "claude-3-5-sonnet-20241022",
        "max_tokens": 8,
        "messages": [{"role": "user", "content": "你好，世界"}],
    }
    components = convert_anthropic_request_to_antigravity_components(payload)
    estimated_input_tokens = _estimate_input_tokens_from_components(components)
    assert estimated_input_tokens > 0

    async def gen():
        if False:
            yield ""

    chunks = []
    async for chunk in antigravity_sse_to_anthropic_sse(
        gen(),
        model="m",
        message_id="msg1",
        initial_input_tokens=estimated_input_tokens,
    ):
        chunks.append(chunk.decode("utf-8"))

    first = chunks[0]
    lines = [l for l in first.splitlines() if l.strip()]
    assert lines[0] == "event: message_start"
    data = json.loads(lines[1].split("data: ", 1)[1])
    assert data["message"]["usage"]["input_tokens"] == estimated_input_tokens


@pytest.mark.asyncio
async def test_tools_schema_type数组_会在下游请求前被归一化_避免模拟下游400():
    """
    这里用一个本地 mock Antigravity 服务模拟“下游遇到 type 为数组直接 400”的行为，
    用于验证修复确实会在下游请求前消除 `type: [...]` 结构。
    """

    def has_type_array(obj) -> bool:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "type" and isinstance(v, list):
                    return True
                if has_type_array(v):
                    return True
            return False
        if isinstance(obj, list):
            return any(has_type_array(i) for i in obj)
        return False

    received = {"ok": False, "saw_tools": False}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("content-length", "0") or "0")
            body = self.rfile.read(length).decode("utf-8", errors="ignore")
            try:
                payload = json.loads(body)
            except Exception:
                self.send_response(400)
                self.end_headers()
                return

            tools = (payload.get("request") or {}).get("tools") or []
            received["saw_tools"] = bool(tools)

            bad = False
            for tool in tools:
                for decl in (tool.get("functionDeclarations") or []) if isinstance(tool, dict) else []:
                    params = decl.get("parameters") if isinstance(decl, dict) else None
                    if has_type_array(params):
                        bad = True
                        break
                if bad:
                    break

            if bad:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "error": {
                                "code": 400,
                                "message": "Invalid JSON payload received: type is array",
                            }
                        }
                    ).encode("utf-8")
                )
                return

            received["ok"] = True
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "response": {
                            "candidates": [{"content": {"parts": [{"text": "ok"}]}, "finishReason": "STOP"}],
                            "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1},
                        }
                    }
                ).encode("utf-8")
            )

        def log_message(self, format, *args):  # noqa: A002
            # 测试中不输出 http.server 的默认日志，避免噪音
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    old_url = os.environ.get("ANTIGRAVITY_API_URL")
    os.environ["ANTIGRAVITY_API_URL"] = f"http://{host}:{port}"

    class FakeCredentialManager:
        async def get_valid_credential(self, *, is_antigravity: bool, model_key: str = ""):
            return "fake.json", {"access_token": "token", "projectId": "p", "sessionId": "s"}

        async def record_api_call_result(self, *args, **kwargs):
            return None

        async def set_cred_disabled(self, *args, **kwargs):
            return None

    try:
        payload = {
            "model": "claude-opus-4-5-20251101",
            "max_tokens": 8,
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {
                    "name": "ask_followup_question",
                    "description": "test",
                    "input_schema": {
                        "type": "object",
                        "properties": {"mode": {"type": ["string", "null"]}},
                        "required": ["mode"],
                        "additionalProperties": False,
                    },
                }
            ],
        }
        components = convert_anthropic_request_to_antigravity_components(payload)
        request_body = build_antigravity_request_body(
            contents=components["contents"],
            model=components["model"],
            project_id="p",
            session_id="s",
            system_instruction=components["system_instruction"],
            tools=components["tools"],
            generation_config=components["generation_config"],
        )

        # 明确断言：null 联合类型已转换为 nullable
        params = request_body["request"]["tools"][0]["functionDeclarations"][0]["parameters"]
        assert params["properties"]["mode"]["type"] == "string"
        assert params["properties"]["mode"]["nullable"] is True

        response_data, _, _ = await send_antigravity_request_no_stream(
            request_body, FakeCredentialManager()
        )

        assert response_data["response"]["candidates"][0]["content"]["parts"][0]["text"] == "ok"
        assert received["saw_tools"] is True
        assert received["ok"] is True
    finally:
        if old_url is None:
            os.environ.pop("ANTIGRAVITY_API_URL", None)
        else:
            os.environ["ANTIGRAVITY_API_URL"] = old_url
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
