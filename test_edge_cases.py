"""
测试工具调用的边界情况
"""

import asyncio
from src.openai_transfer import openai_request_to_gemini_payload
from src.models import ChatCompletionRequest


async def test_tool_response_order_mismatch():
    """测试工具响应顺序与调用顺序不一致的情况"""
    print("=" * 60)
    print("测试：工具响应顺序不匹配")
    print("=" * 60)

    # OpenAI API 允许 tool 消息的顺序与 tool_calls 的顺序不一致
    # 只要 tool_call_id 匹配即可
    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            {
                "role": "user",
                "content": "测试"
            },
            # 调用三个工具
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_001",
                        "type": "function",
                        "function": {
                            "name": "tool_a",
                            "arguments": '{}'
                        }
                    },
                    {
                        "id": "call_002",
                        "type": "function",
                        "function": {
                            "name": "tool_b",
                            "arguments": '{}'
                        }
                    },
                    {
                        "id": "call_003",
                        "type": "function",
                        "function": {
                            "name": "tool_c",
                            "arguments": '{}'
                        }
                    }
                ]
            },
            # 工具响应的顺序：3, 1, 2（乱序）- 都缺少 name
            {
                "role": "tool",
                "tool_call_id": "call_003",  # 第3个
                "content": '{"result": "c"}'
            },
            {
                "role": "tool",
                "tool_call_id": "call_001",  # 第1个
                "content": '{"result": "a"}'
            },
            {
                "role": "tool",
                "tool_call_id": "call_002",  # 第2个
                "content": '{"result": "b"}'
            }
        ],
        "tools": [
            {"type": "function", "function": {"name": "tool_a", "description": "A", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "tool_b", "description": "B", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "tool_c", "description": "C", "parameters": {"type": "object"}}}
        ]
    }

    try:
        openai_request = ChatCompletionRequest(**request_data)
        gemini_payload = await openai_request_to_gemini_payload(openai_request)

        contents = gemini_payload["request"]["contents"]

        # 查找所有 functionResponse 并验证顺序
        function_responses = []
        for content in contents:
            for part in content["parts"]:
                if "functionResponse" in part:
                    function_responses.append(part["functionResponse"])

        print(f"\n找到 {len(function_responses)} 个 functionResponse")
        assert len(function_responses) == 3, f"应该有 3 个 functionResponse"

        # 验证名称是否正确（按响应顺序：tool_c, tool_a, tool_b）
        expected_names = ["tool_c", "tool_a", "tool_b"]
        for i, (fr, expected_name) in enumerate(zip(function_responses, expected_names)):
            actual_name = fr["name"]
            print(f"  响应 {i+1}: name='{actual_name}', response={fr['response']}")
            assert actual_name == expected_name, \
                f"响应 {i+1} 应该是 '{expected_name}'，实际是 '{actual_name}'"

        print("\n✅ 工具响应顺序不匹配测试通过")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_multiple_assistant_messages_with_tools():
    """测试多个 assistant 消息都包含 tool_calls 的情况"""
    print("\n" + "=" * 60)
    print("测试：多个 assistant 消息都有 tool_calls")
    print("=" * 60)

    request_data = {
        "model": "gemini-2.0-flash-exp",
        "messages": [
            {
                "role": "user",
                "content": "第一个请求"
            },
            # 第一个 assistant 有 tool_calls
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "first_call",
                        "type": "function",
                        "function": {
                            "name": "first_tool",
                            "arguments": '{}'
                        }
                    }
                ]
            },
            # 第一个工具响应 - 缺少 name
            {
                "role": "tool",
                "tool_call_id": "first_call",
                "content": '{"result": "first"}'
            },
            # assistant 回复
            {
                "role": "assistant",
                "content": "第一个完成"
            },
            # 用户继续
            {
                "role": "user",
                "content": "第二个请求"
            },
            # 第二个 assistant 也有 tool_calls（使用相同的工具名）
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "second_call",
                        "type": "function",
                        "function": {
                            "name": "first_tool",  # 相同的工具名
                            "arguments": '{}'
                        }
                    }
                ]
            },
            # 第二个工具响应 - 缺少 name
            {
                "role": "tool",
                "tool_call_id": "second_call",
                "content": '{"result": "second"}'
            }
        ],
        "tools": [
            {"type": "function", "function": {"name": "first_tool", "description": "Test", "parameters": {"type": "object"}}}
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
        assert len(function_responses) == 2, f"应该有 2 个 functionResponse"

        # 两个响应都应该正确推断为 first_tool
        for i, fr in enumerate(function_responses):
            print(f"  响应 {i+1}: name='{fr['name']}', response={fr['response']}")
            assert fr["name"] == "first_tool", \
                f"响应 {i+1} 应该是 'first_tool'，实际是 '{fr['name']}'"

        print("\n✅ 多个 assistant 消息测试通过")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_tool_with_both_name_and_inference():
    """测试混合：一些有 name，一些需要推断"""
    print("\n" + "=" * 60)
    print("测试：混合 name 字段和推断")
    print("=" * 60)

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
                        "id": "call_001",
                        "type": "function",
                        "function": {
                            "name": "tool_a",
                            "arguments": '{}'
                        }
                    },
                    {
                        "id": "call_002",
                        "type": "function",
                        "function": {
                            "name": "tool_b",
                            "arguments": '{}'
                        }
                    }
                ]
            },
            # 第一个有 name
            {
                "role": "tool",
                "tool_call_id": "call_001",
                "name": "tool_a",  # 有 name 字段
                "content": '{"result": "a"}'
            },
            # 第二个缺少 name，需要推断
            {
                "role": "tool",
                "tool_call_id": "call_002",
                # 没有 name 字段
                "content": '{"result": "b"}'
            }
        ],
        "tools": [
            {"type": "function", "function": {"name": "tool_a", "description": "A", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "tool_b", "description": "B", "parameters": {"type": "object"}}}
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
        assert len(function_responses) == 2

        # 验证两个都正确
        assert function_responses[0]["name"] == "tool_a"
        print(f"  响应 1: name='tool_a' (使用提供的 name)")
        assert function_responses[1]["name"] == "tool_b"
        print(f"  响应 2: name='tool_b' (从历史推断)")

        print("\n✅ 混合 name 字段测试通过")
        return True

    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有边界测试"""
    results = []

    results.append(await test_tool_response_order_mismatch())
    results.append(await test_multiple_assistant_messages_with_tools())
    results.append(await test_tool_with_both_name_and_inference())

    print("\n" + "=" * 60)
    if all(results):
        print("✅ 所有边界情况测试通过")
    else:
        print("❌ 部分测试失败")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
