from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator, Dict, Optional

from log import log


def _sse_event(event: str, data: Dict[str, Any]) -> bytes:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

_DEBUG_TRUE = {"1", "true", "yes", "on"}

def _remove_nulls_for_tool_input(value: Any) -> Any:
    """
    递归移除 dict/list 中值为 null/None 的字段/元素。

    背景：Roo/Kilo 在 Anthropic native tool 路径下，若收到 tool_use.input 中包含 null，
    可能会把 null 当作真实入参执行（例如“在 null 中搜索”）。因此在输出 input_json_delta 前做兜底清理。
    """
    if isinstance(value, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            cleaned[k] = _remove_nulls_for_tool_input(v)
        return cleaned

    if isinstance(value, list):
        cleaned_list = []
        for item in value:
            if item is None:
                continue
            cleaned_list.append(_remove_nulls_for_tool_input(item))
        return cleaned_list

    return value


def _anthropic_debug_enabled() -> bool:
    return str(os.getenv("ANTHROPIC_DEBUG", "")).strip().lower() in _DEBUG_TRUE


class _StreamingState:
    def __init__(self, message_id: str, model: str):
        self.message_id = message_id
        self.model = model

        self._current_block_type: Optional[str] = None
        self._current_block_index: int = -1
        self._current_thinking_signature: Optional[str] = None

        self.has_tool_use: bool = False
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.has_input_tokens: bool = False
        self.has_output_tokens: bool = False
        self.finish_reason: Optional[str] = None

    def _next_index(self) -> int:
        self._current_block_index += 1
        return self._current_block_index

    def close_block_if_open(self) -> Optional[bytes]:
        if self._current_block_type is None:
            return None
        event = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": self._current_block_index},
        )
        self._current_block_type = None
        self._current_thinking_signature = None
        return event

    def open_text_block(self) -> bytes:
        idx = self._next_index()
        self._current_block_type = "text"
        return _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": {"type": "text", "text": ""},
            },
        )

    def open_thinking_block(self, signature: Optional[str]) -> bytes:
        idx = self._next_index()
        self._current_block_type = "thinking"
        self._current_thinking_signature = signature
        block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
        if signature:
            block["signature"] = signature
        return _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": idx,
                "content_block": block,
            },
        )


async def antigravity_sse_to_anthropic_sse(
    lines: AsyncIterator[str],
    *,
    model: str,
    message_id: str,
    initial_input_tokens: int = 0,
    estimated_input_tokens_components_raw: int = 0,
    calibration_key: Optional[str] = None,
    credential_manager: Any = None,
    credential_name: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """
    将 Antigravity SSE（data: {...}）转换为 Anthropic Messages Streaming SSE。
    """
    state = _StreamingState(message_id=message_id, model=model)
    success_recorded = False

    try:
        initial_input_tokens_int = max(0, int(initial_input_tokens or 0))
    except Exception:
        initial_input_tokens_int = 0

    def pick_usage_metadata(response: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        response_usage = response.get("usageMetadata", {}) or {}
        if not isinstance(response_usage, dict):
            response_usage = {}

        candidate_usage = candidate.get("usageMetadata", {}) or {}
        if not isinstance(candidate_usage, dict):
            candidate_usage = {}

        fields = ("promptTokenCount", "candidatesTokenCount", "totalTokenCount")

        def score(d: Dict[str, Any]) -> int:
            s = 0
            for f in fields:
                if f in d and d.get(f) is not None:
                    s += 1
            return s

        if score(candidate_usage) > score(response_usage):
            return candidate_usage
        return response_usage

    yield _sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": message_id,
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": initial_input_tokens_int, "output_tokens": 0},
            },
        },
    )

    try:
        async for line in lines:
            if not line or not line.startswith("data: "):
                continue

            raw = line[6:].strip()
            if raw == "[DONE]":
                break

            if not success_recorded and credential_manager and credential_name:
                await credential_manager.record_api_call_result(
                    credential_name, True, is_antigravity=True
                )
                success_recorded = True

            try:
                data = json.loads(raw)
            except Exception:
                continue

            response = data.get("response", {}) or {}
            candidate = (response.get("candidates", []) or [{}])[0] or {}
            parts = (candidate.get("content", {}) or {}).get("parts", []) or []

            # 在任意 chunk 中尽早捕获 usageMetadata（优先选择字段更完整的一侧）
            if isinstance(response, dict) and isinstance(candidate, dict):
                usage = pick_usage_metadata(response, candidate)
                if isinstance(usage, dict):
                    if "promptTokenCount" in usage:
                        state.input_tokens = int(usage.get("promptTokenCount", 0) or 0)
                        state.has_input_tokens = True
                    if "candidatesTokenCount" in usage:
                        state.output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
                        state.has_output_tokens = True

            for part in parts:
                if not isinstance(part, dict):
                    continue

                if part.get("thought") is True:
                    if state._current_block_type != "thinking":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            yield stop_evt
                        signature = part.get("thoughtSignature")
                        yield state.open_thinking_block(signature=signature)

                    thinking_text = part.get("text", "")
                    if thinking_text:
                        yield _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "thinking_delta", "thinking": thinking_text},
                            },
                        )
                    continue

                if "text" in part:
                    text = part.get("text", "")
                    if isinstance(text, str) and not text.strip():
                        continue

                    if state._current_block_type != "text":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            yield stop_evt
                        yield state.open_text_block()

                    if text:
                        yield _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                    continue

                if "inlineData" in part:
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        yield stop_evt

                    inline = part.get("inlineData", {}) or {}
                    idx = state._next_index()
                    block = {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": inline.get("mimeType", "image/png"),
                            "data": inline.get("data", ""),
                        },
                    }
                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": block,
                        },
                    )
                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    continue

                if "functionCall" in part:
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        yield stop_evt

                    state.has_tool_use = True

                    fc = part.get("functionCall", {}) or {}
                    tool_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    idx = state._next_index()
                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": {
                                "type": "tool_use",
                                "id": tool_id,
                                "name": tool_name,
                                "input": {},
                            },
                        },
                    )

                    input_json = json.dumps(tool_args, ensure_ascii=False, separators=(",", ":"))
                    yield _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )
                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    continue

            finish_reason = candidate.get("finishReason")
            if finish_reason:
                state.finish_reason = str(finish_reason)
                break

        stop_evt = state.close_block_if_open()
        if stop_evt:
            yield stop_evt

        stop_reason = "tool_use" if state.has_tool_use else "end_turn"
        if state.finish_reason == "MAX_TOKENS" and not state.has_tool_use:
            stop_reason = "max_tokens"

        if _anthropic_debug_enabled():
            estimated_input = initial_input_tokens_int
            estimated_components_raw = max(0, int(estimated_input_tokens_components_raw or 0))
            downstream_input = state.input_tokens if state.has_input_tokens else 0
            log.info(
                f"[ANTHROPIC][TOKEN] 流式 input_tokens 对比: estimated_initial={estimated_input}, "
                f"estimated_components_raw={estimated_components_raw}, downstream={downstream_input}, "
                f"has_downstream={state.has_input_tokens}"
            )

        # 用真实值更新校准器（供下一次预估使用；不记录任何文本内容）
        if state.has_input_tokens and calibration_key and estimated_input_tokens_components_raw:
            try:
                from .token_calibrator import token_calibrator

                token_calibrator.update(
                    calibration_key,
                    raw_tokens=int(estimated_input_tokens_components_raw or 0),
                    downstream_tokens=int(state.input_tokens or 0),
                )
            except Exception as e:
                log.debug(f"[ANTHROPIC][TOKEN] 更新校准器失败（忽略）: {e}")

        yield _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {
                    "input_tokens": state.input_tokens if state.has_input_tokens else initial_input_tokens_int,
                    "output_tokens": state.output_tokens if state.has_output_tokens else 0,
                },
            },
        )
        yield _sse_event("message_stop", {"type": "message_stop"})

    except Exception as e:
        log.error(f"[ANTHROPIC] 流式转换失败: {e}")
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )
