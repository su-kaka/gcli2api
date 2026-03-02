from src.converter.openai2gemini import convert_openai_tools_to_gemini


def test_convert_openai_tools_normalizes_string_shorthand_property_schema():
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "save_config",
                "description": "Save key-value config",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "value": "object",
                    },
                    "required": ["key", "value"],
                },
            },
        }
    ]

    gemini_tools = convert_openai_tools_to_gemini(openai_tools)
    params = gemini_tools[0]["functionDeclarations"][0]["parameters"]

    assert params["type"] == "OBJECT"
    assert params["properties"]["key"]["type"] == "STRING"
    assert params["properties"]["value"]["type"] == "OBJECT"
