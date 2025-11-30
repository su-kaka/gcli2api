import platform
import time
from datetime import datetime, timezone
from typing import Optional


CLI_VERSION = "0.1.5"  # Match current gemini-cli version


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
        error = error_response.get("error", {})
        details = error.get("details", [])

        for detail in details:
            # 查找包含quota重置信息的detail
            if detail.get("@type") == "type.googleapis.com/google.rpc.ErrorInfo":
                metadata = detail.get("metadata", {})
                reset_timestamp_str = metadata.get("quotaResetTimeStamp")

                if reset_timestamp_str:
                    # 解析ISO 8601格式的时间戳
                    # 支持格式: "2025-11-30T14:57:24Z" 或 "2025-11-30T14:57:24+00:00"
                    if reset_timestamp_str.endswith("Z"):
                        reset_timestamp_str = reset_timestamp_str.replace("Z", "+00:00")

                    reset_dt = datetime.fromisoformat(reset_timestamp_str)

                    # 确保时区信息
                    if reset_dt.tzinfo is None:
                        reset_dt = reset_dt.replace(tzinfo=timezone.utc)

                    # 转换为Unix时间戳
                    return reset_dt.timestamp()

            # 也尝试从RetryInfo中提取延迟时间（作为备用）
            elif detail.get("@type") == "type.googleapis.com/google.rpc.RetryInfo":
                retry_delay_str = detail.get("retryDelay")
                if retry_delay_str:
                    # 解析延迟时间格式: "47941.209649640s"
                    if retry_delay_str.endswith("s"):
                        try:
                            delay_seconds = float(retry_delay_str[:-1])
                            return time.time() + delay_seconds
                        except (ValueError, TypeError):
                            pass

        return None

    except Exception as e:
        # 解析失败时不抛出异常，返回None
        return None
