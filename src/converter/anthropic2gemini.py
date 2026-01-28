from src.i18n import ts
"""
Anthropic {ts(f"id_2030")} Gemini {ts('id_2029')}

{ts(f"id_2031")}
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

from log import log
from src.converter.utils import merge_system_messages

from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature
)

DEFAULT_TEMPERATURE = 0.4
_DEBUG_TRUE = {"1", "true", "yes", "on"}

# ============================================================================
# Thinking {ts(f"id_2032")}
# ============================================================================

# {ts(f"id_2033")}
MIN_SIGNATURE_LENGTH = 10


def has_valid_thoughtsignature(block: Dict[str, Any]) -> bool:
    """
    {ts(f"id_1890")} thinking {ts('id_2034')}
    
    Args:
        block: content block {ts(f"id_2035")}
        
    Returns:
        bool: {ts(f"id_2036")}
    """
    if not isinstance(block, dict):
        return True
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return True  # {ts(f"id_1648")} thinking {ts('id_2037')}
    
    thinking = block.get("thinking", "")
    thoughtsignature = block.get("thoughtSignature")
    
    # {ts(f"id_2040")} thinking + {ts('id_2039')} thoughtsignature = {ts('id_2038')} (trailing signature case)
    if not thinking and thoughtsignature is not None:
        return True
    
    # {ts(f"id_2042")} + {ts('id_2041')} thoughtsignature = {ts('id_2038')}
    if thoughtsignature and isinstance(thoughtsignature, str) and len(thoughtsignature) >= MIN_SIGNATURE_LENGTH:
        return True
    
    return False


def sanitize_thinking_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """
    {ts(f"id_2045")} thinking {ts('id_2046')},{ts('id_2043f')}({ts('id_2044')} cache_control {ts('id_118')})
    
    Args:
        block: content block {ts(f"id_2035")}
        
    Returns:
        {ts(f"id_2047")} block {ts('id_2035')}
    """
    if not isinstance(block, dict):
        return block
    
    block_type = block.get("type")
    if block_type not in ("thinking", "redacted_thinking"):
        return block
    
    # {ts(f"id_2049")},{ts('id_2048')}
    sanitized: Dict[str, Any] = {
        "type": block_type,
        "thinking": block.get("thinking", "")
    }
    
    thoughtsignature = block.get("thoughtSignature")
    if thoughtsignature:
        sanitized["thoughtSignature"] = thoughtsignature
    
    return sanitized


def remove_trailing_unsigned_thinking(blocks: List[Dict[str, Any]]) -> None:
    """
    {ts(f"id_2050")} thinking {ts('id_2046')}
    
    Args:
        blocks: content blocks {ts(f"id_2052")} ({ts('id_2051')})
    """
    if not blocks:
        return
    
    # {ts(f"id_2053")}
    end_index = len(blocks)
    for i in range(len(blocks) - 1, -1, -1):
        block = blocks[i]
        if not isinstance(block, dict):
            break
        
        block_type = block.get("type")
        if block_type in ("thinking", "redacted_thinking"):
            if not has_valid_thoughtsignature(block):
                end_index = i
            else:
                break  # {ts(f"id_2054")} thinking {ts('id_2046')},{ts('id_2055')}
        else:
            break  # {ts(f"id_2056")} thinking {ts('id_2046')},{ts('id_2055')}
    
    if end_index < len(blocks):
        removed = len(blocks) - end_index
        del blocks[end_index:]
        log.debug(f"Removed {removed} trailing unsigned thinking block(s)")


def filter_invalid_thinking_blocks(messages: List[Dict[str, Any]]) -> None:
    """
    {ts(f"id_2057")} thinking {ts('id_2059')} thinking {ts('id_2058')} cache_control{ts('id_292')}

    Args:
        messages: Anthropic messages {ts(f"id_2052")} ({ts('id_2051')})
    """
    total_filtered = 0

    for msg in messages:
        # {ts(f"id_2060")} assistant {ts('id_15')} model {ts('id_2061')}
        role = msg.get("role", "")
        if role not in ("assistant", "model"):
            continue

        content = msg.get("content")
        if not isinstance(content, list):
            continue

        original_len = len(content)
        new_blocks: List[Dict[str, Any]] = []

        for block in content:
            if not isinstance(block, dict):
                new_blocks.append(block)
                continue

            block_type = block.get("type")
            if block_type not in ("thinking", "redacted_thinking"):
                new_blocks.append(block)
                continue

            # {ts(f"id_930")} thinking {ts('id_2062')} cache_control {ts('id_2063')}
            # {ts(f"id_1890")} thinking {ts('id_2064')}
            if has_valid_thoughtsignature(block):
                # {ts(f"id_2065")}
                new_blocks.append(sanitize_thinking_block(block))
            else:
                # {ts(f"id_2066")} text {ts('id_2046')}
                thinking_text = block.get("thinking", "")
                if thinking_text and str(thinking_text).strip():
                    log.info(
                        f"[Claude-Handler] Converting thinking block with invalid thoughtSignature to text. "
                        f"Content length: {len(thinking_text)} chars"
                    )
                    new_blocks.append({"type": "text", "text": thinking_text})
                else:
                    log.debug("[Claude-Handler] Dropping empty thinking block with invalid thoughtSignature")

        msg["content"] = new_blocks
        filtered_count = original_len - len(new_blocks)
        total_filtered += filtered_count

        # {ts(f"id_2068")},{ts('id_2067')}
        if not new_blocks:
            msg["content"] = [{"type": "text", "text": ""}]

    if total_filtered > 0:
        log.debug(f"Filtered {total_filtered} invalid thinking block(s) from history")


# ============================================================================
# {ts(f"id_2069")}
# ============================================================================


def _anthropic_debug_enabled() -> bool:
    f"""{ts('id_2070')} Anthropic {ts('id_2071')}"""
    return str(os.getenv("ANTHROPIC_DEBUG", "true")).strip().lower() in _DEBUG_TRUE


def _is_non_whitespace_text(value: Any) -> bool:
    """
    {ts(f"id_2072")}"{ts('id_2074')}"{ts('id_2073')}

    {ts(f"id_2077")}Antigravity/Claude {ts('id_2076')} text {ts('id_2075')}
    - text {ts(f"id_2078")}
    - text {ts(f"id_2079")}/{ts('id_2081')}/{ts('id_2080')}
    """
    if value is None:
        return False
    try:
        return bool(str(value).strip())
    except Exception:
        return False


def _remove_nulls_for_tool_input(value: Any) -> Any:
    """
    {ts(f"id_2082")} dict/list {ts('id_2085')} null/None {ts('id_2084')}/{ts('id_2083')}

    {ts(f"id_2087")}Roo/Kilo {ts('id_429')} Anthropic native tool {ts('id_2086f')} tool_use.input {ts('id_2088')} null{ts('id_2089')}
    {ts(f"id_2091")} null {ts('id_2090')}"{ts(f"id_429")} null {ts('id_2092')}"{ts('id_2093')}
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

# ============================================================================
# 2. JSON Schema {ts(f"id_2045")}
# ============================================================================

def clean_json_schema(schema: Any) -> Any:
    """
    {ts(f"id_2045")} JSON Schema{ts('id_2094')} description{ts('id_672')}
    """
    if not isinstance(schema, dict):
        return schema

    # {ts(f"id_2095")}
    unsupported_keys = {
        "$schema", "$id", "$ref", "$defs", "definitions", "title",
        "example", "examples", "readOnly", "writeOnly", "default",
        "exclusiveMaximum", "exclusiveMinimum", "oneOf", "anyOf", "allOf",
        "const", "additionalItems", "contains", "patternProperties",
        "dependencies", "propertyNames", "if", "then", "else",
        "contentEncoding", "contentMediaType",
    }

    validation_fields = {
        "minLength": "minLength",
        "maxLength": "maxLength",
        "minimum": "minimum",
        "maximum": "maximum",
        "minItems": "minItems",
        "maxItems": "maxItems",
    }
    fields_to_remove = {"additionalProperties"}

    validations: List[str] = []
    for field, label in validation_fields.items():
        if field in schema:
            validations.append(f"{label}: {schema[field]}")

    cleaned: Dict[str, Any] = {}
    for key, value in schema.items():
        if key in unsupported_keys or key in fields_to_remove or key in validation_fields:
            continue

        if key == "type" and isinstance(value, list):
            # type: ["string", "null"] -> type: "string", nullable: true
            has_null = any(
                isinstance(t, str) and t.strip() and t.strip().lower() == "null" for t in value
            )
            non_null_types = [
                t.strip()
                for t in value
                if isinstance(t, str) and t.strip() and t.strip().lower() != "null"
            ]

            cleaned[key] = non_null_types[0] if non_null_types else "string"
            if has_null:
                cleaned["nullable"] = True
            continue

        if key == "description" and validations:
            cleaned[key] = f"{value} ({', '.join(validations)})"
        elif isinstance(value, dict):
            cleaned[key] = clean_json_schema(value)
        elif isinstance(value, list):
            cleaned[key] = [clean_json_schema(item) if isinstance(item, dict) else item for item in value]
        else:
            cleaned[key] = value

    if validations and "description" not in cleaned:
        cleaned["description"] = f"Validation: {', '.join(validations)}"

    # {ts(f"id_2098")} properties {ts('id_2097')} type{ts('id_2096')} object
    if "properties" in cleaned and "type" not in cleaned:
        cleaned["type"] = "object"

    return cleaned


# ============================================================================
# 4. Tools {ts(f"id_2099")}
# ============================================================================

def convert_tools(anthropic_tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    {ts(f"id_101")} Anthropic tools[] {ts('id_2100')} tools{ts('id_1748')}functionDeclarations{ts('id_2101')}
    """
    if not anthropic_tools:
        return None

    gemini_tools: List[Dict[str, Any]] = []
    for tool in anthropic_tools:
        name = tool.get("name", "nameless_function")
        description = tool.get("description", "")
        input_schema = tool.get("input_schema", {}) or {}
        parameters = clean_json_schema(input_schema)

        gemini_tools.append(
            {
                "functionDeclarations": [
                    {
                        "name": name,
                        "description": description,
                        "parameters": parameters,
                    }
                ]
            }
        )

    return gemini_tools or None


# ============================================================================
# 5. Messages {ts(f"id_2099")}
# ============================================================================

def _extract_tool_result_output(content: Any) -> str:
    f"""{ts('id_1731')} tool_result.content {ts('id_2102')}"""
    if isinstance(content, list):
        if not content:
            return ""
        first = content[0]
        if isinstance(first, dict) and first.get("type") == "text":
            return str(first.get("text", ""))
        return str(first)
    if content is None:
        return ""
    return str(content)


def convert_messages_to_contents(
    messages: List[Dict[str, Any]],
    *,
    include_thinking: bool = True
) -> List[Dict[str, Any]]:
    """
    {ts(f"id_101")} Anthropic messages[] {ts('id_2100')} contents[]{ts('id_1748')}role: user/model, parts: []{ts('id_2093')}

    Args:
        messages: Anthropic {ts(f"id_2103")}
        include_thinking: {ts(f"id_2104")} thinking {ts('id_2046')}
    """
    contents: List[Dict[str, Any]] = []

    # {ts(f"id_2105")} tool_use_id -> (name, thoughtsignature) {ts('id_2106')}
    # {ts(f"id_2107")} ID{ts('id_2108')}
    tool_use_info: Dict[str, tuple[str, Optional[str]]] = {}
    for msg in messages:
        raw_content = msg.get("content", "")
        if isinstance(raw_content, list):
            for item in raw_content:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    encoded_tool_id = item.get("id")
                    tool_name = item.get("name")
                    if encoded_tool_id and tool_name:
                        # {ts(f"id_2109")}ID{ts('id_2110')}
                        original_id, thoughtsignature = decode_tool_id_and_signature(encoded_tool_id)
                        # {ts(f"id_2111")}ID -> (name, thoughtsignature)
                        tool_use_info[str(encoded_tool_id)] = (tool_name, thoughtsignature)

    for msg in messages:
        role = msg.get("role", "user")
        
        # system {ts(f"id_2113")} merge_system_messages {ts('id_2112')}
        if role == "system":
            continue
        
        # {ts(f"id_56")} 'assistant' {ts('id_15')} 'model' {ts('id_2114')}Google history usage{ts('id_292')}
        gemini_role = "model" if role in ("assistant", "model") else "user"
        raw_content = msg.get("content", "")

        parts: List[Dict[str, Any]] = []
        if isinstance(raw_content, str):
            if _is_non_whitespace_text(raw_content):
                parts = [{"text": str(raw_content)}]
        elif isinstance(raw_content, list):
            for item in raw_content:
                if not isinstance(item, dict):
                    if _is_non_whitespace_text(item):
                        parts.append({"text": str(item)})
                    continue

                item_type = item.get("type")
                if item_type == "thinking":
                    if not include_thinking:
                        continue

                    thinking_text = item.get("thinking", "")
                    if thinking_text is None:
                        thinking_text = ""
                    
                    part: Dict[str, Any] = {
                        "text": str(thinking_text),
                        "thought": True,
                    }
                    
                    # {ts(f"id_2098")} thoughtsignature {ts('id_2115')}
                    thoughtsignature = item.get("thoughtSignature")
                    if thoughtsignature:
                        part["thoughtSignature"] = thoughtsignature
                    
                    parts.append(part)
                elif item_type == "redacted_thinking":
                    if not include_thinking:
                        continue

                    thinking_text = item.get("thinking")
                    if thinking_text is None:
                        thinking_text = item.get("data", "")
                    
                    part_dict: Dict[str, Any] = {
                        "text": str(thinking_text or ""),
                        "thought": True,
                    }
                    
                    # {ts(f"id_2098")} thoughtsignature {ts('id_2115')}
                    thoughtsignature = item.get("thoughtSignature")
                    if thoughtsignature:
                        part_dict["thoughtSignature"] = thoughtsignature
                    
                    parts.append(part_dict)
                elif item_type == "text":
                    text = item.get("text", "")
                    if _is_non_whitespace_text(text):
                        parts.append({"text": str(text)})
                elif item_type == "image":
                    source = item.get("source", {}) or {}
                    if source.get("type") == "base64":
                        parts.append(
                            {
                                "inlineData": {
                                    "mimeType": source.get("media_type", "image/png"),
                                    "data": source.get("data", ""),
                                }
                            }
                        )
                elif item_type == "tool_use":
                    encoded_id = item.get("id") or ""
                    original_id, thoughtsignature = decode_tool_id_and_signature(encoded_id)

                    fc_part: Dict[str, Any] = {
                        "functionCall": {
                            f"id": original_id,  # {ts('id_2117')}ID{ts('id_2116')}
                            "name": item.get("name"),
                            "args": item.get("input", {}) or {},
                        }
                    }

                    # {ts(f"id_2118")} Gemini API {ts('id_2119')}
                    if thoughtsignature:
                        fc_part["thoughtSignature"] = thoughtsignature
                    else:
                        fc_part["thoughtSignature"] = "skip_thought_signature_validator"

                    parts.append(fc_part)
                elif item_type == "tool_result":
                    output = _extract_tool_result_output(item.get("content"))
                    encoded_tool_use_id = item.get("tool_use_id") or ""
                    
                    # {ts(f"id_2109")}ID{ts('id_1748')}functionResponse{ts('id_2120')}
                    original_tool_use_id, _ = decode_tool_id_and_signature(encoded_tool_use_id)

                    # {ts(f"id_1731")} tool_result {ts('id_712')} name{ts('id_2121')}
                    func_name = item.get("name")
                    if not func_name and encoded_tool_use_id:
                        # {ts(f"id_2123")}ID{ts('id_2122')}
                        tool_info = tool_use_info.get(str(encoded_tool_use_id))
                        if tool_info:
                            func_name = tool_info[0]  # {ts(f"id_712")} name
                    if not func_name:
                        func_name = "unknown_function"
                    
                    parts.append(
                        {
                            "functionResponse": {
                                f"id": original_tool_use_id,  # {ts('id_2124')}ID{ts('id_2125')}functionCall
                                "name": func_name,
                                "response": {"output": output},
                            }
                        }
                    )
                else:
                    parts.append({"text": json.dumps(item, ensure_ascii=False)})
        else:
            if _is_non_whitespace_text(raw_content):
                parts = [{"text": str(raw_content)}]

        if not parts:
            continue

        contents.append({"role": gemini_role, "parts": parts})

    return contents


def reorganize_tool_messages(contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    {ts(f"id_2126")} tool_use/tool_result {ts('id_2127')}
    """
    tool_results: Dict[str, Dict[str, Any]] = {}

    for msg in contents:
        for part in msg.get("parts", []) or []:
            if isinstance(part, dict) and "functionResponse" in part:
                tool_id = (part.get("functionResponse") or {}).get("id")
                if tool_id:
                    tool_results[str(tool_id)] = part

    flattened: List[Dict[str, Any]] = []
    for msg in contents:
        role = msg.get("role")
        for part in msg.get("parts", []) or []:
            flattened.append({"role": role, "parts": [part]})

    new_contents: List[Dict[str, Any]] = []
    i = 0
    while i < len(flattened):
        msg = flattened[i]
        part = msg["parts"][0]

        if isinstance(part, dict) and "functionResponse" in part:
            i += 1
            continue

        if isinstance(part, dict) and "functionCall" in part:
            tool_id = (part.get("functionCall") or {}).get("id")
            new_contents.append({"role": "model", "parts": [part]})

            if tool_id is not None and str(tool_id) in tool_results:
                new_contents.append({"role": "user", "parts": [tool_results[str(tool_id)]]})

            i += 1
            continue

        new_contents.append(msg)
        i += 1

    return new_contents


# ============================================================================
# 7. Tool Choice {ts(f"id_2099")}
# ============================================================================

def convert_tool_choice_to_tool_config(tool_choice: Any) -> Optional[Dict[str, Any]]:
    """
    {ts(f"id_101")} Anthropic tool_choice {ts('id_188')} Gemini toolConfig

    Args:
        tool_choice: Anthropic {ts(f"id_2128")} tool_choice
            - {f"type": "auto"}: {ts('id_2129')}
            - {f"type": "any"}: {ts('id_2130')}
            - {f"type": "tool", "name": "tool_name"}: {ts('id_2131')}

    Returns:
        Gemini {ts(f"id_2128")} toolConfig{ts('id_2132')} None
    """
    if not tool_choice:
        return None
    
    if isinstance(tool_choice, dict):
        choice_type = tool_choice.get("type")
        
        if choice_type == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif choice_type == "any":
            return {"functionCallingConfig": {"mode": "ANY"}}
        elif choice_type == "tool":
            tool_name = tool_choice.get("name")
            if tool_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [tool_name],
                    }
                }
    
    # {ts(f"id_2133")} tool_choice{ts('id_2134')} None
    return None


# ============================================================================
# 8. Generation Config {ts(f"id_1475")}
# ============================================================================

def build_generation_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    {ts(f"id_2136")} Anthropic Messages {ts('id_2135')} generationConfig{ts('id_672')}

    Returns:
        generation_config: {ts(f"id_2137")}
    """
    config: Dict[str, Any] = {
        "topP": 1,
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>",
        ],
    }

    temperature = payload.get("temperature", None)
    config["temperature"] = DEFAULT_TEMPERATURE if temperature is None else temperature

    top_p = payload.get("top_p", None)
    if top_p is not None:
        config["topP"] = top_p

    top_k = payload.get("top_k", None)
    if top_k is not None:
        config["topK"] = top_k

    max_tokens = payload.get("max_tokens")
    if max_tokens is not None:
        config["maxOutputTokens"] = max_tokens

    # {ts(f"id_590")} extended thinking {ts('id_226')} (plan mode)
    thinking = payload.get("thinking")
    is_plan_mode = False
    if thinking and isinstance(thinking, dict):
        thinking_type = thinking.get("type")
        budget_tokens = thinking.get("budget_tokens")
        
        # {ts(f"id_2138")} extended thinking{ts('id_2139')} thinkingConfig
        if thinking_type == "enabled":
            is_plan_mode = True
            thinking_config: Dict[str, Any] = {}
            
            # {ts(f"id_2140")}
            if budget_tokens is not None:
                thinking_config["thinkingBudget"] = budget_tokens
            else:
                # {ts(f"id_2141")}
                thinking_config["thinkingBudget"] = 48000
            
            # {ts(f"id_2142")}
            thinking_config["includeThoughts"] = True
            
            config["thinkingConfig"] = thinking_config
            log.info(f"[ANTHROPIC2GEMINI] Extended thinking enabled with budget: {thinking_config['thinkingBudget']}")
        elif thinking_type == "disabled":
            # {ts(f"id_2143")}
            config["thinkingConfig"] = {
                "includeThoughts": False
            }
            log.info("[ANTHROPIC2GEMINI] Extended thinking explicitly disabled")

    stop_sequences = payload.get("stop_sequences")
    if isinstance(stop_sequences, list) and stop_sequences:
        config["stopSequences"] = config["stopSequences"] + [str(s) for s in stop_sequences]
    elif is_plan_mode:
        # Plan mode {ts(f"id_2145")} stop sequences{ts('id_2144')}
        # {ts(f"id_2147")} stop sequences {ts('id_2146')}
        config["stopSequences"] = []
        log.info("[ANTHROPIC2GEMINI] Plan mode: cleared default stop sequences to prevent premature stopping")
    
    # {ts(f"id_2150")} plan mode {ts('id_2149')} stop_sequences{ts('id_2148')}
    # ({ts(f"id_2152")} config {ts('id_2151')})

    return config


# ============================================================================
# 8. {ts(f"id_2153")}
# ============================================================================

async def anthropic_to_gemini_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    {ts(f"id_101")} Anthropic {ts('id_2154')} Gemini {ts('id_2155')}

    {ts(f"id_2158")}: {ts('id_2156')} normalize_gemini_request {ts('id_2157')}
    ({ts(f"id_716")} thinking config {ts('id_2160')}search tools{ts('id_2159')})

    Args:
        payload: Anthropic {ts(f"id_2161")}

    Returns:
        Gemini {ts(f"id_2162")}:
        - contents: {ts(f"id_2163")}
        - generationConfig: {ts(f"id_2164")}
        - systemInstruction: {ts(f"id_2165")} ({ts('id_2098')})
        - tools: {ts(f"id_2166")} ({ts('id_2098')})
        - toolConfig: {ts(f"id_2167")} ({ts('id_2098')} tool_choice)
    """
    # {ts(f"id_2169")}system{ts('id_2168')}
    payload = await merge_system_messages(payload)

    # {ts(f"id_2170")}
    messages = payload.get("messages") or []
    if not isinstance(messages, list):
        messages = []
    
    # [CRITICAL FIX] {ts(f"id_2171")} Thinking {ts('id_2172')}
    # {ts(f"id_2173")} thinking {ts('id_2046')}
    filter_invalid_thinking_blocks(messages)

    # {ts(f"id_2174")}
    generation_config = build_generation_config(payload)

    # {ts(f"id_2175")}thinking{ts('id_2176')}
    contents = convert_messages_to_contents(messages, include_thinking=True)
    
    # [CRITICAL FIX] {ts(f"id_2177")} thinking {ts('id_2046')}
    # {ts(f"id_2178")}
    for content in contents:
        role = content.get("role", "")
        if role == f"model":  # {ts('id_2060')} model/assistant {ts('id_2061')}
            parts = content.get("parts", [])
            if isinstance(parts, list):
                remove_trailing_unsigned_thinking(parts)
    
    contents = reorganize_tool_messages(contents)

    # {ts(f"id_2179")}
    tools = convert_tools(payload.get("tools"))
    
    # {ts(f"id_2099")} tool_choice
    tool_config = convert_tool_choice_to_tool_config(payload.get("tool_choice"))

    # {ts(f"id_2180")}
    gemini_request = {
        "contents": contents,
        "generationConfig": generation_config,
    }
    
    # {ts(f"id_2183")} merge_system_messages {ts('id_2181')} systemInstruction{ts('id_2182')}
    if "systemInstruction" in payload:
        gemini_request["systemInstruction"] = payload["systemInstruction"]
    
    if tools:
        gemini_request["tools"] = tools
    
    # {ts(f"id_848")} toolConfig{ts('id_2184')} tool_choice{ts('id_292')}
    if tool_config:
        gemini_request["toolConfig"] = tool_config

    return gemini_request


def gemini_to_anthropic_response(
    gemini_response: Dict[str, Any],
    model: str,
    status_code: int = 200
) -> Dict[str, Any]:
    """
    {ts(f"id_101")} Gemini {ts('id_2185')} Anthropic {ts('id_2186')}

    {ts(f"id_2158")}: {ts('id_2188')} 200 {ts('id_2187')}

    Args:
        gemini_response: Gemini {ts(f"id_2189")}
        model: {ts(f"id_1737")}
        status_code: HTTP {ts(f"id_1461")} ({ts('id_7')} 200)

    Returns:
        Anthropic {ts(f"id_2190")} ({ts('id_2191')} 2xx)
    """
    # {ts(f"id_1648")} 2xx {ts('id_2192')}
    if not (200 <= status_code < 300):
        return gemini_response

    # {ts(f"id_590")} GeminiCLI {ts('id_61')} response {ts('id_2193')}
    if "response" in gemini_response:
        response_data = gemini_response["response"]
    else:
        response_data = gemini_response

    # {ts(f"id_2194")}
    candidate = response_data.get("candidates", [{}])[0] or {}
    parts = candidate.get("content", {}).get("parts", []) or []

    # {ts(f"id_712")} usage metadata
    usage_metadata = {}
    if "usageMetadata" in response_data:
        usage_metadata = response_data["usageMetadata"]
    elif "usageMetadata" in candidate:
        usage_metadata = candidate["usageMetadata"]

    # {ts(f"id_2195")}
    content = []
    has_tool_use = False

    for part in parts:
        if not isinstance(part, dict):
            continue

        # {ts(f"id_590")} thinking {ts('id_2046')}
        if part.get("thought") is True:
            thinking_text = part.get("text", "")
            if thinking_text is None:
                thinking_text = ""
            
            block: Dict[str, Any] = {"type": "thinking", "thinking": str(thinking_text)}
            
            # {ts(f"id_2098")} thoughtsignature {ts('id_2115')}
            thoughtsignature = part.get("thoughtSignature")
            if thoughtsignature:
                block["thoughtSignature"] = thoughtsignature
            
            content.append(block)
            continue

        # {ts(f"id_2196")}
        if "text" in part:
            content.append({"type": "text", "text": part.get("text", "")})
            continue

        # {ts(f"id_2197")}
        if "functionCall" in part:
            has_tool_use = True
            fc = part.get("functionCall", {}) or {}
            original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
            thoughtsignature = part.get("thoughtSignature")
            
            # {ts(f"id_2199")}ID{ts('id_2198')}
            encoded_id = encode_tool_id_with_signature(original_id, thoughtsignature)
            content.append(
                {
                    "type": "tool_use",
                    "id": encoded_id,
                    "name": fc.get("name") or "",
                    "input": _remove_nulls_for_tool_input(fc.get("args", {}) or {}),
                }
            )
            continue

        # {ts(f"id_2200")}
        if "inlineData" in part:
            inline = part.get("inlineData", {}) or {}
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": inline.get("mimeType", "image/png"),
                        "data": inline.get("data", ""),
                    },
                }
            )
            continue

    # {ts(f"id_2201")}
    finish_reason = candidate.get("finishReason")
    
    # {ts(f"id_2203")}STOP{ts('id_2202')} tool_use
    # {ts(f"id_2206")} SAFETY{ts('id_189')}MAX_TOKENS {ts('id_2204')} tool_use {ts('id_2205')}
    if has_tool_use and finish_reason == "STOP":
        stop_reason = "tool_use"
    elif finish_reason == "MAX_TOKENS":
        stop_reason = "max_tokens"
    else:
        # {ts(f"id_2207")}SAFETY{ts('id_189')}RECITATION {ts('id_2208')} end_turn
        stop_reason = "end_turn"

    # {ts(f"id_2210")} token {ts('id_2209')}
    input_tokens = usage_metadata.get("promptTokenCount", 0) if isinstance(usage_metadata, dict) else 0
    output_tokens = usage_metadata.get("candidatesTokenCount", 0) if isinstance(usage_metadata, dict) else 0

    # {ts(f"id_1475")} Anthropic {ts('id_1516')}
    message_id = f"msg_{uuid.uuid4().hex}"

    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
        },
    }


async def gemini_stream_to_anthropic_stream(
    gemini_stream: AsyncIterator[bytes],
    model: str,
    status_code: int = 200
) -> AsyncIterator[bytes]:
    """
    {ts(f"id_101")} Gemini {ts('id_2211')} Anthropic SSE {ts('id_2212')}

    {ts(f"id_2158")}: {ts('id_2188')} 200 {ts('id_2187')}

    Args:
        gemini_stream: Gemini {ts(f"id_2213")} (bytes {ts('id_2214')})
        model: {ts(f"id_1737")}
        status_code: HTTP {ts(f"id_1461")} ({ts('id_7')} 200)

    Yields:
        Anthropic SSE {ts(f"id_2215")} (bytes)
    """
    # {ts(f"id_1648")} 2xx {ts('id_2216')}
    if not (200 <= status_code < 300):
        async for chunk in gemini_stream:
            yield chunk
        return

    # {ts(f"id_2217")}
    message_id = f"msg_{uuid.uuid4().hex}"
    message_start_sent = False
    current_block_type: Optional[str] = None
    current_block_index = -1
    current_thinking_signature: Optional[str] = None
    has_tool_use = False
    input_tokens = 0
    output_tokens = 0
    finish_reason: Optional[str] = None

    def _sse_event(event: str, data: Dict[str, Any]) -> bytes:
        f"""{ts('id_2218')} SSE {ts('id_2219')}"""
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")

    def _close_block() -> Optional[bytes]:
        f"""{ts('id_2220')}"""
        nonlocal current_block_type
        if current_block_type is None:
            return None
        event = _sse_event(
            "content_block_stop",
            {"type": "content_block_stop", "index": current_block_index},
        )
        current_block_type = None
        return event

    # {ts(f"id_2221")}
    try:
        async for chunk in gemini_stream:
            # {ts(f"id_2222")}chunk
            log.debug(f"[GEMINI_TO_ANTHROPIC] Raw chunk: {chunk[:200] if chunk else b''}")

            # {ts(f"id_2224")} Gemini {ts('id_2223')}
            if not chunk or not chunk.startswith(b"data: "):
                log.debug(f"[GEMINI_TO_ANTHROPIC] Skipping chunk (not SSE format or empty)")
                continue

            raw = chunk[6:].strip()
            if raw == b"[DONE]":
                log.debug(f"[GEMINI_TO_ANTHROPIC] Received [DONE] marker")
                break

            log.debug(f"[GEMINI_TO_ANTHROPIC] Parsing JSON: {raw[:200]}")

            try:
                data = json.loads(raw.decode('utf-8', errors='ignore'))
                log.debug(f"[GEMINI_TO_ANTHROPIC] Parsed data: {json.dumps(data, ensure_ascii=False)[:300]}")
            except Exception as e:
                log.warning(f"[GEMINI_TO_ANTHROPIC] JSON parse error: {e}")
                continue

            # {ts(f"id_590")} GeminiCLI {ts('id_61')} response {ts('id_2193')}
            if "response" in data:
                response = data["response"]
            else:
                response = data

            candidate = (response.get("candidates", []) or [{}])[0] or {}
            parts = (candidate.get("content", {}) or {}).get("parts", []) or []

            # {ts(f"id_689")} usage metadata
            if "usageMetadata" in response:
                usage = response["usageMetadata"]
                if isinstance(usage, dict):
                    if "promptTokenCount" in usage:
                        input_tokens = int(usage.get("promptTokenCount", 0) or 0)
                    if "candidatesTokenCount" in usage:
                        output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)

            # {ts(f"id_2226")} message_start{ts('id_2225')}
            if not message_start_sent:
                message_start_sent = True
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
                            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                        },
                    },
                )

            # {ts(f"id_2227")} parts
            for part in parts:
                if not isinstance(part, dict):
                    continue

                # {ts(f"id_590")} thinking {ts('id_2046')}
                if part.get("thought") is True:
                    thinking_text = part.get("text", "")
                    thoughtsignature = part.get("thoughtSignature")
                    
                    # {ts(f"id_2228")} thinking {ts('id_2046')}
                    if current_block_type != "thinking":
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt

                        current_block_index += 1
                        current_block_type = "thinking"
                        current_thinking_signature = thoughtsignature

                        block: Dict[str, Any] = {"type": "thinking", "thinking": ""}
                        if thoughtsignature:
                            block["thoughtSignature"] = thoughtsignature
                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": block,
                            },
                        )
                    elif thoughtsignature and thoughtsignature != current_thinking_signature:
                        # {ts(f"id_2229")} thinking {ts('id_2046')}
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt
                        
                        current_block_index += 1
                        current_block_type = "thinking"
                        current_thinking_signature = thoughtsignature
                        
                        block_new: Dict[str, Any] = {"type": "thinking", "thinking": ""}
                        if thoughtsignature:
                            block_new["thoughtSignature"] = thoughtsignature
                        
                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": block_new,
                            },
                        )

                    # {ts(f"id_2226")} thinking {ts('id_2230')}
                    if thinking_text:
                        yield _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": current_block_index,
                                "delta": {"type": "thinking_delta", "thinking": thinking_text},
                            },
                        )
                    continue

                # {ts(f"id_2196")}
                if "text" in part:
                    text = part.get("text", "")
                    if isinstance(text, str) and not text.strip():
                        continue

                    if current_block_type != "text":
                        close_evt = _close_block()
                        if close_evt:
                            yield close_evt

                        current_block_index += 1
                        current_block_type = "text"

                        yield _sse_event(
                            "content_block_start",
                            {
                                "type": "content_block_start",
                                "index": current_block_index,
                                "content_block": {"type": "text", "text": ""},
                            },
                        )

                    if text:
                        yield _sse_event(
                            "content_block_delta",
                            {
                                "type": "content_block_delta",
                                "index": current_block_index,
                                "delta": {"type": "text_delta", "text": text},
                            },
                        )
                    continue

                # {ts(f"id_2197")}
                if "functionCall" in part:
                    close_evt = _close_block()
                    if close_evt:
                        yield close_evt

                    has_tool_use = True
                    fc = part.get("functionCall", {}) or {}
                    original_id = fc.get("id") or f"toolu_{uuid.uuid4().hex}"
                    thoughtsignature = part.get("thoughtSignature")
                    tool_id = encode_tool_id_with_signature(original_id, thoughtsignature)
                    tool_name = fc.get("name") or ""
                    tool_args = _remove_nulls_for_tool_input(fc.get("args", {}) or {})

                    if _anthropic_debug_enabled():
                        log.info(
                            f"[ANTHROPIC][tool_use] {ts('id_2197')}: name={tool_name}, "
                            f"id={tool_id}, has_signature={thoughtsignature is not None}"
                        )

                    current_block_index += 1
                    # {ts(f"id_2232")} current_block_type{ts('id_2231')}

                    yield _sse_event(
                        "content_block_start",
                        {
                            "type": "content_block_start",
                            "index": current_block_index,
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
                            "index": current_block_index,
                            "delta": {"type": "input_json_delta", "partial_json": input_json},
                        },
                    )

                    yield _sse_event(
                        "content_block_stop",
                        {"type": "content_block_stop", "index": current_block_index},
                    )
                    # {ts(f"id_2233")}current_block_type {ts('id_2234')} None
                    
                    if _anthropic_debug_enabled():
                        log.info(f"[ANTHROPIC][tool_use] {ts('id_2235')}: index={current_block_index}")
                    
                    continue

            # {ts(f"id_2236")}
            if candidate.get("finishReason"):
                finish_reason = candidate.get("finishReason")
                break

        # {ts(f"id_2237")}
        close_evt = _close_block()
        if close_evt:
            yield close_evt

        # {ts(f"id_2201")}
        # {ts(f"id_2203")}STOP{ts('id_2202')} tool_use
        # {ts(f"id_2206")} SAFETY{ts('id_189')}MAX_TOKENS {ts('id_2204')} tool_use {ts('id_2205')}
        if has_tool_use and finish_reason == "STOP":
            stop_reason = "tool_use"
        elif finish_reason == "MAX_TOKENS":
            stop_reason = "max_tokens"
        else:
            # {ts(f"id_2207")}SAFETY{ts('id_189')}RECITATION {ts('id_2208')} end_turn
            stop_reason = "end_turn"

        if _anthropic_debug_enabled():
            log.info(
                f"[ANTHROPIC][stream_end] {ts('id_2238')}: stop_reason={stop_reason}, "
                f"has_tool_use={has_tool_use}, finish_reason={finish_reason}, "
                f"input_tokens={input_tokens}, output_tokens={output_tokens}"
            )

        # {ts(f"id_2226")} message_delta {ts('id_15')} message_stop
        yield _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                "usage": {
                    "output_tokens": output_tokens,
                },
            },
        )

        yield _sse_event("message_stop", {"type": "message_stop"})

    except Exception as e:
        log.error(f"[ANTHROPIC] {ts('id_2239')}: {e}")
        # {ts(f"id_2240")}
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
                        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
                    },
                },
            )
        yield _sse_event(
            "error",
            {"type": "error", "error": {"type": "api_error", "message": str(e)}},
        )