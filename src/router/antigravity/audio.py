"""
Antigravity Audio Router - OpenAI 格式的音频转录接口
POST /antigravity/v1/audio/transcriptions
"""

from src.router.audio_common import build_audio_router

router = build_audio_router("/antigravity/v1/audio/transcriptions", backend="antigravity")
