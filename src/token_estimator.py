from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Dict, Optional

from log import log

try:
    import tiktoken
except Exception:
    tiktoken = None


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return str(value)


@lru_cache(maxsize=1)
def _get_encoding_name() -> str:
    if tiktoken is None:
        return ""
    try:
        tiktoken.get_encoding("o200k_base")
        return "o200k_base"
    except Exception:
        return "cl100k_base"


@lru_cache(maxsize=1)
def _get_encoding():
    if tiktoken is None:
        return None
    return tiktoken.get_encoding(_get_encoding_name())


def estimate_input_tokens_from_components(components: Dict[str, Any]) -> int:
    """
    基于 Antigravity components 估算输入 token 数。

    该估算用于：
    - `POST /antigravity/v1/messages/count_tokens`（生态预检）
    - 流式 `message_start.message.usage.input_tokens`（初始展示）

    注意：该值是本地预估口径，最终真实 token 仍以下游 `usageMetadata.promptTokenCount` 为准。
    """
    encoding = _get_encoding()
    if encoding is None:
        log.warning("[TOKEN] tiktoken 不可用，回退到 legacy 估算")
        return estimate_input_tokens_from_components_legacy(components)

    total_tokens = 0
    overhead_tokens = 6

    def add_text(text: Optional[str]) -> None:
        nonlocal total_tokens
        if not text:
            return
        total_tokens += len(encoding.encode(text))

    system_instruction = components.get("system_instruction")
    if isinstance(system_instruction, dict):
        overhead_tokens += 2
        for part in system_instruction.get("parts", []) or []:
            if isinstance(part, dict) and "text" in part:
                add_text(str(part.get("text", "")))

    contents = components.get("contents", []) or []
    if isinstance(contents, list):
        overhead_tokens += 2 * len(contents)

    part_count = 0
    for content in contents:
        if not isinstance(content, dict):
            continue
        add_text(str(content.get("role") or ""))
        for part in content.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            part_count += 1

            if "text" in part:
                add_text(str(part.get("text", "")))
                continue

            if "functionCall" in part:
                fc = part.get("functionCall", {}) or {}
                add_text(str(fc.get("name") or ""))
                add_text(_safe_json_dumps(fc.get("args", {}) or {}))
                continue

            if "functionResponse" in part:
                fr = part.get("functionResponse", {}) or {}
                add_text(str(fr.get("name") or ""))
                add_text(_safe_json_dumps(fr.get("response", {}) or {}))
                continue

            if "inlineData" in part:
                inline = part.get("inlineData", {}) or {}
                add_text(str(inline.get("mimeType") or ""))
                add_text(str(inline.get("data") or ""))
                continue

    overhead_tokens += part_count

    tool_decl_count = 0
    for tool in components.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        for decl in tool.get("functionDeclarations", []) or []:
            if not isinstance(decl, dict):
                continue
            tool_decl_count += 1
            add_text(str(decl.get("name") or ""))
            add_text(str(decl.get("description") or ""))
            add_text(_safe_json_dumps(decl.get("parameters", {}) or {}))

    overhead_tokens += 4 * tool_decl_count

    return max(0, int(total_tokens + overhead_tokens))


def estimate_input_tokens_from_components_legacy(components: Dict[str, Any]) -> int:
    """
    legacy 估算：基于文本长度的近似（兼容旧行为，便于回滚）。
    """
    approx_tokens = 0

    def add_text(text: str) -> None:
        nonlocal approx_tokens
        if not text:
            return
        approx_tokens += max(1, len(text) // 4)

    system_instruction = components.get("system_instruction")
    if isinstance(system_instruction, dict):
        for part in system_instruction.get("parts", []) or []:
            if isinstance(part, dict) and "text" in part:
                add_text(str(part.get("text", "")))

    for content in components.get("contents", []) or []:
        if not isinstance(content, dict):
            continue
        for part in content.get("parts", []) or []:
            if not isinstance(part, dict):
                continue
            if "text" in part:
                add_text(str(part.get("text", "")))
            elif "functionCall" in part:
                fc = part.get("functionCall", {}) or {}
                add_text(str(fc.get("name") or ""))
                add_text(_safe_json_dumps(fc.get("args", {}) or {}))
            elif "functionResponse" in part:
                fr = part.get("functionResponse", {}) or {}
                add_text(str(fr.get("name") or ""))
                add_text(_safe_json_dumps(fr.get("response", {}) or {}))

    for tool in components.get("tools", []) or []:
        if not isinstance(tool, dict):
            continue
        for decl in tool.get("functionDeclarations", []) or []:
            if not isinstance(decl, dict):
                continue
            add_text(str(decl.get("name") or ""))
            add_text(str(decl.get("description") or ""))
            add_text(_safe_json_dumps(decl.get("parameters", {}) or {}))

    return int(approx_tokens)
