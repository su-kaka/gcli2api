"""
Configuration constants for the Geminicli2api proxy server.
Centralizes all configuration to avoid duplication across modules.
"""
import os
from typing import Any, Optional

# Client Configuration

# 需要自动封禁的错误码 (默认值，可通过环境变量或配置覆盖)
AUTO_BAN_ERROR_CODES = [401, 403]

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
async def get_config_value(key: str, default: Any = None, env_var: Optional[str] = None) -> Any:
    """Get configuration value with priority: ENV > Storage > default."""
    # Priority 1: Environment variable
    if env_var and os.getenv(env_var):
        return os.getenv(env_var)
    
    # Priority 2: Storage system
    try:
        from src.storage_adapter import get_storage_adapter
        storage_adapter = await get_storage_adapter()
        value = await storage_adapter.get_config(key)
        # 检查值是否存在（不是None），允许空字符串、0、False等有效值
        if value is not None:
            return value
    except Exception as e:
        # Debug: print import/storage errors
        # print(f"Config storage error for key {key}: {e}")
        pass
    
    return default


# Configuration getters - all async
async def get_proxy_config():
    """Get proxy configuration."""
    proxy_url = await get_config_value("proxy", env_var="PROXY")
    return proxy_url if proxy_url else None

async def get_calls_per_rotation() -> int:
    """Get calls per rotation setting."""
    env_value = os.getenv("CALLS_PER_ROTATION")
    if env_value:
        try:
            return int(env_value)
        except ValueError:
            pass
    
    return int(await get_config_value("calls_per_rotation", 100))

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
    TOML config key: auto_ban_error_codes
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

async def get_anti_truncation_max_attempts() -> int:
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
    
    return int(await get_config_value("anti_truncation_max_attempts", 3))

# Server Configuration
async def get_server_host() -> str:
    """
    Get server host setting.
    
    Environment variable: HOST
    TOML config key: host
    Default: 0.0.0.0
    """
    return str(await get_config_value("host", "0.0.0.0", "HOST"))

async def get_server_port() -> int:
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
    
    return int(await get_config_value("port", 7861))

async def get_api_password() -> str:
    """
    Get API password setting for chat endpoints.
    
    Environment variable: API_PASSWORD
    TOML config key: api_password
    Default: Uses PASSWORD env var for compatibility, otherwise 'pwd'
    """
    # 优先使用 API_PASSWORD，如果没有则使用通用 PASSWORD 保证兼容性
    api_password = await get_config_value("api_password", None, "API_PASSWORD")
    if api_password is not None:
        return str(api_password)
    
    # 兼容性：使用通用密码
    return str(await get_config_value("password", "pwd", "PASSWORD"))

async def get_panel_password() -> str:
    """
    Get panel password setting for web interface.
    
    Environment variable: PANEL_PASSWORD
    TOML config key: panel_password
    Default: Uses PASSWORD env var for compatibility, otherwise 'pwd'
    """
    # 优先使用 PANEL_PASSWORD，如果没有则使用通用 PASSWORD 保证兼容性
    panel_password = await get_config_value("panel_password", None, "PANEL_PASSWORD")
    if panel_password is not None:
        return str(panel_password)
    
    # 兼容性：使用通用密码
    return str(await get_config_value("password", "pwd", "PASSWORD"))

async def get_server_password() -> str:
    """
    Get server password setting (deprecated, use get_api_password or get_panel_password).
    
    Environment variable: PASSWORD
    TOML config key: password
    Default: pwd
    """
    return str(await get_config_value("password", "pwd", "PASSWORD"))

async def get_credentials_dir() -> str:
    """
    Get credentials directory setting.
    
    Environment variable: CREDENTIALS_DIR
    TOML config key: credentials_dir
    Default: ./creds
    """
    return str(await get_config_value("credentials_dir", "./creds", "CREDENTIALS_DIR"))

async def get_code_assist_endpoint() -> str:
    """
    Get Code Assist endpoint setting.
    
    Environment variable: CODE_ASSIST_ENDPOINT
    TOML config key: code_assist_endpoint
    Default: https://cloudcode-pa.googleapis.com
    """
    return str(await get_config_value("code_assist_endpoint", "https://cloudcode-pa.googleapis.com", "CODE_ASSIST_ENDPOINT"))

async def get_auto_load_env_creds() -> bool:
    """
    Get auto load environment credentials setting.
    
    Environment variable: AUTO_LOAD_ENV_CREDS
    TOML config key: auto_load_env_creds
    Default: False
    """
    env_value = os.getenv("AUTO_LOAD_ENV_CREDS")
    if env_value:
        return env_value.lower() in ("true", "1", "yes", "on")
    
    return bool(await get_config_value("auto_load_env_creds", False))

async def get_compatibility_mode_enabled() -> bool:
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
    
    return bool(await get_config_value("compatibility_mode_enabled", True))

async def get_oauth_proxy_url() -> str:
    """
    Get OAuth proxy URL setting.
    
    用于Google OAuth2认证的代理URL。
    
    Environment variable: OAUTH_PROXY_URL
    TOML config key: oauth_proxy_url
    Default: https://oauth2.googleapis.com
    """
    return str(await get_config_value("oauth_proxy_url", "https://oauth2.googleapis.com", "OAUTH_PROXY_URL"))

async def get_googleapis_proxy_url() -> str:
    """
    Get Google APIs proxy URL setting.
    
    用于Google APIs调用的代理URL。
    
    Environment variable: GOOGLEAPIS_PROXY_URL
    TOML config key: googleapis_proxy_url
    Default: https://www.googleapis.com
    """
    return str(await get_config_value("googleapis_proxy_url", "https://www.googleapis.com", "GOOGLEAPIS_PROXY_URL"))


async def get_resource_manager_api_url() -> str:
    """
    Get Google Cloud Resource Manager API URL setting.
    
    用于Google Cloud Resource Manager API的URL。
    
    Environment variable: RESOURCE_MANAGER_API_URL
    TOML config key: resource_manager_api_url
    Default: https://cloudresourcemanager.googleapis.com
    """
    return str(await get_config_value("resource_manager_api_url", "https://cloudresourcemanager.googleapis.com", "RESOURCE_MANAGER_API_URL"))

async def get_service_usage_api_url() -> str:
    """
    Get Google Cloud Service Usage API URL setting.
    
    用于Google Cloud Service Usage API的URL。
    
    Environment variable: SERVICE_USAGE_API_URL
    TOML config key: service_usage_api_url
    Default: https://serviceusage.googleapis.com
    """
    return str(await get_config_value("service_usage_api_url", "https://serviceusage.googleapis.com", "SERVICE_USAGE_API_URL"))


# MongoDB Configuration
async def get_mongodb_uri() -> str:
    """
    Get MongoDB connection URI setting.
    
    MongoDB连接URI，用于分布式部署时的数据存储。
    设置此项后将不再使用本地/creds和TOML文件。
    
    Environment variable: MONGODB_URI
    TOML config key: mongodb_uri
    Default: None (使用本地文件存储)
    
    示例格式:
    - mongodb://username:password@localhost:27017/database
    - mongodb+srv://username:password@cluster.mongodb.net/database
    """
    return str(await get_config_value("mongodb_uri", "", "MONGODB_URI"))

async def get_mongodb_database() -> str:
    """
    Get MongoDB database name setting.
    
    MongoDB数据库名称。
    
    Environment variable: MONGODB_DATABASE
    TOML config key: mongodb_database
    Default: gcli2api
    """
    return str(await get_config_value("mongodb_database", "gcli2api", "MONGODB_DATABASE"))

async def is_mongodb_mode() -> bool:
    """
    Check if MongoDB mode is enabled.
    
    检查是否启用了MongoDB模式。
    如果配置了MongoDB URI，则启用MongoDB模式，不再使用本地文件。
    
    Returns:
        bool: True if MongoDB mode is enabled, False otherwise
    """
    mongodb_uri = await get_mongodb_uri()
    return bool(mongodb_uri and mongodb_uri.strip())