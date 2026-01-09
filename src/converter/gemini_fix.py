"""
Gemini Format Utilities - 统一的 Gemini 格式处理和转换工具
提供对 Gemini API 请求体和响应的标准化处理

字段命名规范 (Field Naming Convention):
────────────────────────────────────────────────────────────────
1. 内部使用 (Internal Usage):
   - 所有 Python 函数参数和变量使用 snake_case: system_instruction
   - 保持代码风格一致,符合 PEP 8 规范

2. API 输出 (API Output):
   - 发送给 Gemini API 时使用 camelCase: systemInstruction
   - 符合 Google Gemini API 的字段命名规范

3. API 输入 (API Input):
   - 接收请求时兼容两种格式: systemInstruction 和 system_instruction
   - 优先识别 systemInstruction (API 标准格式)
   - 向后兼容 system_instruction (方便内部调用)

示例:
  # 函数定义 - 使用 snake_case
  def process_request(system_instruction: Dict) -> Dict:
      ...

  # API 输出 - 转换为 camelCase
  return {"systemInstruction": system_instruction}

  # API 输入 - 兼容两种格式
  system_instruction = request.get("systemInstruction") or request.get("system_instruction")
────────────────────────────────────────────────────────────────
"""

from typing import Any, Dict, List, Optional, Tuple
import json
from log import log

# ==================== 公共工具函数 ====================

def safe_get_nested(obj: Any, *keys: str, default: Any = None) -> Any:
    """安全获取嵌套字典值
    
    Args:
        obj: 字典对象
        *keys: 嵌套键路径
        default: 默认值
    
    Returns:
        获取到的值或默认值
    """
    for key in keys:
        if not isinstance(obj, dict):
            return default
        obj = obj.get(key, default)
        if obj is default:
            return default
    return obj


def update_dict_if_missing(target: Dict[str, Any], updates: Dict[str, Any]) -> None:
    """仅在键不存在时更新字典
    
    Args:
        target: 目标字典
        updates: 要更新的键值对
    """
    for key, value in updates.items():
        if key not in target:
            target[key] = value


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


def extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容和推理内容
    
    Args:
        parts: Gemini 响应中的 parts 列表
    
    Returns:
        (content, reasoning_content): 文本内容和推理内容的元组
    """
    content = ""
    reasoning_content = ""

    for part in parts:
        text = part.get("text", "")
        if text:
            if part.get("thought", False):
                reasoning_content += text
            else:
                content += text

    return content, reasoning_content


def _filter_parts(parts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤掉包含 thought 字段的 parts
    
    Args:
        parts: parts 列表
    
    Returns:
        过滤后的 parts 列表
    """
    return [
        part for part in parts
        if not (isinstance(part, dict) and part.get("thought"))
    ]


def filter_thoughts_from_response(response_data: dict) -> dict:
    """从响应数据中过滤掉思维内容

    Args:
        response_data: Gemini API 响应数据

    Returns:
        修改后的响应数据(已移除 thoughts)
    """
    if not isinstance(response_data, dict):
        return response_data

    # 处理GeminiCLI的response包装格式
    has_wrapper = False
    if "response" in response_data and "candidates" not in response_data:
        has_wrapper = True
        actual_response = response_data["response"]
    else:
        actual_response = response_data

    if "candidates" not in actual_response:
        return response_data

    # 遍历candidates并移除thoughts
    for candidate in actual_response.get("candidates", []):
        parts = safe_get_nested(candidate, "content", "parts")
        if parts and isinstance(parts, list):
            candidate["content"]["parts"] = _filter_parts(parts)

    # 如果有包装，返回包装后的结果
    if has_wrapper:
        return {"response": actual_response}
    return actual_response


def filter_thoughts_from_stream_chunk(chunk_data: dict) -> Optional[dict]:
    """从流式响应块中过滤思维内容
    
    Args:
        chunk_data: 单个流式响应块
    
    Returns:
        过滤后的响应块,如果过滤后为空则返回 None
    """
    if not isinstance(chunk_data, dict):
        return chunk_data

    # 提取候选响应
    candidates = chunk_data.get("candidates", [])
    if not candidates:
        return chunk_data
    
    candidate = candidates[0] if candidates else {}
    parts = safe_get_nested(candidate, "content", "parts", default=[])

    # 过滤掉思维链部分
    filtered_parts = _filter_parts(parts)

    # 如果过滤后为空且原来有内容,返回 None 表示跳过这个块
    if not filtered_parts and parts:
        return None

    # 更新parts
    if filtered_parts != parts and "content" in candidate:
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
    """处理 generationConfig,应用限制和默认值
    
    Args:
        generation_config: 原始的生成配置
        max_output_tokens_limit: maxOutputTokens 的上限
        default_top_k: 默认的 topK 值
    
    Returns:
        处理后的 generationConfig
    """
    config = generation_config.copy() if generation_config else {}
    
    # 限制 maxOutputTokens
    max_tokens = config.get("maxOutputTokens")
    if max_tokens is not None and max_tokens > max_output_tokens_limit:
        config["maxOutputTokens"] = max_output_tokens_limit
    
    # 设置默认的 topK
    update_dict_if_missing(config, {"topK": default_top_k})
    
    return config


def setup_thinking_config(
    generation_config: Dict[str, Any],
    model_name: str,
    get_thinking_budget_func,
    should_include_thoughts_func
) -> Dict[str, Any]:
    """设置 thinkingConfig 配置
    
    Args:
        generation_config: 生成配置字典
        model_name: 模型名称
        get_thinking_budget_func: 获取 thinking budget 的函数
        should_include_thoughts_func: 判断是否包含 thoughts 的函数
    
    Returns:
        更新后的 generationConfig
    """
    config = generation_config.copy()
    thinking_budget = get_thinking_budget_func(model_name)
    
    # 只有在有 thinking budget 时才处理
    if thinking_budget is None:
        return config
    
    # 如果未指定 thinkingConfig,创建新的
    if "thinkingConfig" not in config:
        config["thinkingConfig"] = {
            "thinkingBudget": thinking_budget,
            "includeThoughts": should_include_thoughts_func(model_name)
        }
    else:
        # 填充缺失的字段
        update_dict_if_missing(config["thinkingConfig"], {
            "thinkingBudget": thinking_budget,
            "includeThoughts": should_include_thoughts_func(model_name)
        })
    
    return config


def setup_search_tools(
    request_data: Dict[str, Any],
    model_name: str,
    is_search_model_func
) -> Dict[str, Any]:
    """为搜索模型添加 Google Search 工具
    
    Args:
        request_data: 请求数据
        model_name: 模型名称
        is_search_model_func: 判断是否为搜索模型的函数
    
    Returns:
        更新后的请求数据
    """
    if not is_search_model_func(model_name):
        return request_data
    
    data = request_data.copy()
    tools = data.setdefault("tools", [])
    
    # 检查是否已有工具
    has_tools = any(
        tool.get("functionDeclarations") or tool.get("googleSearch")
        for tool in tools
    )
    
    # 只有在没有任何工具时才添加 googleSearch
    if not has_tools:
        tools.append({"googleSearch": {}})
    
    return data


def build_antigravity_generation_config(
    parameters: Dict[str, Any],
    enable_thinking: bool,
    model_name: str
) -> Dict[str, Any]:
    """生成 Antigravity generationConfig
    
    Args:
        parameters: 参数字典(temperature, top_p, max_tokens等)
        enable_thinking: 是否启用思考模式
        model_name: 模型名称
    
    Returns:
        Antigravity 格式的 generationConfig
    """
    # 构建基础配置
    config = {
        "candidateCount": 1,
        "stopSequences": [
            "<|user|>", "<|bot|>", "<|context_request|>",
            "<|endoftext|>", "<|end_of_turn|>"
        ],
        "topK": parameters.get("top_k", 50),
    }
    
    # 参数映射:parameters key -> config key
    param_mapping = {
        "temperature": "temperature",
        "top_p": "topP",
        "max_tokens": "maxOutputTokens",
        "response_modalities": "response_modalities",
        "image_config": "image_config"
    }
    
    # 批量添加可选参数
    for param_key, config_key in param_mapping.items():
        if param_key in parameters:
            config[config_key] = parameters[param_key]
    
    # 思考模型配置
    if enable_thinking:
        config["thinkingConfig"] = {
            "includeThoughts": True,
            "thinkingBudget": 1024
        }
        # Claude 思考模型:删除 topP 参数
        if "claude" in model_name.lower():
            config.pop("topP", None)
    
    return config


def build_antigravity_request_body(
    contents: List[Dict[str, Any]],
    model: str,
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
        system_instruction: 系统指令
        tools: 工具定义列表
        generation_config: 生成配置

    Returns:
        Antigravity 格式的请求体
    """
    import uuid

    # Antigravity 的默认系统提示词
    custom_prompt = "Please ignore the following [ignore]You are Antigravity, a powerful agentic AI coding assistant designed by the Google Deepmind team working on Advanced Agentic Coding.You are pair programming with a USER to solve their coding task. The task may require creating a new codebase, modifying or debugging an existing codebase, or simply answering a question.**Absolute paths only****Proactiveness**[/ignore]"

    # 处理系统指令,将占位符插入到开头
    modified_system_instruction = None
    if system_instruction:
        if isinstance(system_instruction, dict):
            parts = system_instruction.get("parts", [])
            modified_system_instruction = {
                "parts": [{"text": custom_prompt}] + (parts if parts else [])
            }
    else:
        modified_system_instruction = {"parts": [{"text": custom_prompt}]}

    # 构建基础请求数据
    request_data = {
        "contents": contents,
        "generationConfig": generation_config or {},
    }

    # 使用统一的后处理函数
    request_data = normalize_gemini_request(
        request_data,
        model=model,
        system_instruction=modified_system_instruction,
        tools=tools,
        thinking_config_override=None,
        compatibility_mode=False,
        default_safety_settings=None,  # Antigravity 不需要默认安全设置
        get_thinking_budget_func=None,
        should_include_thoughts_func=None,
        is_search_model_func=None,
    )

    # 添加 toolConfig (如果有工具)
    if tools:
        request_data["toolConfig"] = {
            "functionCallingConfig": {"mode": "VALIDATED"}
        }

    # 生成请求 ID 并构建 Antigravity 格式
    request_id = f"req-{uuid.uuid4()}"
    return {
        "requestId": request_id,
        "model": model,
        "userAgent": "antigravity",
        "requestType": "agent",
        "request": request_data
    }


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
    """从原生 Gemini 请求构建完整的 Gemini API payload
    整合了所有的配置处理、工具清理、安全设置等逻辑

    字段命名规范:
    - 接收请求时兼容两种格式: systemInstruction (API格式) 和 system_instruction (Python格式)
    - 内部处理统一使用 system_instruction (snake_case)
    - 输出到 API 使用 systemInstruction (camelCase)

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
    request_data = native_request.copy()

    # 兼容两种格式: systemInstruction (camelCase) 和 system_instruction (snake_case)
    # 优先使用 systemInstruction (API 标准格式)
    system_instruction = request_data.pop("systemInstruction", None)
    if system_instruction is None:
        system_instruction = request_data.pop("system_instruction", None)

    tools = request_data.pop("tools", None)

    # 使用统一的后处理函数
    request_data = normalize_gemini_request(
        request_data,
        model=model_from_path,
        system_instruction=system_instruction,
        tools=tools,
        thinking_config_override=None,
        compatibility_mode=False,
        default_safety_settings=default_safety_settings,
        get_thinking_budget_func=get_thinking_budget_func,
        should_include_thoughts_func=should_include_thoughts_func,
        is_search_model_func=is_search_model_func,
    )

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
    """从完整响应中提取内容和推理内容(用于假流式)

    Args:
        response_data: Gemini API 响应数据

    Returns:
        (content, reasoning_content, finish_reason): 内容、推理内容和结束原因的元组
    """
    # 处理GeminiCLI的response包装格式
    if "response" in response_data and "candidates" not in response_data:
        response_data = response_data["response"]

    candidates = response_data.get("candidates", [])
    if not candidates:
        return "", "", "STOP"

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason", "STOP")
    parts = safe_get_nested(candidate, "content", "parts", default=[])
    content, reasoning_content = extract_content_and_reasoning(parts)

    return content, reasoning_content, finish_reason


def _build_candidate(parts: List[Dict[str, Any]], finish_reason: str = "STOP") -> Dict[str, Any]:
    """构建标准候选响应结构
    
    Args:
        parts: parts 列表
        finish_reason: 结束原因
    
    Returns:
        候选响应字典
    """
    return {
        "candidates": [{
            "content": {"parts": parts, "role": "model"},
            "finishReason": finish_reason,
            "index": 0,
        }]
    }


def build_gemini_fake_stream_chunks(content: str, reasoning_content: str, finish_reason: str) -> List[Dict[str, Any]]:
    """构建假流式响应的数据块
    
    Args:
        content: 主要内容
        reasoning_content: 推理内容
        finish_reason: 结束原因
    
    Returns:
        响应数据块列表
    """
    # 如果没有正常内容但有思维内容,提供默认回复
    if not content:
        default_text = "[模型正在思考中,请稍后再试或重新提问]" if reasoning_content else "[响应为空,请重新尝试]"
        return [_build_candidate([{"text": default_text}])]
    
    # 构建包含分离内容的响应
    parts = [{"text": content}]
    if reasoning_content:
        parts.append({"text": reasoning_content, "thought": True})
    
    return [_build_candidate(parts, finish_reason)]


def create_gemini_heartbeat_chunk() -> Dict[str, Any]:
    """创建 Gemini 格式的心跳数据块
    
    Returns:
        心跳数据块
    """
    chunk = _build_candidate([{"text": ""}])
    chunk["candidates"][0]["finishReason"] = None
    return chunk


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


# ==================== 统一的 Gemini 请求后处理 ====================

def normalize_gemini_request(
    request_data: Dict[str, Any],
    *,
    model: str,
    system_instruction: Optional[Dict[str, Any]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    thinking_config_override: Optional[Dict[str, Any]] = None,
    compatibility_mode: bool = False,
    default_safety_settings: Optional[List[Dict[str, Any]]] = None,
    get_thinking_budget_func=None,
    should_include_thoughts_func=None,
    is_search_model_func=None
) -> Dict[str, Any]:
    """
    统一的 Gemini 请求后处理函数

    负责处理所有转换器共同的后处理逻辑:
    1. 参数规范化 (topK=64, maxOutputTokens≤65535)
    2. System instruction 处理
    3. Thinking config 处理
    4. Tools 清理和处理
    5. 搜索工具添加
    6. 安全设置补全

    字段命名规范:
    - 内部参数使用 system_instruction (snake_case)
    - 输出到 API 使用 systemInstruction (camelCase)
    - 这样保持 Python 代码风格一致,同时符合 Gemini API 规范

    Args:
        request_data: 基础请求数据 (包含 contents 和 generationConfig)
        model: 模型名称
        system_instruction: 系统指令 (可选,内部使用 snake_case)
        tools: 工具定义列表 (可选)
        thinking_config_override: 显式的 thinking 配置 (可选,优先级最高)
        compatibility_mode: 兼容性模式,影响 system instruction 处理
        default_safety_settings: 默认安全设置列表
        get_thinking_budget_func: 获取 thinking budget 的函数
        should_include_thoughts_func: 判断是否包含 thoughts 的函数
        is_search_model_func: 判断是否为搜索模型的函数

    Returns:
        标准化后的 Gemini 请求数据 (包含 systemInstruction 字段,符合 API 规范)
    """
    result = request_data.copy()

    # 1. 处理 generationConfig
    generation_config = result.setdefault("generationConfig", {})

    # 1.1 限制 maxOutputTokens
    max_tokens = generation_config.get("maxOutputTokens")
    if max_tokens is not None and max_tokens > 65535:
        generation_config["maxOutputTokens"] = 65535
        log.debug(f"Limited maxOutputTokens from {max_tokens} to 65535")

    # 1.2 限制 topK
    topK = generation_config.get("topK")
    if topK is not None and topK > 64:
        generation_config["topK"] = 64
        log.debug(f"Limited topK from {topK} to 64")
    
    # 1.3 处理 thinking config
    if thinking_config_override:
        # 优先使用显式传入的 thinking config
        generation_config["thinkingConfig"] = thinking_config_override
    elif "thinkingConfig" not in generation_config and get_thinking_budget_func and should_include_thoughts_func:
        # 根据模型自动配置 thinking
        thinking_budget = get_thinking_budget_func(model)
        if thinking_budget is not None:
            generation_config["thinkingConfig"] = {
                "thinkingBudget": thinking_budget,
                "includeThoughts": should_include_thoughts_func(model)
            }

    result["generationConfig"] = generation_config

    # 2. 处理 system instruction (如果未启用兼容性模式)
    # 注意: 这里将内部的 system_instruction (snake_case) 转换为 API 的 systemInstruction (camelCase)
    if system_instruction and not compatibility_mode:
        result["systemInstruction"] = system_instruction

    # 3. 处理 tools
    if tools:
        # 清理工具定义中不支持的 JSON Schema 字段
        cleaned_tools = clean_tools_for_gemini(tools)
        result["tools"] = cleaned_tools

    # 4. 为搜索模型添加 Google Search 工具
    if is_search_model_func and is_search_model_func(model):
        result_tools = result.setdefault("tools", [])
        # 检查是否已有 Google Search 工具
        has_google_search = any(
            tool.get("googleSearch") for tool in result_tools
        )
        if not has_google_search:
            result_tools.append({"googleSearch": {}})
            log.debug(f"Added Google Search tool for search model: {model}")

    # 5. 补全安全设置
    if default_safety_settings:
        user_settings = list(result.get("safetySettings", []))
        existing_categories = {s.get("category") for s in user_settings}
        user_settings.extend(
            s for s in default_safety_settings
            if s["category"] not in existing_categories
        )
        result["safetySettings"] = user_settings

    # 6. 移除 None 值
    result = {k: v for k, v in result.items() if v is not None}

    return result


def process_system_messages(
    messages: List[Any],
    compatibility_mode: bool = False
) -> Tuple[List[str], int]:
    """
    从消息列表中提取连续的 system 消息

    Args:
        messages: 消息列表
        compatibility_mode: 兼容性模式下不提取 system 消息

    Returns:
        (system_instructions, first_non_system_index): 系统消息列表和第一个非系统消息的索引
    """
    if compatibility_mode:
        return [], 0

    system_instructions = []
    first_non_system_index = 0

    for i, message in enumerate(messages):
        role = getattr(message, "role", None) if hasattr(message, "role") else message.get("role")

        if role != "system":
            first_non_system_index = i
            break

        # 提取 system 消息内容
        content = getattr(message, "content", None) if hasattr(message, "content") else message.get("content")

        if isinstance(content, str):
            system_instructions.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                    system_instructions.append(part["text"])
    else:
        # 所有消息都是 system 消息
        first_non_system_index = len(messages)

    return system_instructions, first_non_system_index


def build_system_instruction_from_list(system_instructions: List[str]) -> Optional[Dict[str, Any]]:
    """
    从系统消息列表构建 systemInstruction 对象

    Args:
        system_instructions: 系统消息字符串列表

    Returns:
        Gemini 格式的 systemInstruction,如果列表为空则返回 None
    """
    if not system_instructions:
        return None

    combined_text = "\n\n".join(system_instructions)
    return {"parts": [{"text": combined_text}]}


# ==================== Antigravity 格式转换 ====================

def gemini_contents_to_antigravity_contents(gemini_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 Gemini 原生 contents 格式转换为 Antigravity contents 格式
    
    Args:
        gemini_contents: Gemini 格式的 contents
        
    Returns:
        Antigravity 格式的 contents（当前格式一致，直接返回）
    """
    return gemini_contents


def convert_antigravity_response_to_gemini(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    将 Antigravity 非流式响应转换为 Gemini 格式
    
    Args:
        response_data: Antigravity 响应数据
        
    Returns:
        Gemini 格式的响应数据
        
    Note:
        Antigravity 响应格式: {"response": {...}}
        Gemini 响应格式: {...}
    """
    return response_data.get("response", response_data)


async def convert_antigravity_stream_to_gemini(
    lines_generator: Any,
    stream_ctx: Any,
    client: Any,
):
    """
    将 Antigravity 流式响应转换为 Gemini 格式的 SSE 流

    Args:
        lines_generator: 行生成器（已经过滤的 SSE 行）
        stream_ctx: 流上下文
        client: HTTP 客户端
    """
    import json
    from log import log

    try:
        async for line in lines_generator:
            # 处理 bytes 类型
            if isinstance(line, bytes):
                if not line.startswith(b"data: "):
                    continue
                # 解码 bytes 后再解析
                line_str = line.decode('utf-8', errors='ignore')
            else:
                line_str = str(line)
                if not line_str.startswith("data: "):
                    continue

            # 解析 SSE 数据
            try:
                data = json.loads(line_str[6:])  # 去掉 "data: " 前缀
            except:
                continue

            # Antigravity 流式响应格式: {"response": {...}}
            # Gemini 流式响应格式: {...}
            gemini_data = data.get("response", data)

            # 发送 Gemini 格式的数据
            yield f"data: {json.dumps(gemini_data)}\n\n"

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Streaming error: {e}")
        error_response = {
            "error": {
                "message": str(e),
                "code": 500,
                "status": "INTERNAL"
            }
        }
        yield f"data: {json.dumps(error_response)}\n\n"
    finally:
        # 资源清理
        if stream_ctx:
            try:
                await stream_ctx.__aexit__(None, None, None)
            except Exception as e:
                log.debug(f"[ANTIGRAVITY GEMINI] Error closing stream context: {e}")
        
        if client:
            try:
                await client.aclose()
            except Exception as e:
                log.debug(f"[ANTIGRAVITY GEMINI] Error closing client: {e}")


# ==================== Gemini流式回复收集器 ====================

async def collect_streaming_response(stream_generator) -> Dict[str, Any]:
    """
    将Gemini流式响应收集为一条完整的非流式响应
    
    Args:
        stream_generator: 流式响应生成器，产生 "data: {json}" 格式的行
        
    Returns:
        合并后的完整响应字典
        
    Example:
        >>> async for line in stream_generator:
        ...     # line format: "data: {...}"
        >>> response = await collect_streaming_response(stream_generator)
    """
    from log import log
    
    # 初始化响应结构
    merged_response = {
        "response": {
            "candidates": [{
                "content": {
                    "parts": [],
                    "role": "model"
                },
                "finishReason": None,
                "safetyRatings": [],
                "citationMetadata": None
            }],
            "usageMetadata": {
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
                "totalTokenCount": 0
            }
        }
    }
    
    collected_text = []  # 用于收集文本内容
    collected_thought_text = []  # 用于收集思维链内容
    has_data = False
    line_count = 0
    
    log.debug("[STREAM COLLECTOR] Starting to collect streaming response")
    
    try:
        async for line in stream_generator:
            line_count += 1

            # 处理 bytes 类型
            if isinstance(line, bytes):
                line_str = line.decode('utf-8', errors='ignore')
                log.debug(f"[STREAM COLLECTOR] Processing bytes line {line_count}: {line_str[:200] if line_str else 'empty'}")
            elif isinstance(line, str):
                line_str = line
                log.debug(f"[STREAM COLLECTOR] Processing line {line_count}: {line_str[:200] if line_str else 'empty'}")
            else:
                log.debug(f"[STREAM COLLECTOR] Skipping non-string/bytes line: {type(line)}")
                continue

            # 解析流式数据行
            if not line_str.startswith("data: "):
                log.debug(f"[STREAM COLLECTOR] Skipping line without 'data: ' prefix: {line_str[:100]}")
                continue

            raw = line_str[6:].strip()
            if raw == "[DONE]":
                log.debug("[STREAM COLLECTOR] Received [DONE] marker")
                break
                
            try:
                log.debug(f"[STREAM COLLECTOR] Parsing JSON: {raw[:200]}")
                chunk = json.loads(raw)
                has_data = True
                log.debug(f"[STREAM COLLECTOR] Chunk keys: {chunk.keys() if isinstance(chunk, dict) else type(chunk)}")
                
                # 提取响应对象
                response_obj = chunk.get("response", {})
                if not response_obj:
                    log.debug("[STREAM COLLECTOR] No 'response' key in chunk, trying direct access")
                    response_obj = chunk  # 尝试直接使用chunk
                    
                candidates = response_obj.get("candidates", [])
                log.debug(f"[STREAM COLLECTOR] Found {len(candidates)} candidates")
                if not candidates:
                    log.debug(f"[STREAM COLLECTOR] No candidates in chunk, chunk structure: {list(chunk.keys()) if isinstance(chunk, dict) else type(chunk)}")
                    continue
                    
                candidate = candidates[0]
                
                # 收集文本内容
                content = candidate.get("content", {})
                parts = content.get("parts", [])
                log.debug(f"[STREAM COLLECTOR] Processing {len(parts)} parts from candidate")
                
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    
                    text = part.get("text", "")
                    if not text:
                        continue
                    
                    # 区分普通文本和思维链
                    if part.get("thought", False):
                        collected_thought_text.append(text)
                        log.debug(f"[STREAM COLLECTOR] Collected thought text: {text[:100]}")
                    else:
                        collected_text.append(text)
                        log.debug(f"[STREAM COLLECTOR] Collected regular text: {text[:100]}")
                
                # 收集其他信息（使用最后一个块的值）
                if candidate.get("finishReason"):
                    merged_response["response"]["candidates"][0]["finishReason"] = candidate["finishReason"]
                
                if candidate.get("safetyRatings"):
                    merged_response["response"]["candidates"][0]["safetyRatings"] = candidate["safetyRatings"]
                
                if candidate.get("citationMetadata"):
                    merged_response["response"]["candidates"][0]["citationMetadata"] = candidate["citationMetadata"]
                
                # 更新使用元数据
                usage = response_obj.get("usageMetadata", {})
                if usage:
                    merged_response["response"]["usageMetadata"].update(usage)
                    
            except json.JSONDecodeError as e:
                log.debug(f"[STREAM COLLECTOR] Failed to parse JSON chunk: {e}")
                continue
            except Exception as e:
                log.debug(f"[STREAM COLLECTOR] Error processing chunk: {e}")
                continue
    
    except Exception as e:
        log.error(f"[STREAM COLLECTOR] Error collecting stream after {line_count} lines: {e}")
        raise
    
    log.debug(f"[STREAM COLLECTOR] Finished iteration, has_data={has_data}, line_count={line_count}")
    
    # 如果没有收集到任何数据，返回错误
    if not has_data:
        log.error(f"[STREAM COLLECTOR] No data collected from stream after {line_count} lines")
        raise Exception("No data collected from stream")
    
    # 组装最终的parts
    final_parts = []
    
    # 先添加思维链内容（如果有）
    if collected_thought_text:
        final_parts.append({
            "text": "".join(collected_thought_text),
            "thought": True
        })
    
    # 再添加普通文本内容
    if collected_text:
        final_parts.append({
            "text": "".join(collected_text)
        })
    
    # 如果没有任何内容，添加空文本
    if not final_parts:
        final_parts.append({"text": ""})
    
    merged_response["response"]["candidates"][0]["content"]["parts"] = final_parts
    
    log.info(f"[STREAM COLLECTOR] Collected {len(collected_text)} text chunks and {len(collected_thought_text)} thought chunks")
    
    return merged_response