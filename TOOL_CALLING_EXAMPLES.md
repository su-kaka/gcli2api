# 工具调用使用示例

本文档展示如何使用 gcli2api 的工具调用功能。

## 目录
- [基础用法](#基础用法)
- [单轮工具调用](#单轮工具调用)
- [多轮工具调用](#多轮工具调用)
- [tool_choice 选项](#tool_choice-选项)
- [多个工具定义](#多个工具定义)
- [与 Google Search 结合](#与-google-search-结合)
- [流式工具调用](#流式工具调用)

---

## 基础用法

### 定义工具

使用标准的 OpenAI 工具格式定义函数：

```json
{
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取指定城市的天气信息",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {
              "type": "string",
              "description": "城市名称，例如：北京、上海、Tokyo"
            },
            "unit": {
              "type": "string",
              "enum": ["celsius", "fahrenheit"],
              "description": "温度单位"
            }
          },
          "required": ["location"]
        }
      }
    }
  ]
}
```

---

## 单轮工具调用

### 示例 1: 查询天气

**请求：**

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.0-flash-exp",
    "messages": [
      {
        "role": "user",
        "content": "东京现在的天气怎么样？"
      }
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "获取天气信息",
          "parameters": {
            "type": "object",
            "properties": {
              "location": {
                "type": "string",
                "description": "城市名称"
              },
              "unit": {
                "type": "string",
                "enum": ["celsius", "fahrenheit"]
              }
            },
            "required": ["location"]
          }
        }
      }
    ]
  }'
```

**响应：**

```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1699896916,
  "model": "gemini-2.0-flash-exp",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "让我为您查询东京的天气。",
        "tool_calls": [
          {
            "id": "call_abc123def456",
            "type": "function",
            "function": {
              "name": "get_weather",
              "arguments": "{\"location\":\"Tokyo\",\"unit\":\"celsius\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 45,
    "completion_tokens": 20,
    "total_tokens": 65
  }
}
```

---

## 多轮工具调用

### 示例 2: 完整的工具调用对话流程

**第一轮 - 用户提问：**

```json
{
  "model": "gemini-2.0-flash-exp",
  "messages": [
    {
      "role": "user",
      "content": "东京现在的天气怎么样？"
    }
  ],
  "tools": [...]
}
```

**第一轮 - 模型响应（要求调用工具）：**

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "tool_calls": [{
        "id": "call_abc123",
        "function": {
          "name": "get_weather",
          "arguments": "{\"location\":\"Tokyo\"}"
        }
      }]
    },
    "finish_reason": "tool_calls"
  }]
}
```

**第二轮 - 提供工具执行结果：**

```json
{
  "model": "gemini-2.0-flash-exp",
  "messages": [
    {
      "role": "user",
      "content": "东京现在的天气怎么样？"
    },
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [{
        "id": "call_abc123",
        "type": "function",
        "function": {
          "name": "get_weather",
          "arguments": "{\"location\":\"Tokyo\"}"
        }
      }]
    },
    {
      "role": "tool",
      "tool_call_id": "call_abc123",
      "name": "get_weather",
      "content": "{\"temperature\": 18, \"condition\": \"Cloudy\", \"humidity\": 65}"
    }
  ],
  "tools": [...]
}
```

**第二轮 - 模型最终响应：**

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "东京现在的天气是多云，温度 18°C，湿度 65%。比较舒适的天气。"
    },
    "finish_reason": "stop"
  }]
}
```

---

## tool_choice 选项

### auto（默认）- 模型自动决定

```json
{
  "messages": [...],
  "tools": [...],
  "tool_choice": "auto"
}
```

### none - 强制不使用工具

```json
{
  "messages": [...],
  "tools": [...],
  "tool_choice": "none"
}
```

### required - 强制使用工具

```json
{
  "messages": [...],
  "tools": [...],
  "tool_choice": "required"
}
```

### 指定特定工具

```json
{
  "messages": [...],
  "tools": [...],
  "tool_choice": {
    "type": "function",
    "function": {
      "name": "get_weather"
    }
  }
}
```

---

## 多个工具定义

### 示例 3: 定义多个工具

```json
{
  "model": "gemini-2.0-flash-exp",
  "messages": [
    {
      "role": "user",
      "content": "帮我查一下北京的天气，然后计算 15 乘以 7 等于多少"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_weather",
        "description": "获取天气信息",
        "parameters": {
          "type": "object",
          "properties": {
            "location": {"type": "string"}
          },
          "required": ["location"]
        }
      }
    },
    {
      "type": "function",
      "function": {
        "name": "calculate",
        "description": "执行数学计算",
        "parameters": {
          "type": "object",
          "properties": {
            "expression": {
              "type": "string",
              "description": "数学表达式，例如：15 * 7"
            }
          },
          "required": ["expression"]
        }
      }
    }
  ]
}
```

**可能的响应（模型可能同时调用多个工具）：**

```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "tool_calls": [
        {
          "id": "call_weather_001",
          "function": {
            "name": "get_weather",
            "arguments": "{\"location\":\"北京\"}"
          }
        },
        {
          "id": "call_calc_002",
          "function": {
            "name": "calculate",
            "arguments": "{\"expression\":\"15 * 7\"}"
          }
        }
      ]
    },
    "finish_reason": "tool_calls"
  }]
}
```

---

## 与 Google Search 结合

### 示例 4: 自定义工具 + Google Search

对于带 `-search` 后缀的模型，gcli2api 会自动添加 Google Search 工具：

```json
{
  "model": "gemini-2.0-flash-exp-search",
  "messages": [
    {
      "role": "user",
      "content": "Python 3.12 的新特性是什么？并帮我查询今天的日期。"
    }
  ],
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "get_current_date",
        "description": "获取当前日期",
        "parameters": {
          "type": "object",
          "properties": {}
        }
      }
    }
  ]
}
```

此时，Gemini 会同时拥有两个工具：
1. 用户定义的 `get_current_date`
2. 自动添加的 `googleSearch`

---

## 流式工具调用

### 示例 5: 流式响应中的工具调用

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.0-flash-exp",
    "messages": [
      {
        "role": "user",
        "content": "东京的天气如何？"
      }
    ],
    "tools": [
      {
        "type": "function",
        "function": {
          "name": "get_weather",
          "description": "获取天气",
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
    "stream": true
  }'
```

**流式响应格式：**

```
data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1699896916,"model":"gemini-2.0-flash-exp","choices":[{"index":0,"delta":{"content":"让我为您查询"},"finish_reason":null}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","created":1699896916,"model":"gemini-2.0-flash-exp","choices":[{"index":0,"delta":{"tool_calls":[{"id":"call_abc123","type":"function","function":{"name":"get_weather","arguments":"{\"location\":\"Tokyo\"}"}}]},"finish_reason":"tool_calls"}]}

data: [DONE]
```

---

## Python 客户端示例

### 使用 OpenAI Python SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="dummy"  # gcli2api 不需要 API key
)

# 定义工具
tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "获取天气信息",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "城市名称"
                    }
                },
                "required": ["location"]
            }
        }
    }
]

# 第一轮对话
response = client.chat.completions.create(
    model="gemini-2.0-flash-exp",
    messages=[
        {"role": "user", "content": "东京的天气怎么样？"}
    ],
    tools=tools
)

# 检查是否需要调用工具
if response.choices[0].finish_reason == "tool_calls":
    tool_calls = response.choices[0].message.tool_calls

    for tool_call in tool_calls:
        function_name = tool_call.function.name
        function_args = json.loads(tool_call.function.arguments)

        # 执行实际的工具调用
        if function_name == "get_weather":
            result = get_weather(**function_args)  # 你的实际函数

            # 第二轮对话，提供工具结果
            response = client.chat.completions.create(
                model="gemini-2.0-flash-exp",
                messages=[
                    {"role": "user", "content": "东京的天气怎么样？"},
                    response.choices[0].message,  # assistant 的工具调用
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": json.dumps(result)
                    }
                ],
                tools=tools
            )

    print(response.choices[0].message.content)
```

---

## 常见问题

### Q1: 工具定义的参数支持哪些 JSON Schema 特性？

**支持的特性：**
- `type`: string, number, integer, boolean, array, object
- `description`: 参数描述
- `enum`: 枚举值
- `required`: 必需参数列表
- `properties`: 对象属性
- `items`: 数组元素类型

**不支持的特性：**
- `default`: 默认值
- `optional`: 可选标记
- `oneOf`, `anyOf`, `allOf`: 联合类型
- `maximum`, `minimum`: 数值范围

### Q2: 可以同时定义多少个工具？

理论上没有限制，但建议不超过 10 个工具以获得最佳性能。

### Q3: 工具调用会消耗多少 token？

工具定义会作为上下文的一部分，通常每个工具定义会消耗 50-200 tokens，取决于描述的详细程度。

### Q4: 如何处理工具调用失败？

如果工具执行失败，可以在 `role: "tool"` 消息中返回错误信息：

```json
{
  "role": "tool",
  "tool_call_id": "call_abc123",
  "name": "get_weather",
  "content": "{\"error\": \"Failed to fetch weather data\", \"code\": 500}"
}
```

模型会理解错误并给出相应的回复。

### Q5: 支持并行工具调用吗？

是的！Gemini 可以在一次响应中返回多个工具调用。你需要分别执行每个工具，然后在下一轮请求中提供所有工具的结果。

---

## 调试技巧

### 查看转换后的 Gemini 请求

启用调试日志：

```bash
export LOG_LEVEL=DEBUG
python web.py
```

你会看到类似的日志：

```
[DEBUG] OpenAI tools converted to Gemini:
{
  "functionDeclarations": [
    {
      "name": "get_weather",
      "description": "获取天气信息",
      "parameters": {...}
    }
  ]
}
```

### 验证工具定义

使用测试脚本验证工具定义：

```bash
python test_tool_calling.py
```

---

## 更多示例

完整的示例代码可以在 `test_tool_calling.py` 中找到，包括：
- 工具定义转换测试
- tool_choice 转换测试
- 完整的多轮对话示例
- 流式工具调用示例

---

## 相关文档

- [完整分析文档](./TOOL_CALLING_ANALYSIS.md) - 实现原理和技术细节
- [OpenAI Function Calling 文档](https://platform.openai.com/docs/guides/function-calling)
- [Gemini Function Calling 文档](https://ai.google.dev/gemini-api/docs/function-calling)
