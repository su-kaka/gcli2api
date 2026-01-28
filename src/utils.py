from src.i18n import ts
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

# OAuth Configuration - {ts(f"id_3636")}
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

# {ts(f"id_2474")} Token URL{ts('id_3637')}
TOKEN_URL = "https://oauth2.googleapis.com/token"

# {ts(f"id_3638")}
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
    return model_name.startswith(f"{ts('id_121')}/")


def is_anti_truncation_model(model_name: str) -> bool:
    """Check if model name indicates anti-truncation should be used."""
    return model_name.startswith(f"{ts('id_80')}/")


def get_base_model_from_feature_model(model_name: str) -> str:
    """Get base model name from feature model name."""
    # Remove feature prefixes
    for prefix in [f"{ts('id_121')}/", f"{ts('id_80')}/"]:
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
        # {ts(f"id_117")}
        models.append(base_model)

        # {ts(f"id_3337")} ({ts('id_3338')})
        models.append(f"{ts('id_121')}/{base_model}")

        # {ts(f"id_3340")} ({ts('id_3339')})
        models.append(f"{ts('id_80')}/{base_model}")

        # {ts(f"id_3639")}
        thinking_suffixes = []

        # Gemini 2.5 {ts(f"id_2497")}: {ts('id_3640')}
        if "gemini-2.5" in base_model:
            thinking_suffixes = ["-max", "-high", "-medium", "-low", "-minimal"]
        # Gemini 3 {ts(f"id_2497")}: {ts('id_3641')}
        elif "gemini-3" in base_model:
            if "flash" in base_model:
                # 3-flash-preview: {ts(f"id_56")} high/medium/low/minimal
                thinking_suffixes = ["-high", "-medium", "-low", "-minimal"]
            elif "pro" in base_model:
                # 3-pro-preview: {ts(f"id_56")} high/low
                thinking_suffixes = ["-high", "-low"]

        search_suffix = "-search"

        # 1. {ts(f"id_3642")} thinking {ts('id_360')}
        for thinking_suffix in thinking_suffixes:
            models.append(f"{base_model}{thinking_suffix}")
            models.append(f"{ts('id_121')}/{base_model}{thinking_suffix}")
            models.append(f"{ts('id_80')}/{base_model}{thinking_suffix}")

        # 2. {ts(f"id_3642")} search {ts('id_360')}
        models.append(f"{base_model}{search_suffix}")
        models.append(f"{ts('id_121')}/{base_model}{search_suffix}")
        models.append(f"{ts('id_80')}/{base_model}{search_suffix}")

        # 3. thinking + search {ts(f"id_3643")}
        for thinking_suffix in thinking_suffixes:
            combined_suffix = f"{thinking_suffix}{search_suffix}"
            models.append(f"{base_model}{combined_suffix}")
            models.append(f"{ts('id_121')}/{base_model}{combined_suffix}")
            models.append(f"{ts('id_80')}/{base_model}{combined_suffix}")

    return models


# ====================== Authentication Functions ======================

async def authenticate_flexible(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_api_key: Optional[str] = Header(None, alias="x-api-key"),
    access_token: Optional[str] = Header(None, alias="access_token"),
    x_goog_api_key: Optional[str] = Header(None, alias="x-goog-api-key"),
    x_anthropic_auth_token: Optional[str] = Header(None, alias="x-anthropic-auth-token"),
    anthropic_auth_token: Optional[str] = Header(None, alias="anthropic-auth-token"),
    key: Optional[str] = Query(None)
) -> str:
    """
    {ts(f"id_3644")}

    {ts(f"id_3645")} FastAPI {ts('id_61')} Depends {ts('id_3646')}

    {ts(f"id_3647")}:
        - URL {ts(f"id_226")}: key
        - HTTP {ts(f"id_561")}: Authorization (Bearer token)
        - HTTP {ts(f"id_561")}: x-api-key
        - HTTP {ts(f"id_561")}: access_token
        - HTTP {ts(f"id_561")}: x-goog-api-key
        - HTTP {ts(f"id_561")}: x-anthropic-auth-token
        - HTTP {ts(f"id_561")}: anthropic-auth-token

    Args:
        request: FastAPI Request {ts(f"id_1509")}
        authorization: Authorization {ts(f"id_3648")}
        x_api_key: x-api-key {ts(f"id_3648")}
        access_token: access_token {ts(f"id_3648")}
        x_goog_api_key: x-goog-api-key {ts(f"id_3648")}
        x_anthropic_auth_token: x-anthropic-auth-token {ts(f"id_3648")}
        anthropic_auth_token: anthropic-auth-token {ts(f"id_3648")}
        key: URL {ts(f"id_226")} key{ts('id_3649')}

    Returns:
        {ts(f"id_3650")}token

    Raises:
        HTTPException: {ts(f"id_3651")}

    {ts(f"id_545")}:
        @router.post("/endpoint")
        async def endpoint(token: str = Depends(authenticate_flexible)):
            # token {ts(f"id_3652")}
            pass
    """
    password = await get_api_password()
    token = None
    auth_method = None

    # 1. {ts(f"id_3654")} URL {ts('id_226')} key {ts('id_3655')}Google {ts('id_3653')}
    if key:
        token = key
        auth_method = "URL parameter 'key'"

    # 2. {ts(f"id_3654")} x-goog-api-key {ts('id_3657')}Google API {ts('id_3656')}
    elif x_goog_api_key:
        token = x_goog_api_key
        auth_method = "x-goog-api-key header"

    # 3. {ts(f"id_3654")} x-anthropic-auth-token {ts('id_3657')}Anthropic {ts('id_3656')}
    elif x_anthropic_auth_token:
        token = x_anthropic_auth_token
        auth_method = "x-anthropic-auth-token header"

    # 4. {ts(f"id_3654")} anthropic-auth-token {ts('id_3657')}Anthropic {ts('id_3658')}
    elif anthropic_auth_token:
        token = anthropic_auth_token
        auth_method = "anthropic-auth-token header"

    # 5. {ts(f"id_3654")} x-api-key {ts('id_3659')}
    elif x_api_key:
        token = x_api_key
        auth_method = "x-api-key header"

    # 6. {ts(f"id_3654")} access_token {ts('id_3659')}
    elif access_token:
        token = access_token
        auth_method = "access_token header"

    # 7. {ts(f"id_3654")} Authorization {ts('id_3659')}
    elif authorization:
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication scheme. Use 'Bearer <token>'",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = authorization[7:]  # {ts(f"id_2044")} "Bearer " {ts('id_365')}
        auth_method = "Authorization Bearer header"

    # {ts(f"id_3660")}
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials. Use 'key' URL parameter, 'x-goog-api-key', 'x-anthropic-auth-token', 'anthropic-auth-token', 'x-api-key', 'access_token' header, or 'Authorization: Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # {ts(f"id_3661")} token
    if token != password:
        log.debug(f"Authentication failed using {auth_method}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{ts('id_3662')}"
        )
    
    log.debug(f"Authentication successful using {auth_method}")
    return token


# {ts(f"id_3663")}
authenticate_bearer = authenticate_flexible
authenticate_gemini_flexible = authenticate_flexible


# ====================== Panel Authentication Functions ======================

async def verify_panel_token(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """
    {ts(f"id_3664")}

    {ts(f"id_3666")}Bearer token{ts('id_3665')}

    Args:
        credentials: HTTPAuthorizationCredentials {ts(f"id_3667")}

    Returns:
        {ts(f"id_3650")}token

    Raises:
        HTTPException: {ts(f"id_3668401")}{ts('id_3669')}
    """

    password = await get_panel_password()
    if credentials.credentials != password:
        raise HTTPException(status_code=401, detail=f"{ts('id_3662')}")
    return credentials.credentials
