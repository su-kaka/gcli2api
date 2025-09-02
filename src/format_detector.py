"""
Format detection utilities for supporting both OpenAI and Gemini request formats
"""
from typing import Dict, Any

from log import log

def detect_request_format(data: Dict[str, Any]) -> str:
    """
    Detect whether the request is in OpenAI or Gemini format.
    
    Returns:
        "openai" or "gemini"
    """
    # OpenAI format indicators:
    # - Has "messages" field with array of {role, content} objects
    # - Role values are "system", "user", "assistant"
    if "messages" in data and isinstance(data["messages"], list):
        if data["messages"] and isinstance(data["messages"][0], dict):
            # Check for OpenAI role values
            first_role = data["messages"][0].get("role", "")
            if first_role in ["system", "user", "assistant"]:
                return "openai"
    
    # Gemini format indicators:
    # - Has "contents" field with array of {role, parts} objects
    # - Role values are "user", "model"
    # - May have "systemInstruction" field
    if "contents" in data and isinstance(data["contents"], list):
        if data["contents"] and isinstance(data["contents"][0], dict):
            # Check for Gemini structure
            if "parts" in data["contents"][0]:
                return "gemini"
    
    # Additional Gemini indicators
    if "systemInstruction" in data or "generationConfig" in data:
        return "gemini"
    
    # Default to OpenAI if unclear (for backwards compatibility)
    log.debug(f"Unable to definitively detect format, defaulting to OpenAI. Keys present: {list(data.keys())}")
    return "openai"

def gemini_request_to_openai(gemini_request: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert a Gemini format request to OpenAI format.
    
    Args:
        gemini_request: Request in Gemini API format
        
    Returns:
        Dictionary in OpenAI API format
    """
    openai_request = {
        "model": gemini_request.get("model", "gemini-2.5-pro"),
        "messages": []
    }
    
    # Convert system instruction if present
    if "systemInstruction" in gemini_request:
        system_content = ""
        if isinstance(gemini_request["systemInstruction"], dict):
            parts = gemini_request["systemInstruction"].get("parts", [])
            for part in parts:
                if "text" in part:
                    system_content += part["text"]
        elif isinstance(gemini_request["systemInstruction"], str):
            system_content = gemini_request["systemInstruction"]
        
        if system_content:
            openai_request["messages"].append({
                "role": "system",
                "content": system_content
            })
    
    # Convert contents to messages
    contents = gemini_request.get("contents", [])
    for content in contents:
        role = content.get("role", "user")
        # Map Gemini roles to OpenAI roles
        if role == "model":
            role = "assistant"
        
        # Convert parts to content
        parts = content.get("parts", [])
        if len(parts) == 1 and "text" in parts[0]:
            # Simple text message
            openai_request["messages"].append({
                "role": role,
                "content": parts[0]["text"]
            })
        elif len(parts) > 0:
            # Multi-part message (could include images)
            content_parts = []
            for part in parts:
                if "text" in part:
                    content_parts.append({
                        "type": "text",
                        "text": part["text"]
                    })
                elif "inlineData" in part:
                    # Convert Gemini inline data to OpenAI image format
                    inline_data = part["inlineData"]
                    mime_type = inline_data.get("mimeType", "image/jpeg")
                    data = inline_data.get("data", "")
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{data}"
                        }
                    })
            
            if content_parts:
                # If only one text part, use simple string format
                if len(content_parts) == 1 and content_parts[0]["type"] == "text":
                    openai_request["messages"].append({
                        "role": role,
                        "content": content_parts[0]["text"]
                    })
                else:
                    openai_request["messages"].append({
                        "role": role,
                        "content": content_parts
                    })
    
    # Convert generation config if present
    if "generationConfig" in gemini_request:
        config = gemini_request["generationConfig"]
        if "temperature" in config:
            openai_request["temperature"] = config["temperature"]
        if "topP" in config:
            openai_request["top_p"] = config["topP"]
        if "topK" in config:
            openai_request["top_k"] = config["topK"]
        if "maxOutputTokens" in config:
            openai_request["max_tokens"] = config["maxOutputTokens"]
        if "stopSequences" in config:
            openai_request["stop"] = config["stopSequences"]
        if "frequencyPenalty" in config:
            openai_request["frequency_penalty"] = config["frequencyPenalty"]
        if "presencePenalty" in config:
            openai_request["presence_penalty"] = config["presencePenalty"]
        if "candidateCount" in config:
            openai_request["n"] = config["candidateCount"]
        if "seed" in config:
            openai_request["seed"] = config["seed"]
    
    # Preserve stream setting if present
    if "stream" in gemini_request:
        openai_request["stream"] = gemini_request["stream"]
    
    return openai_request

def validate_and_normalize_request(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize the request to OpenAI format.
    Automatically detects format and converts if necessary.
    
    Args:
        data: Raw request data
        
    Returns:
        Normalized request in OpenAI format
    """
    format_type = detect_request_format(data)
    log.info(f"Detected request format: {format_type}")
    
    if format_type == "gemini":
        # Convert Gemini format to OpenAI format
        return gemini_request_to_openai(data)
    else:
        # Already in OpenAI format
        return data