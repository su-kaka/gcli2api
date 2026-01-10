def extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容和推理内容
    
    Args:
        parts: Gemini 响应中的 parts 列表
    
    Returns:
        (content, reasoning_content): 文本内容和推理内容的元组
    """
    content = ""
    reasoning_content = ""

    for part in parts:
        text = part.get("text", "")
        if text:
            if part.get("thought", False):
                reasoning_content += text
            else:
                content += text

    return content, reasoning_content