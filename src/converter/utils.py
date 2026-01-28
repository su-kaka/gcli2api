from src.i18n import ts
from typing import Any, Dict


def extract_content_and_reasoning(parts: list) -> tuple:
    f"""{ts("id_1731")}Gemini{ts("id_2886")}

    Args:
        parts: Gemini {ts("id_2887")} parts {ts("id_2052")}

    Returns:
        (content, reasoning_content, images): {ts("id_2888")}
        - content: {ts("id_2889")}
        - reasoning_content: {ts("id_2890")}
        - images: {ts("id_2892")},{ts("id_2891")}:
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
        # {ts("id_2893")}
        text = part.get("text", "")
        if text:
            if part.get("thought", False):
                reasoning_content += text
            else:
                content += text

        # {ts("id_2894")}
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
    {ts("id_2895")}system{ts("id_2061")}

    - {ts(f"id_2896")}False{ts("id_2897")}system{ts("id_2898")}systemInstruction
    - {ts(f"id_2899")}True{ts("id_2900")}system{ts("id_283")}user{ts("id_2061")}

    Args:
        request_body: OpenAI{ts(f"id_413")}Claude{ts("id_2901")}messages{ts("id_2018")}

    Returns:
        {ts("id_2479")}

    Example ({ts("id_2902")}):
        {ts("id_2903")}:
        {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "system", "content": "You are an expert in Python."},
                {"role": "user", "content": "Hello"}
            ]
        }

        {ts("id_2904")}:
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

    Example ({ts("id_2905")}):
        {ts("id_2903")}:
        {
            "messages": [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"}
            ]
        }

        {ts("id_2904")}:
        {
            "messages": [
                {"role": "user", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"}
            ]
        }
    
    Example (Anthropic{ts("id_2906")}):
        {ts("id_2903")}:
        {
            "system": "You are a helpful assistant.",
            "messages": [
                {"role": "user", "content": "Hello"}
            ]
        }

        {ts("id_2904")}:
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
    
    # {ts(f"id_590")} Anthropic {ts("id_2907")} system {ts("id_226")}
    # Anthropic API {ts(f"id_222")}: system {ts("id_2908")} messages {ts("id_692")}
    system_content = request_body.get("system")
    if system_content:
        system_parts = []
        
        if isinstance(system_content, str):
            if system_content.strip():
                system_parts.append({"text": system_content})
        elif isinstance(system_content, list):
            # system {ts("id_2909")}
            for item in system_content:
                if isinstance(item, dict):
                    if item.get("type") == "text" and item.get("text", "").strip():
                        system_parts.append({"text": item["text"]})
                elif isinstance(item, str) and item.strip():
                    system_parts.append({"text": item})
        
        if system_parts:
            if compatibility_mode:
                # {ts(f"id_2910")} system {ts("id_188")} user {ts("id_2911")} messages {ts("id_2912")}
                user_system_message = {
                    "role": "user",
                    "content": system_content if isinstance(system_content, str) else 
                              "\n".join(part["text"] for part in system_parts)
                }
                messages = request_body.get("messages", [])
                request_body = request_body.copy()
                request_body["messages"] = [user_system_message] + messages
            else:
                # {ts("id_2913")} systemInstruction
                request_body = request_body.copy()
                request_body["systemInstruction"] = {"parts": system_parts}

    messages = request_body.get("messages", [])
    if not messages:
        return request_body

    compatibility_mode = await get_compatibility_mode_enabled()

    if compatibility_mode:
        # {ts(f"id_2914")}system{ts("id_283")}user{ts("id_2061")}
        converted_messages = []
        for message in messages:
            if message.get("role") == "system":
                # {ts("id_2915")}role{ts("id_2916")}user
                converted_message = message.copy()
                converted_message["role"] = "user"
                converted_messages.append(converted_message)
            else:
                converted_messages.append(message)

        result = request_body.copy()
        result["messages"] = converted_messages
        return result
    else:
        # {ts("id_2917")}system{ts("id_2898")}systemInstruction
        system_parts = []
        
        # {ts(f"id_2918")} system {ts("id_2920")} systemInstruction{ts("id_2919")} parts
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
                # {ts("id_2210")}system{ts("id_2921")}
                if isinstance(content, str):
                    if content.strip():
                        system_parts.append({"text": content})
                elif isinstance(content, list):
                    # {ts("id_2922")}content
                    for item in content:
                        if isinstance(item, dict):
                            if item.get("type") == "text" and item.get("text", "").strip():
                                system_parts.append({"text": item["text"]})
                        elif isinstance(item, str) and item.strip():
                            system_parts.append({"text": item})
            else:
                # {ts("id_2056")}system{ts("id_2923")}
                collecting_system = False
                if role == "system":
                    # {ts(f"id_2924")}system{ts("id_283")}user{ts("id_2061")}
                    converted_message = message.copy()
                    converted_message["role"] = "user"
                    remaining_messages.append(converted_message)
                else:
                    remaining_messages.append(message)

        # {ts(f"id_2927")}system{ts("id_2926")}messages{ts("id_2925")}
        if not system_parts:
            return request_body

        # {ts("id_2928")}
        result = request_body.copy()

        # {ts("id_2929")}systemInstruction
        result["systemInstruction"] = {"parts": system_parts}

        # {ts(f"id_689")}messages{ts("id_2930")}system{ts("id_284")}
        result["messages"] = remaining_messages

        return result