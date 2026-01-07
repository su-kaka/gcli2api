
def extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容和推理内容
    
    Returns:
        (content, reasoning_content): 文本内容和推理内容的元组
        reasoning_content 现在是字符串（保持向后兼容）
    """
    content = ""
    reasoning_content = ""

    for part in parts:
        # 处理文本内容
        if part.get("text"):
            # 检查这个部件是否包含thinking tokens
            if part.get("thought", False):
                reasoning_content += part.get("text", "")
            else:
                content += part.get("text", "")

    return content, reasoning_content