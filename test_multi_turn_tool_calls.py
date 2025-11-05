"""
测试连续多轮工具调用场景
"""

import asyncio
from src.openai_transfer import openai_request_to_gemini_payload
from src.models import ChatCompletionRequest


async def test_continuous_tool_calls():
    """测试连续多轮工具调用对话"""
    print("=" * 60)
    print("测试：连续多轮工具调用")
    print("=" * 60)

    # 模拟一个完整的多轮工具调用对话
    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            # 第一轮：用户请求
            {
                "role": "user",
                "content": "今天北京和上海的天气怎么样？"
            },
            # 第一轮：助手调用工具（两个工具调用）
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_beijing_001",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "北京"}'
                        }
                    },
                    {
                        "id": "call_shanghai_001",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "上海"}'
                        }
                    }
                ]
            },
            # 第一轮：工具响应1（北京）- 缺少 name 字段，测试推断
            {
                "role": "tool",
                "tool_call_id": "call_beijing_001",
                "content": '{"temperature": 15, "condition": "晴"}'
            },
            # 第一轮：工具响应2（上海）- 缺少 name 字段，测试推断
            {
                "role": "tool",
                "tool_call_id": "call_shanghai_001",
                "content": '{"temperature": 20, "condition": "多云"}'
            },
            # 第一轮：助手总结
            {
                "role": "assistant",
                "content": "北京今天15度，晴天。上海今天20度，多云。"
            },
            # 第二轮：用户继续提问
            {
                "role": "user",
                "content": "那深圳呢？"
            },
            # 第二轮：助手调用工具
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_shenzhen_001",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "深圳"}'
                        }
                    }
                ]
            },
            # 第二轮：工具响应（深圳）- 缺少 name 字段
            {
                "role": "tool",
                "tool_call_id": "call_shenzhen_001",
                "content": '{"temperature": 25, "condition": "阴"}'
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
                            "city": {
                                "type": "string",
                                "description": "城市名称"
                            }
                        },
                        "required": ["city"]
                    }
                }
            }
        ]
    }

    try:
        openai_request = ChatCompletionRequest(**request_data)
        gemini_payload = await openai_request_to_gemini_payload(openai_request)

        contents = gemini_payload["request"]["contents"]

        print(f"\n转换后的消息数量: {len(contents)}")

        # 验证消息结构
        for i, content in enumerate(contents):
            role = content["role"]
            parts = content["parts"]

            print(f"\n消息 {i+1}: role={role}")
            for j, part in enumerate(parts):
                if "text" in part:
                    print(f"  part {j+1}: text={part['text'][:50]}...")
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    print(f"  part {j+1}: functionCall(name={fc['name']}, args={fc.get('args', {})})")
                elif "functionResponse" in part:
                    fr = part["functionResponse"]
                    print(f"  part {j+1}: functionResponse(name={fr['name']}, response={fr['response']})")

        # 验证关键点
        assert len(contents) > 0, "应该有转换后的消息"

        # 查找所有 functionResponse
        function_responses = []
        for content in contents:
            for part in content["parts"]:
                if "functionResponse" in part:
                    function_responses.append(part["functionResponse"])

        print(f"\n找到 {len(function_responses)} 个 functionResponse")

        # 验证每个 functionResponse 都有正确的 name
        for i, fr in enumerate(function_responses):
            assert "name" in fr, f"functionResponse {i+1} 缺少 name 字段"
            assert fr["name"] == "get_weather", f"functionResponse {i+1} name 应该是 'get_weather'，实际是 '{fr['name']}'"
            print(f"✅ functionResponse {i+1}: name='{fr['name']}'")

        print("\n" + "=" * 60)
        print("✅ 连续多轮工具调用测试通过")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_interleaved_tool_calls():
    """测试交错的工具调用（不同工具）"""
    print("\n" + "=" * 60)
    print("测试：交错的不同工具调用")
    print("=" * 60)

    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            {
                "role": "user",
                "content": "查询天气和设置提醒"
            },
            # 调用天气工具
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_weather_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "北京"}'
                        }
                    }
                ]
            },
            # 天气工具响应 - 缺少 name
            {
                "role": "tool",
                "tool_call_id": "call_weather_123",
                "content": '{"temperature": 15}'
            },
            # 助手响应后再调用另一个工具
            {
                "role": "assistant",
                "content": "天气是15度。现在帮你设置提醒。",
                "tool_calls": [
                    {
                        "id": "call_reminder_456",
                        "type": "function",
                        "function": {
                            "name": "set_reminder",
                            "arguments": '{"time": "18:00", "message": "查看天气"}'
                        }
                    }
                ]
            },
            # 提醒工具响应 - 缺少 name
            {
                "role": "tool",
                "tool_call_id": "call_reminder_456",
                "content": '{"success": true}'
            }
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "获取天气",
                    "parameters": {"type": "object", "properties": {}}
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "set_reminder",
                    "description": "设置提醒",
                    "parameters": {"type": "object", "properties": {}}
                }
            }
        ]
    }

    try:
        openai_request = ChatCompletionRequest(**request_data)
        gemini_payload = await openai_request_to_gemini_payload(openai_request)

        contents = gemini_payload["request"]["contents"]

        # 查找所有 functionResponse
        function_responses = []
        for content in contents:
            for part in content["parts"]:
                if "functionResponse" in part:
                    function_responses.append(part["functionResponse"])

        print(f"\n找到 {len(function_responses)} 个 functionResponse")
        assert len(function_responses) == 2, f"应该有 2 个 functionResponse，实际有 {len(function_responses)}"

        # 验证第一个是 get_weather
        assert function_responses[0]["name"] == "get_weather", \
            f"第一个应该是 'get_weather'，实际是 '{function_responses[0]['name']}'"
        print(f"✅ functionResponse 1: name='get_weather'")

        # 验证第二个是 set_reminder
        assert function_responses[1]["name"] == "set_reminder", \
            f"第二个应该是 'set_reminder'，实际是 '{function_responses[1]['name']}'"
        print(f"✅ functionResponse 2: name='set_reminder'")

        print("\n" + "=" * 60)
        print("✅ 交错工具调用测试通过")
        print("=" * 60)
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    results = []

    results.append(await test_continuous_tool_calls())
    results.append(await test_interleaved_tool_calls())

    print("\n" + "=" * 60)
    if all(results):
        print("✅ 所有多轮工具调用测试通过")
    else:
        print("❌ 部分测试失败")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
