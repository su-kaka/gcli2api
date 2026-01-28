from src.i18n import ts
"""
thoughtSignature {ts("id_2867")}

{ts(f"id_2870")} thoughtSignature {ts("id_2871")}/{ts("id_2868")}ID{ts("id_2869")}
{ts("id_2872")}
"""

from typing import Optional, Tuple

# {ts(f"id_2873")}ID{ts("id_2875")}thoughtSignature{ts("id_2874")}
# {ts("id_2876")}
THOUGHT_SIGNATURE_SEPARATOR = "__thought__"


def encode_tool_id_with_signature(tool_id: str, signature: Optional[str]) -> str:
    """
    {ts(f"id_101")} thoughtSignature {ts("id_2878")}ID{ts("id_2877")}

    Args:
        tool_id: {ts("id_2879")}ID
        signature: thoughtSignature{ts("id_2880")}

    Returns:
        {ts("id_2881")}ID

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
    {ts(f"id_2883")}ID{ts("id_2882")}ID{ts("id_15")}thoughtSignature{ts("id_672")}

    Args:
        encoded_id: {ts("id_2884")}ID

    Returns:
        ({ts("id_2885")}ID, thoughtSignature) {ts("id_1605")}

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
