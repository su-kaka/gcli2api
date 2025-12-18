from __future__ import annotations

import json
import math
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


def _encode_len(encoding: Any, text: str) -> int:
    """
    安全计算 `tiktoken` 的 token 数。

    说明：
    - `tiktoken` 默认会拒绝某些“特殊 token 文本”（例如用户内容里出现 `<|endoftext|>`），并抛异常。
    - 在本项目的“本地预估”场景里，这些字符串应按普通文本计数，而不是让估算失败回退/归零。
    """
    if not text:
        return 0
    try:
        # 将所有 special token 视为普通文本，避免 disallowed special token 异常
        return len(encoding.encode(text, disallowed_special=()))
    except TypeError:
        # 兼容老版本 tiktoken（无 disallowed_special 参数）
        return len(encoding.encode(text))


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
    return estimate_input_tokens_from_components_with_options(components, calibrate=True)


def estimate_input_tokens_from_components_with_options(
    components: Dict[str, Any],
    *,
    calibrate: bool = True,
) -> int:
    """
    基于 Antigravity components 估算输入 token 数（支持可选校准）。

    `calibrate=True` 时会对“符号/日志/JSON/模板标记”密集的输入做启发式上调，
    以缩小与下游 `promptTokenCount` 的系统性偏差。
    """
    encoding = _get_encoding()
    if encoding is None:
        log.warning("[TOKEN] tiktoken 不可用，回退到 legacy 估算")
        return estimate_input_tokens_from_components_legacy(components)

    total_tokens = 0
    overhead_tokens = 5

    # thinkingConfig 的固定额外开销（下游实际 prompt 往往会加一小段内部前缀）
    generation_config = components.get("generation_config") or {}
    if isinstance(generation_config, dict):
        thinking_cfg = generation_config.get("thinkingConfig") or {}
        if isinstance(thinking_cfg, dict) and thinking_cfg.get("includeThoughts") is True:
            overhead_tokens += 28

    profile = {
        "chars_total": 0,
        "ascii": 0,
        "newline": 0,
        "special": 0,
        "backslash": 0,
        "quote": 0,
        "digit": 0,
        "brace": 0,
        "colon": 0,
        "has_angle_pipe": False,
        "has_json_like": False,
        "has_backslash_escapes": False,
        "has_code_fence": False,
    }

    special_chars = set("{}[]()<>\"'`\\|/:=,;@#$%^&*+-_")

    def update_profile(text: str) -> None:
        if not text:
            return
        profile["chars_total"] += len(text)
        profile["newline"] += text.count("\n")

        if "<|" in text or "|>" in text:
            profile["has_angle_pipe"] = True
        if "```" in text:
            profile["has_code_fence"] = True
        if "\\n" in text or "\\t" in text or "\\u" in text:
            profile["has_backslash_escapes"] = True
        if '{"' in text or '":' in text or "data:" in text:
            profile["has_json_like"] = True

        for ch in text:
            o = ord(ch)
            if o < 128:
                profile["ascii"] += 1
            if ch in special_chars:
                profile["special"] += 1
            if ch == "\\":
                profile["backslash"] += 1
            if ch == '"':
                profile["quote"] += 1
            if ch.isdigit():
                profile["digit"] += 1
            if ch in "{}[]":
                profile["brace"] += 1
            if ch == ":":
                profile["colon"] += 1

    def add_text(text: Optional[str]) -> None:
        nonlocal total_tokens
        if not text:
            return
        update_profile(text)
        total_tokens += _encode_len(encoding, text)

    def calibration_factor() -> float:
        """
        计算启发式校准系数（仅上调，避免把估算做小导致 preflight 误判）。

        经验规律：
        - 中文自然语言：本地 tiktoken 与下游偏差较小
        - 大量 ASCII/符号/转义/日志/JSON：下游计数通常更高
        """
        chars_total = int(profile.get("chars_total", 0) or 0)
        if chars_total < 80:
            return 1.0

        ascii_ratio = float(profile.get("ascii", 0) or 0) / chars_total
        special_ratio = float(profile.get("special", 0) or 0) / chars_total
        backslash_ratio = float(profile.get("backslash", 0) or 0) / chars_total
        quote_ratio = float(profile.get("quote", 0) or 0) / chars_total
        digit_ratio = float(profile.get("digit", 0) or 0) / chars_total
        brace_ratio = float(profile.get("brace", 0) or 0) / chars_total
        colon_ratio = float(profile.get("colon", 0) or 0) / chars_total

        newline_count = float(profile.get("newline", 0) or 0)
        newline_per_100 = (newline_count * 100.0 / chars_total) if chars_total else 0.0
        newline_score = min(1.0, newline_per_100 / 6.0)

        marker_score = 0.0
        if profile.get("has_angle_pipe"):
            marker_score += 0.03
        if profile.get("has_backslash_escapes"):
            marker_score += 0.02
        if profile.get("has_json_like"):
            marker_score += 0.02
        if profile.get("has_code_fence"):
            marker_score += 0.02
        marker_score = min(0.06, marker_score)

        # 核心：ASCII/符号/转义/JSON 日志越密集，越倾向上调
        # 说明：这里是“口径修正”，不是精确 tokenizer；只做上调，避免 preflight 偏小。
        ascii_excess = max(0.0, ascii_ratio - 0.30)
        score = (
            0.18 * ascii_excess
            + 0.22 * special_ratio
            + 0.10 * backslash_ratio
            + 0.08 * quote_ratio
            + 0.05 * digit_ratio
            + 0.10 * newline_score
            + marker_score
        )

        # 文本越长，系统性偏差越容易累积：给一个缓慢增长的 size bonus
        if chars_total > 2000:
            size_bonus = min(0.08, max(0.0, math.log(chars_total / 2000.0, 2) * 0.02))
            score += size_bonus

        # 对“明显像日志/JSON/模板标记”的超长输入，允许更高上调上限（但仍限幅）
        looks_like_logs = bool(
            profile.get("has_json_like")
            or profile.get("has_backslash_escapes")
            or profile.get("has_angle_pipe")
            or (brace_ratio > 0.01 and colon_ratio > 0.002 and quote_ratio > 0.005)
        )

        max_score = 0.25
        if looks_like_logs and chars_total > 4000:
            max_score = 0.35

        score = min(max_score, max(0.0, score))
        return 1.0 + score

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
                base64_data = inline.get("data")
                if isinstance(base64_data, str) and base64_data:
                    # 避免把 base64 当作纯文本分词计数，使用启发式折算降低数量级偏差。
                    total_tokens += int(math.ceil(math.sqrt(len(base64_data))))
                else:
                    total_tokens += 300
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

    raw_total = max(0, int(total_tokens + overhead_tokens))
    if not calibrate:
        return raw_total

    factor = calibration_factor()
    calibrated_total = int(round(raw_total * factor))
    return max(raw_total, calibrated_total)


def estimate_input_tokens_from_anthropic_request(payload: Dict[str, Any]) -> int:
    """
    基于 Anthropic Messages 原始请求估算输入 token 数（抽取→拼接→计数）。

    设计目标：
    - 口径尽量贴近 Anthropic 生态客户端的“上下文占用展示”（例如 Roo Code / claude-cli 的预检）
    - 不调用任何下游接口，仅做本地估算
    - 避免将图片 base64 当作纯文本 token 计数导致数量级偏差
    """
    encoding = _get_encoding()
    if encoding is None:
        log.warning("[TOKEN] tiktoken 不可用，回退到 legacy 估算")
        return estimate_input_tokens_from_anthropic_request_legacy(payload)

    def stable_json(value: Any) -> str:
        try:
            return json.dumps(
                value,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        except Exception:
            return _safe_json_dumps(value)

    text_parts: list[str] = []
    image_tokens = 0

    system = payload.get("system")
    if isinstance(system, str) and system:
        text_parts.append(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    text_parts.append(str(text))

    messages = payload.get("messages", []) or []
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                if content:
                    text_parts.append(content)
                continue

            if isinstance(content, dict):
                content = [content]

            if not isinstance(content, list):
                continue

            for block in content:
                if isinstance(block, str):
                    if block:
                        text_parts.append(block)
                    continue

                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")
                if block_type == "text":
                    text = block.get("text", "")
                    if text:
                        text_parts.append(str(text))
                    continue

                if block_type == "tool_use":
                    name = block.get("name", "")
                    if name:
                        text_parts.append(str(name))
                    text_parts.append(stable_json(block.get("input", {}) or {}))
                    continue

                if block_type == "tool_result":
                    tool_result_content = block.get("content", [])
                    if isinstance(tool_result_content, str):
                        if tool_result_content:
                            text_parts.append(tool_result_content)
                        continue
                    if isinstance(tool_result_content, dict):
                        tool_result_content = [tool_result_content]
                    if isinstance(tool_result_content, list):
                        for result_block in tool_result_content:
                            if isinstance(result_block, str):
                                if result_block:
                                    text_parts.append(result_block)
                                continue
                            if isinstance(result_block, dict) and result_block.get("type") == "text":
                                result_text = result_block.get("text", "")
                                if result_text:
                                    text_parts.append(str(result_text))
                        continue
                    continue

                if block_type == "image":
                    source = block.get("source")
                    if isinstance(source, dict) and isinstance(source.get("data"), str):
                        base64_data = source.get("data") or ""
                        image_tokens += int(math.ceil(math.sqrt(len(base64_data))))
                    else:
                        image_tokens += 300  # 无法解析的图片：固定开销
                    continue

    tools = payload.get("tools", []) or []
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("name", "")
            description = tool.get("description", "")
            if name:
                text_parts.append(str(name))
            if description:
                text_parts.append(str(description))
            text_parts.append(stable_json(tool.get("input_schema", {}) or {}))

    full_text = "\n".join([p for p in text_parts if isinstance(p, str) and p])
    try:
        text_tokens = int(_encode_len(encoding, full_text)) if full_text else 0
        return max(0, text_tokens + int(image_tokens))
    except Exception as e:
        log.warning(f"[TOKEN] tiktoken 计数失败，回退到 legacy: {e}")
        return estimate_input_tokens_from_anthropic_request_legacy(payload)


def estimate_input_tokens_from_anthropic_request_legacy(payload: Dict[str, Any]) -> int:
    """
    Anthropic 请求的 legacy 估算：基于抽取文本的长度近似（用于 tiktoken 不可用/失败时回退）。
    """
    approx_tokens = 0

    def add_text(text: str) -> None:
        nonlocal approx_tokens
        if not text:
            return
        approx_tokens += max(1, len(text) // 4)

    def add_image_base64(base64_data: str) -> None:
        nonlocal approx_tokens
        if not base64_data:
            approx_tokens += 300
            return
        approx_tokens += int(math.ceil(math.sqrt(len(base64_data))))

    system = payload.get("system")
    if isinstance(system, str):
        add_text(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict) and block.get("type") == "text":
                add_text(str(block.get("text", "")))

    messages = payload.get("messages", []) or []
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                add_text(content)
                continue
            if isinstance(content, dict):
                content = [content]
            if not isinstance(content, list):
                continue

            for block in content:
                if isinstance(block, str):
                    add_text(block)
                    continue
                if not isinstance(block, dict):
                    continue

                block_type = block.get("type")
                if block_type == "text":
                    add_text(str(block.get("text", "")))
                elif block_type == "tool_use":
                    add_text(str(block.get("name", "")))
                    add_text(_safe_json_dumps(block.get("input", {}) or {}))
                elif block_type == "tool_result":
                    tool_result_content = block.get("content", [])
                    if isinstance(tool_result_content, str):
                        add_text(tool_result_content)
                    elif isinstance(tool_result_content, dict):
                        tool_result_content = [tool_result_content]
                    if isinstance(tool_result_content, list):
                        for result_block in tool_result_content:
                            if isinstance(result_block, str):
                                add_text(result_block)
                            elif isinstance(result_block, dict) and result_block.get("type") == "text":
                                add_text(str(result_block.get("text", "")))
                elif block_type == "image":
                    source = block.get("source")
                    if isinstance(source, dict) and isinstance(source.get("data"), str):
                        add_image_base64(source.get("data") or "")
                    else:
                        approx_tokens += 300

    tools = payload.get("tools", []) or []
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            add_text(str(tool.get("name", "")))
            add_text(str(tool.get("description", "")))
            add_text(_safe_json_dumps(tool.get("input_schema", {}) or {}))

    return int(max(0, approx_tokens))


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
