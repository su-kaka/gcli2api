"""
Anti-Truncation Module - Ensures complete streaming output
保持一个流式请求内完整输出的反截断模块
"""

import re
from typing import Any, Dict, Tuple

from log import log

# 反截断配置
DONE_MARKER = "[done]"
CONTINUATION_PROMPT = f"""请从刚才被截断的地方继续输出剩余的所有内容。

重要提醒：
1. 不要重复前面已经输出的内容
2. 直接继续输出，无需任何前言或解释
3. 当你完整完成所有内容输出后，必须在最后一行单独输出:{DONE_MARKER}
4. {DONE_MARKER} 标记表示你的回答已经完全结束，这是必需的结束标记

现在请继续输出:"""

# 正则替换配置
REGEX_REPLACEMENTS: list[Tuple[str, str, str]] = [
    (
        "age_pattern",  # 替换规则名称
        r"(?:[1-9]|1[0-8])岁(?:的)?|(?:十一|十二|十三|十四|十五|十六|十七|十八|十|一|二|三|四|五|六|七|八|九)岁(?:的)?",  # 正则模式
        "",  # 替换文本
    ),
    # 可在此处添加更多替换规则
    # ("rule_name", r"pattern", "replacement"),
]


def apply_regex_replacements(text: str) -> str:
    """
    对文本应用正则替换规则

    Args:
        text: 要处理的文本

    Returns:
        处理后的文本
    """
    if not text:
        return text

    processed_text = text
    replacement_count = 0

    for rule_name, pattern, replacement in REGEX_REPLACEMENTS:
        try:
            # 编译正则表达式，使用IGNORECASE标志
            regex = re.compile(pattern, re.IGNORECASE)

            # 执行替换
            new_text, count = regex.subn(replacement, processed_text)

            if count > 0:
                log.debug(f"Regex replacement '{rule_name}': {count} matches replaced")
                processed_text = new_text
                replacement_count += count

        except re.error as e:
            log.error(f"Invalid regex pattern in rule '{rule_name}': {e}")
            continue

    if replacement_count > 0:
        log.info(f"Applied {replacement_count} regex replacements to text")

    return processed_text


def normalize_gemini_request(request_body: Dict[str, Any]) -> Dict[str, Any]:
    """
    标准化Gemini格式请求体
    1. 对所有文本内容应用正则替换
    2. 在systemInstruction的最末位添加CONTINUATION_PROMPT

    Args:
        request_body: Gemini格式请求体（字典格式）

    Returns:
        经过正则化处理和增加CONTINUATION_PROMPT的Gemini格式请求体
    """
    result = request_body.copy()

    # 第一步：对contents中的所有文本应用正则替换
    if "contents" in result:
        new_contents = []
        for content in result["contents"]:
            if isinstance(content, dict):
                new_content = content.copy()
                parts = new_content.get("parts", [])
                if parts:
                    new_parts = []
                    for part in parts:
                        if isinstance(part, dict) and "text" in part:
                            new_part = part.copy()
                            new_part["text"] = apply_regex_replacements(part["text"])
                            new_parts.append(new_part)
                        else:
                            new_parts.append(part)
                    new_content["parts"] = new_parts
                new_contents.append(new_content)
            else:
                new_contents.append(content)
        result["contents"] = new_contents

    # 第二步：在systemInstruction的最末位添加CONTINUATION_PROMPT
    system_instruction = result.get("systemInstruction")

    # 提取原有的 parts（如果存在）
    existing_parts = []
    if system_instruction:
        if isinstance(system_instruction, dict):
            existing_parts = system_instruction.get("parts", [])

    # CONTINUATION_PROMPT 放在最后一位，原有内容整体前移
    anti_truncation_instruction = {
        "text": f"""严格执行以下输出结束规则：

1. 当你完成完整回答时，必须在输出的最后单独一行输出:{DONE_MARKER}
2. {DONE_MARKER} 标记表示你的回答已经完全结束，这是必需的结束标记
3. 只有输出了 {DONE_MARKER} 标记，系统才认为你的回答是完整的
4. 如果你的回答被截断，系统会要求你继续输出剩余内容
5. 无论回答长短，都必须以 {DONE_MARKER} 标记结束

示例格式:
```
你的回答内容...
更多回答内容...
{DONE_MARKER}
```

注意:{DONE_MARKER} 必须单独占一行，前面不要有任何其他字符。

这个规则对于确保输出完整性极其重要，请严格遵守。"""
    }

    # 检查是否已经包含反截断指令
    has_done_instruction = any(
        part.get("text", "").find(DONE_MARKER) != -1
        for part in existing_parts
        if isinstance(part, dict)
    )

    if not has_done_instruction:
        result["systemInstruction"] = {
            "parts": existing_parts + [anti_truncation_instruction]
        }
    else:
        # 如果已经有了，保持原样
        if system_instruction:
            result["systemInstruction"] = system_instruction

    log.debug("Normalized Gemini request with regex replacements and anti-truncation prompt")

    return result


def normalize_gemini_response_stream(
    stream_chunk: str, is_last_chunk: bool = False
) -> Tuple[str, bool]:
    """
    标准化Gemini格式回复流
    只处理流的最末端chunk，进行正则化和检查DONE_MARKER

    Args:
        stream_chunk: Gemini格式回复流的一个chunk（str格式）
        is_last_chunk: 是否是最后一个chunk

    Returns:
        Tuple[str, bool]: (处理后的chunk, 是否发现了DONE_MARKER)
    """
    # 如果不是最后一个chunk，直接返回原始内容，不检查
    if not is_last_chunk:
        return stream_chunk, False

    # 只处理最后一个chunk
    found_done_marker = False
    processed_chunk = stream_chunk

    # 检查是否包含DONE_MARKER
    if DONE_MARKER in stream_chunk:
        found_done_marker = True
        log.info(f"Found {DONE_MARKER} marker in last chunk")

        # 应用正则替换，移除DONE_MARKER
        # 编译正则表达式，匹配[done]标记（忽略大小写，包括可能的空白字符）
        done_pattern = re.compile(r"\s*\[done\]\s*", re.IGNORECASE)
        processed_chunk = done_pattern.sub("", stream_chunk)

        log.debug(f"Removed {DONE_MARKER} from chunk")

    return processed_chunk, found_done_marker
