"""
Gemini Format Utilities - 统一的 Gemini 格式处理和转换工具
提供对 Gemini API 请求体和响应的标准化处理
────────────────────────────────────────────────────────────────
"""

from typing import Any, Dict, List, Optional

from log import log
from src.utils import DEFAULT_SAFETY_SETTINGS

# ==================== Gemini API 配置 ====================

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
    # 按照从长到短的顺序排列，避免 -think 先于 -maxthinking 被匹配
    suffixes = ["-maxthinking", "-nothinking", "-search", "-think"]
    result = model_name
    changed = True
    # 持续循环直到没有任何后缀可以移除
    while changed:
        changed = False
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[:-len(suffix)]
                changed = True
                # 不使用 break，继续检查是否还有其他后缀
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
    return "think" in model_name or "pro" in model_name.lower()


def check_last_assistant_has_thinking(contents: List[Dict[str, Any]]) -> bool:
    """
    检查最后一个 assistant 消息是否以 thinking 块开始
    
    根据 Claude API 要求：当启用 thinking 时，最后一个 assistant 消息必须以 thinking 块开始
    
    Args:
        contents: Gemini 格式的 contents 数组
        
    Returns:
        如果最后一个 assistant 消息以 thinking 块开始则返回 True，否则返回 False
    """
    if not contents:
        return True  # 没有 contents，允许启用 thinking
    
    # 从后往前找最后一个 assistant (model) 消息
    last_assistant_content = None
    for content in reversed(contents):
        if isinstance(content, dict) and content.get("role") == "model":
            last_assistant_content = content
            break
    
    if not last_assistant_content:
        return True  # 没有 assistant 消息，允许启用 thinking
    
    # 检查第一个 part 是否是 thinking 块
    parts = last_assistant_content.get("parts", [])
    if not parts:
        return False  # 有 assistant 消息但没有 parts，不允许 thinking
    
    first_part = parts[0]
    if not isinstance(first_part, dict):
        return False
    
    # 检查是否是 thinking 块（有 thought 或 thoughtSignature 字段）
    return "thought" in first_part or "thoughtSignature" in first_part


async def normalize_gemini_request(
    request: Dict[str, Any],
    mode: str = "geminicli"
) -> Dict[str, Any]:
    """
    规范化 Gemini 请求

    处理逻辑:
    1. 模型特性处理 (thinking config, search tools)
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
    generation_config = (result.get("generationConfig") or {}).copy()  # 创建副本避免修改原对象
    tools = result.get("tools")
    system_instruction = result.get("systemInstruction") or result.get("system_instructions")
    
    # 记录原始请求
    log.debug(f"[GEMINI_FIX] 原始请求 - 模型: {model}, mode: {mode}, generationConfig: {generation_config}")

    # 获取配置值
    return_thoughts = await get_return_thoughts_to_frontend()

    # ========== 模式特定处理 ==========
    if mode == "geminicli":
        # 1. 思考设置
        # 优先使用 get_thinking_settings 获取的思考预算
        thinking_budget, _ = get_thinking_settings(model)
        
        # 其次使用传入的思考预算
        if thinking_budget is None:
            thinking_budget = generation_config.get("thinkingConfig", {}).get("thinkingBudget")
        
        # 假如 is_thinking_model 为真或者思考预算不为0，设置 thinkingConfig
        if is_thinking_model(model) or (thinking_budget and thinking_budget != 0):
            # 确保 thinkingConfig 存在
            if "thinkingConfig" not in generation_config:
                generation_config["thinkingConfig"] = {}

            thinking_config = generation_config["thinkingConfig"]

            # 设置思考预算
            if thinking_budget:
                thinking_config["thinkingBudget"] = thinking_budget

            # includeThoughts 使用配置值
            thinking_config["includeThoughts"] = return_thoughts

        # 2. 搜索模型添加 Google Search
        if is_search_model(model):
            result_tools = result.get("tools") or []
            result["tools"] = result_tools
            if not any(tool.get("googleSearch") for tool in result_tools if isinstance(tool, dict)):
                result_tools.append({"googleSearch": {}})

        # 3. 模型名称处理
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
            if is_thinking_model(model) or ("thinkingBudget" in generation_config.get("thinkingConfig", {}) and generation_config["thinkingConfig"]["thinkingBudget"] != 0):
                # 直接设置 thinkingConfig
                if "thinkingConfig" not in generation_config:
                    generation_config["thinkingConfig"] = {}
                
                thinking_config = generation_config["thinkingConfig"]
                # 优先使用传入的思考预算，否则使用默认值
                if "thinkingBudget" not in thinking_config:
                    thinking_config["thinkingBudget"] = 1024
                thinking_config["includeThoughts"] = return_thoughts
                
                # 检查最后一个 assistant 消息是否以 thinking 块开始
                contents = result.get("contents", [])

                if not check_last_assistant_has_thinking(contents) and "claude" in model.lower():
                    # 检测是否有工具调用（MCP场景）
                    has_tool_calls = any(
                        isinstance(content, dict) and 
                        any(
                            isinstance(part, dict) and ("functionCall" in part or "function_call" in part)
                            for part in content.get("parts", [])
                        )
                        for content in contents
                    )
                    
                    if has_tool_calls:
                        # MCP 场景：检测到工具调用，移除 thinkingConfig
                        log.warning(f"[ANTIGRAVITY] 检测到工具调用（MCP场景），移除 thinkingConfig 避免失效")
                        generation_config.pop("thinkingConfig", None)
                    else:
                        # 非 MCP 场景：填充思考块
                        log.warning(f"[ANTIGRAVITY] 最后一个 assistant 消息不以 thinking 块开始，自动填充思考块")
                        
                        # 找到最后一个 model 角色的 content
                        for i in range(len(contents) - 1, -1, -1):
                            content = contents[i]
                            if isinstance(content, dict) and content.get("role") == "model":
                                # 在 parts 开头插入思考块（使用官方跳过验证的虚拟签名）
                                parts = content.get("parts", [])
                                thinking_part = {
                                    "text": "Continuing from previous context...",
                                    # "thought": True,  # 标记为思考块
                                    "thoughtSignature": "skip_thought_signature_validator"  # 官方文档推荐的虚拟签名
                                }
                                # 如果第一个 part 不是 thinking，则插入
                                if not parts or not (isinstance(parts[0], dict) and ("thought" in parts[0] or "thoughtSignature" in parts[0])):
                                    content["parts"] = [thinking_part] + parts
                                    log.debug(f"[ANTIGRAVITY] 已在最后一个 assistant 消息开头插入思考块（含跳过验证签名）")
                                break
                
            # 移除 -thinking 后缀
            model = model.replace("-thinking", "")

            # 4. Claude 模型关键词映射
            # 使用关键词匹配而不是精确匹配，更灵活地处理各种变体
            original_model = model
            if "opus" in model.lower():
                model = "claude-opus-4-5-thinking"
            elif "sonnet" in model.lower() or "haiku" in model.lower():
                model = "claude-sonnet-4-5-thinking"
            elif "haiku" in model.lower():
                model = "gemini-2.5-flash"
            elif "claude" in model.lower():
                # Claude 模型兜底：如果包含 claude 但不是 opus/sonnet/haiku
                model = "claude-sonnet-4-5-thinking"
            
            result["model"] = model
            if original_model != model:
                log.debug(f"[ANTIGRAVITY] 映射模型: {original_model} -> {model}")

    # ========== 公共处理 ==========

    # 1. 安全设置覆盖
    result["safetySettings"] = DEFAULT_SAFETY_SETTINGS

    # 2. 参数范围限制
    if generation_config:
        max_tokens = generation_config.get("maxOutputTokens")
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = 64000

        top_k = generation_config.get("topK")
        if top_k is not None:
            generation_config["topK"] = 64

    if "contents" in result:
        cleaned_contents = []
        for content in result["contents"]:
            if isinstance(content, dict) and "parts" in content:
                # 过滤掉空的或无效的 parts
                valid_parts = []
                for part in content["parts"]:
                    if not isinstance(part, dict):
                        continue
                    
                    # 检查 part 是否有有效的非空值
                    # 过滤掉空字典或所有值都为空的 part
                    has_valid_value = any(
                        value not in (None, "", {}, [])
                        for key, value in part.items()
                        if key != "thought"  # thought 字段可以为空
                    )
                    
                    if has_valid_value:
                        # 清理 text 字段的尾随空格
                        if "text" in part and isinstance(part["text"], str):
                            part = part.copy()
                            part["text"] = part["text"].rstrip()
                        valid_parts.append(part)
                    else:
                        log.warning(f"[GEMINI_FIX] 移除空的或无效的 part: {part}")
                
                # 只添加有有效 parts 的 content
                if valid_parts:
                    cleaned_content = content.copy()
                    cleaned_content["parts"] = valid_parts
                    cleaned_contents.append(cleaned_content)
                else:
                    log.warning(f"[GEMINI_FIX] 跳过没有有效 parts 的 content: {content.get('role')}")
            else:
                cleaned_contents.append(content)
        
        result["contents"] = cleaned_contents

    if generation_config:
        result["generationConfig"] = generation_config

    return result