import asyncio
import json

from src.converter.openai2gemini import (
    _STREAM_TOOL_INDEX,
    convert_gemini_to_openai_stream,
    convert_openai_to_gemini_request,
)


def _chunk(function_name, args, finish_reason=None):
    """构造一个只带单个 functionCall part 的 Gemini 流式块。"""
    candidate = {"content": {"role": "model", "parts": [{"functionCall": {"name": function_name, "args": args}}]}}
    if finish_reason:
        candidate["finishReason"] = finish_reason
    return "data: " + json.dumps({"candidates": [candidate]})


def _tool_calls_of(sse):
    payload = json.loads(sse[len("data: "):])
    return payload["choices"][0]["delta"].get("tool_calls", [])


def test_parallel_tool_calls_get_distinct_stream_indices():
    """并行 functionCall 分散在多个 chunk 里，index 必须递增而不是全为 0。"""
    response_id = "resp-parallel"
    _STREAM_TOOL_INDEX.pop(response_id, None)

    indices = []
    for city in ("北京", "上海", "广州"):
        sse = convert_gemini_to_openai_stream(_chunk("get_weather", {"city": city}), "gemini-3.5-flash", response_id)
        indices.append(_tool_calls_of(sse)[0]["index"])

    assert indices == [0, 1, 2]


def test_tool_calls_in_one_chunk_are_numbered_consecutively():
    response_id = "resp-single-chunk"
    _STREAM_TOOL_INDEX.pop(response_id, None)

    chunk = "data: " + json.dumps({
        "candidates": [{"content": {"role": "model", "parts": [
            {"text": "先查两个城市"},
            {"functionCall": {"name": "get_weather", "args": {"city": "北京"}}},
            {"functionCall": {"name": "get_weather", "args": {"city": "上海"}}},
        ]}}]
    })
    sse = convert_gemini_to_openai_stream(chunk, "gemini-3.5-flash", response_id)

    assert [tc["index"] for tc in _tool_calls_of(sse)] == [0, 1]


def test_stream_index_is_isolated_per_response_id():
    _STREAM_TOOL_INDEX.pop("resp-a", None)
    _STREAM_TOOL_INDEX.pop("resp-b", None)

    convert_gemini_to_openai_stream(_chunk("f", {"x": 1}), "gemini-3.5-flash", "resp-a")
    sse_b = convert_gemini_to_openai_stream(_chunk("f", {"x": 1}), "gemini-3.5-flash", "resp-b")
    sse_a = convert_gemini_to_openai_stream(_chunk("f", {"x": 2}), "gemini-3.5-flash", "resp-a")

    assert _tool_calls_of(sse_b)[0]["index"] == 0
    assert _tool_calls_of(sse_a)[0]["index"] == 1


def test_stream_index_released_after_finish_reason():
    response_id = "resp-finish"
    _STREAM_TOOL_INDEX.pop(response_id, None)

    convert_gemini_to_openai_stream(_chunk("f", {"x": 1}), "gemini-3.5-flash", response_id)
    convert_gemini_to_openai_stream(_chunk("f", {"x": 2}, finish_reason="STOP"), "gemini-3.5-flash", response_id)

    assert response_id not in _STREAM_TOOL_INDEX


def test_unparsable_tool_call_arguments_keep_function_call():
    """arguments 非法时仍要保留 functionCall，否则后面的 functionResponse 变孤儿。"""
    # 拼接串：正是 index 恒为 0 时客户端归并出来的产物
    broken_args = '{"city": "北京"}{"city": "上海"}'
    request = {
        "model": "gemini-3.5-flash",
        "messages": [
            {"role": "user", "content": "查天气"},
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call_abc", "type": "function",
                 "function": {"name": "get_weather", "arguments": broken_args}}
            ]},
            {"role": "tool", "tool_call_id": "call_abc", "name": "get_weather", "content": "参数解析失败"},
        ],
        "tools": [{"type": "function", "function": {
            "name": "get_weather",
            "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}],
    }

    gemini_request = asyncio.run(convert_openai_to_gemini_request(request))
    contents = gemini_request["contents"]

    function_calls = [
        part["functionCall"]
        for content in contents for part in content.get("parts", [])
        if "functionCall" in part
    ]
    function_responses = [
        part for content in contents for part in content.get("parts", [])
        if "functionResponse" in part
    ]

    # functionResponse 不能没有对应的 functionCall
    assert len(function_calls) == 1
    assert len(function_responses) == 1
    assert function_calls[0]["name"] == "get_weather"
    # 救出拼接串里的第一个 JSON 对象
    assert function_calls[0]["args"] == {"city": "北京"}
