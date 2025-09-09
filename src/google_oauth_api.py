"""
Google OAuth2 认证模块
"""
import time
import jwt
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List
from urllib.parse import urlencode

from config import get_oauth_proxy_url, get_googleapis_proxy_url, get_resource_manager_api_url, get_service_usage_api_url
from log import log
from .httpx_client import get_async, post_async


class TokenError(Exception):
    """Token相关错误"""
    pass

class Credentials:
    """凭证类"""
    
    def __init__(self, access_token: str, refresh_token: str = None,
                 client_id: str = None, client_secret: str = None,
                 expires_at: datetime = None, project_id: str = None):
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
    
    async def refresh(self, max_retries: int = 3, base_delay: float = 1.0):
        """刷新访问令牌，支持重试机制"""
        if not self.refresh_token:
            raise TokenError("无刷新令牌")
        
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'refresh_token': self.refresh_token,
            'grant_type': 'refresh_token'
        }
        
        last_exception = None
        for attempt in range(max_retries + 1):
            try:
                oauth_base_url = await get_oauth_proxy_url()
                token_url = f"{oauth_base_url.rstrip('/')}/token"
                response = await post_async(
                    token_url,
                    data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'}
                )
                response.raise_for_status()
                
                token_data = response.json()
                self.access_token = token_data['access_token']
                
                if 'expires_in' in token_data:
                    expires_in = int(token_data['expires_in'])
                    self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                
                if 'refresh_token' in token_data:
                    self.refresh_token = token_data['refresh_token']
                
                if attempt > 0:
                    log.debug(f"Token刷新成功（第{attempt + 1}次尝试），过期时间: {self.expires_at}")
                else:
                    log.debug(f"Token刷新成功，过期时间: {self.expires_at}")
                return
                
            except Exception as e:
                last_exception = e
                error_msg = str(e)
                
                # 检查是否是不可恢复的错误，如果是则不重试
                if self._is_non_retryable_error(error_msg):
                    log.error(f"Token刷新遇到不可恢复错误: {error_msg}")
                    break
                
                if attempt < max_retries:
                    # 计算退避延迟时间（指数退避）
                    delay = base_delay * (2 ** attempt)
                    log.warning(f"Token刷新失败（第{attempt + 1}次尝试）: {error_msg}，{delay}秒后重试...")
                    await asyncio.sleep(delay)
                else:
                    break
        
        # 所有重试都失败了
        error_msg = f"Token刷新失败（已重试{max_retries}次）: {str(last_exception)}"
        log.error(error_msg)
        raise TokenError(error_msg)
    
    def _is_non_retryable_error(self, error_msg: str) -> bool:
        """判断是否是不需要重试的错误"""
        non_retryable_patterns = [
            "400 Bad Request",
            "invalid_grant",
            "refresh_token_expired",
            "invalid_refresh_token", 
            "unauthorized_client",
            "access_denied",
            "401 Unauthorized"
        ]
        
        error_msg_lower = error_msg.lower()
        for pattern in non_retryable_patterns:
            if pattern.lower() in error_msg_lower:
                return True
                
        return False
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Credentials':
        """从字典创建凭证"""
        # 处理过期时间
        expires_at = None
        if 'expiry' in data and data['expiry']:
            try:
                expiry_str = data['expiry']
                if isinstance(expiry_str, str):
                    if expiry_str.endswith('Z'):
                        expires_at = datetime.fromisoformat(expiry_str.replace('Z', '+00:00'))
                    elif '+' in expiry_str:
                        expires_at = datetime.fromisoformat(expiry_str)
                    else:
                        expires_at = datetime.fromisoformat(expiry_str).replace(tzinfo=timezone.utc)
            except ValueError:
                log.warning(f"无法解析过期时间: {expiry_str}")
        
        return cls(
            access_token=data.get('token') or data.get('access_token', ''),
            refresh_token=data.get('refresh_token'),
            client_id=data.get('client_id'),
            client_secret=data.get('client_secret'),
            expires_at=expires_at,
            project_id=data.get('project_id')
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """转为字典"""
        result = {
            'access_token': self.access_token,
            'refresh_token': self.refresh_token,
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'project_id': self.project_id
        }
        
        if self.expires_at:
            result['expiry'] = self.expires_at.isoformat()
        
        return result


class Flow:
    """OAuth流程类"""
    
    def __init__(self, client_id: str, client_secret: str, scopes: List[str],
                 redirect_uri: str = None):
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
            'client_id': self.client_id,
            'redirect_uri': self.redirect_uri,
            'scope': ' '.join(self.scopes),
            'response_type': 'code',
            'access_type': 'offline',
            'prompt': 'consent',
            'include_granted_scopes': 'true'
        }
        
        if state:
            params['state'] = state
        
        params.update(kwargs)
        return f"{self.auth_endpoint}?{urlencode(params)}"
    
    async def exchange_code(self, code: str) -> Credentials:
        """用授权码换取token"""
        data = {
            'client_id': self.client_id,
            'client_secret': self.client_secret,
            'redirect_uri': self.redirect_uri,
            'code': code,
            'grant_type': 'authorization_code'
        }
        
        try:
            oauth_base_url = await get_oauth_proxy_url()
            token_url = f"{oauth_base_url.rstrip('/')}/token"
            response = await post_async(
                token_url,
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            
            token_data = response.json()
            
            # 计算过期时间
            expires_at = None
            if 'expires_in' in token_data:
                expires_in = int(token_data['expires_in'])
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            
            # 创建凭证对象
            self.credentials = Credentials(
                access_token=token_data['access_token'],
                refresh_token=token_data.get('refresh_token'),
                client_id=self.client_id,
                client_secret=self.client_secret,
                expires_at=expires_at
            )
            
            return self.credentials
            
        except Exception as e:
            error_msg = f"获取token失败: {str(e)}"
            log.error(error_msg)
            raise TokenError(error_msg)


class ServiceAccount:
    """Service Account类"""
    
    def __init__(self, email: str, private_key: str, project_id: str = None,
                 scopes: List[str] = None):
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
            'iss': self.email,
            'scope': ' '.join(self.scopes) if self.scopes else '',
            'aud': self.token_endpoint,
            'exp': now + 3600,
            'iat': now
        }
        
        return jwt.encode(payload, self.private_key, algorithm='RS256')
    
    async def get_access_token(self) -> str:
        """获取访问令牌"""
        if not self.is_expired() and self.access_token:
            return self.access_token
        
        assertion = self.create_jwt()
        
        data = {
            'grant_type': 'urn:ietf:params:oauth:grant-type:jwt-bearer',
            'assertion': assertion
        }
        
        try:
            oauth_base_url = await get_oauth_proxy_url()
            token_url = f"{oauth_base_url.rstrip('/')}/token"
            response = await post_async(
                token_url,
                data=data,
                headers={'Content-Type': 'application/x-www-form-urlencoded'}
            )
            response.raise_for_status()
            
            token_data = response.json()
            self.access_token = token_data['access_token']
            
            if 'expires_in' in token_data:
                expires_in = int(token_data['expires_in'])
                self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
            
            return self.access_token
            
        except Exception as e:
            error_msg = f"Service Account获取token失败: {str(e)}"
            log.error(error_msg)
            raise TokenError(error_msg)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any], scopes: List[str] = None) -> 'ServiceAccount':
        """从字典创建Service Account凭证"""
        return cls(
            email=data['client_email'],
            private_key=data['private_key'],
            project_id=data.get('project_id'),
            scopes=scopes
        )


# 工具函数
async def get_user_info(credentials: Credentials) -> Optional[Dict[str, Any]]:
    """获取用户信息"""
    await credentials.refresh_if_needed()
    
    try:
        googleapis_base_url = await get_googleapis_proxy_url()
        userinfo_url = f"{googleapis_base_url.rstrip('/')}/oauth2/v2/userinfo"
        response = await get_async(
            userinfo_url,
            headers={'Authorization': f'Bearer {credentials.access_token}'}
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
            log.warning(f"无法从凭证数据创建凭证对象或获取访问令牌")
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
            "cloudaicompanion.googleapis.com"    # Gemini for Google Cloud API
        ]
        
        for service in required_services:
            log.info(f"正在检查并启用服务: {service}")
            
            # 检查服务是否已启用
            service_usage_base_url = await get_service_usage_api_url()
            check_url = f"{service_usage_base_url.rstrip('/')}/v1/projects/{project_id}/services/{service}"
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
                    log.warning(f"⚠️ 启用服务 {service} 失败: {enable_response.status_code} - {enable_response.text}")
                    
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
            projects = data.get('projects', [])
            # 只返回活跃的项目
            active_projects = [
                project for project in projects 
                if project.get('lifecycleState') == 'ACTIVE'
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
        display_name = project.get('displayName', '').lower()
        project_id = project.get('projectId', '')
        if 'default' in display_name or 'default' in project_id.lower():
            log.info(f"选择默认项目: {project_id} ({project.get('displayName', project_id)})")
            return project_id
    
    # 策略2：选择第一个项目
    first_project = projects[0]
    project_id = first_project.get('projectId', '')
    log.info(f"选择第一个项目作为默认: {project_id} ({first_project.get('displayName', project_id)})")
    return project_id