from typing import Any, Dict


def extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容和推理内容

    Args:
        parts: Gemini 响应中的 parts 列表

    Returns:
        (content, reasoning_content, images): 文本内容、推理内容和图片数据的元组
        - content: 文本内容字符串
        - reasoning_content: 推理内容字符串
        - images: 图片数据列表,每个元素格式为:
          {
              "type": "image_url",
              "image_url": {
                  "url": "data:{mime_type};base64,{base64_data}"
              }
          }
    """
    content = ""
    reasoning_content = ""
    images = []

    for part in parts:
        # 提取文本内容
        text = part.get("text", "")
        if text:
            if part.get("thought", False):
                reasoning_content += text
            else:
                content += text

        # 提取图片数据
        if "inlineData" in part:
            inline_data = part["inlineData"]
            mime_type = inline_data.get("mimeType", "image/png")
            base64_data = inline_data.get("data", "")
            images.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{base64_data}"
                }
            })

    return content, reasoning_content, images


async def merge_system_messages(request_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    根据兼容性模式处理请求体中的system消息

    - 兼容性模式关闭（False）：将连续的system消息合并为systemInstruction
    - 兼容性模式开启（True）：将所有system消息转换为user消息

    Args:
        request_body: OpenAI或Claude格式的请求体，包含messages字段

    Returns:
        处理后的请求体

    Example (兼容性模式关闭):
        输入:
        {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "system", "content": "You are an expert in Python."},
                {"role": "user", "content": "Hello"}
            ]
        }

        输出:
        {
            "systemInstruction": {
                "parts": [
                    {"text": "You are a helpful assistant."},
                    {"text": "You are an expert in Python."}
                ]
            },
            "messages": [
                {"role": "user", "content": "Hello"}
            ]
        }

    Example (兼容性模式开启):
        输入:
        {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"}
            ]
        }

        输出:
        {
            "messages": [
                {"role": "user", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"}
            ]
        }
    
    Example (Anthropic格式，兼容性模式关闭):
        输入:
        {
            "system": "You are a helpful assistant.",
            "messages": [
                {"role": "user", "content": "Hello"}
            ]
        }

        输出:
        {
            "systemInstruction": {
                "parts": [
                    {"text": "You are a helpful assistant."}
                ]
            },
            "messages": [
                {"role": "user", "content": "Hello"}
            ]
        }
    """
    from config import get_compatibility_mode_enabled

    compatibility_mode = await get_compatibility_mode_enabled()
    
    # 处理 Anthropic 格式的顶层 system 参数
    # Anthropic API 规范: system 是顶层参数，不在 messages 中
    system_content = request_body.get("system")
    if system_content:
        system_parts = []
        
        if isinstance(system_content, str):
            if system_content.strip():
                system_parts.append({"text": system_content})
        elif isinstance(system_content, list):
            # system 可以是包含多个块的列表
            for item in system_content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text", "").strip():
                        system_parts.append({"text": item["text"]})
                elif isinstance(item, str) and item.strip():
                    system_parts.append({"text": item})
        
        if system_parts:
            if compatibility_mode:
                # 兼容性模式：将 system 转换为 user 消息插入到 messages 开头
                user_system_message = {
                    "role": "user",
                    "content": system_content if isinstance(system_content, str) else 
                              "\n".join(part["text"] for part in system_parts)
                }
                messages = request_body.get("messages", [])
                request_body = request_body.copy()
                request_body["messages"] = [user_system_message] + messages
            else:
                # 非兼容性模式：添加为 systemInstruction
                request_body = request_body.copy()
                request_body["systemInstruction"] = {"parts": system_parts}

    messages = request_body.get("messages", [])
    if not messages:
        return request_body

    compatibility_mode = await get_compatibility_mode_enabled()

    if compatibility_mode:
        # 兼容性模式开启：将所有system消息转换为user消息
        converted_messages = []
        for message in messages:
            if message.get("role") == "system":
                # 创建新的消息对象，将role改为user
                converted_message = message.copy()
                converted_message["role"] = "user"
                converted_messages.append(converted_message)
            else:
                converted_messages.append(message)

        result = request_body.copy()
        result["messages"] = converted_messages
        return result
    else:
        # 兼容性模式关闭：提取连续的system消息合并为systemInstruction
        system_parts = []
        
        # 如果已经从顶层 system 参数创建了 systemInstruction，获取现有的 parts
        if "systemInstruction" in request_body:
            existing_instruction = request_body.get("systemInstruction", {})
            if isinstance(existing_instruction, dict):
                system_parts = existing_instruction.get("parts", []).copy()
        
        remaining_messages = []
        collecting_system = True

        for message in messages:
            role = message.get("role", "")
            content = message.get("content", "")

            if role == "system" and collecting_system:
                # 提取system消息的文本内容
                if isinstance(content, str):
                    if content.strip():
                        system_parts.append({"text": content})
                elif isinstance(content, list):
                    # 处理列表格式的content
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text" and item.get("text", "").strip():
                                system_parts.append({"text": item["text"]})
                        elif isinstance(item, str) and item.strip():
                            system_parts.append({"text": item})
            else:
                # 遇到非system消息，停止收集
                collecting_system = False
                if role == "system":
                    # 将后续的system消息转换为user消息
                    converted_message = message.copy()
                    converted_message["role"] = "user"
                    remaining_messages.append(converted_message)
                else:
                    remaining_messages.append(message)

        # 如果没有找到任何system消息（包括顶层参数和messages中的），返回原始请求体
        if not system_parts:
            return request_body

        # 构建新的请求体
        result = request_body.copy()

        # 添加或更新systemInstruction
        result["systemInstruction"] = {"parts": system_parts}

        # 更新messages列表（移除已处理的system消息）
        result["messages"] = remaining_messages

        return result