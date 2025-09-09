"""
Anti-Truncation Module - Ensures complete streaming output
保持一个流式请求内完整输出的反截断模块
"""
import json
import re
from typing import Dict, Any, AsyncGenerator, List, Tuple

from fastapi.responses import StreamingResponse

from log import log

# 反截断配置
DONE_MARKER = "[done]"
MAX_CONTINUATION_ATTEMPTS = 3
CONTINUATION_PROMPT = f"""请从刚才被截断的地方继续输出剩余的所有内容。

重要提醒：
1. 不要重复前面已经输出的内容
2. 直接继续输出，无需任何前言或解释
3. 当你完整完成所有内容输出后，必须在最后一行单独输出：{DONE_MARKER}
4. {DONE_MARKER} 标记表示你的回答已经完全结束，这是必需的结束标记

现在请继续输出："""

# 正则替换配置
REGEX_REPLACEMENTS: List[Tuple[str, str, str]] = [
    (
        "age_pattern",  # 替换规则名称
        r"(?:[1-9]|1[0-8])岁(?:的)?|(?:十一|十二|十三|十四|十五|十六|十七|十八|十|一|二|三|四|五|六|七|八|九)岁(?:的)?",  # 正则模式
        ""  # 替换文本
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

def apply_regex_replacements_to_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    对请求payload中的文本内容应用正则替换
    
    Args:
        payload: 请求payload
        
    Returns:
        应用替换后的payload
    """
    if not REGEX_REPLACEMENTS:
        return payload
    
    modified_payload = payload.copy()
    request_data = modified_payload.get("request", {})
    
    # 处理contents中的文本
    contents = request_data.get("contents", [])
    if contents:
        new_contents = []
        for content in contents:
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
        
        request_data["contents"] = new_contents
        modified_payload["request"] = request_data
        log.debug("Applied regex replacements to request contents")
    
    return modified_payload

def apply_anti_truncation(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    对请求payload应用反截断处理和正则替换
    在systemInstruction中添加提醒，要求模型在结束时输出DONE_MARKER标记
    
    Args:
        payload: 原始请求payload
        
    Returns:
        添加了反截断指令并应用了正则替换的payload
    """
    # 首先应用正则替换
    modified_payload = apply_regex_replacements_to_payload(payload)
    request_data = modified_payload.get("request", {})
    
    # 获取或创建systemInstruction
    system_instruction = request_data.get("systemInstruction", {})
    if not system_instruction:
        system_instruction = {"parts": []}
    elif "parts" not in system_instruction:
        system_instruction["parts"] = []
    
    # 添加反截断指令
    anti_truncation_instruction = {
        "text": f"""严格执行以下输出结束规则：

1. 当你完成完整回答时，必须在输出的最后单独一行输出：{DONE_MARKER}
2. {DONE_MARKER} 标记表示你的回答已经完全结束，这是必需的结束标记
3. 只有输出了 {DONE_MARKER} 标记，系统才认为你的回答是完整的
4. 如果你的回答被截断，系统会要求你继续输出剩余内容
5. 无论回答长短，都必须以 {DONE_MARKER} 标记结束

示例格式：
```
你的回答内容...
更多回答内容...
{DONE_MARKER}
```

注意：{DONE_MARKER} 必须单独占一行，前面不要有任何其他字符。

这个规则对于确保输出完整性极其重要，请严格遵守。"""
    }
    
    # 检查是否已经包含反截断指令
    has_done_instruction = any(
        part.get("text", "").find(DONE_MARKER) != -1 
        for part in system_instruction["parts"] 
        if isinstance(part, dict)
    )
    
    if not has_done_instruction:
        system_instruction["parts"].append(anti_truncation_instruction)
        request_data["systemInstruction"] = system_instruction
        modified_payload["request"] = request_data
        
        log.debug("Applied anti-truncation instruction to request")
    
    return modified_payload

class AntiTruncationStreamProcessor:
    """反截断流式处理器"""
    
    def __init__(self, 
                 original_request_func,
                 payload: Dict[str, Any],
                 max_attempts: int = MAX_CONTINUATION_ATTEMPTS):
        self.original_request_func = original_request_func
        self.base_payload = payload.copy()
        self.max_attempts = max_attempts
        self.collected_content = []  # 使用列表避免字符串重复拼接
        self.current_attempt = 0
        
    async def process_stream(self) -> AsyncGenerator[bytes, None]:
        """处理流式响应，检测并处理截断"""
        
        while self.current_attempt < self.max_attempts:
            self.current_attempt += 1
            
            # 构建当前请求payload
            current_payload = self._build_current_payload()
            
            log.debug(f"Anti-truncation attempt {self.current_attempt}/{self.max_attempts}")
            
            # 发送请求
            try:
                response = await self.original_request_func(current_payload)
                
                if not isinstance(response, StreamingResponse):
                    # 非流式响应，直接处理
                    yield await self._handle_non_streaming_response(response)
                    return
                
                # 处理流式响应
                chunk_content = ""
                found_done_marker = False
                
                async for chunk in response.body_iterator:
                    if not chunk:
                        yield chunk
                        continue
                    
                    # 处理不同数据类型的startswith问题
                    if isinstance(chunk, bytes):
                        if not chunk.startswith(b'data: '):
                            yield chunk
                            continue
                        payload_data = chunk[len(b'data: '):]
                    else:
                        chunk_str = str(chunk)
                        if not chunk_str.startswith('data: '):
                            yield chunk
                            continue
                        payload_data = chunk_str[len('data: '):].encode()
                    
                    # 解析chunk内容
                    
                    if payload_data.strip() == b'[DONE]':
                        # 检查是否找到了done标记
                        if found_done_marker:
                            log.info("Anti-truncation: Found [done] marker, output complete")
                            yield chunk
                            return
                        else:
                            log.warning("Anti-truncation: Stream ended without [done] marker")
                            # 不发送[DONE]，准备继续
                            break
                    
                    try:
                        data = json.loads(payload_data.decode())
                        content = self._extract_content_from_chunk(data)
                        
                        if content:
                            chunk_content += content
                            
                            # 检查是否包含done标记
                            if self._check_done_marker_in_chunk_content(content):
                                found_done_marker = True
                                log.info("Anti-truncation: Found [done] marker in chunk")
                        
                        # 清理chunk中的[done]标记后再发送
                        cleaned_chunk = self._remove_done_marker_from_chunk(chunk, data)
                        yield cleaned_chunk
                        
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        yield chunk
                        continue
                
                # 更新收集的内容 - 使用列表避免字符串重复拼接
                if chunk_content:
                    self.collected_content.append(chunk_content)
                
                # 如果找到了done标记，结束
                if found_done_marker:
                    # 立即清理内容释放内存
                    self.collected_content.clear()
                    yield b'data: [DONE]\n\n'
                    return
                
                # 只有在单个chunk中没有找到done标记时，才检查累积内容（防止done标记跨chunk出现）
                if not found_done_marker:
                    accumulated_text = ''.join(self.collected_content) if self.collected_content else ""
                    if self._check_done_marker_in_text(accumulated_text):
                        log.info("Anti-truncation: Found [done] marker in accumulated content")
                        # 立即清理内容释放内存
                        self.collected_content.clear()
                        yield b'data: [DONE]\n\n'
                        return
                
                # 如果没找到done标记且不是最后一次尝试，准备续传
                if self.current_attempt < self.max_attempts:
                    total_length = sum(len(chunk) for chunk in self.collected_content) if self.collected_content else 0
                    log.info(f"Anti-truncation: No [done] marker found in output (length: {total_length}), preparing continuation (attempt {self.current_attempt + 1})")
                    if self.collected_content and total_length > 100:
                        last_chunk = self.collected_content[-1] if self.collected_content else ""
                        log.debug(f"Anti-truncation: Current collected content ends with: {'...' + last_chunk[-100:]}")
                    # 在下一次循环中会继续
                    continue
                else:
                    # 最后一次尝试，直接结束
                    log.warning("Anti-truncation: Max attempts reached, ending stream")
                    # 立即清理内容释放内存
                    self.collected_content.clear()
                    yield b'data: [DONE]\n\n'
                    return
                
            except Exception as e:
                log.error(f"Anti-truncation error in attempt {self.current_attempt}: {str(e)}")
                if self.current_attempt >= self.max_attempts:
                    # 发送错误chunk
                    error_chunk = {
                        "error": {
                            "message": f"Anti-truncation failed: {str(e)}",
                            "type": "api_error",
                            "code": 500
                        }
                    }
                    yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                    yield b'data: [DONE]\n\n'
                    return
                # 否则继续下一次尝试
                
        # 如果所有尝试都失败了
        log.error("Anti-truncation: All attempts failed")
        # 确保清理内容释放内存
        self.collected_content.clear()
        yield b'data: [DONE]\n\n'
    
    def _build_current_payload(self) -> Dict[str, Any]:
        """构建当前请求的payload"""
        if self.current_attempt == 1:
            # 第一次请求，使用原始payload（已经包含反截断指令）
            return self.base_payload
        
        # 后续请求，添加续传指令
        continuation_payload = self.base_payload.copy()
        request_data = continuation_payload.get("request", {})
        
        # 获取原始对话内容
        contents = request_data.get("contents", [])
        new_contents = contents.copy()
        
        # 如果有收集到的内容，添加到对话中
        if self.collected_content:
            # 拼接收集的内容并添加模型的回复
            accumulated_text = ''.join(self.collected_content)
            new_contents.append({
                "role": "model",
                "parts": [{"text": accumulated_text}]
            })
        
        # 构建具体的续写指令，包含前面的内容摘要
        content_summary = ""
        if self.collected_content:
            accumulated_text = ''.join(self.collected_content)
            if len(accumulated_text) > 200:
                content_summary = f"\n\n前面你已经输出了约 {len(accumulated_text)} 个字符的内容，结尾是：\n\"...{accumulated_text[-100:]}\""
            else:
                content_summary = f"\n\n前面你已经输出的内容是：\n\"{accumulated_text}\""
        
        detailed_continuation_prompt = f"""{CONTINUATION_PROMPT}{content_summary}"""
        
        # 添加继续指令
        continuation_message = {
            "role": "user", 
            "parts": [{"text": detailed_continuation_prompt}]
        }
        new_contents.append(continuation_message)
        
        request_data["contents"] = new_contents
        continuation_payload["request"] = request_data
        
        return continuation_payload
    
    def _extract_content_from_chunk(self, data: Dict[str, Any]) -> str:
        """从chunk数据中提取文本内容"""
        content = ""
        
        # 处理Gemini格式
        if "candidates" in data:
            for candidate in data["candidates"]:
                if "content" in candidate:
                    parts = candidate["content"].get("parts", [])
                    for part in parts:
                        if "text" in part:
                            content += part["text"]
        
        # 处理OpenAI格式
        elif "choices" in data:
            for choice in data["choices"]:
                if "delta" in choice and "content" in choice["delta"]:
                    content += choice["delta"]["content"]
                elif "message" in choice and "content" in choice["message"]:
                    content += choice["message"]["content"]
        
        return content
    
    async def _handle_non_streaming_response(self, response) -> bytes:
        """处理非流式响应"""
        try:
            if hasattr(response, 'body'):
                content = response.body.decode() if isinstance(response.body, bytes) else response.body
            elif hasattr(response, 'content'):
                content = response.content.decode() if isinstance(response.content, bytes) else response.content
            else:
                content = str(response)
            
            response_data = json.loads(content)
            
            # 检查是否包含done标记
            text_content = self._extract_content_from_response(response_data)
            has_done_marker = self._check_done_marker_in_text(text_content)
            
            if not has_done_marker and self.current_attempt < self.max_attempts:
                log.info("Anti-truncation: Non-streaming response needs continuation")
                if text_content:
                    self.collected_content.append(text_content)
                # 递归处理续传
                return await self._handle_non_streaming_response(
                    await self.original_request_func(self._build_current_payload())
                )
            
            return content.encode()
            
        except Exception as e:
            log.error(f"Anti-truncation non-streaming error: {str(e)}")
            return json.dumps({
                "error": {
                    "message": f"Anti-truncation failed: {str(e)}",
                    "type": "api_error",
                    "code": 500
                }
            }).encode()
    
    def _check_done_marker_in_text(self, text: str) -> bool:
        """检测文本中是否包含DONE_MARKER（只检测指定标记）"""
        if not text:
            return False

        # 只要文本中出现DONE_MARKER即可
        return DONE_MARKER in text
    
    def _check_done_marker_in_chunk_content(self, content: str) -> bool:
        """检查单个chunk内容中是否包含done标记"""
        return self._check_done_marker_in_text(content)
    
    def _extract_content_from_response(self, data: Dict[str, Any]) -> str:
        """从响应数据中提取文本内容"""
        content = ""
        
        # 处理Gemini格式
        if "candidates" in data:
            for candidate in data["candidates"]:
                if "content" in candidate:
                    parts = candidate["content"].get("parts", [])
                    for part in parts:
                        if "text" in part:
                            content += part["text"]
        
        # 处理OpenAI格式
        elif "choices" in data:
            for choice in data["choices"]:
                if "message" in choice and "content" in choice["message"]:
                    content += choice["message"]["content"]
        
        return content
    
    def _remove_done_marker_from_chunk(self, chunk: bytes, data: Dict[str, Any]) -> bytes:
        """使用正则表达式从chunk中移除[done]标记"""
        try:
            # 首先检查是否真的包含[done]标记，如果没有则直接返回原始chunk
            chunk_text = chunk.decode('utf-8', errors='ignore') if isinstance(chunk, bytes) else str(chunk)
            if '[done]' not in chunk_text.lower():
                return chunk  # 没有[done]标记，直接返回原始chunk
            
            # 编译正则表达式，匹配[done]标记（忽略大小写，包括可能的空白字符）
            done_pattern = re.compile(r'\s*\[done\]\s*', re.IGNORECASE)
            
            # 处理Gemini格式
            if "candidates" in data:
                modified_data = data.copy()
                modified_data["candidates"] = []
                
                for i, candidate in enumerate(data["candidates"]):
                    modified_candidate = candidate.copy()
                    # 只在最后一个candidate中清理[done]标记
                    is_last_candidate = (i == len(data["candidates"]) - 1)
                    
                    if "content" in candidate:
                        modified_content = candidate["content"].copy()
                        if "parts" in modified_content:
                            modified_parts = []
                            for part in modified_content["parts"]:
                                if "text" in part and isinstance(part["text"], str):
                                    modified_part = part.copy()
                                    # 只在最后一个candidate中清理[done]标记
                                    if is_last_candidate:
                                        modified_part["text"] = done_pattern.sub('', part["text"])
                                    modified_parts.append(modified_part)
                                else:
                                    modified_parts.append(part)
                            modified_content["parts"] = modified_parts
                        modified_candidate["content"] = modified_content
                    modified_data["candidates"].append(modified_candidate)
                
                # 重新编码为chunk格式，保持原始的换行符
                if isinstance(chunk, bytes):
                    prefix = b'data: '
                    suffix = b'\n\n'  # 确保有正确的换行符
                    json_data = json.dumps(modified_data, separators=(',',':'), ensure_ascii=False).encode('utf-8')
                    return prefix + json_data + suffix
                else:
                    return f"data: {json.dumps(modified_data, separators=(',',':'), ensure_ascii=False)}\n\n"
            
            # 处理OpenAI格式
            elif "choices" in data:
                modified_data = data.copy()
                modified_data["choices"] = []
                
                for choice in data["choices"]:
                    modified_choice = choice.copy()
                    if "delta" in choice and "content" in choice["delta"]:
                        modified_delta = choice["delta"].copy()
                        modified_delta["content"] = done_pattern.sub('', choice["delta"]["content"])
                        modified_choice["delta"] = modified_delta
                    elif "message" in choice and "content" in choice["message"]:
                        modified_message = choice["message"].copy()
                        modified_message["content"] = done_pattern.sub('', choice["message"]["content"])
                        modified_choice["message"] = modified_message
                    modified_data["choices"].append(modified_choice)
                
                # 重新编码为chunk格式，保持原始的换行符
                if isinstance(chunk, bytes):
                    prefix = b'data: '
                    suffix = b'\n\n'  # 确保有正确的换行符
                    json_data = json.dumps(modified_data, separators=(',',':'), ensure_ascii=False).encode('utf-8')
                    return prefix + json_data + suffix
                else:
                    return f"data: {json.dumps(modified_data, separators=(',',':'), ensure_ascii=False)}\n\n"
            
            # 如果没有找到支持的格式，返回原始chunk
            return chunk
            
        except Exception as e:
            log.warning(f"Failed to remove [done] marker from chunk: {str(e)}")
            return chunk

async def apply_anti_truncation_to_stream(
    request_func,
    payload: Dict[str, Any],
    max_attempts: int = MAX_CONTINUATION_ATTEMPTS
) -> StreamingResponse:
    """
    对流式请求应用反截断处理
    
    Args:
        request_func: 原始请求函数
        payload: 请求payload
        max_attempts: 最大续传尝试次数
        
    Returns:
        处理后的StreamingResponse
    """
    
    # 首先对payload应用反截断指令
    anti_truncation_payload = apply_anti_truncation(payload)
    
    # 创建反截断处理器
    processor = AntiTruncationStreamProcessor(
        lambda p: request_func(p),
        anti_truncation_payload,
        max_attempts
    )
    
    # 返回包装后的流式响应
    return StreamingResponse(
        processor.process_stream(),
        media_type="text/event-stream"
    )

def is_anti_truncation_enabled(request_data: Dict[str, Any]) -> bool:
    """
    检查请求是否启用了反截断功能
    
    Args:
        request_data: 请求数据
        
    Returns:
        是否启用反截断
    """
    return request_data.get("enable_anti_truncation", False)