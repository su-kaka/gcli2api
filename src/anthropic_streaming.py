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
    credential_manager: Any = None,
    credential_name: Optional[str] = None,
) -> AsyncIterator[bytes]:
    """
    将 Antigravity SSE（data: {...}）转换为 Anthropic Messages Streaming SSE。
    """
    state = _StreamingState(message_id=message_id, model=model)
    success_recorded = False
    message_start_sent = False
    pending_output: list[bytes] = []

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

    def enqueue(evt: bytes) -> None:
        pending_output.append(evt)

    def flush_pending_ready(ready: list[bytes]) -> None:
        if not pending_output:
            return
        ready.extend(pending_output)
        pending_output.clear()

    def send_message_start(ready: list[bytes], *, input_tokens: int) -> None:
        nonlocal message_start_sent
        if message_start_sent:
            return
        message_start_sent = True
        ready.append(
            _sse_event(
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
                        "usage": {"input_tokens": int(input_tokens or 0), "output_tokens": 0},
                    },
                },
            )
        )
        flush_pending_ready(ready)

    try:
        async for line in lines:
            ready_output: list[bytes] = []
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

            # 为保证 message_start 永远是首个事件：在拿到真实值之前，把所有事件暂存到 pending_output。
            if state.has_input_tokens and not message_start_sent:
                send_message_start(ready_output, input_tokens=state.input_tokens)

            for part in parts:
                if not isinstance(part, dict):
                    continue

                if _anthropic_debug_enabled() and "thoughtSignature" in part:
                    try:
                        sig_val = part.get("thoughtSignature")
                        sig_len = len(str(sig_val)) if sig_val is not None else 0
                    except Exception:
                        sig_len = -1
                    log.info(
                        "[ANTHROPIC][thinking_signature] 收到 thoughtSignature 字段: "
                        f"current_block_type={state._current_block_type}, "
                        f"current_index={state._current_block_index}, len={sig_len}"
                    )

                # 兼容：下游可能会把 thoughtSignature 单独作为一个空 part 发送（此时未必带 thought=true）。
                # 只要当前处于 thinking 块且尚未记录 signature，就用 signature_delta 补发。
                signature = part.get("thoughtSignature")
                if (
                    signature
                    and state._current_block_type == "thinking"
                    and not state._current_thinking_signature
                ):
                    evt = _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": state._current_block_index,
                            "delta": {"type": "signature_delta", "signature": signature},
                        },
                    )
                    state._current_thinking_signature = str(signature)
                    if message_start_sent:
                        ready_output.append(evt)
                    else:
                        enqueue(evt)
                    if _anthropic_debug_enabled():
                        log.info(
                            "[ANTHROPIC][thinking_signature] 已输出 signature_delta: "
                            f"index={state._current_block_index}"
                        )

                if part.get("thought") is True:
                    if state._current_block_type != "thinking":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            if message_start_sent:
                                ready_output.append(stop_evt)
                            else:
                                enqueue(stop_evt)
                        signature = part.get("thoughtSignature")
                        evt = state.open_thinking_block(signature=signature)
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    thinking_text = part.get("text", "")
                    if thinking_text:
                        evt = _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "thinking_delta", "thinking": thinking_text},
                            },
                        )
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    continue

                if "text" in part:
                    text = part.get("text", "")
                    if isinstance(text, str) and not text.strip():
                        continue

                    if state._current_block_type != "text":
                        stop_evt = state.close_block_if_open()
                        if stop_evt:
                            if message_start_sent:
                                ready_output.append(stop_evt)
                            else:
                                enqueue(stop_evt)
                        evt = state.open_text_block()
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)

                    if text:
                        evt = _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": state._current_block_index,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                        if message_start_sent:
                            ready_output.append(evt)
                        else:
                            enqueue(evt)
                    continue

                if "inlineData" in part:
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        if message_start_sent:
                            ready_output.append(stop_evt)
                        else:
                            enqueue(stop_evt)

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
                    evt1 = _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": idx,
                            "content_block": block,
                        },
                    )
                    evt2 = _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    if message_start_sent:
                        ready_output.extend([evt1, evt2])
                    else:
                        enqueue(evt1)
                        enqueue(evt2)
                    continue

                if "functionCall" in part:
                    stop_evt = state.close_block_if_open()
                    if stop_evt:
                        if message_start_sent:
                            ready_output.append(stop_evt)
                        else:
                            enqueue(stop_evt)

                    state.has_tool_use = True

                    fc = part.get("functionCall", {}) or {}
                    tool_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    idx = state._next_index()
                    evt_start = _sse_event(
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
                    evt_delta = _sse_event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": idx,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )
                    evt_stop = _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": idx},
                    )
                    if message_start_sent:
                        ready_output.extend([evt_start, evt_delta, evt_stop])
                    else:
                        enqueue(evt_start)
                        enqueue(evt_delta)
                        enqueue(evt_stop)
                    continue

            finish_reason = candidate.get("finishReason")

            if ready_output:
                for evt in ready_output:
                    yield evt

            if finish_reason:
                state.finish_reason = str(finish_reason)
                break

        stop_evt = state.close_block_if_open()
        if stop_evt:
            if message_start_sent:
                yield stop_evt
            else:
                enqueue(stop_evt)

        # 流结束仍未拿到下游 usageMetadata 时，兜底使用估算值发送 message_start，保证协议完整。
        if not message_start_sent:
            ready_output = []
            send_message_start(ready_output, input_tokens=initial_input_tokens_int)
            for evt in ready_output:
                yield evt

        stop_reason = "tool_use" if state.has_tool_use else "end_turn"
        if state.finish_reason == "MAX_TOKENS" and not state.has_tool_use:
            stop_reason = "max_tokens"

        if _anthropic_debug_enabled():
            estimated_input = initial_input_tokens_int
            downstream_input = state.input_tokens if state.has_input_tokens else 0
            log.info(
                f"[ANTHROPIC][TOKEN] 流式 token: estimated={estimated_input}, "
                f"downstream={downstream_input}"
            )

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
        # 错误场景也尽量保证客户端先收到 message_start（否则部分客户端会直接挂起）。
        if not message_start_sent:
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
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )
