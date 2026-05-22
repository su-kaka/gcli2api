import json
from typing import Any, Mapping

from fastapi import Response

from src.converter.thoughtSignature_fix import (
    is_internal_placeholder_text,
    is_skip_thought_signature_placeholder,
)


EMPTY_MODEL_OUTPUT_STATUS_CODE = 461
EMPTY_MODEL_OUTPUT_MESSAGE = "可能触发外审导致空回"
EMPTY_MODEL_OUTPUT_STATUS = "EMPTY_MODEL_OUTPUT"

_STRUCTURED_OUTPUT_KEYS = (
    "functionCall",
    "function_call",
    "functionResponse",
    "function_response",
    "inlineData",
    "inline_data",
    "fileData",
    "file_data",
    "executableCode",
    "executable_code",
    "codeExecutionResult",
    "code_execution_result",
)


def build_empty_model_output_response() -> Response:
    return Response(
        content=json.dumps(
            {
                "error": {
                    "code": EMPTY_MODEL_OUTPUT_STATUS_CODE,
                    "message": EMPTY_MODEL_OUTPUT_MESSAGE,
                    "status": EMPTY_MODEL_OUTPUT_STATUS,
                }
            },
            ensure_ascii=False,
        ),
        status_code=EMPTY_MODEL_OUTPUT_STATUS_CODE,
        media_type="application/json",
    )


def is_empty_model_output(raw_content: Any) -> bool:
    if raw_content is None:
        return True

    if isinstance(raw_content, bytes):
        content_text = raw_content.decode("utf-8", errors="ignore")
    elif isinstance(raw_content, str):
        content_text = raw_content
    else:
        content_text = str(raw_content)

    if not content_text.strip():
        return True

    try:
        payload = json.loads(content_text)
    except (TypeError, ValueError):
        return False

    return is_empty_model_output_payload(payload)


def is_empty_model_output_payload(payload: Any) -> bool:
    response_data = _get_response_data(payload)
    if response_data is None:
        return False

    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return True

    return not any(_candidate_has_visible_output(candidate) for candidate in candidates)


def has_visible_model_output_payload(payload: Any) -> bool:
    response_data = _get_response_data(payload)
    if response_data is None:
        return False

    candidates = response_data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return False

    return any(_candidate_has_visible_output(candidate) for candidate in candidates)


def stream_chunk_has_visible_output(chunk: Any) -> bool:
    if chunk is None:
        return False

    if isinstance(chunk, bytes):
        chunk_text = chunk.decode("utf-8", errors="ignore")
    elif isinstance(chunk, str):
        chunk_text = chunk
    else:
        chunk_text = str(chunk)

    for payload_text in _iter_stream_payloads(chunk_text):
        try:
            payload = json.loads(payload_text)
        except (TypeError, ValueError):
            continue

        if has_visible_model_output_payload(payload):
            return True

    return False


def _get_response_data(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None

    if payload.get("error"):
        return None

    response_data = payload.get("response") if isinstance(payload.get("response"), Mapping) else payload
    if not isinstance(response_data, Mapping) or response_data.get("error"):
        return None

    return response_data


def _iter_stream_payloads(chunk_text: str):
    stripped = chunk_text.strip()
    if not stripped:
        return

    if stripped.startswith("data:"):
        for line in chunk_text.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload_text = line[5:].strip()
            if payload_text and payload_text != "[DONE]":
                yield payload_text
        return

    if stripped != "[DONE]":
        yield stripped


def _candidate_has_visible_output(candidate: Any) -> bool:
    if not isinstance(candidate, Mapping):
        return False

    content = candidate.get("content")
    if not isinstance(content, Mapping):
        return False

    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        return False

    for part in parts:
        if not isinstance(part, Mapping):
            continue

        if is_skip_thought_signature_placeholder(part):
            continue

        if any(key in part for key in _STRUCTURED_OUTPUT_KEYS):
            return True

        if part.get("thought") is True:
            continue

        if "text" not in part:
            continue

        text = part.get("text")
        if isinstance(text, str):
            if is_internal_placeholder_text(text):
                continue
            if text.strip():
                return True
        elif text is not None:
            return True

    return False
