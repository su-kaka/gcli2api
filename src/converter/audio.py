"""
Audio Converter - OpenAI 音频接口与 Gemini inlineData 之间的转换

提供音频 MIME 检测、大小校验、请求构建和转录文本提取等纯函数，
供各后端的 /v1/audio/transcriptions 路由复用。
"""

import base64
from typing import Any, Dict, Optional

# Gemini 支持的音频 MIME 类型
# 参考: https://ai.google.dev/gemini-api/docs/audio
# 注意: webm / mp4 等容器格式不在支持列表内，需要客户端自行转码
EXTENSION_MIME_TYPES: Dict[str, str] = {
    "mp3": "audio/mp3",
    "mpeg": "audio/mp3",
    "mpga": "audio/mp3",
    "wav": "audio/wav",
    "wave": "audio/wav",
    "m4a": "audio/aac",
    "aac": "audio/aac",
    "ogg": "audio/ogg",
    "oga": "audio/ogg",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "aiff": "audio/aiff",
    "aif": "audio/aiff",
}

# 支持的 MIME 类型集合（用于校验客户端传来的 Content-Type）
SUPPORTED_MIME_TYPES = frozenset(EXTENSION_MIME_TYPES.values())

# 单次内联音频的大小上限。Gemini 内联请求体上限为 20MB，
# 留出 base64 膨胀和其他字段的余量后取 15MB（约 16 分钟 MP3）。
MAX_INLINE_AUDIO_BYTES = 15 * 1024 * 1024

# 未显式提供 prompt 时使用的默认转录指令
DEFAULT_TRANSCRIPTION_PROMPT = "Generate a transcript of the speech."


def normalize_audio_mime(fmt: Optional[str]) -> Optional[str]:
    """
    将 OpenAI 风格的音频格式标识归一化为 Gemini 需要的 MIME 类型。

    接受 "wav" / "audio/wav" / "audio/x-wav" / "audio/mpeg" 等写法。

    Args:
        fmt: 格式标识或 MIME 类型

    Returns:
        归一化后的 MIME 类型，无法识别时返回 None
    """
    if not fmt:
        return None

    bare = fmt.split(";")[0].strip().lower()
    if bare.startswith("audio/"):
        bare = bare[len("audio/"):]
    # 去掉 x- / vnd. 之类的厂商前缀
    for prefix in ("x-", "vnd."):
        if bare.startswith(prefix):
            bare = bare[len(prefix):]

    return EXTENSION_MIME_TYPES.get(bare)


def detect_audio_mime_type(
    filename: Optional[str],
    content_type: Optional[str] = None,
) -> str:
    """
    检测音频 MIME 类型，优先使用文件扩展名，其次使用客户端声明的 Content-Type。

    Args:
        filename: 上传的文件名
        content_type: multipart 中客户端声明的 Content-Type

    Returns:
        Gemini 可接受的 MIME 类型

    Raises:
        ValueError: 无法识别或不受支持的音频格式
    """
    if filename and "." in filename:
        extension = filename.rsplit(".", 1)[-1]
        mime_type = normalize_audio_mime(extension)
        if mime_type:
            return mime_type

    mime_type = normalize_audio_mime(content_type)
    if mime_type:
        return mime_type

    supported = ", ".join(sorted(set(EXTENSION_MIME_TYPES)))
    raise ValueError(
        f"Unsupported audio format: {filename or content_type or 'unknown'}. "
        f"Supported formats: {supported}"
    )


def exceeds_size_limit(size_bytes: int) -> bool:
    """判断音频是否超过内联大小上限"""
    return size_bytes > MAX_INLINE_AUDIO_BYTES


def encode_to_base64(audio_data: bytes) -> str:
    """将音频字节编码为 base64 字符串"""
    return base64.b64encode(audio_data).decode("ascii")


def build_transcription_request(
    audio_base64: str,
    mime_type: str,
    prompt: Optional[str] = None,
    language: Optional[str] = None,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    构建 Gemini generateContent 请求体（不含 model 字段）。

    Args:
        audio_base64: base64 编码后的音频数据
        mime_type: 音频 MIME 类型
        prompt: 转录指令，为空时使用默认指令
        language: ISO-639-1 语言代码，作为提示传给模型
        temperature: 采样温度

    Returns:
        Gemini 格式的请求体
    """
    instruction = (prompt or "").strip() or DEFAULT_TRANSCRIPTION_PROMPT
    if language:
        instruction = (
            f"{instruction}\n"
            f"The audio is in language '{language}'. "
            f"Return only the transcript text, without commentary or timestamps."
        )

    request: Dict[str, Any] = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": instruction},
                    {"inlineData": {"mimeType": mime_type, "data": audio_base64}},
                ],
            }
        ]
    }

    if temperature is not None:
        request["generationConfig"] = {"temperature": temperature}

    return request


def extract_transcription_text(payload: Dict[str, Any]) -> str:
    """
    从 Gemini 响应中提取转录文本。

    兼容 v1internal 的 {"response": {...}} 包装格式，并跳过思考内容。

    Args:
        payload: 解析后的 Gemini 响应体

    Returns:
        拼接后的转录文本，提取失败时返回空字符串
    """
    if not isinstance(payload, dict):
        return ""

    # 处理 v1internal 的 response 包装格式
    inner = payload.get("response")
    if isinstance(inner, dict):
        payload = inner

    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return ""

    first = candidates[0]
    if not isinstance(first, dict):
        return ""

    parts = (first.get("content") or {}).get("parts")
    if not isinstance(parts, list):
        return ""

    texts = [
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
        and not part.get("thought", False)
    ]

    return "".join(texts).strip()
