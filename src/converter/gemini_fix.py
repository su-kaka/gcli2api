"""
Gemini Format Utilities - 统一的 Gemini 格式处理和转换工具
提供对 Gemini API 请求体和响应的标准化处理
"""

from typing import Any, Dict, List, Optional

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


def extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容和推理内容
    
    Args:
        parts: Gemini 响应中的 parts 列表
    
    Returns:
        (content, reasoning_content): 文本内容和推理内容的元组
        reasoning_content 现在是字符串（保持向后兼容）
    """
    content = ""
    reasoning_content = ""

    for part in parts:
        # 处理文本内容
        if part.get("text"):
            # 检查这个部件是否包含thinking tokens
            if part.get("thought", False):
                reasoning_content += part.get("text", "")
            else:
                content += part.get("text", "")

    return content, reasoning_content


def filter_thoughts_from_response(response_data: dict) -> dict:
    """
    从响应数据中过滤掉思维内容（如果配置禁用）
    
    Args:
        response_data: Gemini API 响应数据
    
    Returns:
        修改后的响应数据（已移除 thoughts）
    """
    if not isinstance(response_data, dict):
        return response_data

    # 检查是否存在candidates字段
    if "candidates" not in response_data:
        return response_data

    # 遍历candidates并移除thoughts
    for candidate in response_data.get("candidates", []):
        if "content" in candidate and isinstance(candidate["content"], dict):
            if "parts" in candidate["content"]:
                # 过滤掉包含thought字段的parts
                candidate["content"]["parts"] = [
                    part for part in candidate["content"]["parts"]
                    if not isinstance(part, dict) or "thought" not in part
                ]

    return response_data


def filter_thoughts_from_stream_chunk(chunk_data: dict) -> Optional[dict]:
    """
    从流式响应块中过滤思维内容
    
    Args:
        chunk_data: 单个流式响应块
    
    Returns:
        过滤后的响应块，如果过滤后为空则返回 None
    """
    if not isinstance(chunk_data, dict):
        return chunk_data

    # 提取候选响应
    candidate = (chunk_data.get("candidates", []) or [{}])[0] or {}
    parts = (candidate.get("content", {}) or {}).get("parts", []) or []

    # 过滤掉思维链部分
    filtered_parts = [
        part for part in parts 
        if not (isinstance(part, dict) and part.get("thought") is True)
    ]

    # 如果过滤后为空且原来有内容，返回 None 表示跳过这个块
    if not filtered_parts and parts:
        return None

    # 更新parts
    if filtered_parts != parts:
        candidate["content"]["parts"] = filtered_parts

    return chunk_data


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


def process_generation_config(
    generation_config: Optional[Dict[str, Any]] = None,
    max_output_tokens_limit: int = 65535,
    default_top_k: int = 64
) -> Dict[str, Any]:
    """
    处理 generationConfig，应用限制和默认值
    
    Args:
        generation_config: 原始的生成配置
        max_output_tokens_limit: maxOutputTokens 的上限
        default_top_k: 默认的 topK 值
    
    Returns:
        处理后的 generationConfig
    """
    if not generation_config:
        generation_config = {}
    else:
        generation_config = generation_config.copy()
    
    # 限制 maxOutputTokens
    if "maxOutputTokens" in generation_config and generation_config["maxOutputTokens"] is not None:
        if generation_config["maxOutputTokens"] > max_output_tokens_limit:
            generation_config["maxOutputTokens"] = max_output_tokens_limit
    
    # 设置默认的 topK
    if "topK" not in generation_config:
        generation_config["topK"] = default_top_k
    
    return generation_config


def setup_thinking_config(
    generation_config: Dict[str, Any],
    model_name: str,
    get_thinking_budget_func,
    should_include_thoughts_func
) -> Dict[str, Any]:
    """
    设置 thinkingConfig 配置
    
    Args:
        generation_config: 生成配置字典
        model_name: 模型名称
        get_thinking_budget_func: 获取 thinking budget 的函数
        should_include_thoughts_func: 判断是否包含 thoughts 的函数
    
    Returns:
        更新后的 generationConfig
    """
    generation_config = generation_config.copy()
    
    # 如果未指定 thinkingConfig
    if "thinkingConfig" not in generation_config:
        thinking_budget = get_thinking_budget_func(model_name)
        
        # 只有在有 thinking budget 时才添加 thinkingConfig
        if thinking_budget is not None:
            generation_config["thinkingConfig"] = {
                "thinkingBudget": thinking_budget,
                "includeThoughts": should_include_thoughts_func(model_name)
            }
    else:
        # 如果用户已经提供了 thinkingConfig，但没有设置某些字段，填充默认值
        thinking_config = generation_config["thinkingConfig"]
        if "thinkingBudget" not in thinking_config:
            thinking_budget = get_thinking_budget_func(model_name)
            if thinking_budget is not None:
                thinking_config["thinkingBudget"] = thinking_budget
        if "includeThoughts" not in thinking_config:
            thinking_config["includeThoughts"] = should_include_thoughts_func(model_name)
    
    return generation_config


def setup_search_tools(
    request_data: Dict[str, Any],
    model_name: str,
    is_search_model_func
) -> Dict[str, Any]:
    """
    为搜索模型添加 Google Search 工具
    
    Args:
        request_data: 请求数据
        model_name: 模型名称
        is_search_model_func: 判断是否为搜索模型的函数
    
    Returns:
        更新后的请求数据
    """
    request_data = request_data.copy()
    
    if not is_search_model_func(model_name):
        return request_data
    
    if "tools" not in request_data:
        request_data["tools"] = []
    
    # 检查是否已有 functionDeclarations 或 googleSearch 工具
    has_function_declarations = any(
        tool.get("functionDeclarations") for tool in request_data["tools"]
    )
    has_google_search = any(
        tool.get("googleSearch") for tool in request_data["tools"]
    )
    
    # 只有在没有任何工具时才添加 googleSearch
    if not has_function_declarations and not has_google_search:
        request_data["tools"].append({"googleSearch": {}})
    
    return request_data


def build_antigravity_generation_config(
    parameters: Dict[str, Any],
    enable_thinking: bool,
    model_name: str
) -> Dict[str, Any]:
    """
    生成 Antigravity generationConfig
    
    Args:
        parameters: 参数字典（temperature, top_p, max_tokens等）
        enable_thinking: 是否启用思考模式
        model_name: 模型名称
    
    Returns:
        Antigravity 格式的 generationConfig
    """
    # 构建基础配置
    config_dict = {
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>",
            "<|bot|>",
            "<|context_request|>",
            "<|endoftext|>",
            "<|end_of_turn|>"
        ],
        "topK": parameters.get("top_k", 50),  # 默认值 50
    }
    
    # 添加可选参数
    if "temperature" in parameters:
        config_dict["temperature"] = parameters["temperature"]
    
    if "top_p" in parameters:
        config_dict["topP"] = parameters["top_p"]
    
    if "max_tokens" in parameters:
        config_dict["maxOutputTokens"] = parameters["max_tokens"]
    
    # 图片生成相关参数
    if "response_modalities" in parameters:
        config_dict["response_modalities"] = parameters["response_modalities"]
    
    if "image_config" in parameters:
        config_dict["image_config"] = parameters["image_config"]
    
    # 思考模型配置
    if enable_thinking:
        config_dict["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": 1024
        }
        
        # Claude 思考模型：删除 topP 参数
        if "claude" in model_name.lower():
            config_dict.pop("topP", None)
    
    return config_dict


def build_antigravity_request_body(
    contents: List[Dict[str, Any]],
    model: str,
    project_id: str,
    session_id: str,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    generation_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    构建 Antigravity API 请求体
    整合系统提示词注入、工具定义、生成配置等逻辑

    Args:
        contents: 消息内容列表
        model: 模型名称
        project_id: 项目 ID
        session_id: 会话 ID
        system_instruction: 系统指令
        tools: 工具定义列表
        generation_config: 生成配置

    Returns:
        Antigravity 格式的请求体
    """
    import uuid
    
    # 生成请求 ID
    request_id = f"req-{uuid.uuid4()}"
    
    request_body = {
        "project": project_id,
        "requestId": request_id,
        "model": model,
        "userAgent": "antigravity",
        "requestType": "agent",
        "request": {
            "contents": contents,
            "session_id": session_id,
        }
    }

    # 添加系统指令
    # Antigravity 的默认系统提示词
    custom_prompt = "You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.**Absolute paths only****Proactiveness**"
    
    if system_instruction:
        # 存在 systemInstruction，将占位符放在位置0，原有内容降格到位置1及以下
        if isinstance(system_instruction, dict):
            parts = system_instruction.get("parts", [])
            if parts:
                # 将占位符插入到位置0，原有内容后移
                system_instruction["parts"] = [{"text": custom_prompt}] + parts
            else:
                # parts 为空，创建新的
                system_instruction["parts"] = [{"text": custom_prompt}]
        request_body["request"]["systemInstruction"] = system_instruction
    else:
        # 不存在 systemInstruction，创建新的
        request_body["request"]["systemInstruction"] = {
            "parts": [{"text": custom_prompt}]
        }

    # 添加工具定义
    if tools:
        # 清理工具定义（移除不支持的字段）
        cleaned_tools = clean_tools_for_gemini(tools)
        request_body["request"]["tools"] = cleaned_tools
        request_body["request"]["toolConfig"] = {
            "functionCallingConfig": {"mode": "VALIDATED"}
        }

    # 添加生成配置
    if generation_config:
        request_body["request"]["generationConfig"] = generation_config

    return request_body


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
    
    request_body["requestType"] = "image_gen"
    request_body["model"] = "gemini-3-pro-image"  # 统一使用基础模型名
    request_body["request"]["generationConfig"] = {
        "candidateCount": 1,
        "imageConfig": image_config
    }
    
    # 移除不需要的字段
    for key in ("systemInstruction", "tools", "toolConfig"):
        request_body["request"].pop(key, None)
    
    return request_body


def build_gemini_request_payload(
    native_request: Dict[str, Any],
    model_from_path: str,
    get_base_model_name_func,
    get_thinking_budget_func,
    should_include_thoughts_func,
    is_search_model_func,
    default_safety_settings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    从原生 Gemini 请求构建完整的 Gemini API payload
    整合了所有的配置处理、工具清理、安全设置等逻辑
    
    Args:
        native_request: 原生 Gemini 格式请求
        model_from_path: 从路径中提取的模型名称
        get_base_model_name_func: 获取基础模型名称的函数
        get_thinking_budget_func: 获取 thinking budget 的函数
        should_include_thoughts_func: 判断是否包含 thoughts 的函数
        is_search_model_func: 判断是否为搜索模型的函数
        default_safety_settings: 默认安全设置列表
    
    Returns:
        完整的 Gemini API payload
    """
    # 创建请求副本以避免修改原始数据
    request_data = native_request.copy()
    
    # 1. 增量补全安全设置
    user_settings = list(request_data.get("safetySettings", []))
    existing_categories = {s.get("category") for s in user_settings}
    user_settings.extend(
        default_setting for default_setting in default_safety_settings
        if default_setting["category"] not in existing_categories
    )
    request_data["safetySettings"] = user_settings
    
    # 2. 确保 generationConfig 存在并处理
    if "generationConfig" not in request_data:
        request_data["generationConfig"] = {}
    
    generation_config = request_data["generationConfig"]
    
    # 3. 配置 thinkingConfig
    generation_config = setup_thinking_config(
        generation_config,
        model_from_path,
        get_thinking_budget_func,
        should_include_thoughts_func
    )
    request_data["generationConfig"] = generation_config
    
    # 4. 清理工具定义中不支持的 JSON Schema 字段
    if "tools" in request_data and request_data["tools"]:
        request_data["tools"] = clean_tools_for_gemini(request_data["tools"])
    
    # 5. 为搜索模型添加 Google Search 工具
    request_data = setup_search_tools(
        request_data,
        model_from_path,
        is_search_model_func
    )
    
    # 6. 构建最终 payload
    return {
        "model": get_base_model_name_func(model_from_path),
        "request": request_data
    }


def parse_google_api_response(raw_response: bytes, return_thoughts: bool) -> Dict[str, Any]:
    """
    解析 Google API 原始响应
    
    Args:
        raw_response: 原始响应字节
        return_thoughts: 是否返回思维内容
    
    Returns:
        解析后的标准 Gemini 响应
    """
    import json
    
    google_api_response = raw_response.decode("utf-8")
    if google_api_response.startswith("data: "):
        google_api_response = google_api_response[len("data: "):]
    
    google_api_response = json.loads(google_api_response)
    standard_gemini_response = google_api_response.get("response")
    
    # 如果配置为不返回思维链，则过滤
    if not return_thoughts:
        standard_gemini_response = filter_thoughts_from_response(standard_gemini_response)
    
    return standard_gemini_response


def parse_streaming_chunk(chunk: str, return_thoughts: bool) -> Optional[Dict[str, Any]]:
    """
    解析单个流式响应块
    
    Args:
        chunk: 流式响应块字符串（以 "data: " 开头）
        return_thoughts: 是否返回思维内容
    
    Returns:
        解析后的数据字典，如果无效则返回 None
    """
    import json
    
    if not chunk or not chunk.startswith("data: "):
        return None
    
    payload = chunk[len("data: "):]
    try:
        obj = json.loads(payload)
        if "response" in obj:
            data = obj["response"]
            # 如果配置为不返回思维链，则过滤
            if not return_thoughts:
                data = filter_thoughts_from_response(data)
            return data
        else:
            return obj
    except json.JSONDecodeError:
        return None


def parse_response_for_fake_stream(response_data: Dict[str, Any]) -> tuple:
    """
    从完整响应中提取内容和推理内容（用于假流式）
    
    Args:
        response_data: Gemini API 响应数据
    
    Returns:
        (content, reasoning_content, finish_reason): 内容、推理内容和结束原因的元组
    """
    content = ""
    reasoning_content = ""
    finish_reason = "STOP"
    
    if "candidates" in response_data and response_data["candidates"]:
        candidate = response_data["candidates"][0]
        finish_reason = candidate.get("finishReason", "STOP")
        
        if "content" in candidate and "parts" in candidate["content"]:
            parts = candidate["content"]["parts"]
            content, reasoning_content = extract_content_and_reasoning(parts)
    
    return content, reasoning_content, finish_reason


def build_gemini_fake_stream_chunks(content: str, reasoning_content: str, finish_reason: str) -> List[Dict[str, Any]]:
    """
    构建假流式响应的数据块
    
    Args:
        content: 主要内容
        reasoning_content: 推理内容
        finish_reason: 结束原因
    
    Returns:
        响应数据块列表
    """
    chunks = []
    
    # 如果没有正常内容但有思维内容，提供默认回复
    if not content and reasoning_content:
        content = "[模型正在思考中，请稍后再试或重新提问]"
    
    if content:
        # 构建包含分离内容的响应
        parts_response = [{"text": content}]
        if reasoning_content:
            parts_response.append({"text": reasoning_content, "thought": True})
        
        chunk = {
            "candidates": [{
                "content": {"parts": parts_response, "role": "model"},
                "finishReason": finish_reason,
                "index": 0,
            }]
        }
        chunks.append(chunk)
    else:
        # 提供默认回复
        chunk = {
            "candidates": [{
                "content": {
                    "parts": [{"text": "[响应为空，请重新尝试]"}],
                    "role": "model",
                },
                "finishReason": "STOP",
                "index": 0,
            }]
        }
        chunks.append(chunk)
    
    return chunks


def create_gemini_heartbeat_chunk() -> Dict[str, Any]:
    """
    创建 Gemini 格式的心跳数据块
    
    Returns:
        心跳数据块
    """
    return {
        "candidates": [{
            "content": {"parts": [{"text": ""}], "role": "model"},
            "finishReason": None,
            "index": 0,
        }]
    }


def create_gemini_error_chunk(message: str, error_type: str = "api_error", code: int = 500) -> Dict[str, Any]:
    """
    创建 Gemini 格式的错误数据块
    
    Args:
        message: 错误消息
        error_type: 错误类型
        code: 错误代码
    
    Returns:
        错误数据块
    """
    return {
        "error": {
            "message": message,
            "type": error_type,
            "code": code,
        }
    }