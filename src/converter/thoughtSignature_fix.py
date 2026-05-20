"""
thoughtSignature 处理公共模块

提供统一的 thoughtSignature 编码/解码功能，用于在工具调用ID中保留签名信息。
这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段。
"""

from typing import Any, Mapping, Optional, Tuple

# 在工具调用ID中嵌入thoughtSignature的分隔符
# 这使得签名能够在客户端往返传输中保留，即使客户端会删除自定义字段
THOUGHT_SIGNATURE_SEPARATOR = "__thought__"
SKIP_THOUGHT_SIGNATURE_VALIDATOR = "skip_thought_signature_validator"
SKIP_THOUGHT_SIGNATURE_PLACEHOLDER_TEXT = "..."


def is_internal_placeholder_text(text: Any) -> bool:
    if not isinstance(text, str):
        return False
    return text.strip() in (SKIP_THOUGHT_SIGNATURE_PLACEHOLDER_TEXT, "…")


def is_skip_thought_signature_placeholder(part: Mapping[str, Any]) -> bool:
    """Return True for the internal placeholder that should not reach clients."""
    if not isinstance(part, Mapping):
        return False
    if part.get("thoughtSignature") != SKIP_THOUGHT_SIGNATURE_VALIDATOR:
        return False
    if "functionCall" in part or "function_call" in part or "functionResponse" in part:
        return False
    return is_internal_placeholder_text(part.get("text"))


def encode_tool_id_with_signature(tool_id: str, signature: Optional[str]) -> str:
    """
    将 thoughtSignature 编码到工具调用ID中，以便往返保留。

    Args:
        tool_id: 原始工具调用ID
        signature: thoughtSignature（可选）

    Returns:
        编码后的工具调用ID

    Examples:
        >>> encode_tool_id_with_signature("call_123", "abc")
        'call_123__thought__abc'
        >>> encode_tool_id_with_signature("call_123", None)
        'call_123'
    """
    if not signature:
        return tool_id
    return f"{tool_id}{THOUGHT_SIGNATURE_SEPARATOR}{signature}"


def decode_tool_id_and_signature(encoded_id: str) -> Tuple[str, Optional[str]]:
    """
    从编码的ID中提取原始工具ID和thoughtSignature。

    Args:
        encoded_id: 编码的工具调用ID

    Returns:
        (原始工具ID, thoughtSignature) 元组

    Examples:
        >>> decode_tool_id_and_signature("call_123__thought__abc")
        ('call_123', 'abc')
        >>> decode_tool_id_and_signature("call_123")
        ('call_123', None)
    """
    if not encoded_id or THOUGHT_SIGNATURE_SEPARATOR not in encoded_id:
        return encoded_id, None
    parts = encoded_id.split(THOUGHT_SIGNATURE_SEPARATOR, 1)
    return parts[0], parts[1] if len(parts) == 2 else None
