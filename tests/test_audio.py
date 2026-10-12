import base64

import pytest

from src.converter.audio import (
    build_transcription_request,
    detect_audio_mime_type,
    encode_to_base64,
    exceeds_size_limit,
    extract_transcription_text,
    normalize_audio_mime,
    MAX_INLINE_AUDIO_BYTES,
)


def test_normalize_audio_mime_accepts_bare_and_prefixed_forms():
    assert normalize_audio_mime("wav") == "audio/wav"
    assert normalize_audio_mime("audio/wav") == "audio/wav"
    assert normalize_audio_mime("audio/x-wav") == "audio/wav"
    assert normalize_audio_mime("audio/mpeg") == "audio/mp3"
    assert normalize_audio_mime("m4a") == "audio/aac"
    assert normalize_audio_mime("audio/wav; codecs=1") == "audio/wav"
    assert normalize_audio_mime("video/mp4") is None
    assert normalize_audio_mime(None) is None


def test_detect_mime_type_prefers_extension():
    assert detect_audio_mime_type("speech.MP3") == "audio/mp3"
    # 扩展名可识别时，忽略浏览器给出的通用 Content-Type
    assert detect_audio_mime_type("speech.flac", "application/octet-stream") == "audio/flac"


def test_detect_mime_type_falls_back_to_content_type():
    assert detect_audio_mime_type("blob", "audio/ogg") == "audio/ogg"
    assert detect_audio_mime_type(None, "audio/wav") == "audio/wav"


def test_detect_mime_type_rejects_unsupported_format():
    # webm 不在 Gemini 支持的音频格式内，必须显式报错而不是猜一个 MIME
    with pytest.raises(ValueError) as excinfo:
        detect_audio_mime_type("recording.webm", "audio/webm")
    assert "Unsupported audio format" in str(excinfo.value)


def test_size_limit_boundary():
    assert not exceeds_size_limit(MAX_INLINE_AUDIO_BYTES)
    assert exceeds_size_limit(MAX_INLINE_AUDIO_BYTES + 1)


def test_build_transcription_request_shape():
    payload = build_transcription_request(
        audio_base64=encode_to_base64(b"fake audio"),
        mime_type="audio/wav",
    )

    parts = payload["contents"][0]["parts"]
    assert parts[0]["text"] == "Generate a transcript of the speech."
    assert parts[1]["inlineData"]["mimeType"] == "audio/wav"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == b"fake audio"
    # 未指定 temperature 时不应写入 generationConfig
    assert "generationConfig" not in payload


def test_build_transcription_request_honours_prompt_language_and_temperature():
    payload = build_transcription_request(
        audio_base64="QUJD",
        mime_type="audio/mp3",
        prompt="Transcribe verbatim.",
        language="es",
        temperature=0.2,
    )

    instruction = payload["contents"][0]["parts"][0]["text"]
    assert instruction.startswith("Transcribe verbatim.")
    assert "'es'" in instruction
    assert payload["generationConfig"]["temperature"] == 0.2


def test_extract_text_unwraps_v1internal_envelope_and_skips_thoughts():
    response = {
        "response": {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "internal reasoning", "thought": True},
                            {"text": "  Hello "},
                            {"text": "world.  "},
                        ]
                    }
                }
            ]
        }
    }

    assert extract_transcription_text(response) == "Hello world."


def test_extract_text_handles_unwrapped_and_malformed_responses():
    assert (
        extract_transcription_text(
            {"candidates": [{"content": {"parts": [{"text": "plain"}]}}]}
        )
        == "plain"
    )
    assert extract_transcription_text({}) == ""
    assert extract_transcription_text({"candidates": []}) == ""
    assert extract_transcription_text({"candidates": [{"content": {}}]}) == ""
    assert extract_transcription_text(None) == ""
