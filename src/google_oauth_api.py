"""
Google OAuth2 认证模块
"""

import time
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import jwt

from config import (
    get_googleapis_proxy_url,
    get_oauth_proxy_url,
    get_resource_manager_api_url,
    get_service_usage_api_url,
)
from log import log

from .httpx_client import get_async, post_async


class TokenError(Exception):
    """Token相关错误"""

    pass


class Credentials:
    """凭证类"""

    def __init__(
        self,
        access_token: str,
        refresh_token: str = None,
        client_id: str = None,
        client_secret: str = None,
        expires_at: datetime = None,
        project_id: str = None,
    ):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.client_id = client_id
        self.client_secret = client_secret
        self.expires_at = expires_at
        self.project_id = project_id

        # 反代配置将在使用时异步获取
        self.oauth_base_url = None
        self.token_endpoint = None

    def is_expired(self) -> bool:
        """检查token是否过期"""
        if not self.expires_at:
            return True

        # 提前3分钟认为过期
        buffer = timedelta(minutes=3)
        return (self.expires_at - buffer) <= datetime.now(timezone.utc)

    async def refresh_if_needed(self) -> bool:
        """如果需要则刷新token"""
        if not self.is_expired():
            return False

        if not self.refresh_token:
            raise TokenError("需要刷新令牌但未提供")

        await self.refresh()
        return True

    async def refresh(self):
        """刷新访问令牌"""
        if not self.refresh_token:
            raise TokenError("无刷新令牌")

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }

        try:
            oauth_base_url = await get_oauth_proxy_url()
            token_url = f"{oauth_base_url.rstrip('/')}/token"
            response = await post_async(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data["access_token"]

            if "expires_in" in token_data:
                expires_in = int(token_data["expires_in"])
                current_utc = datetime.now(timezone.utc)
                self.expires_at = current_utc + timedelta(seconds=expires_in)
                log.debug(
                    f"Token刷新: 当前UTC时间={current_utc.isoformat()}, "
                    f"有效期={expires_in}秒, "
                    f"过期时间={self.expires_at.isoformat()}"
                )

            if "refresh_token" in token_data:
                self.refresh_token = token_data["refresh_token"]

            log.debug(f"Token刷新成功，过期时间: {self.expires_at}")

        except Exception as e:
            error_msg = str(e)
            status_code = None
            if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                status_code = e.response.status_code
                error_msg = f"Token刷新失败 (HTTP {status_code}): {error_msg}"
            else:
                error_msg = f"Token刷新失败: {error_msg}"

            log.error(error_msg)
            token_error = TokenError(error_msg)
            token_error.status_code = status_code
            raise token_error

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Credentials":
        """从字典创建凭证"""
        # 处理过期时间
        expires_at = None
        if "expiry" in data and data["expiry"]:
            try:
                expiry_str = data["expiry"]
                if isinstance(expiry_str, str):
                    if expiry_str.endswith("Z"):
                        expires_at = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                    elif "+" in expiry_str:
                        expires_at = datetime.fromisoformat(expiry_str)
                    else:
                        expires_at = datetime.fromisoformat(expiry_str).replace(tzinfo=timezone.utc)
            except ValueError:
                log.warning(f"无法解析过期时间: {expiry_str}")

        return cls(
            access_token=data.get("token") or data.get("access_token", ""),
            refresh_token=data.get("refresh_token"),
            client_id=data.get("client_id"),
            client_secret=data.get("client_secret"),
            expires_at=expires_at,
            project_id=data.get("project_id"),
        )

    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        result = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "project_id": self.project_id,
        }

        if self.expires_at:
            result["expiry"] = self.expires_at.isoformat()

        return result


class Flow:
    """OAuth流程类"""

    def __init__(
        self, client_id: str, client_secret: str, scopes: List[str], redirect_uri: str = None
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.scopes = scopes
        self.redirect_uri = redirect_uri

        # 反代配置将在使用时异步获取
        self.oauth_base_url = None
        self.token_endpoint = None
        self.auth_endpoint = "https://accounts.google.com/o/oauth2/auth"

        self.credentials: Optional[Credentials] = None

    def get_auth_url(self, state: str = None, **kwargs) -> str:
        """生成授权URL"""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": " ".join(self.scopes),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }

        if state:
            params["state"] = state

        params.update(kwargs)
        return f"{self.auth_endpoint}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Credentials:
        """用授权码换取token"""
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "code": code,
            "grant_type": "authorization_code",
        }

        try:
            oauth_base_url = await get_oauth_proxy_url()
            token_url = f"{oauth_base_url.rstrip('/')}/token"
            response = await post_async(
                token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()

            token_data = response.json()

            # 计算过期时间
            expires_at = None
            if "expires_in" in token_data:
                expires_in = int(token_data["expires_in"])
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            # 创建凭证对象
            self.credentials = Credentials(
                access_token=token_data["access_token"],
                refresh_token=token_data.get("refresh_token"),
                client_id=self.client_id,
                client_secret=self.client_secret,
                expires_at=expires_at,
            )

            return self.credentials

        except Exception as e:
            error_msg = f"获取token失败: {str(e)}"
            log.error(error_msg)
            raise TokenError(error_msg)


class ServiceAccount:
    """Service Account类"""

    def __init__(
        self, email: str, private_key: str, project_id: str = None, scopes: List[str] = None
    ):
        self.email = email
        self.private_key = private_key
        self.project_id = project_id
        self.scopes = scopes or []

        # 反代配置将在使用时异步获取
        self.oauth_base_url = None
        self.token_endpoint = None

        self.access_token: Optional[str] = None
        self.expires_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        """检查token是否过期"""
        if not self.expires_at:
            return True

        buffer = timedelta(minutes=3)
        return (self.expires_at - buffer) <= datetime.now(timezone.utc)

    def create_jwt(self) -> str:
        """创建JWT令牌"""
        now = int(time.time())

        payload = {
            "iss": self.email,
            "scope": " ".join(self.scopes) if self.scopes else "",
            "aud": self.token_endpoint,
            "exp": now + 3600,
            "iat": now,
        }

        return jwt.encode(payload, self.private_key, algorithm="RS256")

    async def get_access_token(self) -> str:
        """获取访问令牌"""
        if not self.is_expired() and self.access_token:
            return self.access_token

        assertion = self.create_jwt()

        data = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}

        try:
            oauth_base_url = await get_oauth_proxy_url()
            token_url = f"{oauth_base_url.rstrip('/')}/token"
            response = await post_async(
                token_url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            response.raise_for_status()

            token_data = response.json()
            self.access_token = token_data["access_token"]

            if "expires_in" in token_data:
                expires_in = int(token_data["expires_in"])
                self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)

            return self.access_token

        except Exception as e:
            error_msg = f"Service Account获取token失败: {str(e)}"
            log.error(error_msg)
            raise TokenError(error_msg)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], scopes: List[str] = None) -> "ServiceAccount":
        """从字典创建Service Account凭证"""
        return cls(
            email=data["client_email"],
            private_key=data["private_key"],
            project_id=data.get("project_id"),
            scopes=scopes,
        )


# 工具函数
async def get_user_info(credentials: Credentials) -> Optional[Dict[str, Any]]:
    """获取用户信息"""
    await credentials.refresh_if_needed()

    try:
        googleapis_base_url = await get_googleapis_proxy_url()
        userinfo_url = f"{googleapis_base_url.rstrip('/')}/oauth2/v2/userinfo"
        response = await get_async(
            userinfo_url, headers={"Authorization": f"Bearer {credentials.access_token}"}
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log.error(f"获取用户信息失败: {e}")
        return None


async def get_user_email(credentials: Credentials) -> Optional[str]:
    """获取用户邮箱地址"""
    try:
        # 确保凭证有效
        await credentials.refresh_if_needed()

        # 调用Google userinfo API获取邮箱
        user_info = await get_user_info(credentials)
        if user_info:
            email = user_info.get("email")
            if email:
                log.info(f"成功获取邮箱地址: {email}")
                return email
            else:
                log.warning(f"userinfo响应中没有邮箱信息: {user_info}")
                return None
        else:
            log.warning("获取用户信息失败")
            return None

    except Exception as e:
        log.error(f"获取用户邮箱失败: {e}")
        return None


async def fetch_user_email_from_file(cred_data: Dict[str, Any]) -> Optional[str]:
    """从凭证数据获取用户邮箱地址（支持统一存储）"""
    try:
        # 直接从凭证数据创建凭证对象
        credentials = Credentials.from_dict(cred_data)
        if not credentials or not credentials.access_token:
            log.warning("无法从凭证数据创建凭证对象或获取访问令牌")
            return None

        # 获取邮箱
        return await get_user_email(credentials)

    except Exception as e:
        log.error(f"从凭证数据获取用户邮箱失败: {e}")
        return None


async def validate_token(token: str) -> Optional[Dict[str, Any]]:
    """验证访问令牌"""
    try:
        oauth_base_url = await get_oauth_proxy_url()
        tokeninfo_url = f"{oauth_base_url.rstrip('/')}/tokeninfo?access_token={token}"

        response = await get_async(tokeninfo_url)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        log.error(f"验证令牌失败: {e}")
        return None


async def enable_required_apis(credentials: Credentials, project_id: str) -> bool:
    """自动启用必需的API服务"""
    try:
        # 确保凭证有效
        if credentials.is_expired() and credentials.refresh_token:
            await credentials.refresh()

        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "geminicli-oauth/1.0",
        }

        # 需要启用的服务列表
        required_services = [
            "geminicloudassist.googleapis.com",  # Gemini Cloud Assist API
            "cloudaicompanion.googleapis.com",  # Gemini for Google Cloud API
        ]

        for service in required_services:
            log.info(f"正在检查并启用服务: {service}")

            # 检查服务是否已启用
            service_usage_base_url = await get_service_usage_api_url()
            check_url = (
                f"{service_usage_base_url.rstrip('/')}/v1/projects/{project_id}/services/{service}"
            )
            try:
                check_response = await get_async(check_url, headers=headers)
                if check_response.status_code == 200:
                    service_data = check_response.json()
                    if service_data.get("state") == "ENABLED":
                        log.info(f"服务 {service} 已启用")
                        continue
            except Exception as e:
                log.debug(f"检查服务状态失败，将尝试启用: {e}")

            # 启用服务
            enable_url = f"{service_usage_base_url.rstrip('/')}/v1/projects/{project_id}/services/{service}:enable"
            try:
                enable_response = await post_async(enable_url, headers=headers, json={})

                if enable_response.status_code in [200, 201]:
                    log.info(f"✅ 成功启用服务: {service}")
                elif enable_response.status_code == 400:
                    error_data = enable_response.json()
                    if "already enabled" in error_data.get("error", {}).get("message", "").lower():
                        log.info(f"✅ 服务 {service} 已经启用")
                    else:
                        log.warning(f"⚠️ 启用服务 {service} 时出现警告: {error_data}")
                else:
                    log.warning(
                        f"⚠️ 启用服务 {service} 失败: {enable_response.status_code} - {enable_response.text}"
                    )

            except Exception as e:
                log.warning(f"⚠️ 启用服务 {service} 时发生异常: {e}")

        return True

    except Exception as e:
        log.error(f"启用API服务时发生错误: {e}")
        return False


async def get_user_projects(credentials: Credentials) -> List[Dict[str, Any]]:
    """获取用户可访问的Google Cloud项目列表"""
    try:
        # 确保凭证有效
        if credentials.is_expired() and credentials.refresh_token:
            await credentials.refresh()

        headers = {
            "Authorization": f"Bearer {credentials.access_token}",
            "User-Agent": "geminicli-oauth/1.0",
        }

        # 使用Resource Manager API的正确域名和端点
        resource_manager_base_url = await get_resource_manager_api_url()
        url = f"{resource_manager_base_url.rstrip('/')}/v1/projects"
        log.info(f"正在调用API: {url}")
        response = await get_async(url, headers=headers)

        log.info(f"API响应状态码: {response.status_code}")
        if response.status_code != 200:
            log.error(f"API响应内容: {response.text}")

        if response.status_code == 200:
            data = response.json()
            projects = data.get("projects", [])
            # 只返回活跃的项目
            active_projects = [
                project for project in projects if project.get("lifecycleState") == "ACTIVE"
            ]
            log.info(f"获取到 {len(active_projects)} 个活跃项目")
            return active_projects
        else:
            log.warning(f"获取项目列表失败: {response.status_code} - {response.text}")
            return []

    except Exception as e:
        log.error(f"获取用户项目列表失败: {e}")
        return []


async def select_default_project(projects: List[Dict[str, Any]]) -> Optional[str]:
    """从项目列表中选择默认项目"""
    if not projects:
        return None

    # 策略1：查找显示名称或项目ID包含"default"的项目
    for project in projects:
        display_name = project.get("displayName", "").lower()
        # Google API returns projectId in camelCase
        project_id = project.get("projectId", "")
        if "default" in display_name or "default" in project_id.lower():
            log.info(f"选择默认项目: {project_id} ({project.get('displayName', project_id)})")
            return project_id

    # 策略2：选择第一个项目
    first_project = projects[0]
    # Google API returns projectId in camelCase
    project_id = first_project.get("projectId", "")
    log.info(
        f"选择第一个项目作为默认: {project_id} ({first_project.get('displayName', project_id)})"
    )
    return project_id


async def fetch_project_id(
    access_token: str,
    user_agent: str,
    api_base_url: str
) -> Optional[str]:
    """
    从 API 获取 project_id，如果 loadCodeAssist 失败则回退到 onboardUser

    Args:
        access_token: Google OAuth access token
        user_agent: User-Agent header
        api_base_url: API base URL (e.g., antigravity or code assist endpoint)

    Returns:
        project_id 字符串，如果获取失败返回 None
    """
    headers = {
        'User-Agent': user_agent,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip'
    }

    # 步骤 1: 尝试 loadCodeAssist
    try:
        project_id = await _try_load_code_assist(api_base_url, headers)
        if project_id:
            return project_id

        log.warning("[fetch_project_id] loadCodeAssist did not return project_id, falling back to onboardUser")

    except Exception as e:
        log.warning(f"[fetch_project_id] loadCodeAssist failed: {type(e).__name__}: {e}")
        log.warning("[fetch_project_id] Falling back to onboardUser")

    # 步骤 2: 回退到 onboardUser
    try:
        project_id = await _try_onboard_user(api_base_url, headers)
        if project_id:
            return project_id

        log.error("[fetch_project_id] Failed to get project_id from both loadCodeAssist and onboardUser")
        return None

    except Exception as e:
        log.error(f"[fetch_project_id] onboardUser failed: {type(e).__name__}: {e}")
        import traceback
        log.debug(f"[fetch_project_id] Traceback: {traceback.format_exc()}")
        return None


async def _try_load_code_assist(
    api_base_url: str,
    headers: dict
) -> Optional[str]:
    """
    尝试通过 loadCodeAssist 获取 project_id

    Returns:
        project_id 或 None
    """
    request_url = f"{api_base_url.rstrip('/')}/v1internal:loadCodeAssist"
    request_body = {
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI"
        }
    }

    log.debug(f"[loadCodeAssist] Fetching project_id from: {request_url}")
    log.debug(f"[loadCodeAssist] Request body: {request_body}")

    response = await post_async(
        request_url,
        json=request_body,
        headers=headers,
        timeout=30.0,
    )

    log.debug(f"[loadCodeAssist] Response status: {response.status_code}")

    if response.status_code == 200:
        response_text = response.text
        log.debug(f"[loadCodeAssist] Response body: {response_text}")

        data = response.json()
        log.debug(f"[loadCodeAssist] Response JSON keys: {list(data.keys())}")

        # 检查是否有 currentTier（表示用户已激活）
        current_tier = data.get("currentTier")
        if current_tier:
            log.info("[loadCodeAssist] User is already activated")

            # 使用服务器返回的 project_id
            project_id = data.get("cloudaicompanionProject")
            if project_id:
                log.info(f"[loadCodeAssist] Successfully fetched project_id: {project_id}")
                return project_id

            log.warning("[loadCodeAssist] No project_id in response")
            return None
        else:
            log.info("[loadCodeAssist] User not activated yet (no currentTier)")
            return None
    else:
        log.warning(f"[loadCodeAssist] Failed: HTTP {response.status_code}")
        log.warning(f"[loadCodeAssist] Response body: {response.text[:500]}")
        raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")


async def _try_onboard_user(
    api_base_url: str,
    headers: dict
) -> Optional[str]:
    """
    尝试通过 onboardUser 获取 project_id（长时间运行操作，需要轮询）

    Returns:
        project_id 或 None
    """
    request_url = f"{api_base_url.rstrip('/')}/v1internal:onboardUser"

    # 首先需要获取用户的 tier 信息
    tier_id = await _get_onboard_tier(api_base_url, headers)
    if not tier_id:
        log.error("[onboardUser] Failed to determine user tier")
        return None

    log.info(f"[onboardUser] User tier: {tier_id}")

    # 构造 onboardUser 请求
    # 注意：FREE tier 不应该包含 cloudaicompanionProject
    request_body = {
        "tierId": tier_id,
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI"
        }
    }

    log.debug(f"[onboardUser] Request URL: {request_url}")
    log.debug(f"[onboardUser] Request body: {request_body}")

    # onboardUser 是长时间运行操作，需要轮询
    # 最多等待 10 秒（5 次 * 2 秒）
    max_attempts = 5
    attempt = 0

    while attempt < max_attempts:
        attempt += 1
        log.debug(f"[onboardUser] Polling attempt {attempt}/{max_attempts}")

        response = await post_async(
            request_url,
            json=request_body,
            headers=headers,
            timeout=30.0,
        )

        log.debug(f"[onboardUser] Response status: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            log.debug(f"[onboardUser] Response data: {data}")

            # 检查长时间运行操作是否完成
            if data.get("done"):
                log.info("[onboardUser] Operation completed")

                # 从响应中提取 project_id
                response_data = data.get("response", {})
                project_obj = response_data.get("cloudaicompanionProject", {})

                if isinstance(project_obj, dict):
                    project_id = project_obj.get("id")
                elif isinstance(project_obj, str):
                    project_id = project_obj
                else:
                    project_id = None

                if project_id:
                    log.info(f"[onboardUser] Successfully fetched project_id: {project_id}")
                    return project_id
                else:
                    log.warning("[onboardUser] Operation completed but no project_id in response")
                    return None
            else:
                log.debug("[onboardUser] Operation still in progress, waiting 2 seconds...")
                await asyncio.sleep(2)
        else:
            log.warning(f"[onboardUser] Failed: HTTP {response.status_code}")
            log.warning(f"[onboardUser] Response body: {response.text[:500]}")
            raise Exception(f"HTTP {response.status_code}: {response.text[:200]}")

    log.error("[onboardUser] Timeout: Operation did not complete within 10 seconds")
    return None


async def _get_onboard_tier(
    api_base_url: str,
    headers: dict
) -> Optional[str]:
    """
    从 loadCodeAssist 响应中获取用户应该注册的 tier

    Returns:
        tier_id (如 "FREE", "STANDARD", "LEGACY") 或 None
    """
    request_url = f"{api_base_url.rstrip('/')}/v1internal:loadCodeAssist"
    request_body = {
        "metadata": {
            "ideType": "ANTIGRAVITY",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI"
        }
    }

    log.debug(f"[_get_onboard_tier] Fetching tier info from: {request_url}")

    response = await post_async(
        request_url,
        json=request_body,
        headers=headers,
        timeout=30.0,
    )

    if response.status_code == 200:
        data = response.json()
        log.debug(f"[_get_onboard_tier] Response data: {data}")

        # 查找默认的 tier
        allowed_tiers = data.get("allowedTiers", [])
        for tier in allowed_tiers:
            if tier.get("isDefault"):
                tier_id = tier.get("id")
                log.info(f"[_get_onboard_tier] Found default tier: {tier_id}")
                return tier_id

        # 如果没有默认 tier，使用 LEGACY 作为回退
        log.warning("[_get_onboard_tier] No default tier found, using LEGACY")
        return "LEGACY"
    else:
        log.error(f"[_get_onboard_tier] Failed to fetch tier info: HTTP {response.status_code}")
        return None


