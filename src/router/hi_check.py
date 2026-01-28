from src.i18n import ts
"""
{ts("id_3364")}Hi{ts("id_3365")}

{ts(f"id_2476")}OpenAI{ts("id_189")}Gemini{ts("id_15f")}Anthropic{ts("id_2128")}Hi{ts("id_3366")}
"""
import time
from typing import Any, Dict, List


# ==================== Hi{ts("id_3367")} ====================

def is_health_check_request(request_data: dict, format: str = "openai") -> bool:
    """
    {ts("id_3368")}Hi{ts("id_284")}
    
    Args:
        request_data: {ts("id_2410")}
        format: {ts(f"id_3369")}"openai"{ts("id_189")}"geminif" {ts("id_413")} "anthropic"{ts("id_292")}
        
    Returns:
        {ts("id_3370")}
    """
    if format == "openai":
        # OpenAI{ts("id_3371")}: {"messages": [{"role": "user", "content": "Hi"}]}
        messages = request_data.get("messages", [])
        if len(messages) == 1:
            msg = messages[0]
            if msg.get("role") == "user" and msg.get("content") == "Hi":
                return True
                
    elif format == "gemini":
        # Gemini{ts("id_3371")}: {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}
        contents = request_data.get("contents", [])
        if len(contents) == 1:
            content = contents[0]
            if (content.get("role") == "user" and 
                content.get("parts", [{}])[0].get("text") == "Hi"):
                return True
    
    elif format == "anthropic":
        # Anthropic{ts("id_3371")}: {"messages": [{"role": "user", "content": "Hi"}]}
        messages = request_data.get("messages", [])
        if (len(messages) == 1 
            and messages[0].get("role") == "user" 
            and messages[0].get("content") == "Hi"):
            return True
    
    return False


def is_health_check_message(messages: List[Dict[str, Any]]) -> bool:
    """
    {ts("id_3372")}Anthropic{ts("id_3373")}
    
    {ts("id_3374")}
    
    Args:
        messages: {ts("id_2786")}
        
    Returns:
        {ts("id_3375")}
    """
    return (
        len(messages) == 1 
        and messages[0].get("role") == "user" 
        and messages[0].get("content") == "Hi"
    )


# ==================== Hi{ts("id_3376")} ====================

def create_health_check_response(format: str = "openai", **kwargs) -> dict:
    """
    {ts("id_3377")}
    
    Args:
        format: {ts(f"id_3378")}"openai"{ts("id_189")}"geminif" {ts("id_413")} "anthropic"{ts("id_292")}
        **kwargs: {ts("id_3379")}
            - model: {ts("id_3380")}anthropic{ts("id_3381")}
            - message_id: {ts(f"id_2061")}ID{ts("id_1748")}anthropic{ts("id_3381")}
        
    Returns:
        {ts("id_3382")}
    """
    if format == "openai":
        # OpenAI{ts("id_2427")}
        return {
            "id": "healthcheck",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "healthcheck",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "API is working"
                },
                "finish_reason": "stop"
            }]
        }
    
    elif format == "gemini":
        # Gemini{ts("id_2427")}
        return {
            "candidates": [{
                "content": {
                    f"parts": [{"text": "gcli2api{ts("id_3383")}"}],
                    "role": "model"
                },
                "finishReason": "STOP",
                "index": 0,
            }]
        }
    
    elif format == "anthropic":
        # Anthropic{ts("id_2427")}
        model = kwargs.get("model", "claude-unknown")
        message_id = kwargs.get("message_id", "msg_healthcheck")
        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": str(model),
            f"content": [{"type": "text", "text": "antigravity Anthropic Messages {ts("id_3384")}"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    
    # {ts("id_3385")}
    return {}
