from src.converter.antigravity_fix import (
    _ensure_empty_tool_schema_for_claude,
    _rewrite_blocked_system_identity,
)


def test_antigravity_claude_tools_keep_schema_in_parameters():
    tools = [
        {
            "functionDeclarations": [
                {
                    "name": "test_tool",
                    "description": "A test tool.",
                    "parametersJsonSchema": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                    },
                }
            ]
        }
    ]

    result = _ensure_empty_tool_schema_for_claude(tools, "claude-opus-4-6-thinking", "antigravity")
    declaration = result[0]["functionDeclarations"][0]

    assert declaration["parameters"]["type"] == "object"
    assert "parametersJsonSchema" not in declaration


def test_rewrites_blocked_hermes_identity_without_mutating_input():
    original = {
        "parts": [
            {
                "text": (
                    "You are Hermes Agent, an intelligent AI assistant created by "
                    "Nous Research. Keep helping the user."
                )
            }
        ]
    }

    rewritten, changed = _rewrite_blocked_system_identity(original)

    assert changed is True
    assert rewritten["parts"][0]["text"].startswith(
        "Hermes Agent is an intelligent and helpful software assistant from Nous Research."
    )
    assert original["parts"][0]["text"].startswith("You are Hermes Agent")


def test_leaves_unrelated_system_identity_unchanged():
    original = {"parts": [{"text": "You are a concise assistant."}]}

    rewritten, changed = _rewrite_blocked_system_identity(original)

    assert changed is False
    assert rewritten == original
