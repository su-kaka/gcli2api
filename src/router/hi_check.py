"""
统一的健康检查（Hi消息）处理模块

提供对OpenAI、Gemini和Anthropic格式的Hi消息的解析和响应
"""
import time
from typing import Any, Dict, List


# ==================== Hi消息检测 ====================

def is_health_check_request(request_data: dict, format: str = "openai") -> bool:
    """
    检查是否是健康检查请求（Hi消息）
    
    Args:
        request_data: 请求数据
        format: 请求格式（"openai"、"gemini" 或 "anthropic"）
        
    Returns:
        是否是健康检查请求
    """
    if format == "openai":
        # OpenAI格式健康检查: {"messages": [{"role": "user", "content": "Hi"}]}
        messages = request_data.get("messages", [])
        if len(messages) == 1:
            msg = messages[0]
            if msg.get("role") == "user" and msg.get("content") == "Hi":
                return True
                
    elif format == "gemini":
        # Gemini格式健康检查: {"contents": [{"role": "user", "parts": [{"text": "Hi"}]}]}
        contents = request_data.get("contents", [])
        if len(contents) == 1:
            content = contents[0]
            if (content.get("role") == "user" and 
                content.get("parts", [{}])[0].get("text") == "Hi"):
                return True
    
    elif format == "anthropic":
        # Anthropic格式健康检查: {"messages": [{"role": "user", "content": "Hi"}]}
        messages = request_data.get("messages", [])
        if (len(messages) == 1 
            and messages[0].get("role") == "user" 
            and messages[0].get("content") == "Hi"):
            return True
    
    return False


def is_health_check_message(messages: List[Dict[str, Any]]) -> bool:
    """
    直接检查消息列表是否为健康检查消息（Anthropic专用）
    
    这是一个便捷函数，用于已经提取出消息列表的场景。
    
    Args:
        messages: 消息列表
        
    Returns:
        是否为健康检查消息
    """
    return (
        len(messages) == 1 
        and messages[0].get("role") == "user" 
        and messages[0].get("content") == "Hi"
    )


# ==================== Hi消息响应生成 ====================

def create_health_check_response(format: str = "openai", **kwargs) -> dict:
    """
    创建健康检查响应
    
    Args:
        format: 响应格式（"openai"、"gemini" 或 "anthropic"）
        **kwargs: 格式特定的额外参数
            - model: 模型名称（anthropic格式需要）
            - message_id: 消息ID（anthropic格式需要）
        
    Returns:
        健康检查响应字典
    """
    if format == "openai":
        # OpenAI格式响应
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
        # Gemini格式响应
        return {
            "candidates": [{
                "content": {
                    "parts": [{"text": "gcli2api工作中"}],
                    "role": "model"
                },
                "finishReason": "STOP",
                "index": 0,
            }]
        }
    
    elif format == "anthropic":
        # Anthropic格式响应
        model = kwargs.get("model", "claude-unknown")
        message_id = kwargs.get("message_id", "msg_healthcheck")
        return {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": str(model),
            "content": [{"type": "text", "text": "antigravity Anthropic Messages 正常工作中"}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
    
    # 未知格式返回空字典
    return {}
