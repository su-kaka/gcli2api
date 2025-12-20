"""简单的 token 估算，不追求精确"""
from __future__ import annotations

from typing import Any, Dict


def estimate_input_tokens(payload: Dict[str, Any]) -> int:
    """粗略估算 token 数：字符数 / 4 + 图片固定值"""
    total_chars = 0
    image_count = 0

    # 统计所有文本字符
    def count_str(obj: Any) -> None:
        nonlocal total_chars, image_count
        if isinstance(obj, str):
            total_chars += len(obj)
        elif isinstance(obj, dict):
            # 检测图片
            if obj.get("type") == "image" or "inlineData" in obj:
                image_count += 1
            for v in obj.values():
                count_str(v)
        elif isinstance(obj, list):
            for item in obj:
                count_str(item)

    count_str(payload)

    # 粗略估算：字符数/4 + 每张图片300 tokens
    return max(1, total_chars // 4 + image_count * 300)
