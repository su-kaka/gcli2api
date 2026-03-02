import asyncio

from src.converter.anthropic2gemini import anthropic_to_gemini_request


def test_anthropic_tools_schema_shorthand_object_is_normalized():
    payload = {
        "model": "gemini-3-flash-preview-high-search",
        "max_tokens": 128,
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "save_config",
                "description": "Save key-value config",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": "object",
                    },
                    "required": ["key", "value"],
                },
            }
        ],
    }

    gemini_request = asyncio.run(anthropic_to_gemini_request(payload))
    params = gemini_request["tools"][0]["functionDeclarations"][0]["parameters"]

    assert params["type"] == "object"
    assert params["properties"]["key"]["type"] == "string"
    assert params["properties"]["value"]["type"] == "object"
