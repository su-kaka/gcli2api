"""
Tool Calling Implementation Tests
测试工具调用功能的实现
"""

import json
import asyncio
from src.openai_transfer import (
    convert_openai_tools_to_gemini,
    convert_tool_choice_to_tool_config,
    extract_tool_calls_from_parts,
    openai_request_to_gemini_payload,
    gemini_response_to_openai,
    gemini_stream_chunk_to_openai,
)
from src.models import ChatCompletionRequest, OpenAIChatMessage


def test_convert_openai_tools_to_gemini():
    """测试 OpenAI 工具格式到 Gemini 格式的转换"""
    print("测试 1: 工具定义转换")

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

    result = convert_openai_tools_to_gemini(openai_tools)

    assert len(result) == 1, "应该返回一个工具对象"
    assert "functionDeclarations" in result[0], "应该包含 functionDeclarations"
    assert len(result[0]["functionDeclarations"]) == 1, "应该有一个函数声明"
    assert result[0]["functionDeclarations"][0]["name"] == "get_weather", "函数名应该匹配"
    assert "parameters" in result[0]["functionDeclarations"][0], "应该包含参数"

    print("✅ 工具定义转换测试通过")
    print(f"   结果: {json.dumps(result, indent=2, ensure_ascii=False)}\n")


def test_convert_tool_choice():
    """测试 tool_choice 转换"""
    print("测试 2: tool_choice 转换")

    # 测试 "auto"
    result_auto = convert_tool_choice_to_tool_config("auto")
    assert result_auto["functionCallingConfig"]["mode"] == "AUTO"
    print("✅ tool_choice='auto' 转换正确")

    # 测试 "required"
    result_required = convert_tool_choice_to_tool_config("required")
    assert result_required["functionCallingConfig"]["mode"] == "ANY"
    print("✅ tool_choice='required' 转换正确")

    # 测试 "none"
    result_none = convert_tool_choice_to_tool_config("none")
    assert result_none["functionCallingConfig"]["mode"] == "NONE"
    print("✅ tool_choice='none' 转换正确")

    # 测试指定函数
    result_specific = convert_tool_choice_to_tool_config({
        "type": "function",
        "function": {"name": "my_func"}
    })
    assert result_specific["functionCallingConfig"]["mode"] == "ANY"
    assert "my_func" in result_specific["functionCallingConfig"]["allowedFunctionNames"]
    print("✅ tool_choice 指定函数转换正确\n")


def test_extract_tool_calls():
    """测试从 Gemini parts 提取工具调用"""
    print("测试 3: 提取工具调用")

    parts = [
        {
            "text": "让我为您查询天气。"
        },
        {
            "functionCall": {
                "name": "get_weather",
                "args": {
                    "location": "Boston",
                    "unit": "celsius"
                }
            }
        }
    ]

    tool_calls, text = extract_tool_calls_from_parts(parts)

    assert len(tool_calls) == 1, "应该提取到一个工具调用"
    assert tool_calls[0]["type"] == "function", "工具类型应该是 function"
    assert tool_calls[0]["function"]["name"] == "get_weather", "函数名应该匹配"
    assert "Boston" in tool_calls[0]["function"]["arguments"], "参数应该包含 Boston"
    assert "让我为您查询天气" in text, "应该提取到文本内容"

    print("✅ 工具调用提取测试通过")
    print(f"   提取到的工具调用: {tool_calls[0]['function']['name']}")
    print(f"   提取到的文本: {text}\n")


async def test_full_request_conversion():
    """测试完整的请求转换流程"""
    print("测试 4: 完整请求转换")

    # 构造 OpenAI 请求
    request_data = {
        "model": "gemini-2.0-flash-exp",
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
                            "location": {"type": "string", "description": "City name"}
                        },
                        "required": ["location"]
                    }
                }
            }
        ],
        "tool_choice": "auto"
    }

    openai_request = ChatCompletionRequest(**request_data)
    gemini_payload = await openai_request_to_gemini_payload(openai_request)

    # 验证转换结果
    assert "model" in gemini_payload
    assert "request" in gemini_payload
    request = gemini_payload["request"]

    assert "tools" in request, "应该包含 tools"
    assert "functionDeclarations" in request["tools"][0], "应该有 functionDeclarations"
    assert request["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"

    assert "toolConfig" in request, "应该包含 toolConfig"
    assert request["toolConfig"]["functionCallingConfig"]["mode"] == "AUTO"

    print("✅ 完整请求转换测试通过")
    print(f"   模型: {gemini_payload['model']}")
    print(f"   工具数量: {len(request['tools'][0]['functionDeclarations'])}")
    print(f"   toolConfig 模式: {request['toolConfig']['functionCallingConfig']['mode']}\n")


def test_response_conversion_with_tool_calls():
    """测试包含工具调用的响应转换"""
    print("测试 5: 响应转换（包含工具调用）")

    # 模拟 Gemini 响应
    gemini_response = {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [
                        {
                            "text": "我将为您查询东京的天气。"
                        },
                        {
                            "functionCall": {
                                "name": "get_weather",
                                "args": {
                                    "location": "Tokyo"
                                }
                            }
                        }
                    ]
                },
                "finishReason": "STOP",
                "index": 0
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 15,
            "totalTokenCount": 35
        }
    }

    openai_response = gemini_response_to_openai(gemini_response, "gemini-2.0-flash-exp")

    # 验证响应
    assert "choices" in openai_response
    assert len(openai_response["choices"]) == 1

    choice = openai_response["choices"][0]
    message = choice["message"]

    assert "tool_calls" in message, "应该包含 tool_calls"
    assert len(message["tool_calls"]) == 1, "应该有一个工具调用"
    assert message["tool_calls"][0]["function"]["name"] == "get_weather"
    assert choice["finish_reason"] == "tool_calls", "finish_reason 应该是 tool_calls"

    # 验证 usage
    assert "usage" in openai_response
    assert openai_response["usage"]["prompt_tokens"] == 20
    assert openai_response["usage"]["completion_tokens"] == 15

    print("✅ 响应转换测试通过")
    print(f"   finish_reason: {choice['finish_reason']}")
    print(f"   工具调用: {message['tool_calls'][0]['function']['name']}")
    print(f"   文本内容: {message.get('content', 'None')}\n")


async def test_multi_turn_with_tool_result():
    """测试包含工具执行结果的多轮对话"""
    print("测试 6: 多轮对话（包含工具结果）")

    request_data = {
        "model": "gemini-2.0-flash-exp",
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
                "content": '{"temperature": 18, "condition": "Cloudy", "humidity": 65}'
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string"}
                        }
                    }
                }
            }
        ]
    }

    openai_request = ChatCompletionRequest(**request_data)
    gemini_payload = await openai_request_to_gemini_payload(openai_request)

    contents = gemini_payload["request"]["contents"]

    # 应该有 3 条消息：user, model (with functionCall), user (with functionResponse)
    assert len(contents) == 3, f"应该有 3 条消息，实际有 {len(contents)}"

    # 检查第一条消息（用户提问）
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0]["text"] == "What's the weather in Tokyo?"

    # 检查第二条消息（助手的工具调用）
    assert contents[1]["role"] == "model"
    assert "functionCall" in contents[1]["parts"][0]
    assert contents[1]["parts"][0]["functionCall"]["name"] == "get_weather"

    # 检查第三条消息（工具结果）
    assert contents[2]["role"] == "user"
    assert "functionResponse" in contents[2]["parts"][0]
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "get_weather"
    assert "temperature" in contents[2]["parts"][0]["functionResponse"]["response"]

    print("✅ 多轮对话测试通过")
    print(f"   消息数量: {len(contents)}")
    print(f"   工具结果已正确转换为 functionResponse\n")


def test_tool_message_without_name():
    """测试 tool 消息缺少 name 字段的错误处理"""
    print("测试 7: tool 消息缺少 name 字段")

    from src.openai_transfer import convert_tool_message_to_function_response

    # 创建一个没有 name 的 tool 消息（使用普通对象模拟）
    class MockMessage:
        def __init__(self):
            self.role = "tool"
            self.tool_call_id = "call_123"
            self.content = '{"result": "success"}'
            self.name = None  # 缺少 name

    message = MockMessage()

    try:
        convert_tool_message_to_function_response(message)
        assert False, "应该抛出 ValueError"
    except ValueError as e:
        assert "name" in str(e).lower(), "错误消息应该提到 'name'"
        print("✅ 正确捕获缺少 name 的错误")
        print(f"   错误消息: {e}\n")


async def test_tool_message_name_inference():
    """测试从历史消息中推断 tool 消息的 name 字段"""
    print("测试 8: tool 消息 name 字段自动推断")

    # 创建一个完整的对话，包括 assistant 的 tool_call 和 user 的 tool 响应
    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            {
                "role": "user",
                "content": "What's the weather?"
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
                # 故意不设置 name 字段，测试推断功能
                "content": '{"temperature": 20, "condition": "sunny"}'
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]
    }

    openai_request = ChatCompletionRequest(**request_data)
    gemini_payload = await openai_request_to_gemini_payload(openai_request)

    contents = gemini_payload["request"]["contents"]

    # 应该有 3 条消息
    assert len(contents) == 3, f"应该有 3 条消息，实际有 {len(contents)}"

    # 检查第三条消息（工具结果）- name 应该被正确推断
    assert contents[2]["role"] == "user"
    assert "functionResponse" in contents[2]["parts"][0]
    function_response = contents[2]["parts"][0]["functionResponse"]

    # 验证 name 被正确推断为 get_weather
    assert function_response["name"] == "get_weather", \
        f"应该推断出 name='get_weather'，实际是 '{function_response['name']}'"
    assert "temperature" in function_response["response"]

    print("✅ 成功从历史消息中推断出函数名")
    print(f"   tool_call_id='call_abc123' -> name='get_weather'")
    print(f"   工具响应已正确转换\n")


async def test_invalid_tool_call_arguments():
    """测试无效的 tool_call arguments 处理"""
    print("测试 9: 无效的 tool_call arguments")

    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            {
                "role": "user",
                "content": "测试"
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "test_function",
                            "arguments": "这不是有效的JSON{{"  # 无效的 JSON
                        }
                    }
                ]
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "test_function",
                    "description": "测试函数",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]
    }

    openai_request = ChatCompletionRequest(**request_data)

    try:
        gemini_payload = await openai_request_to_gemini_payload(openai_request)
        # 如果没有抛出异常，检查是否正确跳过了无效的 tool_call
        # 因为没有 content，应该抛出 ValueError
        assert False, "应该抛出 ValueError，因为所有 tool_calls 都失败且没有 content"
    except ValueError as e:
        assert "tool calls failed" in str(e).lower()
        print("✅ 正确处理所有 tool_calls 解析失败的情况")
        print(f"   错误消息: {e}\n")


async def test_partial_tool_call_failure():
    """测试部分 tool_calls 失败的处理"""
    print("测试 10: 部分 tool_calls 失败")

    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            {
                "role": "user",
                "content": "测试"
            },
            {
                "role": "assistant",
                "content": "正在处理",
                "tool_calls": [
                    {
                        "id": "call_good",
                        "type": "function",
                        "function": {
                            "name": "good_function",
                            "arguments": '{"param": "value"}'  # 有效的 JSON
                        }
                    },
                    {
                        "id": "call_bad",
                        "type": "function",
                        "function": {
                            "name": "bad_function",
                            "arguments": "无效JSON{{"  # 无效的 JSON
                        }
                    }
                ]
            }
        ],
        "tools": [{"type": "function", "function": {"name": "test", "parameters": {}}}]
    }

    openai_request = ChatCompletionRequest(**request_data)
    gemini_payload = await openai_request_to_gemini_payload(openai_request)

    # 检查结果
    contents = gemini_payload["request"]["contents"]
    assert len(contents) == 2  # user 和 assistant 消息

    # 检查 assistant 消息
    assistant_msg = contents[1]
    assert assistant_msg["role"] == "model"

    # 应该有 1 个有效的 functionCall 和 1 个文本 part
    parts = assistant_msg["parts"]
    has_text = any("text" in part for part in parts)
    has_function_call = any("functionCall" in part for part in parts)

    assert has_text, "应该有文本内容"
    assert has_function_call, "应该有至少一个成功的 functionCall"

    # 统计成功解析的 functionCall
    function_calls = [p for p in parts if "functionCall" in p]
    assert len(function_calls) == 1, f"应该有 1 个成功的 functionCall，实际有 {len(function_calls)}"
    assert function_calls[0]["functionCall"]["name"] == "good_function"

    print("✅ 正确处理部分 tool_calls 失败的情况")
    print(f"   成功解析: 1/2 tool_calls")
    print(f"   保留了文本内容和有效的工具调用\n")


def test_streaming_tool_calls_with_index():
    """测试流式响应中的 tool_calls 包含 index 字段"""
    print("测试 11: 流式响应 tool_calls index 字段")

    # 模拟 Gemini 流式响应块，包含工具调用
    gemini_chunk = {
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
                        },
                        {
                            "functionCall": {
                                "name": "get_time",
                                "args": {"timezone": "Asia/Tokyo"}
                            }
                        }
                    ]
                },
                "finishReason": "STOP"
            }
        ]
    }

    # 转换为 OpenAI 流式格式
    result = gemini_stream_chunk_to_openai(gemini_chunk, "gemini-pro", "test-id-123")

    # 验证结果
    assert "choices" in result
    assert len(result["choices"]) > 0

    choice = result["choices"][0]
    assert "delta" in choice
    assert "tool_calls" in choice["delta"]

    tool_calls = choice["delta"]["tool_calls"]
    assert len(tool_calls) == 2, f"应该有 2 个 tool_calls，实际有 {len(tool_calls)}"

    # 验证每个 tool_call 都有 index 字段
    for i, tool_call in enumerate(tool_calls):
        assert "index" in tool_call, f"tool_call {i} 缺少 index 字段"
        assert "id" in tool_call, f"tool_call {i} 缺少 id 字段"
        assert "type" in tool_call, f"tool_call {i} 缺少 type 字段"
        assert tool_call["type"] == "function"
        assert "function" in tool_call
        assert "name" in tool_call["function"]
        assert "arguments" in tool_call["function"]

    # 验证 index 值正确
    assert tool_calls[0]["index"] == 0, f"第一个 tool_call 的 index 应该是 0，实际是 {tool_calls[0]['index']}"
    assert tool_calls[1]["index"] == 1, f"第二个 tool_call 的 index 应该是 1，实际是 {tool_calls[1]['index']}"

    # 验证 finish_reason 是 tool_calls
    assert choice["finish_reason"] == "tool_calls"

    print("✅ 流式响应 tool_calls 正确包含 index 字段")
    print(f"   验证了 {len(tool_calls)} 个 tool_calls 的格式")
    print(f"   每个 tool_call 都有必需的 index 字段\n")


def test_function_name_normalization():
    """测试函数名规范化转换"""
    print("测试 12: 函数名规范化转换")

    from src.openai_transfer import convert_openai_tools_to_gemini

    # 测试用例：需要转换的函数名
    normalization_cases = [
        ("123start", "_123start", "以数字开头 -> 添加下划线前缀"),
        ("-start", "_start", "以短横线开头 -> 添加下划线前缀"),
        (".start", "_start", "以点开头 -> 添加下划线前缀"),
        ("has space", "has_space", "包含空格 -> 替换为下划线"),
        ("has@symbol", "has_symbol", "包含 @ -> 替换为下划线"),
        ("has#hash", "has_hash", "包含 # -> 替换为下划线"),
        ("multiple   spaces", "multiple_spaces", "多个空格 -> 合并为单个下划线"),
        ("has__double", "has_double", "连续下划线 -> 合并为单个"),
        ("a" * 65, "a" * 64, "超过 64 字符 -> 截断"),
        ("", "_unnamed_function", "空字符串 -> 使用默认名称"),
        ("_leading", "_leading", "下划线开头 -> 保持不变"),
        ("中文函数名", "zhongwenhanshuming", "中文字符 -> 转换为拼音"),
        ("获取天气", "huoqutianqi", "中文函数 -> 拼音转换"),
        ("get用户info", "getyonghuinfo", "中英混合 -> 中文转拼音保留英文"),
    ]

    failed_count = 0
    for original_name, expected_name, description in normalization_cases:
        tools = [{
            "type": "function",
            "function": {
                "name": original_name,
                "description": "Test function",
                "parameters": {"type": "object"}
            }
        }]

        try:
            result = convert_openai_tools_to_gemini(tools)
            assert len(result) == 1, f"应该返回 1 个工具，实际返回 {len(result)}"

            actual_name = result[0]["functionDeclarations"][0]["name"]

            if actual_name == expected_name:
                print(f"   ✅ {description}")
                print(f"      '{original_name}' -> '{actual_name}'")
            else:
                print(f"   ❌ {description}")
                print(f"      期望: '{expected_name}'")
                print(f"      实际: '{actual_name}'")
                failed_count += 1
        except Exception as e:
            print(f"   ❌ 转换失败: {description}")
            print(f"      错误: {e}")
            failed_count += 1

    # 测试用例：不需要转换的有效函数名
    valid_names = [
        ("get_weather", "标准命名"),
        ("GetWeather", "驼峰命名"),
        ("_private_function", "下划线开头"),
        ("function_123", "包含数字"),
        ("function.with.dots", "包含点"),
        ("function-with-dashes", "包含短横线"),
        ("a" * 64, "正好 64 字符"),
    ]

    for valid_name, description in valid_names:
        tools = [{
            "type": "function",
            "function": {
                "name": valid_name,
                "description": "Test function",
                "parameters": {"type": "object"}
            }
        }]

        try:
            result = convert_openai_tools_to_gemini(tools)
            assert len(result) == 1
            actual_name = result[0]["functionDeclarations"][0]["name"]
            assert actual_name == valid_name, f"有效名称不应被修改: {valid_name} -> {actual_name}"
        except Exception as e:
            print(f"   ❌ 有效名称处理失败: {description} - '{valid_name}'")
            print(f"      错误: {e}")
            failed_count += 1

    if failed_count == 0:
        print(f"✅ 函数名规范化测试通过（测试了 {len(normalization_cases)} 个转换用例和 {len(valid_names)} 个有效名称）\n")
    else:
        print(f"❌ 函数名规范化测试失败：{failed_count} 个测试未通过\n")
        raise AssertionError(f"{failed_count} normalization tests failed")


async def run_all_tests():
    """运行所有测试"""
    print("=" * 60)
    print("开始运行工具调用功能测试")
    print("=" * 60 + "\n")

    try:
        # 基础功能测试
        test_convert_openai_tools_to_gemini()
        test_convert_tool_choice()
        test_extract_tool_calls()
        await test_full_request_conversion()
        test_response_conversion_with_tool_calls()
        await test_multi_turn_with_tool_result()

        # 错误处理测试
        test_tool_message_without_name()
        await test_tool_message_name_inference()
        await test_invalid_tool_call_arguments()
        await test_partial_tool_call_failure()

        # 流式响应测试
        test_streaming_tool_calls_with_index()

        # 函数名规范化测试
        test_function_name_normalization()

        print("=" * 60)
        print("✅ 所有测试通过！")
        print("=" * 60)
        return True
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"\n❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    exit(0 if success else 1)
