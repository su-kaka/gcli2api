from datetime import datetime, timezone
from typing import List, Optional

from config import get_api_password, get_panel_password
from fastapi import Depends, HTTPException, Header, Query, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from log import log

# HTTP Bearer security scheme
security = HTTPBearer()

# ====================== OAuth Configuration ======================

GEMINICLI_USER_AGENT = "GeminiCLI/0.1.5 (Windows; AMD64)"

ANTIGRAVITY_USER_AGENT = "antigravity/1.11.3 windows/amd64"

# OAuth Configuration - 标准模式
CLIENT_ID = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
CLIENT_SECRET = "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"
SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]

# Antigravity OAuth Configuration
ANTIGRAVITY_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
ANTIGRAVITY_CLIENT_SECRET = "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"
ANTIGRAVITY_SCOPES = [
    'https://www.googleapis.com/auth/cloud-platform',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'https://www.googleapis.com/auth/cclog',
    'https://www.googleapis.com/auth/experimentsandconfigs'
]

# 统一的 Token URL（两种模式相同）
TOKEN_URL = "https://oauth2.googleapis.com/token"

# 回调服务器配置
CALLBACK_HOST = "localhost"

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
    "gemini-3-flash-preview"
]


# ====================== Model Helper Functions ======================

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


# ====================== Authentication Functions ======================

async def authenticate_bearer(
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    access_token: Optional[str] = Header(None, alias="access_token")
) -> str:
    """
    Bearer Token 认证

    此函数可以直接用作 FastAPI 的 Depends 依赖

    支持的认证字段:
        - authorization (Bearer token)
        - x-api-key
        - access_token

    Args:
        authorization: Authorization 头部值（自动注入）
        x_api_key: x-api-key 头部值（自动注入）
        access_token: access_token 头部值（自动注入）

    Returns:
        验证通过的token

    Raises:
        HTTPException: 认证失败时抛出401或403异常

    使用示例:
        @router.post("/endpoint")
        async def endpoint(token: str = Depends(authenticate_bearer)):
            # token 已验证通过
            pass
    """

    password = await get_api_password()
    token = None

    # 1. 尝试从 x-api-key 获取
    if x_api_key:
        token = x_api_key

    # 2. 尝试从 access_token 获取
    elif access_token:
        token = access_token

    # 3. 尝试从 authorization 获取
    elif authorization:
        # 检查是否是 Bearer token
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme. Use 'Bearer <token>'",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # 提取 token
        token = authorization[7:]  # 移除 "Bearer " 前缀

    # 检查是否提供了任何认证凭据
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials. Use 'Authorization: Bearer <token>', 'x-api-key: <token>', or 'access_token: <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 验证 token
    if token != password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="密码错误"
        )

    return token


async def authenticate_gemini_flexible(
    request: Request,
    x_goog_api_key: Optional[str] = Header(None, alias="x-goog-api-key"),
    key: Optional[str] = Query(None)
) -> str:
    """
    Gemini 灵活认证：支持 x-goog-api-key 头部、URL 参数 key 或 Authorization Bearer

    此函数可以直接用作 FastAPI 的 Depends 依赖

    Args:
        request: FastAPI Request 对象
        x_goog_api_key: x-goog-api-key 头部值（自动注入）
        key: URL 参数 key（自动注入）

    Returns:
        验证通过的API密钥

    Raises:
        HTTPException: 认证失败时抛出400异常

    使用示例:
        @router.post("/endpoint")
        async def endpoint(api_key: str = Depends(authenticate_gemini_flexible)):
            # api_key 已验证通过
            pass
    """

    password = await get_api_password()

    # 尝试从URL参数key获取（Google官方标准方式）
    if key:
        log.debug("Using URL parameter key authentication")
        if key == password:
            return key

    # 尝试从Authorization头获取（兼容旧方式）
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # 移除 "Bearer " 前缀
        log.debug("Using Bearer token authentication")
        if token == password:
            return token

    # 尝试从x-goog-api-key头获取（新标准方式）
    if x_goog_api_key:
        log.debug("Using x-goog-api-key authentication")
        if x_goog_api_key == password:
            return x_goog_api_key

    log.error(f"Authentication failed. Headers: {dict(request.headers)}, Query params: key={key}")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing or invalid authentication. Use 'key' URL parameter, 'x-goog-api-key' header, or 'Authorization: Bearer <token>'",
    )


# ====================== Panel Authentication Functions ======================

async def verify_panel_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    简化的控制面板密码验证函数

    直接验证Bearer token是否等于控制面板密码

    Args:
        credentials: HTTPAuthorizationCredentials 自动注入

    Returns:
        验证通过的token

    Raises:
        HTTPException: 密码错误时抛出401异常
    """

    password = await get_panel_password()
    if credentials.credentials != password:
        raise HTTPException(status_code=401, detail="密码错误")
    return credentials.credentials
