"""
Configuration constants for the Geminicli2api proxy server.
Centralizes all configuration to avoid duplication across modules.
"""
import os
import toml
from typing import Any, Optional

# Client Configuration
CLI_VERSION = "0.1.5"  # Match current gemini-cli version

# 凭证目录
CREDENTIALS_DIR = os.getenv("CREDENTIALS_DIR", "./creds")

# 自动封禁配置
AUTO_BAN_ENABLED = os.getenv("AUTO_BAN", "false").lower() in ("true", "1", "yes", "on")

# 需要自动封禁的错误码 (可通过环境变量 AUTO_BAN_ERROR_CODES 覆盖)
AUTO_BAN_ERROR_CODES = [400, 401, 403]


# Default Safety Settings for Google API
DEFAULT_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
]

# Helper function to get base model name from any variant
def get_base_model_name(model_name):
    """Convert variant model name to base model name."""
    # Remove all possible suffixes in order
    suffixes = ["-maxthinking", "-nothinking", "-search"]
    for suffix in suffixes:
        if model_name.endswith(suffix):
            return model_name[:-len(suffix)]
    return model_name

# Helper function to check if model uses search grounding
def is_search_model(model_name):
    """Check if model name indicates search grounding should be enabled."""
    return "-search" in model_name

# Helper function to check if model uses no thinking
def is_nothinking_model(model_name):
    """Check if model name indicates thinking should be disabled."""
    return "-nothinking" in model_name

# Helper function to check if model uses max thinking
def is_maxthinking_model(model_name):
    """Check if model name indicates maximum thinking budget should be used."""
    return "-maxthinking" in model_name

# Helper function to get thinking budget for a model
def get_thinking_budget(model_name):
    """Get the appropriate thinking budget for a model based on its name and variant."""
    
    if is_nothinking_model(model_name):
        return 128  # Limited thinking for pro
    elif is_maxthinking_model(model_name):
        return 32768
    else:
        # Default thinking budget for regular models
        return -1  # Default for all models

# Helper function to check if thinking should be included in output
def should_include_thoughts(model_name):
    """Check if thoughts should be included in the response."""
    if is_nothinking_model(model_name):
        # For nothinking mode, still include thoughts if it's a pro model
        base_model = get_base_model_name(model_name)
        return "gemini-2.5-pro" in base_model
    else:
        # For all other modes, include thoughts
        return True

# Dynamic Configuration System - Optimized for memory efficiency
def _load_toml_config() -> dict:
    """Load configuration from dedicated config.toml file directly from disk."""
    try:
        config_file = os.path.join(CREDENTIALS_DIR, "config.toml")
        
        # Check if file exists
        if not os.path.exists(config_file):
            return {}
        
        # Load config directly from disk each time
        with open(config_file, "r", encoding="utf-8") as f:
            toml_data = toml.load(f)
        
        return toml_data
    
    except Exception:
        return {}

def get_config_value(key: str, default: Any = None, env_var: Optional[str] = None) -> Any:
    """Get configuration value with priority: ENV > TOML > default."""
    # Check environment variable first
    if env_var and os.getenv(env_var):
        return os.getenv(env_var)
    
    # Check TOML configuration
    toml_config = _load_toml_config()
    if key in toml_config:
        return toml_config[key]
    
    # Return default
    return default

def save_config_to_toml(config_data: dict) -> None:
    """Save configuration to config.toml file."""
    try:
        config_file = os.path.join(CREDENTIALS_DIR, "config.toml")
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        
        with open(config_file, "w", encoding="utf-8") as f:
            toml.dump(config_data, f)
        
    except Exception as e:
        raise Exception(f"Failed to save config: {e}")

def reload_config_cache() -> None:
    """Reload configuration - now a no-op since we read directly from disk."""
    pass  # No cache to reload since we read from disk each time

# Proxy Configuration
def get_proxy_config():
    """Get proxy configuration from PROXY environment variable or TOML config."""
    proxy_url = get_config_value("proxy", env_var="PROXY")
    if not proxy_url:
        return None
    
    # httpx supports http, https, socks5 proxies
    # Format: http://proxy:port, https://proxy:port, socks5://proxy:port
    return proxy_url

# Dynamic configuration getters
def get_calls_per_rotation() -> int:
    """
    Get calls per rotation setting.
    
    Environment variable: CALLS_PER_ROTATION
    TOML config key: calls_per_rotation
    Default: 100
    """
    env_value = os.getenv("CALLS_PER_ROTATION")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(get_config_value("calls_per_rotation", 100))


def get_auto_ban_enabled() -> bool:
    """Get auto ban enabled setting."""
    env_value = os.getenv("AUTO_BAN")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(get_config_value("auto_ban_enabled", AUTO_BAN_ENABLED))

def get_auto_ban_error_codes() -> list:
    """
    Get auto ban error codes.
    
    Environment variable: AUTO_BAN_ERROR_CODES (comma-separated, e.g., "400,403")
    TOML config key: auto_ban_error_codes
    Default: [400, 403]
    """
    env_value = os.getenv("AUTO_BAN_ERROR_CODES")
    if env_value:
        try:
            return [int(code.strip()) for code in env_value.split(",") if code.strip()]
        except ValueError:
            pass
    
    toml_codes = get_config_value("auto_ban_error_codes")
    if toml_codes and isinstance(toml_codes, list):
        return toml_codes
    return AUTO_BAN_ERROR_CODES

def get_retry_429_max_retries() -> int:
    """Get max retries for 429 errors."""
    env_value = os.getenv("RETRY_429_MAX_RETRIES")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(get_config_value("retry_429_max_retries", 5))

def get_retry_429_enabled() -> bool:
    """Get 429 retry enabled setting."""
    env_value = os.getenv("RETRY_429_ENABLED")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(get_config_value("retry_429_enabled", True))

def get_retry_429_interval() -> float:
    """Get 429 retry interval in seconds."""
    env_value = os.getenv("RETRY_429_INTERVAL")
    if env_value:
        try:
            return float(env_value)
        except ValueError:
            pass
    
    return float(get_config_value("retry_429_interval", 1))

def get_log_level() -> str:
    """
    Get log level.
    
    Environment variable: LOG_LEVEL
    TOML config key: log_level
    Default: info
    Valid values: debug, info, warning, error, critical
    """
    level = get_config_value("log_level", "info", "LOG_LEVEL")
    if isinstance(level, str):
        level = level.lower()
        if level in ["debug", "info", "warning", "error", "critical"]:
            return level
    return "info"

def get_log_file() -> str:
    """
    Get log file path.
    
    Environment variable: LOG_FILE
    TOML config key: log_file
    Default: log.txt
    """
    return str(get_config_value("log_file", "log.txt", "LOG_FILE"))

# Model name lists for different features
BASE_MODELS = [
    "gemini-2.5-pro-preview-06-05",
    "gemini-2.5-pro", 
    "gemini-2.5-pro-preview-05-06",
    "gemini-2.5-flash",
]

def get_available_models(router_type="openai"):
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
        
        # 假流式模型 (前缀格式)
        models.append(f"假流式/{base_model}")
        
        # 流式抗截断模型 (仅在流式传输时有效，前缀格式)
        models.append(f"流式抗截断/{base_model}")
        
        # 支持thinking模式后缀与功能前缀组合
        for thinking_suffix in ["-maxthinking", "-nothinking", "-search"]:
            # 基础模型 + thinking后缀
            models.append(f"{base_model}{thinking_suffix}")
            
            # 假流式 + thinking后缀
            models.append(f"假流式/{base_model}{thinking_suffix}")
            
            # 流式抗截断 + thinking后缀
            models.append(f"流式抗截断/{base_model}{thinking_suffix}")
    
    return models

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
            return model_name[len(prefix):]
    return model_name

def get_anti_truncation_max_attempts() -> int:
    """
    Get maximum attempts for anti-truncation continuation.
    
    Environment variable: ANTI_TRUNCATION_MAX_ATTEMPTS
    TOML config key: anti_truncation_max_attempts
    Default: 3
    """
    env_value = os.getenv("ANTI_TRUNCATION_MAX_ATTEMPTS")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(get_config_value("anti_truncation_max_attempts", 3))

# Server Configuration
def get_server_host() -> str:
    """
    Get server host setting.
    
    Environment variable: HOST
    TOML config key: host
    Default: 0.0.0.0
    """
    return str(get_config_value("host", "0.0.0.0", "HOST"))

def get_server_port() -> int:
    """
    Get server port setting.
    
    Environment variable: PORT
    TOML config key: port
    Default: 7861
    """
    env_value = os.getenv("PORT")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(get_config_value("port", 7861))

def get_api_password() -> str:
    """
    Get API password setting for chat endpoints.
    
    Environment variable: API_PASSWORD
    TOML config key: api_password
    Default: Uses PASSWORD env var for compatibility, otherwise 'pwd'
    """
    # 优先使用 API_PASSWORD，如果没有则使用通用 PASSWORD 保证兼容性
    api_password = get_config_value("api_password", None, "API_PASSWORD")
    if api_password:
        return str(api_password)
    
    # 兼容性：使用通用密码
    return str(get_config_value("password", "pwd", "PASSWORD"))

def get_panel_password() -> str:
    """
    Get panel password setting for web interface.
    
    Environment variable: PANEL_PASSWORD
    TOML config key: panel_password
    Default: Uses PASSWORD env var for compatibility, otherwise 'pwd'
    """
    # 优先使用 PANEL_PASSWORD，如果没有则使用通用 PASSWORD 保证兼容性
    panel_password = get_config_value("panel_password", None, "PANEL_PASSWORD")
    if panel_password:
        return str(panel_password)
    
    # 兼容性：使用通用密码
    return str(get_config_value("password", "pwd", "PASSWORD"))

def get_server_password() -> str:
    """
    Get server password setting (deprecated, use get_api_password or get_panel_password).
    
    Environment variable: PASSWORD
    TOML config key: password
    Default: pwd
    """
    return str(get_config_value("password", "pwd", "PASSWORD"))

def get_credentials_dir() -> str:
    """
    Get credentials directory setting.
    
    Environment variable: CREDENTIALS_DIR
    TOML config key: credentials_dir
    Default: ./creds
    """
    return str(get_config_value("credentials_dir", CREDENTIALS_DIR, "CREDENTIALS_DIR"))

def get_code_assist_endpoint() -> str:
    """
    Get Code Assist endpoint setting.
    
    Environment variable: CODE_ASSIST_ENDPOINT
    TOML config key: code_assist_endpoint
    Default: https://cloudcode-pa.googleapis.com
    """
    return str(get_config_value("code_assist_endpoint", "https://cloudcode-pa.googleapis.com", "CODE_ASSIST_ENDPOINT"))

def get_auto_load_env_creds() -> bool:
    """
    Get auto load environment credentials setting.
    
    Environment variable: AUTO_LOAD_ENV_CREDS
    TOML config key: auto_load_env_creds
    Default: False
    """
    env_value = os.getenv("AUTO_LOAD_ENV_CREDS")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(get_config_value("auto_load_env_creds", False))

def get_compatibility_mode_enabled() -> bool:
    """
    Get compatibility mode setting.
    
    兼容性模式：启用后所有system消息全部转换成user，停用system_instructions。
    该选项可能会降低模型理解能力，但是能避免流式空回的情况。
    
    Environment variable: COMPATIBILITY_MODE
    TOML config key: compatibility_mode_enabled
    Default: True
    """
    env_value = os.getenv("COMPATIBILITY_MODE")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(get_config_value("compatibility_mode_enabled", True))

def get_oauth_proxy_url() -> str:
    """
    Get OAuth proxy URL setting.
    
    用于Google OAuth2认证的代理URL。
    
    Environment variable: OAUTH_PROXY_URL
    TOML config key: oauth_proxy_url
    Default: https://oauth2.googleapis.com
    """
    return str(get_config_value("oauth_proxy_url", "https://oauth2.googleapis.com", "OAUTH_PROXY_URL"))

def get_googleapis_proxy_url() -> str:
    """
    Get Google APIs proxy URL setting.
    
    用于Google APIs调用的代理URL。
    
    Environment variable: GOOGLEAPIS_PROXY_URL
    TOML config key: googleapis_proxy_url
    Default: https://www.googleapis.com
    """
    return str(get_config_value("googleapis_proxy_url", "https://www.googleapis.com", "GOOGLEAPIS_PROXY_URL"))


def get_resource_manager_api_url() -> str:
    """
    Get Google Cloud Resource Manager API URL setting.
    
    用于Google Cloud Resource Manager API的URL。
    
    Environment variable: RESOURCE_MANAGER_API_URL
    TOML config key: resource_manager_api_url
    Default: https://cloudresourcemanager.googleapis.com
    """
    return str(get_config_value("resource_manager_api_url", "https://cloudresourcemanager.googleapis.com", "RESOURCE_MANAGER_API_URL"))

def get_service_usage_api_url() -> str:
    """
    Get Google Cloud Service Usage API URL setting.
    
    用于Google Cloud Service Usage API的URL。
    
    Environment variable: SERVICE_USAGE_API_URL
    TOML config key: service_usage_api_url
    Default: https://serviceusage.googleapis.com
    """
    return str(get_config_value("service_usage_api_url", "https://serviceusage.googleapis.com", "SERVICE_USAGE_API_URL"))