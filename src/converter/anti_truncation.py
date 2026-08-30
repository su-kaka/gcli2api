"""
Anti-Truncation Module - Synthetic Tool Call Approach
通过注入合成工具（emit_answer）让模型将最终回答放入 functionCall 的 content 参数中，
网关再从 functionCall 中提取内容还原为普通 assistant 文本，以此绕过外审截断。

参考: https://github.com/Xeltra233/Antigravity-anti-truncation-gateway
"""

import io
import json
import re
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from fastapi.responses import StreamingResponse

from log import log

# ==================== 合成工具配置 ====================

SYNTHETIC_TOOL_NAME = "emit_answer"

SYNTHETIC_TOOL_DECLARATION = {
    "name": SYNTHETIC_TOOL_NAME,
    "description": (
        "You MUST call this tool exactly once to output your final user-visible answer. "
        "Put the complete answer in the 'content' argument. "
        "Do NOT output any text outside this tool call."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The complete final answer to output to the user.",
            },
        },
        "required": ["content"],
    },
}

CONTROL_INSTRUCTION = f"""严格执行以下输出规则：

1. 你必须调用 `{SYNTHETIC_TOOL_NAME}` 工具来输出你的最终回答
2. 将完整的回答内容放入该工具的 `content` 参数中
3. 不要在普通文本中输出任何内容，所有回答必须通过 `{SYNTHETIC_TOOL_NAME}` 工具输出
4. 如果你的回答被截断，系统会要求你继续输出剩余内容
5. 续传时，将剩余内容继续通过 `{SYNTHETIC_TOOL_NAME}` 工具输出

这个规则对于确保输出完整性极其重要，请严格遵守。"""

CONTINUATION_PROMPT = f"""你之前的回复被截断了。请调用 `{SYNTHETIC_TOOL_NAME}` 工具继续输出剩余的所有内容。

重要提醒：
1. 不要重复前面已经输出的内容
2. 直接继续输出，无需任何前言或解释
3. 将剩余内容放入 `{SYNTHETIC_TOOL_NAME}` 工具的 `content` 参数中

现在请继续输出："""


# ==================== 请求注入 ====================


def apply_anti_truncation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    对请求 payload 应用反截断处理：注入合成工具和控制指令

    Args:
        payload: 原始请求 payload，格式为 {"model": ..., "request": {...}}

    Returns:
        注入了合成工具和控制指令的 payload
    """
    modified_payload = payload.copy()
    request_data = modified_payload.get("request", {})

    # 1. 注入合成工具到 tools 列表
    tools = request_data.get("tools") or []
    # 检查是否已注入
    already_injected = any(
        isinstance(tool, dict)
        and any(
            decl.get("name") == SYNTHETIC_TOOL_NAME
            for decl in (tool.get("functionDeclarations") or [])
            if isinstance(decl, dict)
        )
        for tool in tools
    )
    if not already_injected:
        tools.append({"functionDeclarations": [SYNTHETIC_TOOL_DECLARATION]})
        request_data["tools"] = tools
        log.debug(f"Anti-truncation: Injected synthetic tool '{SYNTHETIC_TOOL_NAME}'")

    # 2. 确保 toolConfig.functionCallingConfig.mode 允许工具调用
    tool_config = request_data.get("toolConfig") or {}
    func_config = tool_config.get("functionCallingConfig") or {}
    current_mode = func_config.get("mode", "")
    # 如果当前模式是 NONE（禁止工具调用），需要改为 AUTO
    if current_mode == "NONE":
        func_config["mode"] = "AUTO"
        tool_config["functionCallingConfig"] = func_config
        request_data["toolConfig"] = tool_config
        log.debug("Anti-truncation: Changed functionCallingConfig.mode from NONE to AUTO")
    elif not current_mode:
        # 未设置时默认 AUTO
        func_config["mode"] = "AUTO"
        tool_config["functionCallingConfig"] = func_config
        request_data["toolConfig"] = tool_config

    # 3. 注入控制指令到 systemInstruction
    system_instruction = request_data.get("systemInstruction") or {}
    if "parts" not in system_instruction:
        system_instruction["parts"] = []

    has_control_instruction = any(
        isinstance(part, dict) and SYNTHETIC_TOOL_NAME in part.get("text", "")
        for part in system_instruction["parts"]
    )
    if not has_control_instruction:
        system_instruction["parts"].append({"text": CONTROL_INSTRUCTION})
        request_data["systemInstruction"] = system_instruction
        log.debug("Anti-truncation: Injected control instruction into systemInstruction")

    modified_payload["request"] = request_data
    return modified_payload


# ==================== 响应提取 ====================


def extract_synthetic_content_from_response(
    data: Dict[str, Any],
) -> Tuple[str, List[Dict[str, Any]], bool]:
    """
    从 Gemini 响应中提取合成工具的内容和真实工具调用

    Args:
        data: Gemini 响应数据（可能包含 response 包装层）

    Returns:
        (synthetic_content, real_function_calls, found_synthetic) 元组
        - synthetic_content: 从 emit_answer 工具提取的文本内容
        - real_function_calls: 真实工具调用的 parts 列表（原样保留）
        - found_synthetic: 是否找到了合成工具调用
    """
    # 解包 response 字段
    if "response" in data:
        data = data["response"]

    synthetic_content = ""
    real_function_calls = []
    found_synthetic = False

    for candidate in data.get("candidates", []):
        content = candidate.get("content", {})
        parts = content.get("parts", [])
        for part in parts:
            if not isinstance(part, dict):
                continue
            if "functionCall" in part:
                fc = part["functionCall"]
                if fc.get("name") == SYNTHETIC_TOOL_NAME:
                    found_synthetic = True
                    args = fc.get("args", {})
                    if isinstance(args, dict):
                        synthetic_content += args.get("content", "")
                    elif isinstance(args, str):
                        # 尝试解析 JSON 字符串
                        extracted = _extract_content_from_json_str(args)
                        if extracted is not None:
                            synthetic_content += extracted
                else:
                    # 真实工具调用，原样保留
                    real_function_calls.append(part)

    return synthetic_content, real_function_calls, found_synthetic


def _extract_content_from_json_str(args_str: str) -> Optional[str]:
    """
    从 JSON 字符串中提取 content 字段（带正则 fallback）

    Args:
        args_str: JSON 字符串

    Returns:
        提取的 content 或 None
    """
    # 先尝试标准 JSON 解析
    try:
        parsed = json.loads(args_str)
        if isinstance(parsed, dict) and "content" in parsed:
            return str(parsed["content"])
    except (json.JSONDecodeError, TypeError):
        pass

    # 正则 fallback：匹配 "content": "..." 模式
    # 支持转义字符
    match = re.search(
        r'"content"\s*:\s*"((?:[^"\\]|\\.)*)"',
        args_str,
        re.DOTALL,
    )
    if match:
        raw_content = match.group(1)
        # 解码 JSON 转义序列
        try:
            return json.loads(f'"{raw_content}"')
        except (json.JSONDecodeError, ValueError):
            # 手动处理常见转义
            return (
                raw_content.replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace('\\"', '"')
                .replace("\\\\", "\\")
            )

    return None


def build_text_chunk_from_synthetic(
    original_data: Dict[str, Any],
    synthetic_content: str,
    real_function_calls: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """
    将合成工具提取的内容构建为普通文本 chunk

    Args:
        original_data: 原始 Gemini chunk 数据
        synthetic_content: 从合成工具提取的文本
        real_function_calls: 真实工具调用 parts

    Returns:
        修改后的 chunk 数据，functionCall(emit_answer) 被替换为 text part
    """
    # 解包 response 字段
    has_response_wrapper = "response" in original_data
    if has_response_wrapper:
        inner_data = original_data["response"]
    else:
        inner_data = original_data

    modified_inner = inner_data.copy()
    modified_candidates = []

    for candidate in inner_data.get("candidates", []):
        modified_candidate = candidate.copy()
        content = candidate.get("content", {})
        parts = content.get("parts", [])

        new_parts = []
        for part in parts:
            if not isinstance(part, dict):
                new_parts.append(part)
                continue

            if "functionCall" in part:
                fc = part["functionCall"]
                if fc.get("name") == SYNTHETIC_TOOL_NAME:
                    # 替换为文本 part
                    if synthetic_content:
                        new_parts.append({"text": synthetic_content})
                    # 如果 content 为空，跳过此 part
                    continue
                else:
                    # 真实工具调用，保留
                    new_parts.append(part)
            else:
                new_parts.append(part)

        modified_content = content.copy()
        modified_content["parts"] = new_parts
        modified_candidate["content"] = modified_content

        modified_candidates.append(modified_candidate)

    modified_inner["candidates"] = modified_candidates

    if has_response_wrapper:
        result = original_data.copy()
        result["response"] = modified_inner
        return result
    return modified_inner


# ==================== 流式处理器 ====================


class AntiTruncationStreamProcessor:
    """反截断流式处理器 - 基于合成工具调用"""

    def __init__(
        self,
        original_request_func,
        payload: Dict[str, Any],
        max_attempts: int = 3,
        enable_prefill_mode: bool = False,
    ):
        self.original_request_func = original_request_func
        self.base_payload = payload.copy()
        self.max_attempts = max_attempts
        self.enable_prefill_mode = enable_prefill_mode
        self.collected_content = io.StringIO()
        self.current_attempt = 0

    def _get_collected_text(self) -> str:
        """获取收集的文本内容"""
        return self.collected_content.getvalue()

    def _append_content(self, content: str):
        """追加内容到收集器"""
        if content:
            self.collected_content.write(content)

    def _clear_content(self):
        """清空收集的内容，释放内存"""
        self.collected_content.close()
        self.collected_content = io.StringIO()

    async def process_stream(self) -> AsyncGenerator[bytes, None]:
        """处理流式响应，检测并处理截断"""

        while self.current_attempt < self.max_attempts:
            self.current_attempt += 1

            # 构建当前请求 payload
            current_payload = self._build_current_payload()

            log.debug(f"Anti-truncation attempt {self.current_attempt}/{self.max_attempts}")

            try:
                response = await self.original_request_func(current_payload)

                if not isinstance(response, StreamingResponse):
                    # 非流式响应，直接处理
                    yield await self._handle_non_streaming_response(response)
                    return

                # 处理流式响应
                found_synthetic = False
                has_real_tool_calls = False
                side_buffer = io.StringIO()  # 暂存普通文本（防拼接）

                async for line in response.body_iterator:
                    if not line:
                        yield line
                        continue

                    # 处理上游生成器 yield 出 Response 对象的情况（错误响应）
                    from fastapi import Response as FastAPIResponse

                    if isinstance(line, FastAPIResponse):
                        log.error(
                            f"Anti-truncation: Received Response object from stream "
                            f"(status={line.status_code}), treating as error"
                        )
                        error_chunk = {
                            "error": {
                                "message": (
                                    line.body.decode("utf-8", errors="ignore")
                                    if hasattr(line, "body") and line.body
                                    else "Upstream error"
                                ),
                                "type": "api_error",
                                "code": line.status_code,
                            }
                        }
                        yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                        yield b"data: [DONE]\n\n"
                        return

                    # 解码 bytes 为字符串
                    if isinstance(line, bytes):
                        line_str = line.decode("utf-8", errors="ignore").strip()
                    else:
                        line_str = str(line).strip()

                    if not line_str:
                        yield line
                        continue

                    # 处理 SSE 格式的数据行
                    if line_str.startswith("data: "):
                        payload_str = line_str[6:]

                        # 检查是否是 [DONE] 标记
                        if payload_str.strip() == "[DONE]":
                            if found_synthetic:
                                log.info(
                                    "Anti-truncation: Stream complete with synthetic tool call"
                                )
                                yield line
                                side_buffer.close()
                                self._clear_content()
                                return
                            else:
                                log.warning(
                                    "Anti-truncation: Stream ended without synthetic tool call"
                                )
                                # 不发送 [DONE]，准备续传
                                break

                        # 尝试解析 JSON 数据
                        try:
                            data = json.loads(payload_str)
                        except (json.JSONDecodeError, ValueError):
                            yield line
                            continue

                        # 提取合成工具内容和真实工具调用
                        synthetic_content, real_calls, chunk_has_synthetic = (
                            extract_synthetic_content_from_response(data)
                        )

                        if chunk_has_synthetic:
                            found_synthetic = True
                            # 防拼接：丢弃之前暂存的普通文本
                            if side_buffer.getvalue():
                                log.warning(
                                    "Anti-truncation: Discarding side-buffered text "
                                    "(content conflict with synthetic tool)"
                                )
                                side_buffer.close()
                                side_buffer = io.StringIO()

                            # 收集内容用于续传
                            self._append_content(synthetic_content)

                            # 构建替换后的 chunk
                            modified_data = build_text_chunk_from_synthetic(
                                data, synthetic_content, real_calls
                            )
                            json_str = json.dumps(
                                modified_data, separators=(",", ":"), ensure_ascii=False
                            )
                            yield f"data: {json_str}\n\n".encode("utf-8")

                        elif real_calls:
                            # 真实工具调用，原样透传
                            has_real_tool_calls = True
                            yield line

                        else:
                            # 普通文本 chunk
                            if found_synthetic:
                                # 已有合成工具调用，丢弃普通文本（防拼接）
                                log.debug(
                                    "Anti-truncation: Dropping plain text after synthetic tool call"
                                )
                                continue
                            else:
                                # 暂存到 side buffer，等待看是否有合成工具调用
                                text = self._extract_text_from_chunk(data)
                                if text:
                                    side_buffer.write(text)
                                # 暂时不透传，等流结束时决定
                                continue

                    else:
                        # 非 data: 开头的行，直接传递
                        yield line

                # 流结束（break 或正常结束）
                side_text = side_buffer.getvalue()
                side_buffer.close()

                if found_synthetic:
                    # 成功收到合成工具调用
                    log.info("Anti-truncation: Found synthetic tool call, output complete")
                    self._clear_content()
                    yield b"data: [DONE]\n\n"
                    return

                # 未收到合成工具调用
                if side_text:
                    # 有普通文本作为 fallback，输出它
                    log.info(
                        f"Anti-truncation: No synthetic tool call, "
                        f"using side-buffered text as fallback (length: {len(side_text)})"
                    )
                    self._append_content(side_text)
                    # 构建一个包含 side buffer 文本的 chunk 输出
                    fallback_chunk = self._build_fallback_text_chunk(side_text)
                    if fallback_chunk:
                        yield fallback_chunk

                # 触发续传
                if self.current_attempt < self.max_attempts:
                    accumulated_text = self._get_collected_text()
                    total_length = len(accumulated_text)
                    log.info(
                        f"Anti-truncation: No synthetic tool call in output "
                        f"(length: {total_length}), preparing continuation "
                        f"(attempt {self.current_attempt + 1})"
                    )
                    continue
                else:
                    log.warning("Anti-truncation: Max attempts reached, ending stream")
                    self._clear_content()
                    yield b"data: [DONE]\n\n"
                    return

            except Exception as e:
                log.error(f"Anti-truncation error in attempt {self.current_attempt}: {str(e)}")
                if self.current_attempt >= self.max_attempts:
                    error_chunk = {
                        "error": {
                            "message": f"Anti-truncation failed: {str(e)}",
                            "type": "api_error",
                            "code": 500,
                        }
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                    yield b"data: [DONE]\n\n"
                    return

        # 所有尝试都失败
        log.error("Anti-truncation: All attempts failed")
        self._clear_content()
        yield b"data: [DONE]\n\n"

    def _build_current_payload(self) -> Dict[str, Any]:
        """构建当前请求的 payload"""
        if self.current_attempt == 1:
            return self.base_payload

        # 后续请求，添加续传指令
        continuation_payload = self.base_payload.copy()
        request_data = continuation_payload.get("request", {})

        contents = request_data.get("contents", [])
        new_contents = contents.copy()

        # 如果有收集到的内容，添加到对话中
        accumulated_text = self._get_collected_text()
        if accumulated_text:
            new_contents.append({"role": "model", "parts": [{"text": accumulated_text}]})

        # 预填充模式：直接用拼接内容作为末尾 model 预填充
        if self.enable_prefill_mode:
            log.debug("Anti-truncation: Using prefill continuation mode")
            request_data["contents"] = new_contents
            continuation_payload["request"] = request_data
            return continuation_payload

        # 构建续写指令
        content_summary = ""
        if accumulated_text:
            if len(accumulated_text) > 200:
                content_summary = (
                    f"\n\n前面你已经输出了约 {len(accumulated_text)} 个字符的内容，"
                    f'结尾是：\n"...{accumulated_text[-100:]}"'
                )
            else:
                content_summary = f'\n\n前面你已经输出的内容是：\n"{accumulated_text}"'

        detailed_continuation_prompt = f"{CONTINUATION_PROMPT}{content_summary}"

        continuation_message = {"role": "user", "parts": [{"text": detailed_continuation_prompt}]}
        new_contents.append(continuation_message)

        request_data["contents"] = new_contents
        continuation_payload["request"] = request_data

        return continuation_payload

    def _extract_text_from_chunk(self, data: Dict[str, Any]) -> str:
        """从 chunk 数据中提取普通文本内容"""
        if "response" in data:
            data = data["response"]

        text = ""
        for candidate in data.get("candidates", []):
            content = candidate.get("content", {})
            for part in content.get("parts", []):
                if isinstance(part, dict) and "text" in part:
                    text += part["text"]
        return text

    def _build_fallback_text_chunk(self, text: str) -> Optional[bytes]:
        """构建 fallback 文本 chunk（当没有合成工具调用时输出暂存的普通文本）"""
        if not text:
            return None

        chunk = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": text}]},
                    "index": 0,
                }
            ]
        }
        json_str = json.dumps(chunk, separators=(",", ":"), ensure_ascii=False)
        return f"data: {json_str}\n\n".encode("utf-8")

    async def _handle_non_streaming_response(self, response) -> bytes:
        """处理非流式响应"""
        while True:
            try:
                # 特殊处理：如果返回的是 StreamingResponse
                if isinstance(response, StreamingResponse):
                    log.error(
                        "Anti-truncation: Received StreamingResponse in non-streaming handler"
                    )
                    chunks = []
                    async for chunk in response.body_iterator:
                        chunks.append(chunk)
                    content = b"".join(chunks).decode() if chunks else ""
                elif hasattr(response, "body"):
                    content = (
                        response.body.decode()
                        if isinstance(response.body, bytes)
                        else response.body
                    )
                elif hasattr(response, "content"):
                    content = (
                        response.content.decode()
                        if isinstance(response.content, bytes)
                        else response.content
                    )
                else:
                    log.error(f"Anti-truncation: Unknown response type: {type(response)}")
                    content = str(response)

                if not content or not content.strip():
                    log.error("Anti-truncation: Received empty response content")
                    return json.dumps(
                        {
                            "error": {
                                "message": "Empty response from server",
                                "type": "api_error",
                                "code": 500,
                            }
                        }
                    ).encode()

                try:
                    response_data = json.loads(content)
                except json.JSONDecodeError as json_err:
                    log.error(
                        f"Anti-truncation: Failed to parse JSON response: {json_err}, "
                        f"content: {content[:200]}"
                    )
                    return content.encode() if isinstance(content, str) else content

                # 提取合成工具内容
                synthetic_content, real_calls, found_synthetic = (
                    extract_synthetic_content_from_response(response_data)
                )

                if found_synthetic or self.current_attempt >= self.max_attempts:
                    if found_synthetic:
                        # 替换响应中的合成工具调用为普通文本
                        modified_data = build_text_chunk_from_synthetic(
                            response_data, synthetic_content, real_calls
                        )
                        return json.dumps(modified_data, ensure_ascii=False).encode()
                    return content.encode() if isinstance(content, str) else content

                # 需要续传
                if synthetic_content:
                    self._append_content(synthetic_content)
                else:
                    # 尝试提取普通文本
                    text = self._extract_text_from_chunk(response_data)
                    if text:
                        self._append_content(text)

                log.info("Anti-truncation: Non-streaming response needs continuation")
                self.current_attempt += 1
                next_payload = self._build_current_payload()
                response = await self.original_request_func(next_payload)

            except Exception as e:
                log.error(f"Anti-truncation non-streaming error: {str(e)}")
                return json.dumps(
                    {
                        "error": {
                            "message": f"Anti-truncation failed: {str(e)}",
                            "type": "api_error",
                            "code": 500,
                        }
                    }
                ).encode()
