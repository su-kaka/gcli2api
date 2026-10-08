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
    # 拷贝 request 层，避免注入原地污染调用方的原始请求体
    # （shallow copy 下 request_data 与调用方共享同一个 dict）
    request_data = dict(modified_payload.get("request") or {})

    # 1. 注入合成工具到 tools 列表
    tools = list(request_data.get("tools") or [])
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

    # 2. 确保 toolConfig.functionCallingConfig.mode 允许工具调用
    tool_config = request_data.get("toolConfig") or {}
    func_config = tool_config.get("functionCallingConfig") or {}
    current_mode = func_config.get("mode", "")
    # 如果当前模式是 NONE（禁止工具调用），需要改为 AUTO
    if current_mode == "NONE":
        func_config["mode"] = "AUTO"
        tool_config["functionCallingConfig"] = func_config
        request_data["toolConfig"] = tool_config
    elif not current_mode:
        # 未设置时默认 AUTO
        func_config["mode"] = "AUTO"
        tool_config["functionCallingConfig"] = func_config
        request_data["toolConfig"] = tool_config

    # 3. 注入控制指令到 systemInstruction
    system_instruction = dict(request_data.get("systemInstruction") or {})
    if "parts" not in system_instruction:
        system_instruction["parts"] = []
    else:
        # parts 列表可能来自调用方的原始对象，同样拷贝避免原地追加
        system_instruction["parts"] = list(system_instruction["parts"])

    has_control_instruction = any(
        isinstance(part, dict) and SYNTHETIC_TOOL_NAME in part.get("text", "")
        for part in system_instruction["parts"]
    )
    if not has_control_instruction:
        system_instruction["parts"].append({"text": CONTROL_INSTRUCTION})
        request_data["systemInstruction"] = system_instruction

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


def _deep_copy_anti_truncation_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """payload 的浅层结构深拷贝（request / contents / messages / tools / toolConfig 层）。

    AntiTruncationStreamProcessor 会在续传时向 contents / messages 追加
    assistant 历史与续传指令，apply_anti_truncation 会向 tools /
    systemInstruction 注入内容。若这些容器与调用方共享引用，注入与追加
    会原地污染原始请求体，导致同一请求重试或多次续传时内容重复叠加。
    这里用 json 往返做整体深拷贝，payload 均为可 JSON 序列化的请求体，
    代价可接受。
    """
    try:
        return json.loads(json.dumps(payload, ensure_ascii=False))
    except (TypeError, ValueError):
        return payload.copy()


class AntiTruncationStreamProcessor:
    """反截断流式处理器 - 基于合成工具调用

    模型未走合成工具、直接输出普通文本时，文本 chunk 实时透传给客户端
    （不再整流缓冲等待），仅在流末统一补发 finishReason 收尾，尽量避免等待。
    """

    def __init__(
        self,
        original_request_func,
        payload: Dict[str, Any],
        max_attempts: int = 3,
        enable_prefill_mode: bool = False,
    ):
        self.original_request_func = original_request_func
        # 深拷贝 request/contents/messages：本类在续传时会向其中追加内容，
        # 拷贝隔离后调用方的原始请求体（可能在重试等场景被复用）不受污染
        self.base_payload = _deep_copy_anti_truncation_payload(payload)
        self.max_attempts = max_attempts
        self.enable_prefill_mode = enable_prefill_mode
        self.collected_content = io.StringIO()
        self.current_attempt = 0
        # 整个请求期间是否有文本实时透传给客户端
        # （异常兜底时判断 salvaged 文本是否已发过，避免重发）
        self.forwarded_any_text = False

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

            # 每轮流内状态：初始化放在 try 之外，流中断的异常路径需要访问
            found_synthetic = False
            has_real_tool_calls = False
            # 收集已实时透传给客户端的普通文本（供续传历史 / 异常抢救使用）
            side_buffer = io.StringIO()
            last_finish_reason: Optional[str] = None
            # finishReason 是否已透传给客户端（避免收尾时重复补发）
            finish_reason_forwarded = False

            try:
                response = await self.original_request_func(current_payload)

                if not isinstance(response, StreamingResponse):
                    # 非流式响应（上游错误 JSON 等），包装为 SSE 格式输出
                    # （聚合层只识别 data: 行，裸 JSON 会导致错误信息丢失）
                    raw = await self._handle_non_streaming_response(response)
                    if isinstance(raw, bytes):
                        yield f"data: {raw.decode('utf-8', errors='ignore').strip()}\n\n".encode("utf-8")
                    else:
                        yield f"data: {str(raw).strip()}\n\n".encode("utf-8")
                    yield b"data: [DONE]\n\n"
                    return

                async for line in response.body_iterator:
                    if not line:
                        yield line
                        continue

                    # 处理上游生成器 yield 出 Response 对象的情况（错误响应）
                    from fastapi import Response as FastAPIResponse

                    if isinstance(line, FastAPIResponse):
                        print(
                            f"Anti-truncation: Received Response object from stream "
                            f"(status={line.status_code}), treating as error",
                            flush=True,
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
                                print(
                                    "Anti-truncation: Stream complete with synthetic tool call",
                                    flush=True,
                                )
                                side_buffer.close()
                                self._clear_content()
                                if not finish_reason_forwarded:
                                    # 流未自然收尾：补发 finishReason 让客户端正常结束
                                    yield self._build_finish_reason_chunk()
                                yield line
                                return
                            else:
                                print(
                                    "Anti-truncation: Stream ended without synthetic tool call",
                                    flush=True,
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
                            # 实时透传模式下 side_buffer 中的文本已发给客户端，
                            # 若模型先输出普通文本又转用合成工具，则视为内容冲突，
                            # 丢弃已透传的普通文本并输出告警（客户端会看到合成工具内容）
                            if side_buffer.getvalue():
                                print(
                                    "Anti-truncation: Model output plain text before "
                                    "synthetic tool call, synthetic content wins "
                                    "(forwarded text conflicts and is dropped)",
                                    flush=True,
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

                            # 替换后的 chunk 保留原 finishReason（若有），
                            # 已随上面的 yield 透传，标记避免收尾重复补发
                            chunk_finish = self._get_finish_reason(data)
                            if chunk_finish:
                                last_finish_reason = chunk_finish
                                finish_reason_forwarded = True

                        elif real_calls:
                            # 真实工具调用，原样透传
                            has_real_tool_calls = True
                            chunk_finish = self._get_finish_reason(data)
                            if chunk_finish:
                                last_finish_reason = chunk_finish
                                # 原样透传的 chunk 自带 finishReason
                                finish_reason_forwarded = True
                            yield line

                        else:
                            # 普通文本 chunk
                            if found_synthetic:
                                # 已有合成工具调用：带 finishReason 的控制 chunk 透传
                                # （Gemini 客户端靠 finishReason 判断流正常结束），
                                # 但需清空其中的 text 避免重复内容；纯 text 内容 chunk 丢弃（防拼接）
                                if self._has_finish_reason(data):
                                    last_finish_reason = self._get_finish_reason(data)
                                    finish_reason_forwarded = True
                                    stripped = self._strip_text_parts(data)
                                    json_str = json.dumps(
                                        stripped, separators=(",", ":"), ensure_ascii=False
                                    )
                                    yield f"data: {json_str}\n\n".encode("utf-8")
                                continue
                            else:
                                # 模型未走合成工具、直接输出普通文本：
                                # 实时透传给客户端（避免整流缓冲等待）
                                text = self._extract_text_from_chunk(data)
                                if text:
                                    side_buffer.write(text)
                                    self.forwarded_any_text = True
                                    if self._has_finish_reason(data):
                                        # 文本与 finishReason 同 chunk：透传剥离
                                        # finishReason 后的文本，收尾信号由流结束
                                        # 逻辑统一决定补发
                                        stripped = self._strip_finish_reason(data)
                                        json_str = json.dumps(
                                            stripped, separators=(",", ":"), ensure_ascii=False
                                        )
                                        yield f"data: {json_str}\n\n".encode("utf-8")
                                    else:
                                        # 纯文本内容 chunk：原样透传
                                        yield line
                                chunk_finish = self._get_finish_reason(data)
                                if chunk_finish:
                                    last_finish_reason = chunk_finish
                                    # 收尾控制信号先暂扣，流结束后决定续传或补发
                                    continue
                                if not text:
                                    # 无文本无工具调用的空 chunk：透传（usage 等）
                                    yield line
                                continue

                    else:
                        # 非 data: 开头的行，直接传递
                        yield line

                # 流结束（break 或正常结束）
                side_text = side_buffer.getvalue()
                side_buffer.close()

                if found_synthetic:
                    # 成功收到合成工具调用
                    print("Anti-truncation: Found synthetic tool call, output complete", flush=True)
                    self._clear_content()
                    if not finish_reason_forwarded:
                        # 流未自然收尾（无 finishReason chunk）：补发收尾信号
                        yield self._build_finish_reason_chunk()
                    yield b"data: [DONE]\n\n"
                    return

                # 模型自然结束（STOP）且未调用合成工具：视为完整回答，不再续传
                # （续传会让模型重发一遍已有内容，浪费上游请求且可能产生重复文本）
                if last_finish_reason == "STOP":
                    print(
                        f"Anti-truncation: Stream ended with STOP without synthetic tool call "
                        f"(text length: {len(side_text)}), treating as complete",
                        flush=True)
                    # 文本已实时透传，仅需补发被暂扣的 finishReason 收尾 chunk
                    if not finish_reason_forwarded:
                        yield self._build_finish_reason_chunk(last_finish_reason)
                    self._clear_content()
                    yield b"data: [DONE]\n\n"
                    return

                # 未收到合成工具调用
                if side_text:
                    # 有普通文本：已实时透传给客户端，无需再补发
                    self._append_content(side_text)

                # 触发续传
                if self.current_attempt < self.max_attempts:
                    accumulated_text = self._get_collected_text()
                    total_length = len(accumulated_text)
                    print(
                        f"Anti-truncation: No synthetic tool call in output "
                        f"(length: {total_length}), preparing continuation "
                        f"(attempt {self.current_attempt + 1})",
                        flush=True,
                    )
                    continue
                else:
                    print("Anti-truncation: Max attempts reached, ending stream", flush=True)
                    # 达到重试上限：补发 finishReason 收尾，让客户端正常结束
                    if side_text and not finish_reason_forwarded:
                        yield self._build_finish_reason_chunk(last_finish_reason or "STOP")
                    self._clear_content()
                    yield b"data: [DONE]\n\n"
                    return

            except Exception as e:
                print(f"Anti-truncation error in attempt {self.current_attempt}: {str(e)}", flush=True)

                # 异常中断：把本轮已收到的文本并入收集器，作为续传历史
                # （否则续传请求不知道模型已输出到哪，可能从头重复输出）
                try:
                    interrupted_text = side_buffer.getvalue()
                except ValueError:
                    # 正常路径已 close 后又抛错：文本此前已并入收集器
                    interrupted_text = ""
                try:
                    side_buffer.close()
                except ValueError:
                    pass
                if interrupted_text:
                    print(
                        f"Anti-truncation: Stream interrupted, salvaging "
                        f"{len(interrupted_text)} chars of buffered text into continuation history",
                        flush=True,
                    )
                    self._append_content(interrupted_text)

                if self.current_attempt >= self.max_attempts:
                    # 重试额度用尽：已实时透传的文本不重复输出，仅补收尾信号
                    salvaged = self._get_collected_text()
                    self._clear_content()
                    if salvaged and self.forwarded_any_text:
                        print(
                            f"Anti-truncation: Max attempts reached after error, "
                            f"salvaged text already forwarded (length: {len(salvaged)})",
                            flush=True,
                        )
                        if not finish_reason_forwarded:
                            yield self._build_finish_reason_chunk(last_finish_reason or "STOP")
                    elif salvaged:
                        # 未透传过的内容（如合成工具收集的部分）：作为 fallback 输出
                        print(
                            f"Anti-truncation: Max attempts reached after error, "
                            f"yielding salvaged text (length: {len(salvaged)})",
                            flush=True,
                        )
                        fallback_chunk = self._build_fallback_text_chunk(salvaged)
                        if fallback_chunk:
                            yield fallback_chunk
                    else:
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
                # 还有重试额度：继续下一轮续传
                continue

        # 所有尝试都失败
        print("Anti-truncation: All attempts failed", flush=True)
        self._clear_content()
        yield b"data: [DONE]\n\n"

    def _build_current_payload(self) -> Dict[str, Any]:
        """构建当前请求的 payload"""
        if self.current_attempt == 1:
            return self.base_payload

        # 后续请求，添加续传指令
        # 深拷贝 request 层与 contents：续传时追加的 model 历史 / user 续传指令
        # 不应写回 self.base_payload，否则下一次续传会在旧续传内容之上重复追加，
        # 导致请求体逐轮膨胀（history 里出现多份 assistant 全文 + 多条续传指令）
        continuation_payload = dict(self.base_payload)
        request_data = dict(continuation_payload.get("request") or {})
        new_contents = [
            dict(c) if isinstance(c, dict) else c
            for c in (request_data.get("contents") or [])
        ]

        # 如果有收集到的内容，添加到对话中
        accumulated_text = self._get_collected_text()
        if accumulated_text:
            new_contents.append({"role": "model", "parts": [{"text": accumulated_text}]})

        # 预填充模式：直接用拼接内容作为末尾 model 预填充
        if self.enable_prefill_mode:
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

    @staticmethod
    def _has_finish_reason(data: Dict[str, Any]) -> bool:
        """判断 chunk 是否携带 finishReason（控制信号 chunk）。"""
        if "response" in data:
            data = data["response"]
        for candidate in data.get("candidates", []):
            if candidate.get("finishReason"):
                return True
        return False

    @staticmethod
    def _get_finish_reason(data: Dict[str, Any]) -> Optional[str]:
        """提取 chunk 中的 finishReason 值（无则 None）。"""
        if "response" in data:
            data = data["response"]
        for candidate in data.get("candidates", []):
            reason = candidate.get("finishReason")
            if reason:
                return reason
        return None

    @staticmethod
    def _strip_text_parts(data: Dict[str, Any]) -> Dict[str, Any]:
        """移除 chunk 中所有 text part 的文本内容（保留 finishReason 等控制字段）。

        用于 found_synthetic 后透传 finishReason chunk 时，避免重复输出文本。
        """
        has_wrapper = "response" in data
        inner = data["response"] if has_wrapper else data

        modified_inner = inner.copy()
        modified_candidates = []
        for candidate in inner.get("candidates", []):
            modified_candidate = candidate.copy()
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            # 过滤掉所有 text part（保留 functionCall 等其他 part，虽然此处通常为空）
            new_parts = [
                part for part in parts
                if not (isinstance(part, dict) and "text" in part)
            ]
            modified_content = content.copy()
            modified_content["parts"] = new_parts
            modified_candidate["content"] = modified_content
            modified_candidates.append(modified_candidate)

        modified_inner["candidates"] = modified_candidates
        if has_wrapper:
            result = data.copy()
            result["response"] = modified_inner
            return result
        return modified_inner

    @staticmethod
    def _strip_finish_reason(data: Dict[str, Any]) -> Dict[str, Any]:
        """移除 chunk 中所有 candidate 的 finishReason（保留 text part 供透传）。"""
        has_wrapper = "response" in data
        inner = data["response"] if has_wrapper else data

        modified_inner = inner.copy()
        modified_candidates = []
        for candidate in inner.get("candidates", []):
            if not isinstance(candidate, dict) or "finishReason" not in candidate:
                modified_candidates.append(candidate)
                continue
            modified_candidate = candidate.copy()
            modified_candidate.pop("finishReason", None)
            modified_candidates.append(modified_candidate)

        modified_inner["candidates"] = modified_candidates
        if has_wrapper:
            result = data.copy()
            result["response"] = modified_inner
            return result
        return modified_inner

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

    def _build_finish_reason_chunk(self, finish_reason: str = "STOP") -> bytes:
        """构建只带 finishReason 的收尾 chunk（文本已被剥离，避免重复内容）"""
        chunk = {
            "candidates": [
                {
                    "content": {"role": "model", "parts": []},
                    "finishReason": finish_reason,
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
                    print(
                        "Anti-truncation: Received StreamingResponse in non-streaming handler",
                        flush=True,
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
                    print(f"Anti-truncation: Unknown response type: {type(response)}", flush=True)
                    content = str(response)

                if not content or not content.strip():
                    print("Anti-truncation: Received empty response content", flush=True)
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
                    print(
                        f"Anti-truncation: Failed to parse JSON response: {json_err}, "
                        f"content: {content[:200]}",
                        flush=True,
                    )
                    return content.encode() if isinstance(content, str) else content

                # 上游错误响应：直接透传错误，不再续传
                if isinstance(response_data, dict) and "error" in response_data:
                    print(
                        f"Anti-truncation: Upstream error response "
                        f"(status={getattr(response, 'status_code', 'unknown')})",
                        flush=True,
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

                print("Anti-truncation: Non-streaming response needs continuation", flush=True)
                self.current_attempt += 1
                next_payload = self._build_current_payload()
                response = await self.original_request_func(next_payload)

            except Exception as e:
                print(f"Anti-truncation non-streaming error: {str(e)}", flush=True)
                return json.dumps(
                    {
                        "error": {
                            "message": f"Anti-truncation failed: {str(e)}",
                            "type": "api_error",
                            "code": 500,
                        }
                    }
                ).encode()


