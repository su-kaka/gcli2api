"""
Configuration constants for the Geminicli2api proxy server.
Centralizes all configuration to avoid duplication across modules.

- 启动时加载一次配置到内存
- 修改配置时调用 reload_config() 重新从数据库加载
"""

import os
import re
from typing import Any, Optional

from log import log

# 全局配置缓存
_config_cache: dict[str, Any] = {}
_config_initialized = False

# Client Configuration

# 需要自动封禁的错误码 (默认值，可通过环境变量或配置覆盖)
AUTO_BAN_ERROR_CODES = [403]

# ====================== 环境变量映射表 ======================
# 统一维护环境变量名和配置键名的映射关系
# 格式: "环境变量名": "配置键名"
ENV_MAPPINGS = {
    "CREDENTIALS_DIR": "credentials_dir",
    "PROXY": "proxy",
    "OAUTH_PROXY_URL": "oauth_proxy_url",
    "GOOGLEAPIS_PROXY_URL": "googleapis_proxy_url",
    "RESOURCE_MANAGER_API_URL": "resource_manager_api_url",
    "SERVICE_USAGE_API_URL": "service_usage_api_url",
    "ANTIGRAVITY_API_URL": "antigravity_api_url",
    "AUTO_BAN": "auto_ban_enabled",
    "AUTO_BAN_ERROR_CODES": "auto_ban_error_codes",
    "RETRY_429_MAX_RETRIES": "retry_429_max_retries",
    "RETRY_429_ENABLED": "retry_429_enabled",
    "RETRY_429_INTERVAL": "retry_429_interval",
    "ANTI_TRUNCATION_MAX_ATTEMPTS": "anti_truncation_max_attempts",
    "RETURN_THOUGHTS_TO_FRONTEND": "return_thoughts_to_frontend",
    "SENSITIVE_WORDS": "sensitive_words",
    "SENSITIVE_WORDS_ENABLED": "sensitive_words_enabled",
    "USAGE_LOG_ENABLED": "usage_log_enabled",
    "CREDENTIAL_SELECT_MODE": "credential_select_mode",
    "HOST": "host",
    "PORT": "port",
    "API_PASSWORD": "api_password",
    "PANEL_PASSWORD": "panel_password",
}


# ====================== 配置系统 ======================

async def init_config():
    """初始化配置缓存（启动时调用一次）"""
    global _config_cache, _config_initialized

    if _config_initialized:
        return

    try:
        from src.storage.database import get_database
        database = await get_database()
        _config_cache = await database.get_all_config()
        _config_initialized = True
    except Exception:
        # 初始化失败时使用空缓存
        _config_cache = {}
        _config_initialized = True


async def reload_config():
    """重新加载配置（修改配置后调用）"""
    global _config_cache, _config_initialized

    try:
        from src.storage.database import get_database
        database = await get_database()

        # 重新加载数据库配置缓存
        await database.reload_config_cache()

        # 重新加载配置缓存
        _config_cache = await database.get_all_config()
        _config_initialized = True
    except Exception:
        pass


def _get_cached_config(key: str, default: Any = None) -> Any:
    """从内存缓存获取配置（同步）"""
    return _config_cache.get(key, default)


async def get_config_value(key: str, default: Any = None, env_var: Optional[str] = None) -> Any:
    """Get configuration value with priority: ENV > Storage > default."""
    # 确保配置已初始化
    if not _config_initialized:
        await init_config()

    # Priority 1: Environment variable
    if env_var and os.getenv(env_var):
        return os.getenv(env_var)

    # Priority 2: Memory cache
    value = _get_cached_config(key)
    if value is not None:
        return value

    return default


# Configuration getters - all async
async def get_proxy_config():
    """Get proxy configuration, validating the URL format.

    Supports http://, https://, socks5://, socks5h:// schemes, all with
    optional embedded credentials (user:pass@host:port).
    """
    proxy_url = await get_config_value("proxy", env_var="PROXY")
    if not proxy_url:
        return None

    proxy_url = proxy_url.strip()
    scheme = proxy_url.split("://", 1)[0].lower()
    if scheme not in ("http", "https", "socks5", "socks5h"):
        log.warning(
            f"不支持的代理协议: {scheme}://（支持 http/https/socks5/socks5h），已忽略代理"
        )
        return None
    return proxy_url


async def get_auto_ban_enabled() -> bool:
    """Get auto ban enabled setting."""
    env_value = os.getenv("AUTO_BAN")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("auto_ban_enabled", False))


async def get_auto_ban_error_codes() -> list:
    """
    Get auto ban error codes.

    Environment variable: AUTO_BAN_ERROR_CODES (comma-separated, e.g., "400,403")
    Database config key: auto_ban_error_codes
    Default: [400, 403]
    """
    env_value = os.getenv("AUTO_BAN_ERROR_CODES")
    if env_value:
        try:
            return [int(code.strip()) for code in env_value.split(",") if code.strip()]
        except ValueError:
            pass

    codes = await get_config_value("auto_ban_error_codes")
    if codes and isinstance(codes, list):
        return codes
    return AUTO_BAN_ERROR_CODES


async def get_retry_429_max_retries() -> int:
    """Get max retries for 429 errors."""
    env_value = os.getenv("RETRY_429_MAX_RETRIES")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass

    return int(await get_config_value("retry_429_max_retries", 5))


async def get_retry_429_enabled() -> bool:
    """Get 429 retry enabled setting."""
    env_value = os.getenv("RETRY_429_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("retry_429_enabled", True))


async def get_retry_429_interval() -> float:
    """Get 429 retry interval in seconds."""
    env_value = os.getenv("RETRY_429_INTERVAL")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            pass

    return float(await get_config_value("retry_429_interval", 1))


async def get_anti_truncation_max_attempts() -> int:
    """
    Get maximum attempts for anti-truncation continuation.

    Environment variable: ANTI_TRUNCATION_MAX_ATTEMPTS
    Database config key: anti_truncation_max_attempts
    Default: 3
    """
    env_value = os.getenv("ANTI_TRUNCATION_MAX_ATTEMPTS")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass

    return int(await get_config_value("anti_truncation_max_attempts", 3))


# Server Configuration
async def get_server_host() -> str:
    """
    Get server host setting.

    Environment variable: HOST
    Database config key: host
    Default: 0.0.0.0
    """
    return str(await get_config_value("host", "0.0.0.0", "HOST"))


async def get_server_port() -> int:
    """
    Get server port setting.

    Environment variable: PORT
    Database config key: port
    Default: 7861
    """
    env_value = os.getenv("PORT")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass

    return int(await get_config_value("port", 7861))


async def get_api_password() -> str:
    """
    Get API password setting for chat endpoints.

    Environment variable: API_PASSWORD
    Database config key: api_password
    Default: pwd
    """
    return str(await get_config_value("api_password", "pwd", "API_PASSWORD"))


async def get_panel_password() -> str:
    """
    Get panel password setting for web interface.

    Environment variable: PANEL_PASSWORD
    Database config key: panel_password
    Default: pwd
    """
    return str(await get_config_value("panel_password", "pwd", "PANEL_PASSWORD"))


async def get_credentials_dir() -> str:
    """
    Get credentials directory setting.

    Environment variable: CREDENTIALS_DIR
    Database config key: credentials_dir
    Default: ./creds
    """
    return str(await get_config_value("credentials_dir", "./creds", "CREDENTIALS_DIR"))

async def get_return_thoughts_to_frontend() -> bool:
    """
    Get return thoughts to frontend setting.

    控制是否将思维链返回到前端。
    启用后，思维链会在响应中返回；禁用后，思维链会在响应中被过滤掉。

    Environment variable: RETURN_THOUGHTS_TO_FRONTEND
    Database config key: return_thoughts_to_frontend
    Default: True
    """
    env_value = os.getenv("RETURN_THOUGHTS_TO_FRONTEND")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("return_thoughts_to_frontend", True))


# ====================== 敏感词过滤 ======================

# 敏感词正则编译缓存: (原始词表指纹, 编译后的 pattern 列表)
_sensitive_words_cache: dict = {"fingerprint": None, "patterns": []}


async def get_sensitive_words_enabled() -> bool:
    """
    敏感词过滤开关。

    Environment variable: SENSITIVE_WORDS_ENABLED
    Database config key: sensitive_words_enabled
    Default: True
    """
    env_value = os.getenv("SENSITIVE_WORDS_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("sensitive_words_enabled", True))


async def get_sensitive_words() -> list:
    """
    敏感词列表（支持正则），每行一个；空列表表示不过滤。

    Environment variable: SENSITIVE_WORDS (逗号或换行分隔)
    Database config key: sensitive_words
    Default: []
    """
    env_value = os.getenv("SENSITIVE_WORDS")
    if env_value:
        words = [w.strip() for w in re.split(r"[,\n]", env_value) if w.strip()]
        return words

    value = await get_config_value("sensitive_words")
    if isinstance(value, list):
        return [str(w) for w in value if str(w).strip()]
    if isinstance(value, str) and value.strip():
        return [w.strip() for w in re.split(r"[,\n]", value) if w.strip()]
    return []


def compile_sensitive_words(words: list):
    """
    编译敏感词为大小写不敏感的正则列表（带缓存）。

    每个词按字面量转义后编译（用户输入的是词/短语而非正则语法），
    触发后整词替换为空。支持中文（re.UNICODE 默认开启）。

    Returns:
        编译后的 Pattern 列表；无效条目跳过并告警。
    """
    fingerprint = tuple(words)
    if _sensitive_words_cache["fingerprint"] == fingerprint:
        return _sensitive_words_cache["patterns"]

    patterns = []
    for word in words:
        word = str(word).strip()
        if not word:
            continue
        try:
            patterns.append(re.compile(re.escape(word), re.IGNORECASE | re.UNICODE))
        except re.error as e:
            log.warning(f"敏感词编译失败，已跳过: {word!r} ({e})")

    _sensitive_words_cache["fingerprint"] = fingerprint
    _sensitive_words_cache["patterns"] = patterns
    return patterns


def apply_sensitive_word_filter(text: str, patterns) -> str:
    """将文本中命中的敏感词（大小写不敏感）替换为空"""
    if not text or not patterns:
        return text
    for pattern in patterns:
        text = pattern.sub("", text)
    return text


async def get_sensitive_word_filter():
    """
    获取生效的敏感词过滤条件（供请求处理管道调用）。

    Returns:
        (enabled, patterns)；未启用时 patterns 为空列表。
    """
    if not await get_sensitive_words_enabled():
        return False, []
    words = await get_sensitive_words()
    if not words:
        return False, []
    return True, compile_sensitive_words(words)


# ====================== 请求日志 ======================

async def get_usage_log_enabled() -> bool:
    """
    请求日志（usage_log）开关。

    Environment variable: USAGE_LOG_ENABLED
    Database config key: usage_log_enabled
    Default: True
    """
    env_value = os.getenv("USAGE_LOG_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")

    return bool(await get_config_value("usage_log_enabled", True))


# ====================== 凭证选择模式 ======================

# 合法的凭证选择模式
CREDENTIAL_SELECT_MODES = ("random", "cache", "sticky")


async def get_credential_select_mode() -> str:
    """
    凭证选择模式:
    - random: 完全随机负载均衡（无亲和）
    - cache:  会话缓存匹配（默认）——相同会话内容复用同一凭证，命中上游 prompt cache
    - sticky: 凭证粘性——尽量保持同一凭证直到其进入 CD 或被禁用

    Environment variable: CREDENTIAL_SELECT_MODE
    Database config key: credential_select_mode
    Default: cache
    """
    value = await get_config_value("credential_select_mode", "cache", "CREDENTIAL_SELECT_MODE")
    value = str(value).strip().lower()
    if value not in CREDENTIAL_SELECT_MODES:
        log.warning(f"无效的凭证选择模式: {value}，回落为 cache")
        return "cache"
    return value


async def get_oauth_proxy_url() -> str:
    """
    Get OAuth proxy URL setting.

    用于Google OAuth2认证的代理URL。

    Environment variable: OAUTH_PROXY_URL
    Database config key: oauth_proxy_url
    Default: https://oauth2.googleapis.com
    """
    return str(
        await get_config_value(
            "oauth_proxy_url", "https://oauth2.googleapis.com", "OAUTH_PROXY_URL"
        )
    )


async def get_googleapis_proxy_url() -> str:
    """
    Get Google APIs proxy URL setting.

    用于Google APIs调用的代理URL。

    Environment variable: GOOGLEAPIS_PROXY_URL
    Database config key: googleapis_proxy_url
    Default: https://www.googleapis.com
    """
    return str(
        await get_config_value(
            "googleapis_proxy_url", "https://www.googleapis.com", "GOOGLEAPIS_PROXY_URL"
        )
    )


async def get_resource_manager_api_url() -> str:
    """
    Get Google Cloud Resource Manager API URL setting.

    用于Google Cloud Resource Manager API的URL。

    Environment variable: RESOURCE_MANAGER_API_URL
    Database config key: resource_manager_api_url
    Default: https://cloudresourcemanager.googleapis.com
    """
    return str(
        await get_config_value(
            "resource_manager_api_url",
            "https://cloudresourcemanager.googleapis.com",
            "RESOURCE_MANAGER_API_URL",
        )
    )


async def get_service_usage_api_url() -> str:
    """
    Get Google Cloud Service Usage API URL setting.

    用于Google Cloud Service Usage API的URL。

    Environment variable: SERVICE_USAGE_API_URL
    Database config key: service_usage_api_url
    Default: https://serviceusage.googleapis.com
    """
    return str(
        await get_config_value(
            "service_usage_api_url", "https://serviceusage.googleapis.com", "SERVICE_USAGE_API_URL"
        )
    )


async def get_antigravity_api_url() -> str:
    """
    Get Antigravity API URL setting.

    用于Google Antigravity API的URL。

    Environment variable: ANTIGRAVITY_API_URL
    Database config key: antigravity_api_url
    Default: https://daily-cloudcode-pa.googleapis.com
    """
    return str(
        await get_config_value(
            "antigravity_api_url",
            "https://daily-cloudcode-pa.googleapis.com",
            "ANTIGRAVITY_API_URL",
        )
    )
