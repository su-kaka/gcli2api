from src.i18n import ts
"""
Anti-Truncation Module - Ensures complete streaming output
{ts("id_2241")}
"""

import io
import json
import re
from typing import Any, AsyncGenerator, Dict, List, Tuple

from fastapi.responses import StreamingResponse

from log import log

# {ts("id_2242")}
DONE_MARKER = "[done]"
CONTINUATION_PROMPT = f"""{ts("id_2243")}

{ts("id_2244")}
1. {ts("id_2245")}
2. {ts("id_2246")}
3. {ts("id_2247")}{DONE_MARKER}
4. {DONE_MARKER} {ts("id_2248")}

{ts("id_2249")}"""

# {ts("id_2250")}
REGEX_REPLACEMENTS: List[Tuple[str, str, str]] = [
    (
        "age_pattern",  # {ts("id_2251")}
        rf"(?:[1-9]|1[0-8]){ts("id_2266")}(?:{ts("id_61f")})?|(?:{ts("id_2257")}|{ts("id_2254f")}|{ts("id_2258")}|{ts("id_2260f")}|{ts("id_2253")}|{ts("id_2255f")}|{ts("id_2256")}|{ts("id_2259f")}|{ts("id_2264")}|{ts("id_2265f")}|{ts("id_2270")}|{ts("id_2267f")}|{ts("id_2262")}|{ts("id_2268f")}|{ts("id_2269")}|{ts("id_2271f")}|{ts("id_2261")}|{ts("id_2263f")}){ts("id_2266")}(?:{ts("id_61")})?",  # {ts("id_2252")}
        "",  # {ts("id_2272")}
    ),
    # {ts("id_2273")}
    # ("rule_name", r"pattern", "replacement"),
]


def apply_regex_replacements(text: str) -> str:
    """
    {ts("id_2274")}

    Args:
        text: {ts("id_2275")}

    Returns:
        {ts("id_2276")}
    """
    if not text:
        return text

    processed_text = text
    replacement_count = 0

    for rule_name, pattern, replacement in REGEX_REPLACEMENTS:
        try:
            # {ts("id_2277")}IGNORECASE{ts("id_2278")}
            regex = re.compile(pattern, re.IGNORECASE)

            # {ts("id_2279")}
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
    {ts("id_2281")}payload{ts("id_2280")}

    Args:
        payload: {ts("id_2282")}payload

    Returns:
        {ts("id_2283")}payload
    """
    if not REGEX_REPLACEMENTS:
        return payload

    modified_payload = payload.copy()
    request_data = modified_payload.get("request", {})

    # {ts("id_590")}contents{ts("id_2284")}
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
    {ts("id_2281")}payload{ts("id_2285")}
    {ts(f"id_429")}systemInstruction{ts("id_2286")}DONE_MARKER{ts("id_2287")}

    Args:
        payload: {ts("id_1602")}payload

    Returns:
        {ts("id_2288")}payload
    """
    # {ts("id_2289")}
    modified_payload = apply_regex_replacements_to_payload(payload)
    request_data = modified_payload.get("request", {})

    # {ts("id_2290")}systemInstruction
    system_instruction = request_data.get("systemInstruction", {})
    if not system_instruction:
        system_instruction = {"parts": []}
    elif "parts" not in system_instruction:
        system_instruction["parts"] = []

    # {ts("id_2291")}
    anti_truncation_instruction = {
        "text": f"""{ts("id_2292")}

1. {ts("id_2293")}{DONE_MARKER}
2. {DONE_MARKER} {ts("id_2248")}
3. {ts("id_2295")} {DONE_MARKER} {ts("id_2294")}
4. {ts("id_2296")}
5. {ts("id_2297")} {DONE_MARKER} {ts("id_2298")}

{ts("id_2299")}
```
{ts("id_2300")}...
{ts("id_2301")}...
{DONE_MARKER}
```

{ts("id_1288")}{DONE_MARKER} {ts("id_2302")}

{ts("id_2303")}"""
    }

    # {ts("id_2304")}
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
    f"""{ts("id_2305")}"""

    def __init__(
        self,
        original_request_func,
        payload: Dict[str, Any],
        max_attempts: int = 3,
    ):
        self.original_request_func = original_request_func
        self.base_payload = payload.copy()
        self.max_attempts = max_attempts
        # {ts("id_463")} StringIO {ts("id_2306")}
        self.collected_content = io.StringIO()
        self.current_attempt = 0

    def _get_collected_text(self) -> str:
        f"""{ts("id_2307")}"""
        return self.collected_content.getvalue()

    def _append_content(self, content: str):
        f"""{ts("id_2308")}"""
        if content:
            self.collected_content.write(content)

    def _clear_content(self):
        f"""{ts("id_2309")}"""
        self.collected_content.close()
        self.collected_content = io.StringIO()

    async def process_stream(self) -> AsyncGenerator[bytes, None]:
        f"""{ts("id_2310")}"""

        while self.current_attempt < self.max_attempts:
            self.current_attempt += 1

            # {ts("id_2311")}payload
            current_payload = self._build_current_payload()

            log.debug(f"Anti-truncation attempt {self.current_attempt}/{self.max_attempts}")

            # {ts("id_2312")}
            try:
                response = await self.original_request_func(current_payload)

                if not isinstance(response, StreamingResponse):
                    # {ts("id_2313")}
                    yield await self._handle_non_streaming_response(response)
                    return

                # {ts("id_2314")}
                chunk_buffer = io.StringIO()  # {ts("id_463")} StringIO {ts("id_2315")}chunk
                found_done_marker = False

                async for line in response.body_iterator:
                    if not line:
                        yield line
                        continue

                    # {ts("id_590")} bytes {ts("id_2316")}
                    if isinstance(line, bytes):
                        # {ts("id_2318")} bytes {ts("id_2317")}
                        line_str = line.decode('utf-8', errors='ignore').strip()
                    else:
                        line_str = str(line).strip()

                    # {ts("id_2319")}
                    if not line_str:
                        yield line
                        continue

                    # {ts("id_590")} SSE {ts("id_2320")}
                    if line_str.startswith("data: "):
                        payload_str = line_str[6:]  # {ts("id_1644")} "data: " {ts("id_365")}

                        # {ts("id_2321")} [DONE] {ts("id_2287")}
                        if payload_str.strip() == "[DONE]":
                            if found_done_marker:
                                log.info("Anti-truncation: Found [done] marker, output complete")
                                yield line
                                # {ts("id_2322")}
                                chunk_buffer.close()
                                self._clear_content()
                                return
                            else:
                                log.warning("Anti-truncation: Stream ended without [done] marker")
                                # {ts("id_2324")}[DONE]{ts("id_2323")}
                                break

                        # {ts("id_1647")} JSON {ts("id_2325")}
                        try:
                            data = json.loads(payload_str)
                            content = self._extract_content_from_chunk(data)

                            log.debug(f"Anti-truncation: Extracted content: {repr(content[:100] if content else '')}")

                            if content:
                                chunk_buffer.write(content)

                                # {ts("id_2326")}done{ts("id_2287")}
                                has_marker = self._check_done_marker_in_chunk_content(content)
                                log.debug(f"Anti-truncation: Check done marker result: {has_marker}, DONE_MARKER='{DONE_MARKER}'")
                                if has_marker:
                                    found_done_marker = True
                                    log.debug(f"Anti-truncation: Found [done] marker in chunk, content: {content[:200]}")

                            # {ts("id_2328")}[done]{ts("id_2327")}
                            cleaned_line = self._remove_done_marker_from_line(line, line_str, data)
                            yield cleaned_line

                        except (json.JSONDecodeError, ValueError):
                            # {ts("id_2329")}
                            yield line
                            continue
                    else:
                        # {ts("id_1648")} data: {ts("id_2330")}
                        yield line

                # {ts(f"id_2331")} - {ts("id_463")} StringIO {ts("id_2332")}
                chunk_text = chunk_buffer.getvalue()
                if chunk_text:
                    self._append_content(chunk_text)
                chunk_buffer.close()

                log.debug(f"Anti-truncation: After processing stream, found_done_marker={found_done_marker}")

                # {ts("id_2334")}done{ts("id_2333")}
                if found_done_marker:
                    # {ts("id_2335")}
                    self._clear_content()
                    yield b"data: [DONE]\n\n"
                    return

                # {ts(f"id_2337")}chunk{ts("id_2338")}done{ts("id_2336f")}done{ts("id_2340")}chunk{ts("id_2339")}
                if not found_done_marker:
                    accumulated_text = self._get_collected_text()
                    if self._check_done_marker_in_text(accumulated_text):
                        log.info("Anti-truncation: Found [done] marker in accumulated content")
                        # {ts("id_2335")}
                        self._clear_content()
                        yield b"data: [DONE]\n\n"
                        return

                # {ts("id_2342")}done{ts("id_2341")}
                if self.current_attempt < self.max_attempts:
                    accumulated_text = self._get_collected_text()
                    total_length = len(accumulated_text)
                    log.info(
                        f"Anti-truncation: No [done] marker found in output (length: {total_length}), preparing continuation (attempt {self.current_attempt + 1})"
                    )
                    if total_length > 100:
                        log.debug(
                            f"Anti-truncation: Current collected content ends with: ...{accumulated_text[-100:]}"
                        )
                    # {ts("id_2343")}
                    continue
                else:
                    # {ts("id_2344")}
                    log.warning("Anti-truncation: Max attempts reached, ending stream")
                    # {ts("id_2335")}
                    self._clear_content()
                    yield b"data: [DONE]\n\n"
                    return

            except Exception as e:
                log.error(f"Anti-truncation error in attempt {self.current_attempt}: {str(e)}")
                if self.current_attempt >= self.max_attempts:
                    # {ts("id_2345")}chunk
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
                # {ts("id_2346")}

        # {ts("id_2347")}
        log.error("Anti-truncation: All attempts failed")
        # {ts("id_2322")}
        self._clear_content()
        yield b"data: [DONE]\n\n"

    def _build_current_payload(self) -> Dict[str, Any]:
        f"""{ts("id_2348")}payload"""
        if self.current_attempt == 1:
            # {ts("id_2350")}payload{ts("id_2349")}
            return self.base_payload

        # {ts("id_2351")}
        continuation_payload = self.base_payload.copy()
        request_data = continuation_payload.get("request", {})

        # {ts("id_2352")}
        contents = request_data.get("contents", [])
        new_contents = contents.copy()

        # {ts("id_2353")}
        accumulated_text = self._get_collected_text()
        if accumulated_text:
            new_contents.append({"role": "model", "parts": [{"text": accumulated_text}]})

        # {ts("id_2354")}
        content_summary = ""
        if accumulated_text:
            if len(accumulated_text) > 200:
                content_summary = ff'\n\n{ts("id_2356")} {len(accumulated_text)} {ts("id_2355")}\n"...{accumulated_text[-100:]}"'
            else:
                content_summary = ff'\n\n{ts("id_2357")}\n"{accumulated_text}"'

        detailed_continuation_prompt = f"""{CONTINUATION_PROMPT}{content_summary}"""

        # {ts("id_2358")}
        continuation_message = {"role": "user", "parts": [{"text": detailed_continuation_prompt}]}
        new_contents.append(continuation_message)

        request_data["contents"] = new_contents
        continuation_payload["request"] = request_data

        return continuation_payload

    def _extract_content_from_chunk(self, data: Dict[str, Any]) -> str:
        f"""{ts("id_1731")}chunk{ts("id_2359")}"""
        content = ""

        # {ts(f"id_2360")} response {ts("id_2361")}Gemini API {ts("id_493")}
        if "response" in data:
            data = data["response"]

        # {ts("id_590")} Gemini {ts("id_57")}
        if "candidates" in data:
            for candidate in data["candidates"]:
                if "content" in candidate:
                    parts = candidate["content"].get("parts", [])
                    for part in parts:
                        if "text" in part:
                            content += part["text"]
        
        # {ts(f"id_590")} OpenAI {ts("id_2362")}choices/delta{ts("id_292")}
        elif "choices" in data:
            for choice in data["choices"]:
                if "delta" in choice and "content" in choice["delta"]:
                    delta_content = choice["delta"]["content"]
                    if delta_content:
                        content += delta_content

        return content

    async def _handle_non_streaming_response(self, response) -> bytes:
        f"""{ts("id_2364")} - {ts("id_2363")}"""
        # {ts("id_2365")}
        while True:
            try:
                # {ts("id_2366")}StreamingResponse{ts("id_2367")}body_iterator
                if isinstance(response, StreamingResponse):
                    log.error("Anti-truncation: Received StreamingResponse in non-streaming handler - this should not happen")
                    # {ts("id_2368")}
                    chunks = []
                    async for chunk in response.body_iterator:
                        chunks.append(chunk)
                    content = b"".join(chunks).decode() if chunks else ""
                # {ts("id_2369")}
                elif hasattr(response, "body"):
                    content = (
                        response.body.decode() if isinstance(response.body, bytes) else response.body
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

                # {ts("id_2370")}
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

                # {ts("id_1647")} JSON
                try:
                    response_data = json.loads(content)
                except json.JSONDecodeError as json_err:
                    log.error(f"Anti-truncation: Failed to parse JSON response: {json_err}, content: {content[:200]}")
                    # {ts("id_2150")} JSON{ts("id_2371")}
                    return content.encode() if isinstance(content, str) else content

                # {ts("id_2326")}done{ts("id_2287")}
                text_content = self._extract_content_from_response(response_data)
                has_done_marker = self._check_done_marker_in_text(text_content)

                if has_done_marker or self.current_attempt >= self.max_attempts:
                    # {ts("id_2373")}done{ts("id_2372")}
                    return content.encode() if isinstance(content, str) else content

                # {ts("id_2374")}
                if text_content:
                    self._append_content(text_content)

                log.info("Anti-truncation: Non-streaming response needs continuation")

                # {ts("id_2375")}
                self.current_attempt += 1

                # {ts("id_2377")}payload{ts("id_2376")}
                next_payload = self._build_current_payload()
                response = await self.original_request_func(next_payload)

                # {ts("id_2378")}

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

    def _check_done_marker_in_text(self, text: str) -> bool:
        f"""{ts("id_2380")}DONE_MARKER{ts("id_2379")}"""
        if not text:
            return False

        # {ts("id_2381")}DONE_MARKER{ts("id_2382")}
        return DONE_MARKER in text

    def _check_done_marker_in_chunk_content(self, content: str) -> bool:
        f"""{ts("id_2384")}chunk{ts("id_2383f")}done{ts("id_2287")}"""
        return self._check_done_marker_in_text(content)

    def _extract_content_from_response(self, data: Dict[str, Any]) -> str:
        f"""{ts("id_2385")}"""
        content = ""

        # {ts(f"id_2360")} response {ts("id_2361")}Gemini API {ts("id_493")}
        if "response" in data:
            data = data["response"]

        # {ts("id_590")}Gemini{ts("id_57")}
        if "candidates" in data:
            for candidate in data["candidates"]:
                if "content" in candidate:
                    parts = candidate["content"].get("parts", [])
                    for part in parts:
                        if "text" in part:
                            content += part["text"]

        # {ts("id_590")}OpenAI{ts("id_57")}
        elif "choices" in data:
            for choice in data["choices"]:
                if "message" in choice and "content" in choice["message"]:
                    content += choice["message"]["content"]

        return content

    def _remove_done_marker_from_line(self, line: bytes, line_str: str, data: Dict[str, Any]) -> bytes:
        f"""{ts("id_2386")}[done]{ts("id_2287")}"""
        try:
            # {ts("id_2387")}[done]{ts("id_2287")}
            if "[done]" not in line_str.lower():
                return line  # {ts("id_2389")}[done]{ts("id_2388")}

            log.info(f"Anti-truncation: Attempting to remove [done] marker from line")
            log.debug(f"Anti-truncation: Original line (first 200 chars): {line_str[:200]}")

            # {ts("id_2391")}[done]{ts("id_2390")}
            done_pattern = re.compile(r"\s*\[done\]\s*", re.IGNORECASE)

            # {ts("id_2392")} response {ts("id_2393")}
            has_response_wrapper = "response" in data
            log.debug(f"Anti-truncation: has_response_wrapper={has_response_wrapper}, data keys={list(data.keys())}")
            if has_response_wrapper:
                # {ts("id_2394")} response {ts("id_2018")}
                inner_data = data["response"]
            else:
                inner_data = data
            
            log.debug(f"Anti-truncation: inner_data keys={list(inner_data.keys())}")

            log.debug(f"Anti-truncation: inner_data keys={list(inner_data.keys())}")

            # {ts("id_590")}Gemini{ts("id_57")}
            if "candidates" in inner_data:
                log.info(f"Anti-truncation: Processing Gemini format to remove [done] marker")
                modified_inner = inner_data.copy()
                modified_inner["candidates"] = []

                for i, candidate in enumerate(inner_data["candidates"]):
                    modified_candidate = candidate.copy()
                    # {ts(f"id_2395")}candidate{ts("id_2396")}[done]{ts("id_2287")}
                    is_last_candidate = i == len(inner_data["candidates"]) - 1

                    if "content" in candidate:
                        modified_content = candidate["content"].copy()
                        if "parts" in modified_content:
                            modified_parts = []
                            for part in modified_content["parts"]:
                                if "text" in part and isinstance(part["text"], str):
                                    modified_part = part.copy()
                                    original_text = part["text"]
                                    # {ts(f"id_2395")}candidate{ts("id_2396")}[done]{ts("id_2287")}
                                    if is_last_candidate:
                                        modified_part["text"] = done_pattern.sub("", part["text"])
                                        if "[done]" in original_text.lower():
                                            log.debug(f"Anti-truncation: Removed [done] from text: '{original_text[:100]}' -> '{modified_part['text'][:100]}'")
                                    modified_parts.append(modified_part)
                                else:
                                    modified_parts.append(part)
                            modified_content["parts"] = modified_parts
                        modified_candidate["content"] = modified_content
                    modified_inner["candidates"].append(modified_candidate)

                # {ts("id_2098")} response {ts("id_2397")}
                if has_response_wrapper:
                    modified_data = data.copy()
                    modified_data["response"] = modified_inner
                else:
                    modified_data = modified_inner

                # {ts("id_2399")} - SSE{ts("id_2398")}
                json_str = json.dumps(modified_data, separators=(",", ":"), ensure_ascii=False)
                result = f"data: {json_str}\n\n".encode("utf-8")
                log.debug(f"Anti-truncation: Modified line (first 200 chars): {result.decode('utf-8', errors='ignore')[:200]}")
                return result

            # {ts("id_590")}OpenAI{ts("id_57")}
            elif "choices" in inner_data:
                modified_inner = inner_data.copy()
                modified_inner["choices"] = []

                for choice in inner_data["choices"]:
                    modified_choice = choice.copy()
                    if "delta" in choice and "content" in choice["delta"]:
                        modified_delta = choice["delta"].copy()
                        modified_delta["content"] = done_pattern.sub("", choice["delta"]["content"])
                        modified_choice["delta"] = modified_delta
                    elif "message" in choice and "content" in choice["message"]:
                        modified_message = choice["message"].copy()
                        modified_message["content"] = done_pattern.sub("", choice["message"]["content"])
                        modified_choice["message"] = modified_message
                    modified_inner["choices"].append(modified_choice)

                # {ts("id_2098")} response {ts("id_2397")}
                if has_response_wrapper:
                    modified_data = data.copy()
                    modified_data["response"] = modified_inner
                else:
                    modified_data = modified_inner

                # {ts("id_2399")} - SSE{ts("id_2398")}
                json_str = json.dumps(modified_data, separators=(",", ":"), ensure_ascii=False)
                return f"data: {json_str}\n\n".encode("utf-8")

            # {ts("id_2400")}
            return line

        except Exception as e:
            log.warning(f"Failed to remove [done] marker from line: {str(e)}")
            return line


async def apply_anti_truncation_to_stream(
    request_func, payload: Dict[str, Any], max_attempts: int = 3
) -> StreamingResponse:
    """
    {ts("id_2401")}

    Args:
        request_func: {ts("id_2402")}
        payload: {ts("id_2282")}payload
        max_attempts: {ts("id_2403")}

    Returns:
        {ts("id_2404")}StreamingResponse
    """

    # {ts("id_2406")}payload{ts("id_2405")}
    anti_truncation_payload = apply_anti_truncation(payload)

    # {ts("id_2407")}
    processor = AntiTruncationStreamProcessor(
        lambda p: request_func(p), anti_truncation_payload, max_attempts
    )

    # {ts("id_2408")}
    return StreamingResponse(processor.process_stream(), media_type="text/event-stream")


def is_anti_truncation_enabled(request_data: Dict[str, Any]) -> bool:
    """
    {ts("id_2409")}

    Args:
        request_data: {ts("id_2410")}

    Returns:
        {ts("id_2411")}
    """
    return request_data.get("enable_anti_truncation", False)