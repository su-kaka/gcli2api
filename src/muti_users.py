"""
多用户管理模块 - Multi-User Management Module
提供用户级别的凭证隔离和管理功能

功能:
1. /user - 用户凭证管理 (使用密钥认证)
   - 上传和认证凭证
   - 管理自己的凭证
2. /admin - 管理员功能 (使用管理员密码)
   - 生成用户密钥
   - 删除用户
   - 禁用/启用用户
   - 查看用户使用情况
"""

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from config import get_api_password
from log import log

from .storage_adapter import get_storage_adapter

# 创建路由器
router = APIRouter()
security = HTTPBearer()


# ==================== 数据模型 ====================


class UserCreate(BaseModel):
    """创建用户请求"""

    username: str
    description: Optional[str] = None


class UserUpdate(BaseModel):
    """更新用户请求"""

    disabled: Optional[bool] = None
    description: Optional[str] = None


class CredentialUpload(BaseModel):
    """凭证上传请求"""

    credential_name: str
    credential_data: Dict[str, Any]


# ==================== 用户管理器 ====================


class MultiUserManager:
    """多用户管理器"""

    def __init__(self):
        self._storage = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def initialize(self):
        """初始化"""
        async with self._lock:
            if self._initialized:
                return
            self._storage = await get_storage_adapter()
            self._initialized = True
            log.info("MultiUserManager initialized")

    async def _ensure_initialized(self):
        """确保已初始化"""
        if not self._initialized:
            await self.initialize()

    # ==================== 用户管理 ====================

    async def create_user(self, username: str, description: Optional[str] = None) -> Dict[str, Any]:
        """
        创建新用户

        Args:
            username: 用户名
            description: 用户描述

        Returns:
            包含用户信息和密钥的字典
        """
        await self._ensure_initialized()

        # 检查用户是否已存在
        existing_user = await self._get_user(username)
        if existing_user:
            raise HTTPException(status_code=400, detail=f"用户 {username} 已存在")

        # 生成唯一的用户密钥
        user_key = self._generate_user_key()

        # 创建用户数据
        user_data = {
            "username": username,
            "user_key": user_key,
            "description": description or "",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "disabled": False,
            "credential_count": 0,
            "total_calls": 0,
            "last_active": None,
        }

        # 存储用户数据
        await self._storage.store_user(username, user_data)

        log.info(f"创建用户: {username}")
        return user_data

    async def get_user_by_key(self, user_key: str) -> Optional[Dict[str, Any]]:
        """
        通过密钥获取用户信息

        Args:
            user_key: 用户密钥

        Returns:
            用户信息字典，如果不存在返回None
        """
        await self._ensure_initialized()

        # 遍历所有用户查找匹配的密钥
        user_list = await self._storage.list_users()
        for username in user_list:
            user_data = await self._storage.get_user(username)
            if user_data and user_data.get("user_key") == user_key:
                # 检查用户是否被禁用
                if user_data.get("disabled", False):
                    raise HTTPException(status_code=403, detail="用户已被禁用")
                return user_data

        return None

    async def delete_user(self, username: str) -> bool:
        """
        删除用户及其所有凭证

        Args:
            username: 用户名

        Returns:
            是否成功
        """
        await self._ensure_initialized()

        # 检查用户是否存在
        user_data = await self._get_user(username)
        if not user_data:
            raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")

        # 删除用户的所有凭证
        credentials = await self.list_user_credentials(username)
        for cred_name in credentials:
            await self._delete_user_credential(username, cred_name)

        # 删除用户数据
        await self._storage.delete_user(username)

        log.info(f"删除用户: {username}")
        return True

    async def update_user(self, username: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        更新用户信息

        Args:
            username: 用户名
            updates: 更新数据

        Returns:
            更新后的用户数据
        """
        await self._ensure_initialized()

        user_data = await self._get_user(username)
        if not user_data:
            raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")

        # 更新字段
        if "disabled" in updates:
            user_data["disabled"] = updates["disabled"]
        if "description" in updates:
            user_data["description"] = updates["description"]

        user_data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # 保存更新
        await self._storage.update_user(username, user_data)

        log.info(f"更新用户: {username}, updates: {updates}")
        return user_data

    async def list_users(self) -> List[Dict[str, Any]]:
        """
        列出所有用户

        Returns:
            用户列表
        """
        await self._ensure_initialized()

        user_list = await self._get_user_list()
        users = []

        for username in user_list:
            user_data = await self._get_user(username)
            if user_data:
                # 不返回密钥信息
                safe_user_data = user_data.copy()
                safe_user_data.pop("user_key", None)
                users.append(safe_user_data)

        return users

    async def get_user_stats(self, username: str) -> Dict[str, Any]:
        """
        获取用户使用统计

        Args:
            username: 用户名

        Returns:
            统计信息
        """
        await self._ensure_initialized()

        user_data = await self._get_user(username)
        if not user_data:
            raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")

        # 获取用户的所有凭证统计
        credentials = await self.list_user_credentials(username)
        credential_stats = []

        for cred_name in credentials:
            full_cred_name = f"user_{username}_{cred_name}"
            cred_state = await self._storage.get_credential_state(full_cred_name)
            cred_stats = {
                "credential_name": cred_name,
                "disabled": cred_state.get("disabled", False),
                "last_success": cred_state.get("last_success"),
                "error_codes": cred_state.get("error_codes", []),
            }
            credential_stats.append(cred_stats)

        return {
            "username": username,
            "credential_count": len(credentials),
            "total_calls": user_data.get("total_calls", 0),
            "last_active": user_data.get("last_active"),
            "credentials": credential_stats,
        }

    # ==================== 凭证管理 ====================

    async def add_user_credential(
        self, username: str, credential_name: str, credential_data: Dict[str, Any]
    ) -> bool:
        """
        为用户添加凭证

        Args:
            username: 户名
            credential_name: 凭证名称
            credential_data: 凭证数据

        Returns:
            是否成功
        """
        await self._ensure_initialized()

        user_data = await self._get_user(username)
        if not user_data:
            raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")

        if user_data.get("disabled", False):
            raise HTTPException(status_code=403, detail="用户已被禁用")

        # 在凭证数据中添加user_id字段
        credential_data["user_id"] = username

        # 生成唯一的凭证文件名: user_username_credentialname
        full_cred_name = f"user_{username}_{credential_name}"

        # 存储凭证
        success = await self._storage.store_credential(full_cred_name, credential_data)

        if success:
            # 更新用户的凭证计数
            user_data["credential_count"] = user_data.get("credential_count", 0) + 1
            user_data["last_active"] = datetime.now(timezone.utc).isoformat()
            await self._storage.update_user(username, user_data)

            log.info(f"用户 {username} 添加凭证: {credential_name}")

        return success

    async def list_user_credentials(self, username: str) -> List[str]:
        """
        列出用户的所有凭证

        Args:
            username: 用户名

        Returns:
            凭证名称列表
        """
        await self._ensure_initialized()

        # 获取所有凭证
        all_credentials = await self._storage.list_credentials()

        # 过滤出属于该用户的凭证
        user_prefix = f"user_{username}_"
        user_credentials = []

        for cred_name in all_credentials:
            if cred_name.startswith(user_prefix):
                # 移除前缀，返回原始凭证名
                original_name = cred_name[len(user_prefix) :]
                user_credentials.append(original_name)

        return user_credentials

    async def get_user_credential(
        self, username: str, credential_name: str
    ) -> Optional[Dict[str, Any]]:
        """
        获取用户的凭证数据

        Args:
            username: 用户名
            credential_name: 凭证名称

        Returns:
            凭证数据
        """
        await self._ensure_initialized()

        full_cred_name = f"user_{username}_{credential_name}"
        credential_data = await self._storage.get_credential(full_cred_name)

        return credential_data

    async def delete_user_credential(self, username: str, credential_name: str) -> bool:
        """
        删除用户的凭证

        Args:
            username: 用户名
            credential_name: 凭证名称

        Returns:
            是否成功
        """
        await self._ensure_initialized()

        success = await self._delete_user_credential(username, credential_name)

        if success:
            # 更新用户的凭证计数
            user_data = await self._get_user(username)
            if user_data:
                user_data["credential_count"] = max(0, user_data.get("credential_count", 0) - 1)
                await self._storage.update_user(username, user_data)

        return success

    async def record_user_api_call(self, username: str):
        """
        记录用户API调用

        Args:
            username: 用户名
        """
        await self._ensure_initialized()

        user_data = await self._get_user(username)
        if user_data:
            user_data["total_calls"] = user_data.get("total_calls", 0) + 1
            user_data["last_active"] = datetime.now(timezone.utc).isoformat()
            await self._storage.update_user(username, user_data)

    # ==================== 内部辅助方法 ====================

    def _generate_user_key(self) -> str:
        """生成用户密钥"""
        return f"musr_{secrets.token_urlsafe(32)}"

    async def _get_user_list(self) -> List[str]:
        """获取用户列表"""
        user_list = await self._storage.list_users()
        return user_list if isinstance(user_list, list) else []

    async def _get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """获取用户数据"""
        user_data = await self._storage.get_user(username)
        return user_data

    async def _delete_user_credential(self, username: str, credential_name: str) -> bool:
        """内部删除凭证方法"""
        full_cred_name = f"user_{username}_{credential_name}"

        # 删除凭证数据
        success = await self._storage.delete_credential(full_cred_name)

        # 删除凭证状态
        if hasattr(self._storage, "delete_credential_state"):
            try:
                await self._storage.delete_credential_state(full_cred_name)
            except Exception as e:
                log.warning(f"删除凭证状态失败 {full_cred_name}: {e}")

        return success


# ==================== 全局实例 ====================

_multi_user_manager: Optional[MultiUserManager] = None


async def get_multi_user_manager() -> MultiUserManager:
    """获取全局多用户管理器实例"""
    global _multi_user_manager

    if _multi_user_manager is None:
        _multi_user_manager = MultiUserManager()
        await _multi_user_manager.initialize()

    return _multi_user_manager


# ==================== 认证依赖 ====================


async def authenticate_admin(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """验证管理员密码"""
    password = await get_api_password()
    token = credentials.credentials

    if token != password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="管理员密码错误")

    return token


async def authenticate_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """验证用户密钥"""
    user_key = credentials.credentials

    manager = await get_multi_user_manager()
    user_data = await manager.get_user_by_key(user_key)

    if not user_data:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="用户密钥无效")

    return user_data


# ==================== 用户路由 (/user) ====================


@router.get("/user/info")
async def get_user_info(user_data: Dict[str, Any] = Depends(authenticate_user)):
    """获取当前用户信息"""
    safe_data = user_data.copy()
    safe_data.pop("user_key", None)  # 不返回密钥
    return JSONResponse(content=safe_data)


@router.get("/user/credentials")
async def list_user_credentials_endpoint(user_data: Dict[str, Any] = Depends(authenticate_user)):
    """列出当前用户的所有凭证"""
    manager = await get_multi_user_manager()
    username = user_data["username"]

    credentials = await manager.list_user_credentials(username)

    # 获取凭证详细信息
    cred_details = []
    for cred_name in credentials:
        storage = await get_storage_adapter()
        cred_state = await storage.get_credential_state(f"user_{username}_{cred_name}")
        cred_details.append(
            {
                "name": cred_name,
                "disabled": cred_state.get("disabled", False),
                "last_success": cred_state.get("last_success"),
                "error_codes": cred_state.get("error_codes", []),
            }
        )

    return JSONResponse(content={"credentials": cred_details})


@router.post("/user/credentials/upload")
async def upload_credential(
    credential_name: str,
    credential_file: UploadFile = File(...),
    user_data: Dict[str, Any] = Depends(authenticate_user),
):
    """上传凭证文件"""
    manager = await get_multi_user_manager()
    username = user_data["username"]

    try:
        # 后端验证：文件大小限制 2KB
        MAX_FILE_SIZE = 2 * 1024  # 2KB
        content = await credential_file.read()
        file_size = len(content)

        if file_size > MAX_FILE_SIZE:
            raise HTTPException(
                status_code=400,
                detail=f"文件过大: {file_size / 1024:.2f}KB (最大 2KB)"
            )

        # 后端验证：文件类型必须是 .json
        if not credential_file.filename.lower().endswith('.json'):
            raise HTTPException(
                status_code=400,
                detail="文件类型错误: 只支持 .json 文件"
            )

        # 后端验证：解析 JSON
        try:
            credential_data = json.loads(content)
        except json.JSONDecodeError as e:
            raise HTTPException(
                status_code=400,
                detail=f"无效的 JSON 文件: {str(e)}"
            )

        # 后端验证：验证凭证结构
        validation_result = _validate_credential_structure(credential_data)
        if not validation_result["valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"无效的凭证文件: {validation_result['reason']}"
            )

        # 添加凭证
        success = await manager.add_user_credential(username, credential_name, credential_data)

        if success:
            return JSONResponse(
                content={"message": f"凭证 {credential_name} 上传成功", "credential_name": credential_name}
            )
        else:
            raise HTTPException(status_code=500, detail="凭证上传失败")

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"上传凭证失败: {e}")
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


def _validate_credential_structure(credential_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    验证凭证文件结构

    Args:
        credential_data: 凭证数据

    Returns:
        包含 valid (bool) 和 reason (str) 的字典
    """
    # 检查是否是字典对象
    if not isinstance(credential_data, dict):
        return {"valid": False, "reason": "文件内容必须是 JSON 对象"}

    # Google Service Account 凭证的必要字段
    service_account_required_fields = ["type", "project_id", "private_key", "client_email"]
    has_service_account_fields = all(field in credential_data for field in service_account_required_fields)

    # Google OAuth2 凭证包含 installed 或 web
    has_oauth2_fields = "installed" in credential_data or "web" in credential_data

    # 至少要满足其中一种凭证格式
    if not has_service_account_fields and not has_oauth2_fields:
        return {
            "valid": False,
            "reason": "文件不是有效的 Google 凭证 (缺少必要字段: type, project_id, private_key, client_email 或 installed/web)"
        }

    # 如果是 Service Account，验证 type 字段
    if has_service_account_fields:
        if credential_data.get("type") != "service_account":
            return {"valid": False, "reason": 'Service Account 凭证的 type 字段必须为 "service_account"'}

        # 验证 private_key 格式
        private_key = credential_data.get("private_key", "")
        if not isinstance(private_key, str) or "-----BEGIN PRIVATE KEY-----" not in private_key:
            return {"valid": False, "reason": "private_key 字段格式无效"}

        # 验证 client_email 格式
        client_email = credential_data.get("client_email", "")
        if not isinstance(client_email, str) or "@" not in client_email:
            return {"valid": False, "reason": "client_email 字段格式无效"}

    return {"valid": True, "reason": ""}


@router.delete("/user/credentials/{credential_name}")
async def delete_credential(
    credential_name: str, user_data: Dict[str, Any] = Depends(authenticate_user)
):
    """删除凭证"""
    manager = await get_multi_user_manager()
    username = user_data["username"]

    success = await manager.delete_user_credential(username, credential_name)

    if success:
        return JSONResponse(content={"message": f"凭证 {credential_name} 删除成功"})
    else:
        raise HTTPException(status_code=500, detail="凭证删除失败")


# ==================== 管理员路由 (/admin) ====================


@router.post("/admin/users")
async def create_user(user_create: UserCreate, _token: str = Depends(authenticate_admin)):
    """创建新用户"""
    manager = await get_multi_user_manager()

    user_data = await manager.create_user(user_create.username, user_create.description)

    return JSONResponse(
        content={
            "message": f"用户 {user_create.username} 创建成功",
            "username": user_data["username"],
            "user_key": user_data["user_key"],
            "created_at": user_data["created_at"],
        }
    )


@router.get("/admin/users")
async def list_users(_token: str = Depends(authenticate_admin)):
    """列出所有用户"""
    manager = await get_multi_user_manager()
    users = await manager.list_users()

    return JSONResponse(content={"users": users})


@router.get("/admin/users/{username}")
async def get_user_details(username: str, _token: str = Depends(authenticate_admin)):
    """获取用户详细信息"""
    manager = await get_multi_user_manager()
    user_data = await manager._get_user(username)

    if not user_data:
        raise HTTPException(status_code=404, detail=f"用户 {username} 不存在")

    # 获取用户统计
    stats = await manager.get_user_stats(username)

    return JSONResponse(content={"user": user_data, "stats": stats})


@router.patch("/admin/users/{username}")
async def update_user(
    username: str, user_update: UserUpdate, _token: str = Depends(authenticate_admin)
):
    """更新用户信息"""
    manager = await get_multi_user_manager()

    updates = {}
    if user_update.disabled is not None:
        updates["disabled"] = user_update.disabled
    if user_update.description is not None:
        updates["description"] = user_update.description

    user_data = await manager.update_user(username, updates)

    return JSONResponse(content={"message": f"用户 {username} 更新成功", "user": user_data})


@router.delete("/admin/users/{username}")
async def delete_user(username: str, _token: str = Depends(authenticate_admin)):
    """删除用户"""
    manager = await get_multi_user_manager()

    success = await manager.delete_user(username)

    if success:
        return JSONResponse(content={"message": f"用户 {username} 删除成功"})
    else:
        raise HTTPException(status_code=500, detail="用户删除失败")


@router.get("/admin/users/{username}/stats")
async def get_user_stats_endpoint(username: str, _token: str = Depends(authenticate_admin)):
    """获取用户使用统计"""
    manager = await get_multi_user_manager()
    stats = await manager.get_user_stats(username)

    return JSONResponse(content=stats)


# ==================== HTML管理界面 ====================


@router.get("/user", response_class=HTMLResponse)
async def user_management_page():
    """用户管理页面"""
    return FileResponse("front/multi_user_auth.html")


@router.get("/admin", response_class=HTMLResponse)
async def admin_management_page():
    """管理员管理页面"""
    return FileResponse("front/multi_user_admin.html")
