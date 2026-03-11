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
    request_body: Dict[str, Any], model: str
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
    image_size = (
        "4K" if "-4k" in model_lower else "2K" if "-2k" in model_lower else None
    )

    # 解析比例
    aspect_ratio = None
    for suffix, ratio in [
        ("-21x9", "21:9"),
        ("-16x9", "16:9"),
        ("-9x16", "9:16"),
        ("-4x3", "4:3"),
        ("-3x4", "3:4"),
        ("-1x1", "1:1"),
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

    request_body["model"] = "gemini-3.1-flash-image"  # 统一使用基础模型名
    request_body["generationConfig"] = {
        "candidateCount": 1,
        "imageConfig": image_config,
    }

    # 移除不需要的字段
    for key in ("systemInstruction", "tools", "toolConfig"):
        request_body.pop(key, None)

    return request_body


# ==================== 模型特性辅助函数 ====================


def get_base_model_name(model_name: str) -> str:
    """移除模型名称中的后缀,返回基础模型名"""
    # 按照从长到短的顺序排列，避免短后缀先于长后缀被匹配
    suffixes = [
        "-maxthinking",
        "-nothinking",  # 兼容旧模式
        "-minimal",
        "-medium",
        "-search",
        "-think",  # 中等长度后缀
        "-high",
        "-max",
        "-low",  # 短后缀
    ]
    result = model_name
    changed = True
    # 持续循环直到没有任何后缀可以移除
    while changed:
        changed = False
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[: -len(suffix)]
                changed = True
                # 不使用 break，继续检查是否还有其他后缀
    return result


def get_thinking_settings(model_name: str) -> tuple[Optional[int], Optional[str]]:
    """
    根据模型名称获取思考配置

    支持两种模式:
    1. CLI 模式思考预算 (Gemini 2.5 系列): -max, -high, -medium, -low, -minimal
    2. CLI 模式思考等级 (Gemini 3 Preview 系列): -high, -medium, -low, -minimal (仅 3-flash)
    3. 兼容旧模式: -maxthinking, -nothinking (不返回给用户)

    Returns:
        (thinking_budget, thinking_level): 思考预算和思考等级
    """
    base_model = get_base_model_name(model_name)

    # ========== 兼容旧模式 (不返回给用户) ==========
    if "-nothinking" in model_name:
        # nothinking 模式: 限制思考
        if "flash" in base_model:
            return 0, None
        return 128, None
    elif "-maxthinking" in model_name:
        # maxthinking 模式: 最大思考预算
        budget = 24576 if "flash" in base_model else 32768
        if "gemini-3" in base_model:
            # Gemini 3 系列不支持 thinkingBudget，返回 high 等级
            return None, "high"
        else:
            return budget, None

    # ========== 新 CLI 模式: 基于思考预算/等级 ==========

    # Gemini 3 Preview 系列: 使用 thinkingLevel
    if "gemini-3" in base_model:
        if "-high" in model_name:
            return None, "high"
        elif "-medium" in model_name:
            # 仅 3-flash-preview 支持 medium
            if "flash" in base_model:
                return None, "medium"
            # pro 系列不支持 medium，返回 Default
            return None, None
        elif "-low" in model_name:
            return None, "low"
        elif "-minimal" in model_name:
            return None, None
        else:
            # Default: 不设置 thinking 配置
            return None, None

    # Gemini 2.5 系列: 使用 thinkingBudget
    elif "gemini-2.5" in base_model:
        if "-max" in model_name:
            # 2.5-flash-max: 24576, 2.5-pro-max: 32768
            budget = 24576 if "flash" in base_model else 32768
            return budget, None
        elif "-high" in model_name:
            # 2.5-flash-high: 16000, 2.5-pro-high: 16000
            return 16000, None
        elif "-medium" in model_name:
            # 2.5-flash-medium: 8192, 2.5-pro-medium: 8192
            return 8192, None
        elif "-low" in model_name:
            # 2.5-flash-low: 1024, 2.5-pro-low: 1024
            return 1024, None
        elif "-minimal" in model_name:
            # 2.5-flash-minimal: 0, 2.5-pro-minimal: 128
            budget = 0 if "flash" in base_model else 128
            return budget, None
        else:
            # Default: 不设置 thinking budget
            return None, None

    # 其他模型: 不设置 thinking 配置
    return None, None


def is_search_model(model_name: str) -> bool:
    """检查是否为搜索模型"""
    return "-search" in model_name


# ==================== 统一的 Gemini 请求后处理 ====================


def is_thinking_model(model_name: str) -> bool:
    """检查是否为思考模型 (包含 -thinking 或 pro)"""
    return "think" in model_name or "pro" in model_name.lower()


def validate_function_call_pairs(contents: List[Any]) -> List[Any]:
    """确保 functionCall turn 后紧跟数量匹配的 functionResponse turn。"""
    validated_contents = list(contents)
    index = 0

    while index < len(validated_contents):
        content = validated_contents[index]
        if not isinstance(content, dict) or content.get("role") != "model":
            index += 1
            continue

        parts = content.get("parts") or []
        if not isinstance(parts, list):
            index += 1
            continue

        function_calls = [
            part
            for part in parts
            if isinstance(part, dict) and isinstance(part.get("functionCall"), dict)
        ]
        call_count = len(function_calls)
        if call_count == 0:
            index += 1
            continue

        def _synthesize_response(call_part: Dict[str, Any]) -> Dict[str, Any]:
            call = call_part.get("functionCall", {})
            synthesized = {
                "name": call.get("name") or "unknown_function",
                "response": {"result": "no response"},
            }
            if call.get("id"):
                synthesized["id"] = call["id"]
            return {"functionResponse": synthesized}

        next_index = index + 1
        next_turn = (
            validated_contents[next_index]
            if next_index < len(validated_contents)
            else None
        )

        if not isinstance(next_turn, dict) or next_turn.get("role") != "user":
            synthesized_parts = [
                _synthesize_response(call_part) for call_part in function_calls
            ]
            validated_contents.insert(
                next_index,
                {
                    "role": "user",
                    "parts": synthesized_parts,
                },
            )
            log.warning(
                "[GEMINI_FIX] functionCall turn 后缺少 user/functionResponse，"
                f"已插入 user turn 并补齐 {call_count} 个 response"
            )
            index += 2
            continue

        user_parts = next_turn.get("parts") or []
        if not isinstance(user_parts, list):
            user_parts = []

        function_responses = [
            part
            for part in user_parts
            if isinstance(part, dict) and isinstance(part.get("functionResponse"), dict)
        ]
        response_count = len(function_responses)

        if response_count == call_count:
            index += 1
            continue

        fixed_responses = function_responses[:call_count]
        if response_count < call_count:
            missing_count = call_count - response_count
            for call_part in function_calls[response_count:]:
                fixed_responses.append(_synthesize_response(call_part))
            log.warning(
                "[GEMINI_FIX] functionCall/functionResponse 数量不匹配，"
                f"call={call_count}, response={response_count}，已补齐 {missing_count} 个 response"
            )
        else:
            removed_count = response_count - call_count
            log.warning(
                "[GEMINI_FIX] functionCall/functionResponse 数量不匹配，"
                f"call={call_count}, response={response_count}，已移除 {removed_count} 个多余 response"
            )

        non_function_response_parts = [
            part
            for part in user_parts
            if not (
                isinstance(part, dict)
                and isinstance(part.get("functionResponse"), dict)
            )
        ]
        updated_user_turn = next_turn.copy()
        updated_user_turn["parts"] = non_function_response_parts + fixed_responses
        validated_contents[next_index] = updated_user_turn
        index += 1

    return validated_contents


async def normalize_gemini_request(
    request: Dict[str, Any], mode: str = "geminicli"
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
    generation_config = (
        result.get("generationConfig") or {}
    ).copy()  # 创建副本避免修改原对象
    system_instruction = result.get("systemInstruction")
    for alias_key in ("system_instruction", "system_instructions"):
        alias_value = result.pop(alias_key, None)
        if (not system_instruction) and alias_value:
            system_instruction = alias_value

    if system_instruction:
        result["systemInstruction"] = system_instruction
    else:
        result.pop("systemInstruction", None)

    # 记录原始请求
    log.debug(
        f"[GEMINI_FIX] 原始请求 - 模型: {model}, mode: {mode}, generationConfig: {generation_config}"
    )

    # 获取配置值
    return_thoughts = await get_return_thoughts_to_frontend()

    # ========== 模式特定处理 ==========
    if mode == "geminicli":
        # 1. 思考设置
        # 优先使用 get_thinking_settings 获取的思考预算和等级
        thinking_budget, thinking_level = get_thinking_settings(model)

        # 其次使用传入的思考预算（如果未从模型名称获取）
        if thinking_budget is None and thinking_level is None:
            thinking_budget = generation_config.get("thinkingConfig", {}).get(
                "thinkingBudget"
            )
            thinking_level = generation_config.get("thinkingConfig", {}).get(
                "thinkingLevel"
            )

        # 假如 is_thinking_model 为真或者思考预算/等级不为空，设置 thinkingConfig
        if (
            is_thinking_model(model)
            or thinking_budget is not None
            or thinking_level is not None
        ):
            # 确保 thinkingConfig 存在
            if "thinkingConfig" not in generation_config:
                generation_config["thinkingConfig"] = {}

            thinking_config = generation_config["thinkingConfig"]

            # 设置思考预算或等级（互斥）
            if thinking_budget is not None:
                thinking_config["thinkingBudget"] = thinking_budget
                thinking_config.pop("thinkingLevel", None)  # 避免与 thinkingBudget 冲突
            elif thinking_level is not None:
                thinking_config["thinkingLevel"] = thinking_level
                thinking_config.pop("thinkingBudget", None)  # 避免与 thinkingLevel 冲突

            # includeThoughts 逻辑:
            # 1. 如果是 pro 模型，为 return_thoughts
            # 2. 如果不是 pro 模型，检查是否有思考预算或思考等级
            base_model = get_base_model_name(model)
            if "pro" in base_model:
                include_thoughts = return_thoughts
            elif "3-flash" in base_model:
                if thinking_level is None:
                    include_thoughts = False
                else:
                    include_thoughts = return_thoughts
            else:
                # 非 pro 模型: 有思考预算或等级才包含思考
                # 注意: 思考预算为 0 时不包含思考
                if thinking_budget is None or thinking_budget == 0:
                    include_thoughts = False
                else:
                    include_thoughts = return_thoughts

            thinking_config["includeThoughts"] = include_thoughts

        # 2. 搜索模型添加 Google Search
        if is_search_model(model):
            result_tools = result.get("tools") or []
            result["tools"] = result_tools
            if not any(
                tool.get("googleSearch")
                for tool in result_tools
                if isinstance(tool, dict)
            ):
                result_tools.append({"googleSearch": {}})

        # 3. 模型名称处理
        result["model"] = get_base_model_name(model)

    elif mode == "antigravity":
        """
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
        """

        # 2. 判断图片模型
        if "image" in model.lower():
            # 调用图片生成专用处理函数
            return prepare_image_generation_request(result, model)
        else:
            # 3. 思考模型处理
            if is_thinking_model(model) or (
                "thinkingBudget" in generation_config.get("thinkingConfig", {})
                and generation_config["thinkingConfig"]["thinkingBudget"] != 0
            ):
                # 直接设置 thinkingConfig
                if "thinkingConfig" not in generation_config:
                    generation_config["thinkingConfig"] = {}

                thinking_config = generation_config["thinkingConfig"]
                # 优先使用传入的思考预算，否则使用默认值
                if "thinkingBudget" not in thinking_config:
                    thinking_config["thinkingBudget"] = 1024
                thinking_config.pop("thinkingLevel", None)  # 避免与 thinkingBudget 冲突
                thinking_config["includeThoughts"] = return_thoughts

                # 检查最后一个 assistant 消息是否以 thinking 块开始
                contents = result.get("contents", [])

                if "claude" in model.lower():
                    # 检测是否有工具调用（MCP场景）
                    has_tool_calls = any(
                        isinstance(content, dict)
                        and any(
                            isinstance(part, dict)
                            and ("functionCall" in part or "function_call" in part)
                            for part in content.get("parts", [])
                        )
                        for content in contents
                    )

                    if has_tool_calls:
                        # MCP 场景：检测到工具调用，移除 thinkingConfig
                        log.warning(
                            "[ANTIGRAVITY] 检测到工具调用（MCP场景），移除 thinkingConfig 避免失效"
                        )
                        generation_config.pop("thinkingConfig", None)
                    else:
                        # 非 MCP 场景：填充思考块
                        # log.warning(f"[ANTIGRAVITY] 最后一个 assistant 消息不以 thinking 块开始，自动填充思考块")

                        # 找到最后一个 model 角色的 content
                        for i in range(len(contents) - 1, -1, -1):
                            content = contents[i]
                            if (
                                isinstance(content, dict)
                                and content.get("role") == "model"
                            ):
                                # 在 parts 开头插入思考块（使用官方跳过验证的虚拟签名）
                                parts = content.get("parts", [])
                                thinking_part = {
                                    "text": "...",
                                    # "thought": True,  # 标记为思考块
                                    "thoughtSignature": "skip_thought_signature_validator",  # 官方文档推荐的虚拟签名
                                }
                                # 如果第一个 part 不是 thinking，则插入
                                if not parts or not (
                                    isinstance(parts[0], dict)
                                    and (
                                        "thought" in parts[0]
                                        or "thoughtSignature" in parts[0]
                                    )
                                ):
                                    content["parts"] = [thinking_part] + parts
                                    log.debug(
                                        "[ANTIGRAVITY] 已在最后一个 assistant 消息开头插入思考块（含跳过验证签名）"
                                    )
                                break

            # 移除 -thinking 后缀
            model = model.replace("-thinking", "")

            # 4. Claude 模型关键词映射
            # 使用关键词匹配而不是精确匹配，更灵活地处理各种变体
            original_model = model
            if "opus" in model.lower():
                model = "claude-opus-4-6-thinking"
            elif "sonnet" in model.lower():
                if "4-5" in model:
                    model = "claude-sonnet-4-5-thinking"
                else:
                    model = "claude-sonnet-4-6"
            elif "haiku" in model.lower():
                model = "gemini-2.5-flash"
            elif "claude" in model.lower():
                # Claude 模型兜底：如果包含 claude 但不是 opus/sonnet/haiku
                model = "claude-sonnet-4-6"

            result["model"] = model
            if original_model != model:
                log.debug(f"[ANTIGRAVITY] 映射模型: {original_model} -> {model}")

        # 5. 模型特殊处理：循环移除末尾的 model 消息，保证以用户消息结尾
        # 因为该模型不支持预填充
        if (
            "claude-opus-4-6-thinking" in model.lower()
            or "claude-sonnet-4-6" in model.lower()
        ):
            contents = result.get("contents", [])
            removed_count = 0
            while (
                contents
                and isinstance(contents[-1], dict)
                and contents[-1].get("role") == "model"
            ):
                contents.pop()
                removed_count += 1
            if removed_count > 0:
                log.warning(
                    f"[ANTIGRAVITY] {model} 不支持预填充，移除了 {removed_count} 条末尾 model 消息"
                )
                result["contents"] = contents

        # 6. 移除 antigravity 模式不支持的字段
        generation_config.pop("presencePenalty", None)
        generation_config.pop("frequencyPenalty", None)

    # ========== 公共处理 ==========

    # 1. 安全设置覆盖
    result["safetySettings"] = DEFAULT_SAFETY_SETTINGS

    # 2. 参数范围限制
    if generation_config:
        # 强制设置 maxOutputTokens 为 64000
        generation_config["maxOutputTokens"] = 64000
        # 强制设置 topK 为 64
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

                    part = part.copy()

                    # functionCall 场景需要 thoughtSignature，缺失时补齐占位符
                    # 兼容 thought_signature（snake_case）输入并统一为 thoughtSignature
                    if "functionCall" in part:
                        if (
                            "thoughtSignature" not in part
                            and "thought_signature" in part
                        ):
                            part["thoughtSignature"] = part["thought_signature"]
                        part.pop("thought_signature", None)
                        if "thoughtSignature" not in part:
                            part["thoughtSignature"] = (
                                "skip_thought_signature_validator"
                            )
                            log.debug(
                                "[GEMINI_FIX] functionCall 缺少 thoughtSignature，已补齐占位符"
                            )

                    # 检查 part 是否有有效的非空值
                    # 过滤掉空字典或所有值都为空的 part
                    # functionCall/functionResponse 豁免空值过滤
                    if "functionCall" in part or "functionResponse" in part:
                        has_valid_value = True
                    else:
                        has_valid_value = any(
                            value not in (None, "", {}, [])
                            for key, value in part.items()
                            if key != "thought"  # thought 字段可以为空
                        )

                    if has_valid_value:
                        # 修复 text 字段：确保是字符串而不是列表
                        if "text" in part:
                            text_value = part["text"]
                            if isinstance(text_value, list):
                                # 如果是列表，合并为字符串
                                log.warning(
                                    f"[GEMINI_FIX] text 字段是列表，自动合并: {text_value}"
                                )
                                part["text"] = " ".join(str(t) for t in text_value if t)
                            elif isinstance(text_value, str):
                                # 清理尾随空格
                                part["text"] = text_value.rstrip()
                            else:
                                # 其他类型转为字符串
                                log.warning(
                                    f"[GEMINI_FIX] text 字段类型异常 ({type(text_value)}), 转为字符串: {text_value}"
                                )
                                part["text"] = str(text_value)

                        valid_parts.append(part)
                    else:
                        log.warning(f"[GEMINI_FIX] 移除空的或无效的 part: {part}")

                # 只添加有有效 parts 的 content
                if valid_parts:
                    cleaned_content = content.copy()
                    cleaned_content["parts"] = valid_parts
                    cleaned_contents.append(cleaned_content)
                else:
                    log.warning(
                        f"[GEMINI_FIX] 跳过没有有效 parts 的 content: {content.get('role')}"
                    )
            else:
                cleaned_contents.append(content)

        result["contents"] = cleaned_contents
        result["contents"] = validate_function_call_pairs(result["contents"])

    if generation_config:
        result["generationConfig"] = generation_config

    return result
