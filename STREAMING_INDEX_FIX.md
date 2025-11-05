# 流式响应 tool_calls index 字段修复

## 问题描述

在使用 Cherry Studio 等客户端时，流式响应中的 tool_calls 触发验证错误：

```
AI_TypeValidationError: Type validation failed
Error: Invalid input: expected number, received undefined
Path: ["choices", 0, "delta", "tool_calls", 0, "index"]
```

## 根本原因

根据 OpenAI API 规范：
- **流式响应**：`delta.tool_calls` 数组中的每个元素**必须包含** `index` 字段
- **非流式响应**：`message.tool_calls` 数组中的元素**不需要** `index` 字段

我们的实现在流式响应中没有添加 `index` 字段，导致客户端验证失败。

## 修复方案

### 1. 修改 `extract_tool_calls_from_parts()` 函数

**文件**: `src/openai_transfer.py:824`

添加 `is_streaming` 参数来区分流式和非流式响应：

```python
def extract_tool_calls_from_parts(
    parts: List[Dict[str, Any]],
    is_streaming: bool = False  # 新增参数
) -> Tuple[List[Dict[str, Any]], str]:
    """
    从 Gemini response parts 中提取工具调用和文本内容

    Args:
        parts: Gemini response 的 parts 数组
        is_streaming: 是否为流式响应（流式响应需要添加 index 字段）
    """
    tool_calls = []
    text_content = ""

    for idx, part in enumerate(parts):  # 使用 enumerate 获取索引
        if "functionCall" in part:
            function_call = part["functionCall"]
            tool_call = {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": function_call.get("name"),
                    "arguments": json.dumps(function_call.get("args", {}))
                }
            }
            # 流式响应需要 index 字段
            if is_streaming:
                tool_call["index"] = idx  # 添加 index
            tool_calls.append(tool_call)
```

### 2. 更新流式响应调用

**文件**: `src/openai_transfer.py:398`

在 `gemini_stream_chunk_to_openai()` 中传递 `is_streaming=True`：

```python
# 提取工具调用和文本内容（流式响应需要 index 字段）
tool_calls, text_content = extract_tool_calls_from_parts(parts, is_streaming=True)
```

### 3. 非流式响应保持不变

**文件**: `src/openai_transfer.py:320`

在 `gemini_response_to_openai()` 中使用默认参数（`is_streaming=False`）：

```python
# 提取工具调用和文本内容
tool_calls, text_content = extract_tool_calls_from_parts(parts)  # 默认 False
```

## 格式对比

### 流式响应（修复后）

```json
{
  "choices": [{
    "delta": {
      "tool_calls": [
        {
          "index": 0,  // ✅ 必需
          "id": "call_xxx",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"location\": \"Tokyo\"}"
          }
        }
      ]
    }
  }]
}
```

### 非流式响应（保持不变）

```json
{
  "choices": [{
    "message": {
      "tool_calls": [
        {
          // ❌ 不包含 index 字段
          "id": "call_xxx",
          "type": "function",
          "function": {
            "name": "get_weather",
            "arguments": "{\"location\": \"Tokyo\"}"
          }
        }
      ]
    }
  }]
}
```

## 测试验证

### 新增测试

**测试 10**: `test_streaming_tool_calls_with_index()`

```python
def test_streaming_tool_calls_with_index():
    """测试流式响应中的 tool_calls 包含 index 字段"""

    # 模拟包含多个 tool_calls 的流式响应
    gemini_chunk = {
        "candidates": [{
            "content": {
                "role": "model",
                "parts": [
                    {"functionCall": {"name": "get_weather", "args": {...}}},
                    {"functionCall": {"name": "get_time", "args": {...}}}
                ]
            },
            "finishReason": "STOP"
        }]
    }

    result = gemini_stream_chunk_to_openai(gemini_chunk, "gemini-pro", "test-id")

    tool_calls = result["choices"][0]["delta"]["tool_calls"]

    # 验证每个 tool_call 都有 index 字段
    assert tool_calls[0]["index"] == 0
    assert tool_calls[1]["index"] == 1
```

### 测试结果

```
✅ 测试 10: 流式响应 tool_calls index 字段
   验证了 2 个 tool_calls 的格式
   每个 tool_call 都有必需的 index 字段

✅ 所有 11 个测试通过！
```

## 影响范围

- ✅ **流式响应**: 修复，添加了 index 字段
- ✅ **非流式响应**: 不受影响，保持原有行为
- ✅ **向后兼容**: 完全兼容
- ✅ **客户端兼容**: 符合 OpenAI API 规范，修复 Cherry Studio 等客户端的验证错误

## 相关提交

- **commit**: `7d21ed6` - 修复流式响应中 tool_calls 缺少 index 字段的问题

## 参考资料

- [OpenAI API - Chat Completions](https://platform.openai.com/docs/api-reference/chat/streaming)
- OpenAI 流式响应格式规范：`ChatCompletionChunk.choices[].delta.tool_calls[].index`
