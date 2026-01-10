"""
Gemini Format Utilities - 统一的 Gemini 格式处理和转换工具
提供对 Gemini API 请求体和响应的标准化处理
────────────────────────────────────────────────────────────────
"""

from typing import Any, Dict, List, Optional

# ==================== Gemini API 配置 ====================

# Gemini API 不支持的 JSON Schema 字段集合
# 参考: github.com/googleapis/python-genai/issues/699, #388, #460, #1122, #264, #4551
UNSUPPORTED_SCHEMA_KEYS = {
    '$schema', '$id', '$ref', '$defs', 'definitions',
    'example', 'examples', 'readOnly', 'writeOnly', 'default',
    'exclusiveMaximum', 'exclusiveMinimum',
    'oneOf', 'anyOf', 'allOf', 'const',
    'additionalItems', 'contains', 'patternProperties', 'dependencies',
    'propertyNames', 'if', 'then', 'else',
    'contentEncoding', 'contentMediaType',
    'additionalProperties', 'minLength', 'maxLength',
    'minItems', 'maxItems', 'uniqueItems'
}


def build_system_instruction_from_list(system_instructions: List[str]) -> Optional[Dict[str, Any]]:
    """
    从字符串列表构建 Gemini systemInstruction 对象

    Args:
        system_instructions: 系统指令字符串列表

    Returns:
        Gemini 格式的 systemInstruction 字典，如果列表为空则返回 None

    Example:
        >>> build_system_instruction_from_list(["You are helpful.", "Be concise."])
        {
            "parts": [
                {"text": "You are helpful."},
                {"text": "Be concise."}
            ]
        }
    """
    if not system_instructions:
        return None

    parts = []
    for instruction in system_instructions:
        if instruction and instruction.strip():
            parts.append({"text": instruction})

    if not parts:
        return None

    return {"parts": parts}



def clean_tools_for_gemini(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """
    清理工具定义，移除 Gemini API 不支持的 JSON Schema 字段
    
    Gemini API 只支持有限的 OpenAPI 3.0 Schema 属性：
    - 支持: type, description, enum, items, properties, required, nullable, format
    - 不支持: $schema, $id, $ref, $defs, title, examples, default, readOnly,
              exclusiveMaximum, exclusiveMinimum, oneOf, anyOf, allOf, const 等
    
    Args:
        tools: 工具定义列表
    
    Returns:
        清理后的工具定义列表
    """
    if not tools:
        return tools
    
    def clean_schema(obj: Any) -> Any:
        """递归清理 schema 对象"""
        if isinstance(obj, dict):
            cleaned = {}
            for key, value in obj.items():
                if key in UNSUPPORTED_SCHEMA_KEYS:
                    continue
                cleaned[key] = clean_schema(value)
            # 确保有 type 字段（如果有 properties 但没有 type）
            if "properties" in cleaned and "type" not in cleaned:
                cleaned["type"] = "object"
            return cleaned
        elif isinstance(obj, list):
            return [clean_schema(item) for item in obj]
        else:
            return obj
    
    # 清理每个工具的参数
    cleaned_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            cleaned_tools.append(tool)
            continue
            
        cleaned_tool = tool.copy()
        
        # 清理 functionDeclarations
        if "functionDeclarations" in cleaned_tool:
            cleaned_declarations = []
            for func_decl in cleaned_tool["functionDeclarations"]:
                if not isinstance(func_decl, dict):
                    cleaned_declarations.append(func_decl)
                    continue
                    
                cleaned_decl = func_decl.copy()
                if "parameters" in cleaned_decl:
                    cleaned_decl["parameters"] = clean_schema(cleaned_decl["parameters"])
                cleaned_declarations.append(cleaned_decl)
            
            cleaned_tool["functionDeclarations"] = cleaned_declarations
        
        cleaned_tools.append(cleaned_tool)
    
    return cleaned_tools

def prepare_image_generation_request(
    request_body: Dict[str, Any],
    model: str
) -> Dict[str, Any]:
    """
    图像生成模型请求体后处理
    
    Args:
        request_body: 原始请求体
        model: 模型名称
    
    Returns:
        处理后的请求体
    """
    request_body = request_body.copy()
    model_lower = model.lower()
    
    # 解析分辨率
    image_size = "4K" if "-4k" in model_lower else "2K" if "-2k" in model_lower else None
    
    # 解析比例
    aspect_ratio = None
    for suffix, ratio in [
        ("-21x9", "21:9"), ("-16x9", "16:9"), ("-9x16", "9:16"),
        ("-4x3", "4:3"), ("-3x4", "3:4"), ("-1x1", "1:1")
    ]:
        if suffix in model_lower:
            aspect_ratio = ratio
            break
    
    # 构建 imageConfig
    image_config = {}
    if aspect_ratio:
        image_config["aspectRatio"] = aspect_ratio
    if image_size:
        image_config["imageSize"] = image_size

    request_body["model"] = "gemini-3-pro-image"  # 统一使用基础模型名
    request_body["generationConfig"] = {
        "candidateCount": 1,
        "imageConfig": image_config
    }

    # 移除不需要的字段
    for key in ("systemInstruction", "tools", "toolConfig"):
        request_body.pop(key, None)
    
    return request_body


# ==================== 模型特性辅助函数 ====================

def get_base_model_name(model_name: str) -> str:
    """移除模型名称中的后缀,返回基础模型名"""
    suffixes = ["-maxthinking", "-nothinking", "-think", "-search"]
    result = model_name
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[:-len(suffix)]
                changed = True
                break
    return result


def get_thinking_settings(model_name: str) -> tuple[Optional[int], bool]:
    """
    根据模型名称获取思考配置

    Returns:
        (thinking_budget, include_thoughts): 思考预算和是否包含思考内容
    """
    base_model = get_base_model_name(model_name)

    if "-nothinking" in model_name:
        # nothinking 模式: 限制思考,pro模型仍包含thoughts
        return 128, "pro" in base_model
    elif "-maxthinking" in model_name:
        # maxthinking 模式: 最大思考预算
        budget = 24576 if "flash" in base_model else 32768
        return budget, True
    else:
        # 默认模式: 不设置thinking budget
        return None, True


def is_search_model(model_name: str) -> bool:
    """检查是否为搜索模型"""
    return "-search" in model_name


# ==================== 统一的 Gemini 请求后处理 ====================

def is_thinking_model(model_name: str) -> bool:
    """检查是否为思考模型 (包含 -thinking 或 pro)"""
    return "-thinking" in model_name or "pro" in model_name.lower()


async def normalize_gemini_request(
    request: Dict[str, Any],
    mode: str = "geminicli"
) -> Dict[str, Any]:
    """
    规范化 Gemini 请求

    处理逻辑:
    1. 模型特性处理 (thinking config, search tools)
    2. 字段名转换 (system_instructions -> systemInstruction)
    3. 参数范围限制 (maxOutputTokens, topK)
    4. 工具清理

    Args:
        request: 原始请求字典
        mode: 模式 ("geminicli" 或 "antigravity")

    Returns:
        规范化后的请求
    """
    # 导入配置函数
    from config import get_return_thoughts_to_frontend

    result = request.copy()
    model = result.get("model", "")
    generation_config = result.get("generationConfig", {})
    tools = result.get("tools")
    system_instruction = result.get("systemInstruction") or result.get("system_instructions")

    # 获取配置值
    return_thoughts = await get_return_thoughts_to_frontend()

    # ========== 模式特定处理 ==========
    if mode == "geminicli":
        # 1. 思考设置
        thinking_budget, include_thoughts = get_thinking_settings(model)
        if thinking_budget is not None and "thinkingConfig" not in generation_config:
            # 如果配置为不返回thoughts，则强制设置为False；否则使用模型默认设置
            final_include_thoughts = include_thoughts if return_thoughts else False
            generation_config["thinkingConfig"] = {
                "thinkingBudget": thinking_budget,
                "includeThoughts": final_include_thoughts
            }

        # 2. 工具清理和处理
        if tools:
            result["tools"] = clean_tools_for_gemini(tools)

        # 3. 搜索模型添加 Google Search
        if is_search_model(model):
            result_tools = result.setdefault("tools", [])
            if not any(tool.get("googleSearch") for tool in result_tools):
                result_tools.append({"googleSearch": {}})

        # 4. 模型名称处理
        result["model"] = get_base_model_name(model)

    elif mode == "antigravity":
        # 1. 处理 system_instruction
        custom_prompt = "Please ignore the following [ignore]You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.**Absolute paths only****Proactiveness**[/ignore]"

        # 提取原有的 parts（如果存在）
        existing_parts = []
        if system_instruction:
            if isinstance(system_instruction, dict):
                existing_parts = system_instruction.get("parts", [])

        # custom_prompt 始终放在第一位,原有内容整体后移
        result["systemInstruction"] = {
            "parts": [{"text": custom_prompt}] + existing_parts
        }

        # 2. 判断图片模型
        if "image" in model.lower():
            # 调用图片生成专用处理函数
            return prepare_image_generation_request(result, model)
        else:
            # 3. 思考模型处理
            if is_thinking_model(model):
                if "thinkingConfig" not in generation_config:
                    generation_config["thinkingConfig"] = {
                        "thinkingBudget": 32768,
                        "includeThoughts": return_thoughts
                    }
                # 移除 -thinking 后缀
                model = model.replace("-thinking", "")

            # 4. 特殊模型映射
            model_mapping = {
                "claude-opus-4-5": "claude-opus-4-5-thinking",
                "claude-haiku-4": "gemini-2.5-flash"
            }
            result["model"] = model_mapping.get(model, model)

    # ========== 公共处理 ==========
    # 1. 字段名转换
    if "system_instructions" in result:
        result["systemInstruction"] = result.pop("system_instructions")

    # 2. 参数范围限制
    if generation_config:
        max_tokens = generation_config.get("maxOutputTokens")
        if max_tokens is not None and max_tokens > 65535:
            generation_config["maxOutputTokens"] = 65535

        top_k = generation_config.get("topK")
        if top_k is not None and top_k > 64:
            generation_config["topK"] = 64

    # 3. 工具清理
    if tools and mode == "antigravity":
        result["tools"] = clean_tools_for_gemini(tools)

    if generation_config:
        result["generationConfig"] = generation_config

    return result


# ==================== 图片响应处理 ====================

def process_image_response(response_data: Any, is_streaming: bool = False) -> Any:
    """
    处理包含图片的 Gemini 响应,转换为前端可读的 Markdown 格式

    对于图片生成模型的响应,将 inlineData 格式转换为 Markdown 图片格式:
    - 非流式: 将图片转换为 Markdown 格式文本
    - 流式: 将图片转换为 Markdown 格式并包装在 SSE 数据块中

    Args:
        response_data: Gemini 响应数据 (可以是字符串、字节或字典)
        is_streaming: 是否为流式响应

    Returns:
        处理后的响应数据 (字符串或字节,保持原格式)
    """
    from log import log
    import json

    # 记录输入类型
    input_type = type(response_data).__name__
    log.debug(f"[process_image_response] Input type: {input_type}, is_streaming: {is_streaming}")

    # 如果是字节,先转换为字符串处理
    is_bytes = isinstance(response_data, bytes)
    if is_bytes:
        try:
            response_str = response_data.decode('utf-8')
        except UnicodeDecodeError:
            log.warning("[process_image_response] Failed to decode bytes, returning as-is")
            return response_data
    elif isinstance(response_data, str):
        response_str = response_data
    elif isinstance(response_data, dict):
        # 如果已经是字典,直接使用
        response_dict = response_data
        response_str = None
    else:
        log.warning(f"[process_image_response] Unexpected type {input_type}, returning as-is")
        return response_data

    # 解析 JSON (如果还没有解析)
    if response_str is not None:
        # 对于流式响应，可能是 SSE 格式: "data: {json}\n\n"
        if is_streaming and response_str.strip().startswith("data: "):
            try:
                # 提取 SSE 数据部分
                lines = response_str.strip().split('\n')
                json_line = None
                for line in lines:
                    if line.startswith("data: "):
                        json_str = line[6:].strip()  # 去掉 "data: " 前缀
                        if json_str and json_str != "[DONE]":
                            json_line = json_str
                            break

                if json_line:
                    response_dict = json.loads(json_line)
                    log.debug(f"[process_image_response] Parsed SSE format successfully")
                else:
                    # 没有找到有效的 JSON 数据，返回原始数据
                    log.debug("[process_image_response] No valid JSON in SSE format, returning as-is")
                    return response_data
            except json.JSONDecodeError as e:
                log.debug(f"[process_image_response] Failed to parse SSE JSON (likely empty chunk): {e}, returning as-is")
                return response_data
        else:
            # 非流式或非 SSE 格式，直接解析 JSON
            # 如果是空字符串，直接返回
            if not response_str or not response_str.strip():
                log.debug("[process_image_response] Empty string, returning as-is")
                return response_data

            try:
                response_dict = json.loads(response_str)
            except json.JSONDecodeError:
                log.debug("[process_image_response] Failed to parse JSON (likely empty or malformed), returning as-is")
                return response_data

    # 检查是否是包装格式 {"response": {...}}
    if "response" in response_dict and "candidates" not in response_dict:
        response_dict = response_dict["response"]
        log.debug("[process_image_response] Unwrapped response field")

    # 检查是否包含图片数据并转换为 Markdown
    has_image = False
    if "candidates" in response_dict:
        for candidate in response_dict["candidates"]:
            parts = candidate.get("content", {}).get("parts", [])
            new_parts = []

            for part in parts:
                if "inlineData" in part:
                    has_image = True
                    inline_data = part["inlineData"]
                    mime_type = inline_data.get("mimeType", "image/png")
                    base64_data = inline_data.get("data", "")

                    # 转换为 Markdown 格式: ![Generated Image](data:image/png;base64,xxx)
                    markdown_image = f"![Generated Image](data:{mime_type};base64,{base64_data})"

                    log.info(f"[process_image_response] Converting image to Markdown, mimeType: {mime_type}, data length: {len(base64_data)}")

                    # 将图片转换为文本格式的 part
                    new_parts.append({"text": markdown_image})
                elif "text" in part:
                    # 保留文本 part
                    new_parts.append(part)
                elif "thoughtSignature" in part or part.get("thought"):
                    # 保留 thinking 相关的 part
                    new_parts.append(part)
                # 忽略其他类型的 part (如 functionCall 等)

            # 更新 parts
            if new_parts:  # 只要有内容就更新
                candidate["content"]["parts"] = new_parts

    # 如果没有图片,直接返回原始数据
    if not has_image:
        log.debug("[process_image_response] No image found, returning as-is")
        return response_data

    # 图片响应处理
    log.info("[process_image_response] Converted image to Markdown format for frontend")

    if is_streaming:
        # 流式响应: 需要重新包装为原始格式
        # 如果原始数据包含 response 字段，需要恢复
        if response_str and '"response"' in response_str:
            # 重新包装为 {"response": {...}} 格式
            wrapped_dict = {"response": response_dict}
            result_str = f"data: {json.dumps(wrapped_dict, ensure_ascii=False)}\n\n"
        else:
            # 直接使用标准格式
            result_str = f"data: {json.dumps(response_dict, ensure_ascii=False)}\n\n"
        log.debug(f"[process_image_response] Wrapped streaming response: {result_str[:200]}...")
    else:
        # 非流式响应: 保持 JSON 格式
        result_str = json.dumps(response_dict, ensure_ascii=False)
        log.debug(f"[process_image_response] Non-streaming response as JSON")

    # 返回与输入相同的类型
    if is_bytes:
        return result_str.encode('utf-8')
    else:
        return result_str