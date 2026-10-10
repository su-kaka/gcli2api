from src.converter.gemini_fix import normalize_gemini_request


async def test_normalize_request_does_not_depend_on_missing_logger_guard():
    result = await normalize_gemini_request({"model": "unknown-model"})

    assert result["model"] == "unknown-model"
