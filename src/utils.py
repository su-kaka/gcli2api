import platform
import time
from datetime import datetime, timezone
from typing import List, Optional


CLI_VERSION = "0.1.5"  # Match current gemini-cli version

# ====================== Model Configuration ======================

# Default Safety Settings for Google API
DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_HATE", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_IMAGE_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_JAILBREAK", "threshold": "BLOCK_NONE"},
]

# Model name lists for different features
BASE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-3-pro-preview",
]

PUBLIC_API_MODELS = ["gemini-2.5-flash-image", "gemini-2.5-flash-image-preview"]


# ====================== Model Helper Functions ======================

def get_base_model_name(model_name: str) -> str:
    """Convert variant model name to base model name."""
    # Remove all possible suffixes (supports multiple suffixes in any order)
    suffixes = ["-maxthinking", "-nothinking", "-search"]
    result = model_name
    # Keep removing suffixes until no more matches
    changed = True
    while changed:
        changed = False
        for suffix in suffixes:
            if result.endswith(suffix):
                result = result[: -len(suffix)]
                changed = True
                break
    return result


def is_search_model(model_name: str) -> bool:
    """Check if model name indicates search grounding should be enabled."""
    return "-search" in model_name


def is_nothinking_model(model_name: str) -> bool:
    """Check if model name indicates thinking should be disabled."""
    return "-nothinking" in model_name


def is_maxthinking_model(model_name: str) -> bool:
    """Check if model name indicates maximum thinking budget should be used."""
    return "-maxthinking" in model_name


def get_thinking_budget(model_name: str) -> Optional[int]:
    """Get the appropriate thinking budget for a model based on its name and variant."""
    if is_nothinking_model(model_name):
        return 128  # Limited thinking for pro
    elif is_maxthinking_model(model_name):
        base_model = get_base_model_name(get_base_model_from_feature_model(model_name))
        if "flash" in base_model:
            return 24576
        return 32768
    else:
        # Default thinking budget for regular models
        return None  # Default for all models


def should_include_thoughts(model_name: str) -> bool:
    """Check if thoughts should be included in the response."""
    if is_nothinking_model(model_name):
        # For nothinking mode, still include thoughts if it's a pro model
        base_model = get_base_model_name(model_name)
        return "pro" in base_model
    else:
        # For all other modes, include thoughts
        return True


def is_fake_streaming_model(model_name: str) -> bool:
    """Check if model name indicates fake streaming should be used."""
    return model_name.startswith("假流式/")


def is_anti_truncation_model(model_name: str) -> bool:
    """Check if model name indicates anti-truncation should be used."""
    return model_name.startswith("流式抗截断/")


def get_base_model_from_feature_model(model_name: str) -> str:
    """Get base model name from feature model name."""
    # Remove feature prefixes
    for prefix in ["假流式/", "流式抗截断/"]:
        if model_name.startswith(prefix):
            return model_name[len(prefix) :]
    return model_name


def get_available_models(router_type: str = "openai") -> List[str]:
    """
    Get available models with feature prefixes.

    Args:
        router_type: "openai" or "gemini"

    Returns:
        List of model names with feature prefixes
    """
    models = []

    for base_model in BASE_MODELS:
        # 基础模型
        models.append(base_model)

        if base_model in PUBLIC_API_MODELS:
            continue

        # 假流式模型 (前缀格式)
        models.append(f"假流式/{base_model}")

        # 流式抗截断模型 (仅在流式传输时有效，前缀格式)
        models.append(f"流式抗截断/{base_model}")

        # 支持thinking模式后缀与功能前缀组合
        # 新增: 支持多后缀组合 (thinking + search)
        thinking_suffixes = ["-maxthinking", "-nothinking"]
        search_suffix = "-search"

        # 1. 单独的 thinking 后缀
        for thinking_suffix in thinking_suffixes:
            models.append(f"{base_model}{thinking_suffix}")
            models.append(f"假流式/{base_model}{thinking_suffix}")
            models.append(f"流式抗截断/{base_model}{thinking_suffix}")

        # 2. 单独的 search 后缀
        models.append(f"{base_model}{search_suffix}")
        models.append(f"假流式/{base_model}{search_suffix}")
        models.append(f"流式抗截断/{base_model}{search_suffix}")

        # 3. thinking + search 组合后缀
        for thinking_suffix in thinking_suffixes:
            combined_suffix = f"{thinking_suffix}{search_suffix}"
            models.append(f"{base_model}{combined_suffix}")
            models.append(f"假流式/{base_model}{combined_suffix}")
            models.append(f"流式抗截断/{base_model}{combined_suffix}")

    return models


def get_model_group(model_name: str) -> str:
    """
    获取模型组，用于 GCLI CD 机制。

    Args:
        model_name: 模型名称

    Returns:
        "pro" 或 "flash"

    说明:
        - pro 组: gemini-2.5-pro, gemini-3-pro-preview 共享额度
        - flash 组: gemini-2.5-flash 单独额度
    """
    # 去除功能前缀和后缀，获取基础模型名
    base_model = get_base_model_from_feature_model(model_name)
    base_model = get_base_model_name(base_model)

    # 判断模型组
    if "flash" in base_model.lower():
        return "flash"
    else:
        # pro 模型（包括 gemini-2.5-pro 和 gemini-3-pro-preview）
        return "pro"


# ====================== User Agent ======================


def get_user_agent():
    """Generate User-Agent string matching gemini-cli format."""
    version = CLI_VERSION
    system = platform.system()
    arch = platform.machine()
    return f"GeminiCLI/{version} ({system}; {arch})"


def parse_quota_reset_timestamp(error_response: dict) -> Optional[float]:
    """
    从Google API错误响应中提取quota重置时间戳

    Args:
        error_response: Google API返回的错误响应字典

    Returns:
        Unix时间戳（秒），如果无法解析则返回None

    示例错误响应:
    {
      "error": {
        "code": 429,
        "message": "You have exhausted your capacity...",
        "status": "RESOURCE_EXHAUSTED",
        "details": [
          {
            "@type": "type.googleapis.com/google.rpc.ErrorInfo",
            "reason": "QUOTA_EXHAUSTED",
            "metadata": {
              "quotaResetTimeStamp": "2025-11-30T14:57:24Z",
              "quotaResetDelay": "13h19m1.20964964s"
            }
          }
        ]
      }
    }
    """
    try:
        details = error_response.get("error", {}).get("details", [])

        for detail in details:
            if detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
                reset_timestamp_str = detail.get("metadata", {}).get("quotaResetTimeStamp")

                if reset_timestamp_str:
                    if reset_timestamp_str.endswith("Z"):
                        reset_timestamp_str = reset_timestamp_str.replace("Z", "+00:00")

                    reset_dt = datetime.fromisoformat(reset_timestamp_str)
                    if reset_dt.tzinfo is None:
                        reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                    return reset_dt.astimezone(timezone.utc).timestamp()

        return None

    except Exception:
        return None
