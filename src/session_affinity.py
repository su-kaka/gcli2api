"""
Helpers for cache-friendly credential routing.

The goal is simple: requests from the same chat/task should usually hit the
same Google account so upstream prefix cache has a better chance to work.
"""

import hashlib
import json
import re
from typing import Any, Mapping, Optional


_CLAUDE_SESSION_RE = re.compile(r"_session_([a-fA-F0-9-]+)$")


def extract_cache_session_key(
    payload: Optional[Mapping[str, Any]],
    headers: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    if not isinstance(payload, Mapping):
        return None

    explicit = payload.get("cache_session_key")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    header_key = _session_key_from_headers(headers)
    if header_key:
        return header_key

    metadata_key = _session_key_from_metadata(payload.get("metadata"))
    if metadata_key:
        return metadata_key

    conversation_id = payload.get("conversation_id")
    if isinstance(conversation_id, str) and conversation_id.strip():
        return f"conv:{conversation_id.strip()}"

    request_payload = payload.get("request")
    if isinstance(request_payload, Mapping):
        request_key = _session_key_from_gemini_like_payload(request_payload)
        if request_key:
            return request_key

    gemini_key = _session_key_from_gemini_like_payload(payload)
    if gemini_key:
        return gemini_key

    first_user_text = _first_user_text(payload)
    if first_user_text:
        digest = hashlib.sha256(first_user_text.encode("utf-8")).hexdigest()[:16]
        return f"msg:{digest}"

    return None


def _session_key_from_headers(headers: Optional[Mapping[str, str]]) -> Optional[str]:
    if headers is None:
        return None

    header_names = (
        ("x-session-id", "header"),
        ("session_id", "codex"),
        ("x-amp-thread-id", "amp"),
        ("x-client-request-id", "clientreq"),
    )

    for header_name, prefix in header_names:
        value = _get_header(headers, header_name)
        if value:
            return f"{prefix}:{value}"

    return None


def _get_header(headers: Mapping[str, str], name: str) -> Optional[str]:
    value = None
    get_method = getattr(headers, "get", None)
    if callable(get_method):
        value = get_method(name)
        if value is None:
            value = get_method(name.lower())
        if value is None:
            value = get_method(name.upper())
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _session_key_from_metadata(metadata: Any) -> Optional[str]:
    if not isinstance(metadata, Mapping):
        return None

    user_id = metadata.get("user_id")
    if not isinstance(user_id, str) or not user_id.strip():
        return None

    user_id = user_id.strip()
    match = _CLAUDE_SESSION_RE.search(user_id)
    if match:
        return f"claude:{match.group(1)}"

    if user_id.startswith("{"):
        try:
            parsed = json.loads(user_id)
            session_id = parsed.get("session_id") if isinstance(parsed, Mapping) else None
            if isinstance(session_id, str) and session_id.strip():
                return f"claude:{session_id.strip()}"
        except Exception:
            pass

    return f"user:{user_id}"


def _session_key_from_gemini_like_payload(payload: Mapping[str, Any]) -> Optional[str]:
    session_id = payload.get("sessionId") or payload.get("session_id")
    if isinstance(session_id, str) and session_id.strip():
        return f"gemini-session:{session_id.strip()}"
    if isinstance(session_id, int):
        return f"gemini-session:{session_id}"
    return None


def _first_user_text(payload: Mapping[str, Any]) -> Optional[str]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, Mapping) or message.get("role") != "user":
                continue
            text = _text_from_content(message.get("content"))
            if text:
                return text

    contents = payload.get("contents")
    if isinstance(contents, list):
        for content in contents:
            if not isinstance(content, Mapping) or content.get("role") not in ("user", None):
                continue
            text = _text_from_parts(content.get("parts"))
            if text:
                return text

    request_payload = payload.get("request")
    if isinstance(request_payload, Mapping):
        return _first_user_text(request_payload)

    return None


def _text_from_content(content: Any) -> Optional[str]:
    if isinstance(content, str):
        return content.strip() or None
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, Mapping):
                text = part.get("text")
                if not isinstance(text, str):
                    text = part.get("content")
                if isinstance(text, str):
                    texts.append(text)
        joined = " ".join(texts).strip()
        return joined or None
    return None


def _text_from_parts(parts: Any) -> Optional[str]:
    if not isinstance(parts, list):
        return None
    texts = []
    for part in parts:
        if isinstance(part, Mapping):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    joined = " ".join(texts).strip()
    return joined or None
