from typing import Any, Dict, List, Tuple
import json
from src.converter.utils import extract_content_and_reasoning
from log import log
from src.converter.openai2gemini import _convert_usage_metadata

def safe_get_nested(obj: Any, *keys: str, default: Any = None) -> Any:
    """安全获取嵌套字典值
    
    Args:
        obj: 字典对象
        *keys: 嵌套键路径
        default: 默认值
    
    Returns:
        获取到的值或默认值
    """
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key, default)
        if obj is default:
            return default
    return obj

def parse_response_for_fake_stream(response_data: Dict[str, Any]) -> tuple:
    """从完整响应中提取内容和推理内容(用于假流式)

    Args:
        response_data: Gemini API 响应数据

    Returns:
        (content, reasoning_content, finish_reason): 内容、推理内容和结束原因的元组
    """
    # 处理GeminiCLI的response包装格式
    if "response" in response_data and "candidates" not in response_data:
        response_data = response_data["response"]

    candidates = response_data.get("candidates", [])
    if not candidates:
        return "", "", "STOP"

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason", "STOP")
    parts = safe_get_nested(candidate, "content", "parts", default=[])
    content, reasoning_content = extract_content_and_reasoning(parts)

    return content, reasoning_content, finish_reason

def extract_fake_stream_content(response: Any) -> Tuple[str, str, Dict[str, int]]:
    """
    从 Gemini 非流式响应中提取内容，用于假流式处理
    
    Args:
        response: Gemini API 响应对象
    
    Returns:
        (content, reasoning_content, usage) 元组
    """
    from src.converter.utils import extract_content_and_reasoning
    
    # 解析响应体
    if hasattr(response, "body"):
        body_str = (
            response.body.decode()
            if isinstance(response.body, bytes)
            else str(response.body)
        )
    elif hasattr(response, "content"):
        body_str = (
            response.content.decode()
            if isinstance(response.content, bytes)
            else str(response.content)
        )
    else:
        body_str = str(response)

    try:
        response_data = json.loads(body_str)

        # GeminiCLI 返回的格式是 {"response": {...}, "traceId": "..."}
        # 需要先提取 response 字段
        if "response" in response_data:
            gemini_response = response_data["response"]
        else:
            gemini_response = response_data

        # 从Gemini响应中提取内容，使用思维链分离逻辑
        content = ""
        reasoning_content = ""
        if "candidates" in gemini_response and gemini_response["candidates"]:
            # Gemini格式响应 - 使用思维链分离
            candidate = gemini_response["candidates"][0]
            if "content" in candidate and "parts" in candidate["content"]:
                parts = candidate["content"]["parts"]
                content, reasoning_content = extract_content_and_reasoning(parts)
        elif "choices" in gemini_response and gemini_response["choices"]:
            # OpenAI格式响应
            content = gemini_response["choices"][0].get("message", {}).get("content", "")

        # 如果没有正常内容但有思维内容，给出警告
        if not content and reasoning_content:
            log.warning("Fake stream response contains only thinking content")
            content = "[模型正在思考中，请稍后再试或重新提问]"
        
        # 如果完全没有内容，提供默认回复
        if not content:
            log.warning(f"No content found in response: {gemini_response}")
            content = "[响应为空，请重新尝试]"

        # 转换usageMetadata为OpenAI格式
        usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))
        
        return content, reasoning_content, usage

    except json.JSONDecodeError:
        # 如果不是JSON，直接返回原始文本
        return body_str, "", None

def _build_candidate(parts: List[Dict[str, Any]], finish_reason: str = "STOP") -> Dict[str, Any]:
    """构建标准候选响应结构
    
    Args:
        parts: parts 列表
        finish_reason: 结束原因
    
    Returns:
        候选响应字典
    """
    return {
        "candidates": [{
            "content": {"parts": parts, "role": "model"},
            "finishReason": finish_reason,
            "index": 0,
        }]
    }

def create_openai_heartbeat_chunk() -> Dict[str, Any]:
    """
    创建 OpenAI 格式的心跳块（用于假流式）
    
    Returns:
        心跳响应块字典
    """
    return {
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ]
    }

def build_gemini_fake_stream_chunks(content: str, reasoning_content: str, finish_reason: str) -> List[Dict[str, Any]]:
    """构建假流式响应的数据块
    
    Args:
        content: 主要内容
        reasoning_content: 推理内容
        finish_reason: 结束原因
    
    Returns:
        响应数据块列表
    """
    # 如果没有正常内容但有思维内容,提供默认回复
    if not content:
        default_text = "[模型正在思考中,请稍后再试或重新提问]" if reasoning_content else "[响应为空,请重新尝试]"
        return [_build_candidate([{"text": default_text}])]
    
    # 构建包含分离内容的响应
    parts = [{"text": content}]
    if reasoning_content:
        parts.append({"text": reasoning_content, "thought": True})
    
    return [_build_candidate(parts, finish_reason)]


def create_gemini_heartbeat_chunk() -> Dict[str, Any]:
    """创建 Gemini 格式的心跳数据块
    
    Returns:
        心跳数据块
    """
    chunk = _build_candidate([{"text": ""}])
    chunk["candidates"][0]["finishReason"] = None
    return chunk