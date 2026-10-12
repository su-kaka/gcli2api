"""
Gemini CLI Audio Router - OpenAI 格式的音频转录接口
POST /v1/audio/transcriptions
"""

from src.router.audio_common import build_audio_router

router = build_audio_router("/v1/audio/transcriptions", backend="geminicli")
