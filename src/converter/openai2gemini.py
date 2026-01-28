from src.i18n import ts
"""
OpenAI Transfer Module - Handles conversion between OpenAI and Gemini API formats
{ts(f"id_2597")}openai-router{ts('id_2595')}OpenAI{ts('id_2596')}Gemini{ts('id_2594')}
"""

import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, Union

from pypinyin import Style, lazy_pinyin

from src.converter.thoughtSignature_fix import (
    encode_tool_id_with_signature,
    decode_tool_id_and_signature,
)
from src.converter.utils import merge_system_messages

from log import log

def _convert_usage_metadata(usage_metadata: Dict[str, Any]) -> Dict[str, int]:
    """
    {ts(f"id_101")}Gemini{ts('id_61')}usageMetadata{ts('id_188f')}OpenAI{ts('id_2128')}usage{ts('id_2018')}

    Args:
        usage_metadata: Gemini API{ts(f"id_61")}usageMetadata{ts('id_2018')}

    Returns:
        OpenAI{ts(f"id_2128")}usage{ts('id_2598')}usage{ts('id_2599')}None
    """
    if not usage_metadata:
        return None

    return {
        "prompt_tokens": usage_metadata.get("promptTokenCount", 0),
        "completion_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0),
    }


def _build_message_with_reasoning(role: str, content: str, reasoning_content: str) -> dict:
    f"""{ts('id_2600')}"""
    message = {"role": role, "content": content}

    # {ts(f"id_2098")}thinking tokens{ts('id_2601')}reasoning_content
    if reasoning_content:
        message["reasoning_content"] = reasoning_content

    return message


def _map_finish_reason(gemini_reason: str) -> str:
    """
    {ts(f"id_101")}Gemini{ts('id_2602')}OpenAI{ts('id_2435')}

    Args:
        gemini_reason: {ts(f"id_2604")}Gemini API{ts('id_2603')}

    Returns:
        OpenAI{ts(f"id_2605")}
    """
    if gemini_reason == "STOP":
        return "stop"
    elif gemini_reason == "MAX_TOKENS":
        return "length"
    elif gemini_reason in ["SAFETY", "RECITATION"]:
        return "content_filter"
    else:
        # {ts(f"id_2608")} None {ts('id_2607')} finishReason{ts('id_2134')} "stop" {ts('id_2606')}
        # {ts(f"id_2610")} None {ts('id_2611')} MCP {ts('id_2609')}
        return "stop"


# ==================== Tool Conversion Functions ====================


def _normalize_function_name(name: str) -> str:
    """
    {ts(f"id_2612")} Gemini API {ts('id_2119')}

    {ts(f"id_2613")}
    - {ts(f"id_2614")}
    - {ts(f"id_2617")} a-z, A-Z, 0-9, {ts('id_2618')}, {ts('id_2616')}, {ts('id_2615')}
    - {ts(f"id_2619")} 64 {ts('id_1422')}

    {ts(f"id_2620")}
    1. {ts(f"id_2621")}
    2. {ts(f"id_2622")}
    3. {ts(f"id_2624")}/{ts('id_2623')}
    4. {ts(f"id_2625")} 64 {ts('id_1422')}

    Args:
        name: {ts(f"id_2626")}

    Returns:
        {ts(f"id_2627")}
    """
    import re

    if not name:
        return "_unnamed_function"

    # {ts(f"id_4521")}{ts('id_2628')}
    if re.search(r"[\u4e00-\u9fff]", name):
        try:
            parts = []
            for char in name:
                if "\u4e00" <= char <= "\u9fff":
                    # {ts(f"id_2621")}
                    pinyin = lazy_pinyin(char, style=Style.NORMAL)
                    parts.append("".join(pinyin))
                else:
                    parts.append(char)
            normalized = "".join(parts)
        except ImportError:
            log.warning("pypinyin not installed, cannot convert Chinese characters to pinyin")
            normalized = name
    else:
        normalized = name

    # {ts(f"id_4522")}{ts('id_2629')}
    # {ts(f"id_2630")}a-z, A-Z, 0-9, _, ., -
    normalized = re.sub(r"[^a-zA-Z0-9_.\-]", "_", normalized)

    # {ts(f"id_4523")}{ts('id_2631')}
    if normalized and not (normalized[0].isalpha() or normalized[0] == "_"):
        # {ts(f"id_2632")}
        normalized = "_" + normalized

    # {ts(f"id_4524")}{ts('id_2633')} 64 {ts('id_1422')}
    if len(normalized) > 64:
        normalized = normalized[:64]

    # {ts(f"id_4525")}{ts('id_2634')}
    if not normalized:
        normalized = "_unnamed_function"

    return normalized


def _resolve_ref(ref: str, root_schema: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    {ts(f"id_2224")} $ref {ts('id_1828')}
    
    Args:
        ref: {ts(f"id_2635")} "#/definitions/MyType"
        root_schema: {ts(f"id_2636")} schema {ts('id_1509')}
        
    Returns:
        {ts(f"id_1457")} schema{ts('id_2637')} None
    """
    if not ref.startswith('#/'):
        return None
    
    path = ref[2:].split('/')
    current = root_schema
    
    for segment in path:
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return None
    
    return current if isinstance(current, dict) else None


def _clean_schema_for_claude(schema: Any, root_schema: Optional[Dict[str, Any]] = None, visited: Optional[set] = None) -> Any:
    """
    {ts(f"id_2045")} JSON Schema{ts('id_2639')} Claude API {ts('id_2638')} JSON Schema draft 2020-12{ts('id_292')}

    {ts(f"id_2640")}
    1. {ts(f"id_2224")} $ref {ts('id_1828')}
    2. {ts(f"id_2641")} allOf {ts('id_2642')} schema
    3. {ts(f"id_2099")} anyOf {ts('id_2643')}
    4. {ts(f"id_2645")} JSON Schema {ts('id_2644')}
    5. {ts(f"id_590")} array {ts('id_61')} items
    6. {ts(f"id_2045")} Claude {ts('id_2646')}

    Args:
        schema: JSON Schema {ts(f"id_1509")}
        root_schema: {ts(f"id_2636")} schema{ts('id_2647')} $ref{ts('id_292')}
        visited: {ts(f"id_2648")}

    Returns:
        {ts(f"id_2047")} schema
    """
    # {ts(f"id_2649")}
    if not isinstance(schema, dict):
        return schema

    # {ts(f"id_1111")}
    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()

    # {ts(f"id_2650")}
    schema_id = id(schema)
    if schema_id in visited:
        return schema
    visited.add(schema_id)

    # {ts(f"id_2518")}
    result = {}

    # 1. {ts(f"id_590")} $ref
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], root_schema)
        if resolved:
            import copy
            result = copy.deepcopy(resolved)
            for key, value in schema.items():
                if key != "$ref":
                    result[key] = value
            schema = result
            result = {}

    # 2. {ts(f"id_590")} allOf{ts('id_2651')} schema{ts('id_292')}
    if "allOf" in schema:
        all_of_schemas = schema["allOf"]
        for item in all_of_schemas:
            cleaned_item = _clean_schema_for_claude(item, root_schema, visited)

            if "properties" in cleaned_item:
                if "properties" not in result:
                    result["properties"] = {}
                result["properties"].update(cleaned_item["properties"])

            if "required" in cleaned_item:
                if "required" not in result:
                    result["required"] = []
                result["required"].extend(cleaned_item["required"])

            for key, value in cleaned_item.items():
                if key not in ["properties", "required"]:
                    result[key] = value

        for key, value in schema.items():
            if key not in ["allOf", "properties", "required"]:
                result[key] = value
            elif key in ["properties", "required"] and key not in result:
                result[key] = value
    else:
        result = dict(schema)

    # 3. {ts(f"id_590")} type {ts('id_2652')} ["string", "null"]{ts('id_292')}
    if "type" in result:
        type_value = result["type"]
        if isinstance(type_value, list):
            # Claude {ts(f"id_56")} type {ts('id_2653')}
            pass

    # 4. {ts(f"id_590")} array {ts('id_61')} items
    if result.get("type") == "array":
        if "items" not in result:
            result["items"] = {}
        elif isinstance(result["items"], list):
            # Tuple {ts(f"id_2654")}
            tuple_items = result["items"]
            first_type = tuple_items[0].get("type") if tuple_items else None
            is_homogeneous = all(item.get("type") == first_type for item in tuple_items)

            if is_homogeneous and first_type:
                result["items"] = _clean_schema_for_claude(tuple_items[0], root_schema, visited)
            else:
                # {ts(f"id_2655")} anyOf {ts('id_2656')}
                result["items"] = {
                    "anyOf": [_clean_schema_for_claude(item, root_schema, visited) for item in tuple_items]
                }
        else:
            result["items"] = _clean_schema_for_claude(result["items"], root_schema, visited)

    # 5. {ts(f"id_590")} anyOf{ts('id_2658')} anyOf{ts('id_2657')}
    if "anyOf" in result:
        result["anyOf"] = [_clean_schema_for_claude(item, root_schema, visited) for item in result["anyOf"]]

    # 6. {ts(f"id_2045")} Claude {ts('id_2659')} JSON Schema 2020-12{ts('id_292')}
    # Claude API {ts(f"id_2660")}
    unsupported_keys = {
        "title", "$schema", "strict",
        f"additionalItems",  # {ts('id_2661')} items {ts('id_2662')}
        f"exclusiveMaximum", "exclusiveMinimum",  # {ts('id_429')} 2020-12 {ts('id_2663')}
        f"$defs", "definitions",  # {ts('id_2044')} definitions {ts('id_2664')}
        "example", "examples", "readOnly", "writeOnly",
        f"const",  # const {ts('id_2665')}
        "contentEncoding", "contentMediaType",
        f"oneOf",  # oneOf {ts('id_2666')} anyOf {ts('id_2662')}
    }

    for key in list(result.keys()):
        if key in unsupported_keys:
            del result[key]

    # {ts(f"id_2667")} additionalProperties{ts('id_2539')}
    if "additionalProperties" in result and isinstance(result["additionalProperties"], dict):
        result["additionalProperties"] = _clean_schema_for_claude(result["additionalProperties"], root_schema, visited)

    # 7. {ts(f"id_2667")} properties
    if "properties" in result:
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            cleaned_props[prop_name] = _clean_schema_for_claude(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props

    # 8. {ts(f"id_2670")} type {ts('id_2668')} properties {ts('id_2669')} type{ts('id_292')}
    if "properties" in result and "type" not in result:
        result["type"] = "object"

    # 9. {ts(f"id_2671")} required {ts('id_2465')}
    if "required" in result and isinstance(result["required"], list):
        result["required"] = list(dict.fromkeys(result["required"]))

    return result


def _clean_schema_for_gemini(schema: Any, root_schema: Optional[Dict[str, Any]] = None, visited: Optional[set] = None) -> Any:
    """
    {ts(f"id_2045")} JSON Schema{ts('id_2639')} Gemini {ts('id_2672')}

    {ts(f"id_583")} worker.mjs {ts('id_61')} transformOpenApiSchemaToGemini {ts('id_2673')}

    {ts(f"id_2640")}
    1. {ts(f"id_2224")} $ref {ts('id_1828')}
    2. {ts(f"id_2641")} allOf {ts('id_2642')} schema
    3. {ts(f"id_2099")} anyOf {ts('id_2432')} enum{ts('id_2674')}
    4. {ts(f"id_2675")}string -> STRING{ts('id_292')}
    5. {ts(f"id_590")} ARRAY {ts('id_61')} items{ts('id_2676')} Tuple{ts('id_292')}
    6. {ts(f"id_101")} default {ts('id_2677')} description
    7. {ts(f"id_2678")}

    Args:
        schema: JSON Schema {ts(f"id_1509")}
        root_schema: {ts(f"id_2636")} schema{ts('id_2647')} $ref{ts('id_292')}
        visited: {ts(f"id_2648")}

    Returns:
        {ts(f"id_2047")} schema
    """
    # {ts(f"id_2649")}
    if not isinstance(schema, dict):
        return schema
    
    # {ts(f"id_1111")}
    if root_schema is None:
        root_schema = schema
    if visited is None:
        visited = set()
    
    # {ts(f"id_2650")}
    schema_id = id(schema)
    if schema_id in visited:
        return schema
    visited.add(schema_id)
    
    # {ts(f"id_2518")}
    result = {}
    
    # 1. {ts(f"id_590")} $ref
    if "$ref" in schema:
        resolved = _resolve_ref(schema["$ref"], root_schema)
        if resolved:
            # {ts(f"id_2679")} schema {ts('id_2680')} schema
            import copy
            result = copy.deepcopy(resolved)
            # {ts(f"id_392")} schema {ts('id_2681')}
            for key, value in schema.items():
                if key != "$ref":
                    result[key] = value
            schema = result
            result = {}
    
    # 2. {ts(f"id_590")} allOf{ts('id_2651')} schema{ts('id_292')}
    if "allOf" in schema:
        all_of_schemas = schema["allOf"]
        for item in all_of_schemas:
            cleaned_item = _clean_schema_for_gemini(item, root_schema, visited)
            
            # {ts(f"id_2641")} properties
            if "properties" in cleaned_item:
                if "properties" not in result:
                    result["properties"] = {}
                result["properties"].update(cleaned_item["properties"])
            
            # {ts(f"id_2641")} required
            if "required" in cleaned_item:
                if "required" not in result:
                    result["required"] = []
                result["required"].extend(cleaned_item["required"])
            
            # {ts(f"id_2682")}
            for key, value in cleaned_item.items():
                if key not in ["properties", "required"]:
                    result[key] = value
        
        # {ts(f"id_2683")}
        for key, value in schema.items():
            if key not in ["allOf", "properties", "required"]:
                result[key] = value
            elif key in ["properties", "required"] and key not in result:
                result[key] = value
    else:
        # {ts(f"id_2684")}
        result = dict(schema)
    
    # 3. {ts(f"id_2685")}
    # {ts(f"id_1288")}Gemini API {ts('id_61')} type {ts('id_2686')}
    if "type" in result:
        type_value = result["type"]

        # {ts(f"id_2183")} type {ts('id_2687')} null{ts('id_292')}
        if isinstance(type_value, list):
            primary_type = next((t for t in type_value if t != "null"), None)
            type_value = primary_type if primary_type else f"STRING"  # {ts('id_2688')} STRING

        # {ts(f"id_2689")}
        type_map = {
            "string": "STRING",
            "number": "NUMBER",
            "integer": "INTEGER",
            "boolean": "BOOLEAN",
            "array": "ARRAY",
            "object": "OBJECT",
        }

        if isinstance(type_value, str) and type_value.lower() in type_map:
            # {ts(f"id_683")} result["type"] {ts('id_2690')}
            result["type"] = type_map[type_value.lower()]
        else:
            # {ts(f"id_2691")}
            del result["type"]
    
    # 4. {ts(f"id_590")} ARRAY {ts('id_61')} items
    if result.get("type") == "ARRAY":
        if "items" not in result:
            # {ts(f"id_2389")} items{ts('id_2692')}
            result["items"] = {}
        elif isinstance(result["items"], list):
            # Tuple {ts(f"id_2694")}items {ts('id_2693')}
            tuple_items = result["items"]
            
            # {ts(f"id_2695")} description
            tuple_types = [item.get("type", "any") for item in tuple_items]
            tuple_desc = f"(Tuple: [{', '.join(tuple_types)}])"
            
            original_desc = result.get("description", "")
            result["description"] = f"{original_desc} {tuple_desc}".strip()
            
            # {ts(f"id_2696")}
            first_type = tuple_items[0].get("type") if tuple_items else None
            is_homogeneous = all(item.get("type") == first_type for item in tuple_items)
            
            if is_homogeneous and first_type:
                # {ts(f"id_2697")} List<Type>
                result["items"] = _clean_schema_for_gemini(tuple_items[0], root_schema, visited)
            else:
                # {ts(f"id_2699")}Gemini {ts('id_2698')} {}
                result["items"] = {}
        else:
            # {ts(f"id_2667")} items
            result["items"] = _clean_schema_for_gemini(result["items"], root_schema, visited)
    
    # 5. {ts(f"id_590")} anyOf{ts('id_2700')} enum{ts('id_292')}
    if "anyOf" in result:
        any_of_schemas = result["anyOf"]
        
        # {ts(f"id_2701")} schema
        cleaned_any_of = [_clean_schema_for_gemini(item, root_schema, visited) for item in any_of_schemas]
        
        # {ts(f"id_2702")} enum
        if all("const" in item for item in cleaned_any_of):
            enum_values = [
                str(item["const"]) 
                for item in cleaned_any_of 
                if item.get("const") not in ["", None]
            ]
            if enum_values:
                result["type"] = "STRING"
                result["enum"] = enum_values
        elif "type" not in result:
            # {ts(f"id_2150")} enum{ts('id_2703')}
            first_valid = next((item for item in cleaned_any_of if item.get("type") or item.get("enum")), None)
            if first_valid:
                result.update(first_valid)
        
        # {ts(f"id_753")} anyOf
        del result["anyOf"]
    
    # 6. {ts(f"id_101")} default {ts('id_2677')} description
    if "default" in result:
        default_value = result["default"]
        original_desc = result.get("description", "")
        result["description"] = f"{original_desc} (Default: {json.dumps(default_value)})".strip()
        del result["default"]
    
    # 7. {ts(f"id_2678")}
    unsupported_keys = {
        "title", "$schema", "$ref", "strict", "exclusiveMaximum",
        "exclusiveMinimum", "additionalProperties", "oneOf", "allOf",
        "$defs", "definitions", "example", "examples", "readOnly",
        "writeOnly", "const", "additionalItems", "contains",
        "patternProperties", "dependencies", "propertyNames",
        "if", "then", "else", "contentEncoding", "contentMediaType"
    }
    
    for key in list(result.keys()):
        if key in unsupported_keys:
            del result[key]
    
    # 8. {ts(f"id_2667")} properties
    if "properties" in result:
        cleaned_props = {}
        for prop_name, prop_schema in result["properties"].items():
            cleaned_props[prop_name] = _clean_schema_for_gemini(prop_schema, root_schema, visited)
        result["properties"] = cleaned_props
    
    # 9. {ts(f"id_2670")} type {ts('id_2668')} properties {ts('id_2669')} type{ts('id_292')}
    if "properties" in result and "type" not in result:
        result["type"] = "OBJECT"
    
    # 10. {ts(f"id_2671")} required {ts('id_2465')}
    if "required" in result and isinstance(result["required"], list):
        result[f"required"] = list(dict.fromkeys(result["required"]))  # {ts('id_2704')}
    
    return result


def fix_tool_call_args_types(
    args: Dict[str, Any],
    parameters_schema: Dict[str, Any]
) -> Dict[str, Any]:
    """
    {ts(f"id_2706")} schema {ts('id_2705')}
    
    {ts(f"id_2707")} "5" {ts('id_2708')} 5{ts('id_2709f')} schema {ts('id_2642')} type {ts('id_2710')}
    
    Args:
        args: {ts(f"id_2711")}
        parameters_schema: {ts(f"id_2712")} parameters schema
        
    Returns:
        {ts(f"id_2713")}
    """
    if not args or not parameters_schema:
        return args
    
    properties = parameters_schema.get("properties", {})
    if not properties:
        return args
    
    fixed_args = {}
    for key, value in args.items():
        if key not in properties:
            # {ts(f"id_2715")} schema {ts('id_2714')}
            fixed_args[key] = value
            continue
        
        param_schema = properties[key]
        param_type = param_schema.get("type")
        
        # {ts(f"id_2136")} schema {ts('id_2716')}
        if param_type == "number" or param_type == "integer":
            # {ts(f"id_2717")}
            if isinstance(value, str):
                try:
                    if param_type == "integer":
                        fixed_args[key] = int(value)
                    else:
                        # {ts(f"id_2719")} float{ts('id_2718')} int
                        num_value = float(value)
                        fixed_args[key] = int(num_value) if num_value.is_integer() else num_value
                    log.debug(f"[OPENAI2GEMINI] {ts('id_2720')}: {key} '{value}' -> {fixed_args[key]} ({param_type})")
                except (ValueError, AttributeError):
                    # {ts(f"id_2721")}
                    fixed_args[key] = value
                    log.warning(f"[OPENAI2GEMINI] {ts('id_2722')} {key} {ts('id_2723f')} '{value}' {ts('id_188')} {param_type}")
            else:
                fixed_args[key] = value
        elif param_type == "boolean":
            # {ts(f"id_2724")}
            if isinstance(value, str):
                if value.lower() in ("true", "1", "yes"):
                    fixed_args[key] = True
                elif value.lower() in ("false", "0", "no"):
                    fixed_args[key] = False
                else:
                    fixed_args[key] = value
                if fixed_args[key] != value:
                    log.debug(f"[OPENAI2GEMINI] {ts('id_2720')}: {key} '{value}' -> {fixed_args[key]} (boolean)")
            else:
                fixed_args[key] = value
        elif param_type == "string":
            # {ts(f"id_2725")}
            if not isinstance(value, str):
                fixed_args[key] = str(value)
                log.debug(f"[OPENAI2GEMINI] {ts('id_2720')}: {key} {value} -> '{fixed_args[key]}' (string)")
            else:
                fixed_args[key] = value
        else:
            # {ts(f"id_2727")}array, object {ts('id_2726')}
            fixed_args[key] = value
    
    return fixed_args


def convert_openai_tools_to_gemini(openai_tools: List, model: str = "") -> List[Dict[str, Any]]:
    """
    {ts(f"id_101")} OpenAI tools {ts('id_2455')} Gemini functionDeclarations {ts('id_57')}

    Args:
        openai_tools: OpenAI {ts(f"id_2728")} Pydantic {ts('id_2729')}
        model: {ts(f"id_2730")} Claude {ts('id_2729')}

    Returns:
        Gemini {ts(f"id_2731")}
    """
    if not openai_tools:
        return []

    # {ts(f"id_2732")} Claude {ts('id_794')}
    is_claude_model = "claude" in model.lower()

    function_declarations = []

    for tool in openai_tools:
        if tool.get("type") != "function":
            log.warning(f"Skipping non-function tool type: {tool.get('type')}")
            continue

        function = tool.get("function")
        if not function:
            log.warning("Tool missing 'function' field")
            continue

        # {ts(f"id_2733")}
        original_name = function.get("name")
        if not original_name:
            log.warning("Tool missing 'name' field, using default")
            original_name = "_unnamed_function"

        normalized_name = _normalize_function_name(original_name)

        # {ts(f"id_2734")}
        if normalized_name != original_name:
            log.debug(f"Function name normalized: '{original_name}' -> '{normalized_name}'")

        # {ts(f"id_1475")} Gemini function declaration
        declaration = {
            "name": normalized_name,
            "description": function.get("description", ""),
        }

        # {ts(f"id_2736")}- {ts('id_2735')}
        if "parameters" in function:
            if is_claude_model:
                cleaned_params = _clean_schema_for_claude(function["parameters"])
                log.debug(f"[OPENAI2GEMINI] Using Claude schema cleaning for tool: {normalized_name}")
            else:
                cleaned_params = _clean_schema_for_gemini(function["parameters"])

            if cleaned_params:
                declaration["parameters"] = cleaned_params

        function_declarations.append(declaration)

    if not function_declarations:
        return []

    # Gemini {ts(f"id_2737")} functionDeclarations
    return [{"functionDeclarations": function_declarations}]


def convert_tool_choice_to_tool_config(tool_choice: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    {ts(f"id_101")} OpenAI tool_choice {ts('id_188')} Gemini toolConfig

    Args:
        tool_choice: OpenAI {ts(f"id_2128")} tool_choice

    Returns:
        Gemini {ts(f"id_2128")} toolConfig
    """
    if isinstance(tool_choice, str):
        if tool_choice == "auto":
            return {"functionCallingConfig": {"mode": "AUTO"}}
        elif tool_choice == "none":
            return {"functionCallingConfig": {"mode": "NONE"}}
        elif tool_choice == "required":
            return {"functionCallingConfig": {"mode": "ANY"}}
    elif isinstance(tool_choice, dict):
        # {"type": "function", "function": {"name": "my_function"}}
        if tool_choice.get("type") == "function":
            function_name = tool_choice.get("function", {}).get("name")
            if function_name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [function_name],
                    }
                }

    # {ts(f"id_2738")} AUTO {ts('id_407')}
    return {"functionCallingConfig": {"mode": "AUTO"}}


def convert_tool_message_to_function_response(message, all_messages: List = None) -> Dict[str, Any]:
    """
    {ts(f"id_101")} OpenAI {ts('id_61')} tool role {ts('id_283')} Gemini functionResponse

    Args:
        message: OpenAI {ts(f"id_2739")}
        all_messages: {ts(f"id_2740")} tool_call_id {ts('id_2741')}

    Returns:
        Gemini {ts(f"id_2128")} functionResponse part
    """
    # {ts(f"id_712")} name {ts('id_2018')}
    name = getattr(message, "name", None)
    encoded_tool_call_id = getattr(message, "tool_call_id", None) or ""

    # {ts(f"id_2109")}ID{ts('id_1748')}functionResponse{ts('id_2120')}
    original_tool_call_id, _ = decode_tool_id_and_signature(encoded_tool_call_id)

    # {ts(f"id_2744")} name{ts('id_2743')} all_messages {ts('id_2742')} tool_call_id
    # {ts(f"id_2746")}ID{ts('id_2745')}ID
    if not name and encoded_tool_call_id and all_messages:
        for msg in all_messages:
            if getattr(msg, "role", None) == "assistant" and hasattr(msg, "tool_calls") and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    if getattr(tool_call, "id", None) == encoded_tool_call_id:
                        func = getattr(tool_call, "function", None)
                        if func:
                            name = getattr(func, "name", None)
                            break
                if name:
                    break

    # {ts(f"id_2747")} name{ts('id_2748')}
    if not name:
        name = "unknown_function"
        log.warning(f"Tool message missing function name, using default: {name}")

    try:
        # {ts(f"id_2749")} content {ts('id_2750')} JSON
        response_data = (
            json.loads(message.content) if isinstance(message.content, str) else message.content
        )
    except (json.JSONDecodeError, TypeError):
        # {ts(f"id_2751")} JSON{ts('id_2752')}
        response_data = {"result": str(message.content)}

    # {ts(f"id_683")} response_data {ts('id_2753')}Gemini API {ts('id_2119')} response {ts('id_2754')}
    if not isinstance(response_data, dict):
        response_data = {"result": response_data}

    return {"functionResponse": {"id": original_tool_call_id, "name": name, "response": response_data}}


def _reverse_transform_value(value: Any) -> Any:
    """
    {ts(f"id_2756")}Gemini {ts('id_2755')}
    
    {ts(f"id_583")} worker.mjs {ts('id_61')} reverseTransformValue
    
    Args:
        value: {ts(f"id_2757")}
        
    Returns:
        {ts(f"id_2758")}
    """
    if not isinstance(value, str):
        return value
    
    # {ts(f"id_2759")}
    if value == 'true':
        return True
    if value == 'false':
        return False
    
    # null
    if value == 'null':
        return None
    
    # {ts(f"id_2760")}
    if value.strip() and not value.startswith('0') and value.replace('.', '', 1).replace('-', '', 1).replace('+', '', 1).isdigit():
        try:
            # {ts(f"id_2761")}
            num_value = float(value)
            # {ts(f"id_2762")} int
            if num_value == int(num_value):
                return int(num_value)
            return num_value
        except ValueError:
            pass
    
    # {ts(f"id_2763")}
    return value


def _reverse_transform_args(args: Any) -> Any:
    """
    {ts(f"id_2764")}
    
    {ts(f"id_583")} worker.mjs {ts('id_61')} reverseTransformArgs
    
    Args:
        args: {ts(f"id_2765")}
        
    Returns:
        {ts(f"id_2766")}
    """
    if not isinstance(args, (dict, list)):
        return args
    
    if isinstance(args, list):
        return [_reverse_transform_args(item) for item in args]
    
    # {ts(f"id_2767")}
    result = {}
    for key, value in args.items():
        if isinstance(value, (dict, list)):
            result[key] = _reverse_transform_args(value)
        else:
            result[key] = _reverse_transform_value(value)
    
    return result


def extract_tool_calls_from_parts(
    parts: List[Dict[str, Any]], is_streaming: bool = False
) -> Tuple[List[Dict[str, Any]], str]:
    """
    {ts(f"id_1731")} Gemini response parts {ts('id_2768')}

    Args:
        parts: Gemini response {ts(f"id_61")} parts {ts('id_2465')}
        is_streaming: {ts(f"id_2769")} index {ts('id_1608')}

    Returns:
        (tool_calls, text_content) {ts(f"id_1605")}
    """
    tool_calls = []
    text_content = ""

    for idx, part in enumerate(parts):
        # {ts(f"id_2770")}
        if "functionCall" in part:
            function_call = part["functionCall"]
            # {ts(f"id_2772")}ID{ts('id_2771')}ID
            original_id = function_call.get("id") or f"call_{uuid.uuid4().hex[:24]}"
            # {ts(f"id_101")}thoughtSignature{ts('id_2774')}ID{ts('id_2773')}
            signature = part.get("thoughtSignature")
            encoded_id = encode_tool_id_with_signature(original_id, signature)

            # {ts(f"id_2775")}
            args = function_call.get("args", {})
            # {ts(f"id_2776")}
            args = _reverse_transform_args(args)

            tool_call = {
                "id": encoded_id,
                "type": "function",
                "function": {
                    "name": function_call.get("name", "nameless_function"),
                    "arguments": json.dumps(args),
                },
            }
            # {ts(f"id_2777")} index {ts('id_2018')}
            if is_streaming:
                tool_call["index"] = idx
            tool_calls.append(tool_call)

        # {ts(f"id_2778")} thinking tokens{ts('id_292')}
        elif "text" in part and not part.get("thought", False):
            text_content += part["text"]

    return tool_calls, text_content


def extract_images_from_content(content: Any) -> Dict[str, Any]:
    """
    {ts(f"id_1731")} OpenAI content {ts('id_2779')}
    
    Args:
        content: OpenAI {ts(f"id_2781")} content {ts('id_2780')}
    
    Returns:
        {ts(f"id_906")} text {ts('id_15')} images {ts('id_2782')}
    """
    result = {"text": "", "images": []}

    if isinstance(content, str):
        result["text"] = content
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    result["text"] += item.get("text", "")
                elif item.get("type") == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    # {ts(f"id_2224")} data:image/png;base64,xxx {ts('id_57')}
                    if image_url.startswith("data:image/"):
                        import re
                        match = re.match(r"^data:image/(\w+);base64,(.+)$", image_url)
                        if match:
                            mime_type = match.group(1)
                            base64_data = match.group(2)
                            result["images"].append({
                                "inlineData": {
                                    "mimeType": f"image/{mime_type}",
                                    "data": base64_data
                                }
                            })

    return result

async def convert_openai_to_gemini_request(openai_request: Dict[str, Any]) -> Dict[str, Any]:
    """
    {ts(f"id_101")} OpenAI {ts('id_2154')} Gemini {ts('id_2155')}

    {ts(f"id_2158")}: {ts('id_2783')},{ts('id_2784')} normalize_gemini_request {ts('id_2157')}
    ({ts(f"id_716")} thinking config, search tools, {ts('id_2785')})

    Args:
        openai_request: OpenAI {ts(f"id_2161")},{ts('id_906')}:
            - messages: {ts(f"id_2786")}
            - temperature, top_p, max_tokens, stop {ts(f"id_2787")}
            - tools, tool_choice ({ts(f"id_54")})
            - response_format ({ts(f"id_54")})

    Returns:
        Gemini {ts(f"id_2161")},{ts('id_906')}:
            - contents: {ts(f"id_2163")}
            - generationConfig: {ts(f"id_2164")}
            - systemInstruction: {ts(f"id_2165")} ({ts('id_2098')})
            - tools, toolConfig ({ts(f"id_2098")})
    """
    # {ts(f"id_2169")}system{ts('id_2168')}
    openai_request = await merge_system_messages(openai_request)

    contents = []

    # {ts(f"id_2788")}
    messages = openai_request.get("messages", [])
    
    # {ts(f"id_1475")} tool_call_id -> (name, original_id, signature) {ts('id_2106')}
    tool_call_mapping = {}
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                encoded_id = tc.get("id", "")
                func_name = tc.get("function", {}).get("name") or ""
                if encoded_id:
                    # {ts(f"id_2109")}ID{ts('id_2110')}
                    original_id, signature = decode_tool_id_and_signature(encoded_id)
                    tool_call_mapping[encoded_id] = (func_name, original_id, signature)
    
    # {ts(f"id_2790")} schema {ts('id_2789')}
    tool_schemas = {}
    if "tools" in openai_request and openai_request["tools"]:
        for tool in openai_request["tools"]:
            if tool.get("type") == "function":
                function = tool.get("function", {})
                func_name = function.get("name")
                if func_name:
                    tool_schemas[func_name] = function.get("parameters", {})

    for message in messages:
        role = message.get("role", "user")
        content = message.get("content", "")

        # {ts(f"id_2791")}tool role{ts('id_292')}
        if role == "tool":
            tool_call_id = message.get("tool_call_id", "")
            func_name = message.get("name")

            # {ts(f"id_2792")}
            if tool_call_id in tool_call_mapping:
                func_name, original_id, _ = tool_call_mapping[tool_call_id]
            else:
                # {ts(f"id_2744")}name,{ts('id_2793')}
                if not func_name and tool_call_id:
                    for msg in messages:
                        if msg.get("role") == "assistant" and msg.get("tool_calls"):
                            for tc in msg["tool_calls"]:
                                if tc.get("id") == tool_call_id:
                                    func_name = tc.get("function", {}).get("name")
                                    break
                            if func_name:
                                break

                # {ts(f"id_2318")} tool_call_id {ts('id_2772')} ID
                original_id, _ = decode_tool_id_and_signature(tool_call_id)

            # {ts(f"id_2794")} func_name {ts('id_2795')}
            if not func_name:
                func_name = "unknown_function"
                log.warning(f"Tool message missing function name for tool_call_id={tool_call_id}, using default: {func_name}")

            # {ts(f"id_2796")}
            try:
                response_data = json.loads(content) if isinstance(content, str) else content
            except (json.JSONDecodeError, TypeError):
                response_data = {"result": str(content)}

            # {ts(f"id_683")} response_data {ts('id_2753')}Gemini API {ts('id_2119')} response {ts('id_2754')}
            if not isinstance(response_data, dict):
                response_data = {"result": response_data}

            # {ts(f"id_2117")} ID{ts('id_2797')}
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "id": original_id,
                        "name": func_name,
                        "response": response_data
                    }
                }]
            })
            continue

        # system {ts(f"id_2113")} merge_system_messages {ts('id_2112')}
        if role == "system":
            continue

        # {ts(f"id_101")}OpenAI{ts('id_2798')}Gemini{ts('id_2799')}
        if role == "assistant":
            role = "model"

        # {ts(f"id_2392")}tool_calls
        tool_calls = message.get("tool_calls")
        if tool_calls:
            parts = []

            # {ts(f"id_2800")},{ts('id_2801')}
            if content:
                parts.append({"text": content})

            # {ts(f"id_2802")}
            for tool_call in tool_calls:
                try:
                    args = (
                        json.loads(tool_call["function"]["arguments"])
                        if isinstance(tool_call["function"]["arguments"], str)
                        else tool_call["function"]["arguments"]
                    )
                    
                    # {ts(f"id_2803")} schema {ts('id_2720')}
                    func_name = tool_call["function"]["name"]
                    if func_name in tool_schemas:
                        args = fix_tool_call_args_types(args, tool_schemas[func_name])

                    # {ts(f"id_2804")}ID{ts('id_15')}thoughtSignature
                    encoded_id = tool_call.get("id", "")
                    original_id, signature = decode_tool_id_and_signature(encoded_id)

                    # {ts(f"id_1475")}functionCall part
                    function_call_part = {
                        "functionCall": {
                            "id": original_id,
                            "name": func_name,
                            "args": args
                        }
                    }

                    # {ts(f"id_2098")}thoughtSignature{ts('id_2805')} Gemini API {ts('id_2119')}
                    if signature:
                        function_call_part["thoughtSignature"] = signature
                    else:
                        function_call_part["thoughtSignature"] = "skip_thought_signature_validator"

                    parts.append(function_call_part)
                except (json.JSONDecodeError, KeyError) as e:
                    log.error(f"Failed to parse tool call: {e}")
                    continue

            if parts:
                contents.append({"role": role, "parts": parts})
            continue

        # {ts(f"id_2806")}
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "text":
                    parts.append({"text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url")
                    if image_url:
                        try:
                            mime_type, base64_data = image_url.split(";")
                            _, mime_type = mime_type.split(":")
                            _, base64_data = base64_data.split(",")
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_data,
                                }
                            })
                        except ValueError:
                            continue
            if parts:
                contents.append({"role": role, "parts": parts})
        elif content:
            contents.append({"role": role, "parts": [{"text": content}]})

    # {ts(f"id_2174")}
    generation_config = {}
    model = openai_request.get("model", "")
    
    # {ts(f"id_2807")}
    if "temperature" in openai_request:
        generation_config["temperature"] = openai_request["temperature"]
    if "top_p" in openai_request:
        generation_config["topP"] = openai_request["top_p"]
    if "top_k" in openai_request:
        generation_config["topK"] = openai_request["top_k"]
    if "max_tokens" in openai_request or "max_completion_tokens" in openai_request:
        # max_completion_tokens {ts(f"id_2808")} max_tokens
        max_tokens = openai_request.get("max_completion_tokens") or openai_request.get("max_tokens")
        generation_config["maxOutputTokens"] = max_tokens
    if "stop" in openai_request:
        stop = openai_request["stop"]
        generation_config["stopSequences"] = [stop] if isinstance(stop, str) else stop
    if "frequency_penalty" in openai_request:
        generation_config["frequencyPenalty"] = openai_request["frequency_penalty"]
    if "presence_penalty" in openai_request:
        generation_config["presencePenalty"] = openai_request["presence_penalty"]
    if "n" in openai_request:
        generation_config["candidateCount"] = openai_request["n"]
    if "seed" in openai_request:
        generation_config["seed"] = openai_request["seed"]
    
    # {ts(f"id_590")} response_format
    if "response_format" in openai_request and openai_request["response_format"]:
        response_format = openai_request["response_format"]
        format_type = response_format.get("type")
        
        if format_type == "json_schema":
            # JSON Schema {ts(f"id_407")}
            if "json_schema" in response_format and "schema" in response_format["json_schema"]:
                schema = response_format["json_schema"]["schema"]
                # {ts(f"id_2045")} schema
                generation_config["responseSchema"] = _clean_schema_for_gemini(schema)
                generation_config["responseMimeType"] = "application/json"
        elif format_type == "json_object":
            # JSON Object {ts(f"id_407")}
            generation_config["responseMimeType"] = "application/json"
        elif format_type == "text":
            # Text {ts(f"id_407")}
            generation_config["responseMimeType"] = "text/plain"
            
    # {ts(f"id_2183")}contents{ts('id_2810')},{ts('id_2809')}
    if not contents:
        contents.append({f"role": "user", "parts": [{"text": "{ts('id_2811')}"}]})

    # {ts(f"id_2812")}
    gemini_request = {
        "contents": contents,
        "generationConfig": generation_config
    }

    # {ts(f"id_2183")} merge_system_messages {ts('id_2181')} systemInstruction{ts('id_2182')}
    if "systemInstruction" in openai_request:
        gemini_request["systemInstruction"] = openai_request["systemInstruction"]

    # {ts(f"id_2814")} - {ts('id_2815')} model {ts('id_2813')}
    model = openai_request.get("model", "")
    if "tools" in openai_request and openai_request["tools"]:
        gemini_request["tools"] = convert_openai_tools_to_gemini(openai_request["tools"], model)

    # {ts(f"id_590")}tool_choice
    if "tool_choice" in openai_request and openai_request["tool_choice"]:
        gemini_request["toolConfig"] = convert_tool_choice_to_tool_config(openai_request["tool_choice"])

    return gemini_request


def convert_gemini_to_openai_response(
    gemini_response: Union[Dict[str, Any], Any],
    model: str,
    status_code: int = 200
) -> Dict[str, Any]:
    """
    {ts(f"id_101")} Gemini {ts('id_2185')} OpenAI {ts('id_2186')}

    {ts(f"id_2158")}: {ts('id_2188')} 200 {ts('id_2818f')},{ts('id_2817')},{ts('id_2816')}

    Args:
        gemini_response: Gemini {ts(f"id_2820")} ({ts('id_2819')})
        model: {ts(f"id_1737")}
        status_code: HTTP {ts(f"id_1461")} ({ts('id_7')} 200)

    Returns:
        OpenAI {ts(f"id_2189")},{ts('id_2821')} ({ts('id_2191')} 2xx)
    """
    # {ts(f"id_1648")} 2xx {ts('id_2192')}
    if not (200 <= status_code < 300):
        if isinstance(gemini_response, dict):
            return gemini_response
        else:
            # {ts(f"id_2822")},{ts('id_2823')}
            try:
                if hasattr(gemini_response, "json"):
                    return gemini_response.json()
                elif hasattr(gemini_response, "body"):
                    body = gemini_response.body
                    if isinstance(body, bytes):
                        return json.loads(body.decode())
                    return json.loads(str(body))
                else:
                    return {"error": str(gemini_response)}
            except:
                return {"error": str(gemini_response)}

    # {ts(f"id_2824")}
    if not isinstance(gemini_response, dict):
        try:
            if hasattr(gemini_response, "json"):
                gemini_response = gemini_response.json()
            elif hasattr(gemini_response, "body"):
                body = gemini_response.body
                if isinstance(body, bytes):
                    gemini_response = json.loads(body.decode())
                else:
                    gemini_response = json.loads(str(body))
            else:
                gemini_response = json.loads(str(gemini_response))
        except:
            return {"error": "Invalid response format"}

    # {ts(f"id_590")} GeminiCLI {ts('id_61')} response {ts('id_2193')}
    if "response" in gemini_response:
        gemini_response = gemini_response["response"]

    # {ts(f"id_188")} OpenAI {ts('id_57')}
    choices = []

    for candidate in gemini_response.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")

        # {ts(f"id_101")}Gemini{ts('id_2825')}OpenAI{ts('id_2799')}
        if role == "model":
            role = "assistant"

        # {ts(f"id_2826")}thinking tokens{ts('id_2827')}
        parts = candidate.get("content", {}).get("parts", [])

        # {ts(f"id_2828")}
        tool_calls, text_content = extract_tool_calls_from_parts(parts)

        # {ts(f"id_2829")}
        content_parts = []
        reasoning_parts = []
        
        for part in parts:
            # {ts(f"id_590")} executableCode{ts('id_2830')}
            if "executableCode" in part:
                exec_code = part["executableCode"]
                lang = exec_code.get("language", "python").lower()
                code = exec_code.get("code", "")
                # {ts(f"id_2831")} Markdown {ts('id_2832')}
                content_parts.append(f"\n```{lang}\n{code}\n```\n")
            
            # {ts(f"id_590")} codeExecutionResult{ts('id_2833')}
            elif "codeExecutionResult" in part:
                result = part["codeExecutionResult"]
                outcome = result.get("outcome")
                output = result.get("output", "")
                
                if output:
                    label = "output" if outcome == "OUTCOME_OK" else "error"
                    content_parts.append(f"\n```{label}\n{output}\n```\n")
            
            # {ts(f"id_590")} thought{ts('id_2834')}
            elif part.get("thought", False) and "text" in part:
                reasoning_parts.append(part["text"])
            
            # {ts(f"id_2835")}
            elif "text" in part and not part.get("thought", False):
                # {ts(f"id_2836")} extract_tool_calls_from_parts {ts('id_2837')}
                pass
            
            # {ts(f"id_590")} inlineData{ts('id_2838')}
            elif "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                # {ts(f"id_463")} Markdown {ts('id_57')}
                content_parts.append(f"![gemini-generated-content](data:{mime_type};base64,{base64_data})")
        
        # {ts(f"id_2839")}
        if content_parts:
            # {ts(f"id_2840")}
            additional_content = "\n\n".join(content_parts)
            if text_content:
                text_content = text_content + "\n\n" + additional_content
            else:
                text_content = additional_content
        
        # {ts(f"id_2641")} reasoning content
        reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else ""

        # {ts(f"id_2841")}
        message = {"role": role}

        # {ts(f"id_712")} Gemini {ts('id_61')} finishReason
        gemini_finish_reason = candidate.get("finishReason")
        
        # {ts(f"id_2842")}
        if tool_calls:
            message["tool_calls"] = tool_calls
            message["content"] = text_content if text_content else None
            # {ts(f"id_2203")}STOP{ts('id_2844')} tool_calls{ts('id_2843')} finish_reason
            # {ts(f"id_2845")} SAFETY{ts('id_189')}MAX_TOKENS {ts('id_2204')} tool_calls {ts('id_2205')}
            if gemini_finish_reason == "STOP":
                finish_reason = "tool_calls"
            else:
                finish_reason = _map_finish_reason(gemini_finish_reason)
        else:
            message["content"] = text_content
            finish_reason = _map_finish_reason(gemini_finish_reason)

        # {ts(f"id_848")} reasoning content ({ts('id_2098')})
        if reasoning_content:
            message["reasoning_content"] = reasoning_content

        choices.append({
            "index": candidate.get("index", 0),
            "message": message,
            "finish_reason": finish_reason,
        })

    # {ts(f"id_2099")} usageMetadata
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

    response_data = {
        "id": str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

    if usage:
        response_data["usage"] = usage

    return response_data


def convert_gemini_to_openai_stream(
    gemini_stream_chunk: str,
    model: str,
    response_id: str,
    status_code: int = 200
) -> Optional[str]:
    """
    {ts(f"id_101")} Gemini {ts('id_2846')} OpenAI SSE {ts('id_2212')}

    {ts(f"id_2158")}: {ts('id_2188')} 200 {ts('id_2818f')},{ts('id_2817')},{ts('id_2847')}

    Args:
        gemini_stream_chunk: Gemini {ts(f"id_2848")} ({ts('id_2850')},{ts('id_2849')} "data: {json}" {ts('id_57')})
        model: {ts(f"id_1737")}
        response_id: {ts(f"id_2851")}ID
        status_code: HTTP {ts(f"id_1461")} ({ts('id_7')} 200)

    Returns:
        OpenAI SSE {ts(f"id_2852")} ({ts('id_716')} "data: {json}\n\n"),
        {ts(f"id_2853")} ({ts('id_2191')} 2xx),
        {ts(f"id_413")} None ({ts('id_2854')})
    """
    # {ts(f"id_1648")} 2xx {ts('id_2855')}
    if not (200 <= status_code < 300):
        return gemini_stream_chunk

    # {ts(f"id_2224")} Gemini {ts('id_2223')}
    try:
        # {ts(f"id_2856")} "data: " {ts('id_365')}
        if isinstance(gemini_stream_chunk, bytes):
            if gemini_stream_chunk.startswith(b"data: "):
                payload_str = gemini_stream_chunk[len(b"data: "):].strip().decode("utf-8")
            else:
                payload_str = gemini_stream_chunk.strip().decode("utf-8")
        else:
            if gemini_stream_chunk.startswith("data: "):
                payload_str = gemini_stream_chunk[len("data: "):].strip()
            else:
                payload_str = gemini_stream_chunk.strip()

        # {ts(f"id_2857")}
        if not payload_str:
            return None

        # {ts(f"id_2224")} JSON
        gemini_chunk = json.loads(payload_str)
    except (json.JSONDecodeError, UnicodeDecodeError):
        # {ts(f"id_2859")},{ts('id_2858')}
        return None

    # {ts(f"id_590")} GeminiCLI {ts('id_61')} response {ts('id_2193')}
    if "response" in gemini_chunk:
        gemini_response = gemini_chunk["response"]
    else:
        gemini_response = gemini_chunk

    # {ts(f"id_188")} OpenAI {ts('id_2860')}
    choices = []

    for candidate in gemini_response.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")

        # {ts(f"id_101")}Gemini{ts('id_2825')}OpenAI{ts('id_2799')}
        if role == "model":
            role = "assistant"

        # {ts(f"id_2826")}thinking tokens{ts('id_2827')}
        parts = candidate.get("content", {}).get("parts", [])

        # {ts(f"id_2828")} ({ts('id_2861')} index)
        tool_calls, text_content = extract_tool_calls_from_parts(parts, is_streaming=True)

        # {ts(f"id_2829")}
        content_parts = []
        reasoning_parts = []
        
        for part in parts:
            # {ts(f"id_590")} executableCode{ts('id_2830')}
            if "executableCode" in part:
                exec_code = part["executableCode"]
                lang = exec_code.get("language", "python").lower()
                code = exec_code.get("code", "")
                content_parts.append(f"\n```{lang}\n{code}\n```\n")
            
            # {ts(f"id_590")} codeExecutionResult{ts('id_2833')}
            elif "codeExecutionResult" in part:
                result = part["codeExecutionResult"]
                outcome = result.get("outcome")
                output = result.get("output", "")
                
                if output:
                    label = "output" if outcome == "OUTCOME_OK" else "error"
                    content_parts.append(f"\n```{label}\n{output}\n```\n")
            
            # {ts(f"id_590")} thought{ts('id_2834')}
            elif part.get("thought", False) and "text" in part:
                reasoning_parts.append(part["text"])
            
            # {ts(f"id_2835")}
            elif "text" in part and not part.get("thought", False):
                # {ts(f"id_2836")} extract_tool_calls_from_parts {ts('id_2837')}
                pass
            
            # {ts(f"id_590")} inlineData{ts('id_2838')}
            elif "inlineData" in part:
                inline_data = part["inlineData"]
                mime_type = inline_data.get("mimeType", "image/png")
                base64_data = inline_data.get("data", "")
                content_parts.append(f"![gemini-generated-content](data:{mime_type};base64,{base64_data})")
        
        # {ts(f"id_2839")}
        if content_parts:
            additional_content = "\n\n".join(content_parts)
            if text_content:
                text_content = text_content + "\n\n" + additional_content
            else:
                text_content = additional_content
        
        # {ts(f"id_2641")} reasoning content
        reasoning_content = "\n\n".join(reasoning_parts) if reasoning_parts else ""

        # {ts(f"id_1475")} delta {ts('id_1509')}
        delta = {}

        if tool_calls:
            delta["tool_calls"] = tool_calls
            if text_content:
                delta["content"] = text_content
        elif text_content:
            delta["content"] = text_content

        if reasoning_content:
            delta["reasoning_content"] = reasoning_content

        # {ts(f"id_712")} Gemini {ts('id_61')} finishReason
        gemini_finish_reason = candidate.get("finishReason")
        finish_reason = _map_finish_reason(gemini_finish_reason)
        
        # {ts(f"id_2203")}STOP{ts('id_2202')} tool_calls
        # {ts(f"id_2206")} SAFETY{ts('id_189')}MAX_TOKENS {ts('id_2204')} tool_calls {ts('id_2205')}
        if tool_calls and gemini_finish_reason == "STOP":
            finish_reason = "tool_calls"

        choices.append({
            "index": candidate.get("index", 0),
            "delta": delta,
            "finish_reason": finish_reason,
        })

    # {ts(f"id_2099")} usageMetadata ({ts('id_2862')})
    usage = _convert_usage_metadata(gemini_response.get("usageMetadata"))

    # {ts(f"id_1475")} OpenAI {ts('id_2863')}
    response_data = {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

    # {ts(f"id_2866")} usage {ts('id_2864')} finish_reason {ts('id_2865')} usage
    if usage:
        has_finish_reason = any(choice.get("finish_reason") for choice in choices)
        if has_finish_reason:
            response_data["usage"] = usage

    # {ts(f"id_188")} SSE {ts('id_57')}: "data: {json}\n\n"
    return f"data: {json.dumps(response_data)}\n\n"
