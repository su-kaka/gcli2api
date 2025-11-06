# Google Gemini CLI 工具调用接口支持分析

## 📋 目录
1. [现状分析](#现状分析)
2. [OpenAI 工具调用格式](#openai-工具调用格式)
3. [Gemini 工具调用格式](#gemini-工具调用格式)
4. [格式对比](#格式对比)
5. [实现方案](#实现方案)
6. [代码示例](#代码示例)
7. [测试计划](#测试计划)

---

## 1. 现状分析

### 当前实现状态

**✅ 已支持的功能：**

1. **Google Search 工具自动注入**
   - 位置：`src/openai_transfer.py:162`
   - 对于搜索模型（带 `-search` 后缀），自动添加 `{"googleSearch": {}}`

2. **Gemini 原生格式完全透传**
   - 位置：`src/google_chat_api.py:515-524`
   - 支持 `tools` 字段完全透传到 Gemini API
   - 支持 `toolConfig` 字段完全透传
   - 支持 `cachedContent` 字段完全透传

3. **数据模型已定义**
   - 位置：`src/models.py:116-117`
   ```python
   tools: Optional[List[Dict[str, Any]]] = None
   toolConfig: Optional[Dict[str, Any]] = None
   ```

**❌ 缺失的功能：**

1. **OpenAI → Gemini 工具格式转换**
   - OpenAI 的 `tools` 使用 `type: "function"` + `function` 对象
   - Gemini 的 `tools` 使用 `functionDeclarations` 数组
   - **当前不支持自动转换**

2. **工具调用响应转换**
   - Gemini 返回 `functionCall` 对象
   - OpenAI 返回 `tool_calls` 数组
   - **当前不支持响应格式转换**

3. **工具执行结果处理**
   - OpenAI 使用 `tool` role 的消息
   - Gemini 使用 `functionResponse` 对象
   - **当前不支持多轮对话的工具结果处理**

---

## 2. OpenAI 工具调用格式

### 请求格式

```json
{
  "model": "gpt-4",
  "messages": [
    {
      "role": "user",
      "content": "What's the weather in Boston?"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {
              "type": "string",
              "description": "The city and state, e.g. San Francisco, CA"
            },
            "unit": {
              "type": "string",
              "enum": ["celsius", "fahrenheit"],
              "description": "The temperature unit to use"
            }
          },
          "required": ["location"]
        }
      }
    }
  ],
  "tool_choice": "auto"
}
```

### 响应格式

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1699896916,
  "model": "gpt-4",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": null,
        "tool_calls": [
          {
            "id": "call_abc123",
            "type": "function",
            "function": {
              "name": "get_current_weather",
              "arguments": "{\"location\":\"Boston, MA\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ]
}
```

### 工具结果返回格式

```json
{
  "model": "gpt-4",
  "messages": [
    {
      "role": "user",
      "content": "What's the weather in Boston?"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_abc123",
          "type": "function",
          "function": {
            "name": "get_current_weather",
            "arguments": "{\"location\":\"Boston, MA\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_abc123",
      "name": "get_current_weather",
      "content": "{\"temperature\": 22, \"unit\": \"celsius\", \"description\": \"Sunny\"}"
    }
  ]
}
```

### tool_choice 选项

- `"auto"` (默认): 模型自动决定是否调用工具
- `"none"`: 强制模型不调用工具
- `{"type": "function", "function": {"name": "my_function"}}`: 强制调用特定工具
- `"required"`: 强制模型必须调用至少一个工具

---

## 3. Gemini 工具调用格式

### 请求格式

```json
{
  "contents": [
    {
      "role": "user",
      "parts": [
        {
          "text": "What's the weather in Boston?"
        }
      ]
    }
  ],
  "tools": [
    {
      "functionDeclarations": [
        {
          "name": "get_current_weather",
          "description": "Get the current weather in a given location",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {
                "type": "string",
                "description": "The city and state, e.g. San Francisco, CA"
              },
              "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"],
                "description": "The temperature unit to use"
              }
            },
            "required": ["location"]
          }
        }
      ]
    }
  ],
  "toolConfig": {
    "functionCallingConfig": {
      "mode": "AUTO"
    }
  }
}
```

### 响应格式

```json
{
  "candidates": [
    {
      "content": {
        "role": "model",
        "parts": [
          {
            "functionCall": {
              "name": "get_current_weather",
              "args": {
                "location": "Boston, MA"
              }
            }
          }
        ]
      },
      "finishReason": "STOP"
    }
  ]
}
```

### 工具结果返回格式

```json
{
  "contents": [
    {
      "role": "user",
      "parts": [{"text": "What's the weather in Boston?"}]
    },
    {
      "role": "model",
      "parts": [
        {
          "functionCall": {
            "name": "get_current_weather",
            "args": {"location": "Boston, MA"}
          }
        }
      ]
    },
    {
      "role": "user",
      "parts": [
        {
          "functionResponse": {
            "name": "get_current_weather",
            "response": {
              "temperature": 22,
              "unit": "celsius",
              "description": "Sunny"
            }
          }
        }
      ]
    }
  ]
}
```

### toolConfig 模式

```json
{
  "functionCallingConfig": {
    "mode": "AUTO|ANY|NONE",
    "allowedFunctionNames": ["function1", "function2"]
  }
}
```

- `AUTO` (默认): 模型自动决定
- `ANY`: 必须调用某个函数
- `NONE`: 禁用函数调用

---

## 4. 格式对比

| 特性 | OpenAI | Gemini | 转换复杂度 |
|------|--------|--------|-----------|
| **工具定义位置** | `tools[].function` | `tools[].functionDeclarations[]` | 🟡 中等 |
| **类型声明** | `type: "function"` | 无需类型字段 | 🟢 简单 |
| **参数格式** | JSON Schema | JSON Schema (子集) | 🟢 简单 |
| **工具选择** | `tool_choice` | `toolConfig.functionCallingConfig` | 🟡 中等 |
| **响应格式** | `tool_calls[]` 数组 | `parts[].functionCall` | 🟡 中等 |
| **工具 ID** | 必需 (`id` 字段) | 不需要 | 🟢 简单 |
| **参数编码** | JSON 字符串 | JSON 对象 | 🟢 简单 |
| **工具结果** | `role: "tool"` 消息 | `functionResponse` 对象 | 🟡 中等 |

### 关键差异

1. **结构嵌套**
   - OpenAI: `tools[i].function` 包含函数定义
   - Gemini: `tools[i].functionDeclarations[]` 数组

2. **参数编码**
   - OpenAI: `arguments` 是 JSON 字符串
   - Gemini: `args` 是 JSON 对象

3. **工具 ID**
   - OpenAI: 需要生成唯一 ID (`call_xxx`)
   - Gemini: 不需要 ID

4. **响应位置**
   - OpenAI: `message.tool_calls[]` 独立数组
   - Gemini: `parts[]` 数组中的一个 part

---

## 5. 实现方案

### 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                   OpenAI Request                            │
│  tools: [{type: "function", function: {...}}]               │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│            openai_request_to_gemini_payload()               │
│  - 转换 tools 格式                                           │
│  - 转换 tool_choice → toolConfig                            │
│  - 处理 tool role 消息 → functionResponse                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Gemini Request                           │
│  tools: [{functionDeclarations: [{...}]}]                   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
                  [Google API]
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   Gemini Response                           │
│  parts: [{functionCall: {...}}]                             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│            gemini_response_to_openai()                      │
│  - 转换 functionCall → tool_calls                           │
│  - 生成 tool_call_id                                        │
│  - 设置 finish_reason                                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   OpenAI Response                           │
│  tool_calls: [{id: "...", function: {...}}]                 │
└─────────────────────────────────────────────────────────────┘
```

### 实现步骤

#### Step 1: 请求转换 - 工具定义

在 `src/openai_transfer.py` 的 `openai_request_to_gemini_payload()` 函数中添加：

```python
# 转换 OpenAI tools 到 Gemini functionDeclarations
if hasattr(openai_request, 'tools') and openai_request.tools:
    gemini_tools = convert_openai_tools_to_gemini(openai_request.tools)
    if gemini_tools:
        request_data["tools"] = gemini_tools

# 转换 tool_choice 到 toolConfig
if hasattr(openai_request, 'tool_choice') and openai_request.tool_choice:
    request_data["toolConfig"] = convert_tool_choice_to_tool_config(
        openai_request.tool_choice
    )
```

#### Step 2: 请求转换 - 工具消息

处理 `role: "tool"` 的消息：

```python
for message in openai_request.messages:
    role = message.role

    if role == "tool":
        # 转换工具结果消息
        function_response = {
            "functionResponse": {
                "name": message.name,
                "response": json.loads(message.content)
            }
        }
        contents.append({
            "role": "user",  # Gemini 中工具响应作为 user 消息
            "parts": [function_response]
        })
        continue
```

#### Step 3: 响应转换 - 工具调用

在 `gemini_response_to_openai()` 和 `gemini_stream_chunk_to_openai()` 中添加：

```python
# 检查是否包含函数调用
tool_calls = []
regular_content = ""

for part in parts:
    if "functionCall" in part:
        # 转换为 OpenAI 格式
        tool_call = {
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": part["functionCall"]["name"],
                "arguments": json.dumps(part["functionCall"]["args"])
            }
        }
        tool_calls.append(tool_call)
    elif "text" in part:
        regular_content += part["text"]

# 构建消息
message = {"role": role}
if tool_calls:
    message["tool_calls"] = tool_calls
    message["content"] = regular_content if regular_content else None
    finish_reason = "tool_calls"
else:
    message["content"] = regular_content
```

#### Step 4: 数据模型更新

在 `src/models.py` 中添加：

```python
class OpenAITool(BaseModel):
    type: str = "function"
    function: Dict[str, Any]

class OpenAIToolCall(BaseModel):
    id: str
    type: str = "function"
    function: Dict[str, Any]

class OpenAIChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]], None] = None
    tool_calls: Optional[List[OpenAIToolCall]] = None
    tool_call_id: Optional[str] = None  # for role="tool"
    name: Optional[str] = None  # function name for role="tool"
    # ... existing fields

class ChatCompletionRequest(BaseModel):
    # ... existing fields
    tools: Optional[List[OpenAITool]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
```

---

## 6. 代码示例

### 完整的转换函数实现

#### 工具定义转换

```python
def convert_openai_tools_to_gemini(openai_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 OpenAI tools 格式转换为 Gemini functionDeclarations 格式

    Args:
        openai_tools: OpenAI 格式的工具列表

    Returns:
        Gemini 格式的工具列表
    """
    if not openai_tools:
        return []

    function_declarations = []

    for tool in openai_tools:
        if tool.get("type") != "function":
            log.warning(f"Skipping non-function tool type: {tool.get('type')}")
            continue

        function = tool.get("function")
        if not function:
            log.warning("Tool missing 'function' field")
            continue

        # 构建 Gemini function declaration
        declaration = {
            "name": function.get("name"),
            "description": function.get("description", ""),
            "parameters": function.get("parameters", {})
        }

        function_declarations.append(declaration)

    if not function_declarations:
        return []

    # Gemini 格式：工具数组中包含 functionDeclarations
    return [{"functionDeclarations": function_declarations}]


def convert_tool_choice_to_tool_config(tool_choice: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    将 OpenAI tool_choice 转换为 Gemini toolConfig

    Args:
        tool_choice: OpenAI 格式的 tool_choice

    Returns:
        Gemini 格式的 toolConfig
    """
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {
                "functionCallingConfig": {
                    "mode": "AUTO"
                }
            }
        elif tool_choice == "none":
            return {
                "functionCallingConfig": {
                    "mode": "NONE"
                }
            }
        elif tool_choice == "required":
            return {
                "functionCallingConfig": {
                    "mode": "ANY"
                }
            }
    elif isinstance(tool_choice, dict):
        # {"type": "function", "function": {"name": "my_function"}}
        if tool_choice.get("type") == "function":
            function_name = tool_choice.get("function", {}).get("name")
            if function_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [function_name]
                    }
                }

    # 默认返回 AUTO 模式
    return {
        "functionCallingConfig": {
            "mode": "AUTO"
        }
    }
```

#### 消息转换（包含工具结果）

```python
def convert_tool_message_to_function_response(message: OpenAIChatMessage) -> Dict[str, Any]:
    """
    将 OpenAI 的 tool role 消息转换为 Gemini functionResponse

    Args:
        message: OpenAI 格式的工具消息

    Returns:
        Gemini 格式的 functionResponse part
    """
    try:
        # 尝试将 content 解析为 JSON
        response_data = json.loads(message.content) if isinstance(message.content, str) else message.content
    except json.JSONDecodeError:
        # 如果不是有效的 JSON，包装为对象
        response_data = {"result": message.content}

    return {
        "functionResponse": {
            "name": message.name,
            "response": response_data
        }
    }
```

#### 响应转换（提取工具调用）

```python
def extract_tool_calls_from_parts(parts: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], str]:
    """
    从 Gemini response parts 中提取工具调用和文本内容

    Args:
        parts: Gemini response 的 parts 数组

    Returns:
        (tool_calls, text_content) 元组
    """
    tool_calls = []
    text_content = ""

    for part in parts:
        # 检查是否是函数调用
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
            tool_calls.append(tool_call)

        # 提取文本内容（排除 thinking tokens）
        elif "text" in part and not part.get("thought", False):
            text_content += part["text"]

    return tool_calls, text_content
```

#### 完整的响应转换

```python
def gemini_response_to_openai_with_tools(
    gemini_response: Dict[str, Any],
    model: str
) -> Dict[str, Any]:
    """
    将包含工具调用的 Gemini 响应转换为 OpenAI 格式

    Args:
        gemini_response: Gemini API 响应
        model: 模型名称

    Returns:
        OpenAI 格式的响应
    """
    choices = []

    for candidate in gemini_response.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")
        if role == "model":
            role = "assistant"

        parts = candidate.get("content", {}).get("parts", [])

        # 提取工具调用和文本内容
        tool_calls, text_content = extract_tool_calls_from_parts(parts)

        # 提取 reasoning content（thinking tokens）
        reasoning_content = ""
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]

        # 构建消息
        message = {"role": role}

        # 如果有工具调用
        if tool_calls:
            message["tool_calls"] = tool_calls
            # content 可以是 None 或包含文本
            message["content"] = text_content if text_content else None
            finish_reason = "tool_calls"
        else:
            message["content"] = text_content
            finish_reason = _map_finish_reason(candidate.get("finishReason"))

        # 添加 reasoning content（如果有）
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        choices.append({
            "index": candidate.get("index", 0),
            "message": message,
            "finish_reason": finish_reason
        })

    # 转换 usage metadata
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

    response_data = {
        "id": str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": choices
    }

    if usage:
        response_data["usage"] = usage

    return response_data
```

#### 流式响应转换

```python
def gemini_stream_chunk_to_openai_with_tools(
    gemini_chunk: Dict[str, Any],
    model: str,
    response_id: str
) -> Dict[str, Any]:
    """
    将包含工具调用的 Gemini 流式响应转换为 OpenAI 格式

    Args:
        gemini_chunk: Gemini 流式响应块
        model: 模型名称
        response_id: 响应 ID

    Returns:
        OpenAI 流式格式
    """
    choices = []

    for candidate in gemini_chunk.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")
        if role == "model":
            role = "assistant"

        parts = candidate.get("content", {}).get("parts", [])

        # 提取工具调用和文本
        tool_calls, text_content = extract_tool_calls_from_parts(parts)

        # 提取 reasoning content
        reasoning_content = ""
        for part in parts:
            if part.get("thought", False) and "text" in part:
                reasoning_content += part["text"]

        # 构建 delta
        delta = {}

        if tool_calls:
            # 流式响应中的工具调用
            delta["tool_calls"] = tool_calls
            if text_content:
                delta["content"] = text_content
        elif text_content:
            delta["content"] = text_content

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content

        finish_reason = _map_finish_reason(candidate.get("finishReason"))
        if finish_reason == "STOP" and tool_calls:
            finish_reason = "tool_calls"

        choices.append({
            "index": candidate.get("index", 0),
            "delta": delta,
            "finish_reason": finish_reason
        })

    # 转换 usage
    usage = _convert_usage_metadata(gemini_chunk.get("usageMetadata"))

    response_data = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": choices
    }

    if usage:
        has_finish_reason = any(choice.get("finish_reason") for choice in choices)
        if has_finish_reason:
            response_data["usage"] = usage

    return response_data
```

---

## 7. 测试计划

### 单元测试

#### 测试 1: 工具定义转换

```python
def test_convert_openai_tools_to_gemini():
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get current weather",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
    ]

    result = convert_openai_tools_to_gemini(openai_tools)

    assert len(result) == 1
    assert "functionDeclarations" in result[0]
    assert len(result[0]["functionDeclarations"]) == 1
    assert result[0]["functionDeclarations"][0]["name"] == "get_weather"
```

#### 测试 2: tool_choice 转换

```python
def test_convert_tool_choice():
    # 测试 "auto"
    result = convert_tool_choice_to_tool_config("auto")
    assert result["functionCallingConfig"]["mode"] == "AUTO"

    # 测试 "required"
    result = convert_tool_choice_to_tool_config("required")
    assert result["functionCallingConfig"]["mode"] == "ANY"

    # 测试指定函数
    result = convert_tool_choice_to_tool_config({
        "type": "function",
        "function": {"name": "my_func"}
    })
    assert result["functionCallingConfig"]["mode"] == "ANY"
    assert "my_func" in result["functionCallingConfig"]["allowedFunctionNames"]
```

#### 测试 3: 工具调用响应提取

```python
def test_extract_tool_calls():
    parts = [
        {
            "functionCall": {
                "name": "get_weather",
                "args": {"location": "Boston"}
            }
        },
        {
            "text": "Let me check the weather for you."
        }
    ]

    tool_calls, text = extract_tool_calls_from_parts(parts)

    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "get_weather"
    assert "Boston" in tool_calls[0]["function"]["arguments"]
    assert "Let me check" in text
```

### 集成测试

#### 测试 4: 完整的工具调用流程

```python
async def test_full_tool_calling_flow():
    """测试从 OpenAI 请求到工具调用响应的完整流程"""

    # 1. 准备 OpenAI 请求
    request = {
        "model": "gemini-2.5-flash-preview",
        "messages": [
            {
                "role": "user",
                "content": "What's the weather in Tokyo?"
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather information",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"}
                        },
                        "required": ["location"]
                    }
                }
            }
        ],
        "tool_choice": "auto"
    }

    # 2. 转换为 Gemini 格式
    openai_req = ChatCompletionRequest(**request)
    gemini_payload = await openai_request_to_gemini_payload(openai_req)

    # 3. 验证转换结果
    assert "tools" in gemini_payload["request"]
    assert "functionDeclarations" in gemini_payload["request"]["tools"][0]
    assert "toolConfig" in gemini_payload["request"]

    # 4. 模拟 Gemini 响应
    gemini_response = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "functionCall": {
                                "name": "get_weather",
                                "args": {"location": "Tokyo"}
                            }
                        }
                    ]
                },
                "finishReason": "STOP"
            }
        ]
    }

    # 5. 转换回 OpenAI 格式
    openai_response = gemini_response_to_openai_with_tools(
        gemini_response,
        request["model"]
    )

    # 6. 验证响应
    assert len(openai_response["choices"]) == 1
    choice = openai_response["choices"][0]
    assert "tool_calls" in choice["message"]
    assert len(choice["message"]["tool_calls"]) == 1
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_weather"
    assert choice["finish_reason"] == "tool_calls"
```

#### 测试 5: 工具结果的多轮对话

```python
async def test_multi_turn_with_tool_result():
    """测试包含工具执行结果的多轮对话"""

    request = {
        "model": "gemini-2.5-flash-preview",
        "messages": [
            {
                "role": "user",
                "content": "What's the weather in Tokyo?"
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"location": "Tokyo"}'
                        }
                    }
                ]
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "name": "get_weather",
                "content": '{"temperature": 18, "condition": "Cloudy"}'
            }
        ],
        "tools": [...]
    }

    openai_req = ChatCompletionRequest(**request)
    gemini_payload = await openai_request_to_gemini_payload(openai_req)

    # 验证工具结果被正确转换
    contents = gemini_payload["request"]["contents"]

    # 应该有 3 条消息：user, model (with functionCall), user (with functionResponse)
    assert len(contents) == 3

    # 检查最后一条消息包含 functionResponse
    last_message = contents[-1]
    assert last_message["role"] == "user"
    assert "functionResponse" in last_message["parts"][0]
    assert last_message["parts"][0]["functionResponse"]["name"] == "get_weather"
```

### 端到端测试

#### 测试 6: 实际 API 调用测试

```bash
# 测试工具调用
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.5-flash-preview",
    "messages": [
      {
        "role": "user",
        "content": "What is 15 * 7?"
      }
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "calculate",
          "description": "Perform mathematical calculation",
          "parameters": {
            "type": "object",
            "properties": {
              "expression": {
                "type": "string",
                "description": "Mathematical expression to evaluate"
              }
            },
            "required": ["expression"]
          }
        }
      }
    ]
  }'
```

#### 测试 7: 流式工具调用

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.5-flash-preview",
    "messages": [...],
    "tools": [...],
    "stream": true
  }'
```

### 边界情况测试

#### 测试 8: 空工具列表

```python
def test_empty_tools():
    result = convert_openai_tools_to_gemini([])
    assert result == []
```

#### 测试 9: 无效的工具类型

```python
def test_invalid_tool_type():
    tools = [{"type": "invalid_type"}]
    result = convert_openai_tools_to_gemini(tools)
    assert result == []
```

#### 测试 10: 多个工具定义

```python
def test_multiple_tools():
    tools = [
        {"type": "function", "function": {"name": "tool1", ...}},
        {"type": "function", "function": {"name": "tool2", ...}}
    ]
    result = convert_openai_tools_to_gemini(tools)
    assert len(result) == 1
    assert len(result[0]["functionDeclarations"]) == 2
```

---

## 8. 实现检查清单

### 代码修改

- [ ] 更新 `src/models.py` - 添加工具相关的数据模型
- [ ] 更新 `src/openai_transfer.py` - 添加工具转换函数
- [ ] 更新 `openai_request_to_gemini_payload()` - 集成工具转换
- [ ] 更新 `gemini_response_to_openai()` - 添加工具调用提取
- [ ] 更新 `gemini_stream_chunk_to_openai()` - 添加流式工具调用支持
- [ ] 添加 `src/tool_converter.py` - 独立的工具转换模块（可选）

### 测试

- [ ] 编写单元测试
- [ ] 编写集成测试
- [ ] 编写端到端测试
- [ ] 测试边界情况
- [ ] 测试错误处理

### 文档

- [ ] 更新 README.md - 添加工具调用使用说明
- [ ] 添加示例代码
- [ ] 更新 API 文档
- [ ] 添加故障排除指南

### 部署

- [ ] 本地测试通过
- [ ] 性能测试
- [ ] 向后兼容性检查
- [ ] 部署到生产环境

---

## 9. 注意事项和限制

### Gemini API 限制

1. **不支持的 JSON Schema 特性**
   - `default` 字段
   - `optional` 字段
   - `maximum`/`minimum` 字段
   - `oneOf`/`anyOf`/`allOf`

2. **工具调用限制**
   - 最多可以定义多少个函数（需要查阅官方文档）
   - 参数大小限制

### OpenAI 兼容性

1. **tool_call_id 生成**
   - 需要生成唯一的 ID
   - 格式：`call_` + 24 位十六进制

2. **parallel_tool_calls**
   - OpenAI 支持 `parallel_tool_calls` 参数
   - Gemini 可能有不同的行为

### 错误处理

1. **工具定义验证**
   - 验证必需字段
   - 处理无效的 schema

2. **工具调用失败**
   - 处理 Gemini 返回的错误
   - 转换为 OpenAI 格式的错误

---

## 10. 总结

### 当前状态

✅ **已完成：**
- Google Search 工具自动注入
- Gemini 原生格式透传
- 基础数据模型定义

❌ **待实现：**
- OpenAI → Gemini 工具格式转换
- Gemini → OpenAI 工具调用响应转换
- 多轮对话中的工具结果处理
- 完整的测试覆盖

### 实现优先级

1. **高优先级（核心功能）**
   - 工具定义格式转换
   - 工具调用响应转换
   - 基本的单轮工具调用

2. **中优先级（完整体验）**
   - 多轮对话支持
   - 工具结果处理
   - tool_choice 转换

3. **低优先级（优化）**
   - 并行工具调用
   - 高级 toolConfig 选项
   - 性能优化

### 预期效果

实现完成后，用户可以：

1. 使用 OpenAI 的工具调用格式与 Gemini 模型交互
2. 无需修改现有的 OpenAI 客户端代码
3. 享受 Gemini 的工具调用能力（如 Google Search）
4. 在流式和非流式模式下都能正常工作

---

## 参考资料

- [Google Gemini API Function Calling Documentation](https://ai.google.dev/gemini-api/docs/function-calling)
- [OpenAI Function Calling Guide](https://platform.openai.com/docs/guides/function-calling)
- [JSON Schema Specification](https://json-schema.org/)
- gcli2api 源代码：`src/openai_transfer.py`, `src/models.py`, `src/google_chat_api.py`
