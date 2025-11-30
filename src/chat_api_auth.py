"""
Chat API Authentication Module
专门负责API端点的鉴权，支持多个API key，每个key可以设定次数，调用模型会消耗对应次数
"""

import secrets
import time
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from config import get_panel_password
from log import log
from src.storage_adapter import get_storage_adapter

# 创建路由器
router = APIRouter()
security = HTTPBearer()

# 默认模型消耗配置 (每次调用消耗的次数)
# 格式: {model_name: cost}
DEFAULT_MODEL_COSTS = {
    # 默认模型消耗配置
    "gemini-2.5-pro-preview-06-05": 10,
    "gemini-2.5-pro": 10,
    "gemini-2.5-flash": 2,
    "gemini-3-pro-preview": 15,
    # 假流式模型
    "假流式/gemini-2.5-pro-preview-06-05": 10,
    "假流式/gemini-2.5-pro": 10,
    "假流式/gemini-2.5-flash": 2,
    "假流式/gemini-3-pro-preview": 15,
    # 流式抗截断模型
    "流式抗截断/gemini-2.5-pro-preview-06-05": 20,
    "流式抗截断/gemini-2.5-pro": 20,
    "流式抗截断/gemini-2.5-flash": 4,
    "流式抗截断/gemini-3-pro-preview": 30,
}

# 默认消耗次数（如果模型未配置）
DEFAULT_MODEL_COST = 1


# Pydantic 模型
class CreateAPIKeyRequest(BaseModel):
    """创建 API Key 请求模型"""
    total_quota: int = 1000  # 总次数配额，默认1000次
    description: Optional[str] = None  # 可选的描述信息

    class Config:
        json_schema_extra = {
            "example": {
                "total_quota": 100,
                "description": "测试用 API Key"
            }
        }


class CreateAPIKeyResponse(BaseModel):
    """创建 API Key 响应模型"""
    api_key: str
    total_quota: int
    used_quota: int
    remaining_quota: int
    created_at: str
    description: Optional[str] = None


class APIKeyInfo(BaseModel):
    """API Key 信息模型"""
    api_key: str
    total_quota: int
    used_quota: int
    remaining_quota: int
    created_at: str
    description: Optional[str] = None


class ModelCostConfig(BaseModel):
    """模型消耗配置模型"""
    model_name: str
    cost: int


class UpdateModelCostRequest(BaseModel):
    """更新模型消耗配置请求"""
    model_name: str
    cost: int


# 鉴权函数
async def authenticate_panel(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """验证面板密码（Bearer Token方式）"""
    password = await get_panel_password()
    token = credentials.credentials
    if token != password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="密码错误")
    return token


async def authenticate_api_key(request: Request) -> dict:
    """
    验证 API Key 并返回 key 信息
    支持多种方式传递 API Key:
    1. Authorization: Bearer <api_key>
    2. x-api-key header
    3. api_key query parameter
    """
    api_key = None

    # 尝试从 Authorization header 获取
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        api_key = auth_header[7:]

    # 尝试从 x-api-key header 获取
    if not api_key:
        api_key = request.headers.get("x-api-key")

    # 尝试从 query parameter 获取
    if not api_key:
        api_key = request.query_params.get("api_key")

    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide it via 'Authorization: Bearer <key>', 'x-api-key' header, or 'api_key' query parameter"
        )

    # 从存储中验证 API Key
    storage = await get_storage_adapter()
    key_info = await storage.get_api_key(api_key)

    if not key_info:
        log.warning(f"Invalid API key attempted: {api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )

    # 检查配额是否用完
    if key_info["used_quota"] >= key_info["total_quota"]:
        log.warning(f"API key quota exhausted: {api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="API key quota exhausted"
        )

    return {"api_key": api_key, "info": key_info}


# 辅助函数
def generate_api_key() -> str:
    """生成一个安全的 API Key"""
    return f"sk-{secrets.token_urlsafe(32)}"


async def get_model_cost(model_name: str) -> int:
    """
    获取模型调用消耗的次数
    支持基础模型和带后缀的模型（如 -maxthinking, -nothinking, -search）
    动态从存储读取配置
    """
    storage = await get_storage_adapter()
    model_costs = await storage.get_config("model_costs")

    # 如果存储中没有配置，使用默认配置
    if not model_costs or not isinstance(model_costs, dict):
        model_costs = DEFAULT_MODEL_COSTS

    # 先尝试直接匹配
    if model_name in model_costs:
        return model_costs[model_name]

    # 移除 thinking 和 search 后缀后再匹配
    from config import get_base_model_name

    base_model = get_base_model_name(model_name)
    if base_model in model_costs:
        return model_costs[base_model]

    # 如果还是找不到，返回默认值
    return DEFAULT_MODEL_COST


async def consume_quota(api_key: str, model_name: str) -> int:
    """
    消耗 API Key 的配额
    返回消耗的次数

    注意：即使剩余配额不足，也允许请求，但配额最低降到 0
    例如：剩余 1 次，请求消耗 10 次的模型，配额会变成 0
    """
    storage = await get_storage_adapter()
    key_info = await storage.get_api_key(api_key)

    if not key_info:
        raise ValueError(f"API key not found: {api_key}")

    cost = get_model_cost(model_name)

    # 检查配额是否已经用完（降到0）
    remaining = key_info["total_quota"] - key_info["used_quota"]
    if remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Quota exhausted. Total quota: {key_info['total_quota']}, Used: {key_info['used_quota']}"
        )

    # 消耗配额，但不会超过 total_quota（最低降到 0）
    # 如果剩余不足，消耗剩余的全部，used_quota = total_quota
    actual_cost = min(cost, remaining)
    used_quota = key_info["used_quota"] + actual_cost

    # 记录模型调用统计（记录实际消耗的次数）
    model_stats = key_info.get("model_stats", {})
    model_stats[model_name] = model_stats.get(model_name, 0) + actual_cost

    # 更新存储
    await storage.update_api_key(api_key, {
        "used_quota": used_quota,
        "model_stats": model_stats
    })

    log.info(f"API key {api_key[:10]}... consumed {actual_cost} quota (cost: {cost}) for model {model_name}. Remaining: {key_info['total_quota'] - used_quota}")

    return actual_cost


# API 路由

@router.post("/auth/keys", response_model=CreateAPIKeyResponse)
async def create_api_key(
    request: CreateAPIKeyRequest,
    _: str = Depends(authenticate_panel)
):
    """
    创建一个新的 API Key
    需要面板密码认证
    """
    # 验证 total_quota 必须是正数
    if request.total_quota <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="total_quota must be greater than 0"
        )

    # 生成新的 API Key
    api_key = generate_api_key()

    # 准备 API Key 信息
    created_at = time.time()
    key_data = {
        "total_quota": request.total_quota,
        "used_quota": 0,
        "created_at": created_at,
        "description": request.description,
        "model_stats": {}
    }

    # 存储到数据库
    storage = await get_storage_adapter()
    await storage.store_api_key(api_key, key_data)

    log.info(f"Created new API key: {api_key[:10]}... with quota: {request.total_quota}")

    return CreateAPIKeyResponse(
        api_key=api_key,
        total_quota=request.total_quota,
        used_quota=0,
        remaining_quota=request.total_quota,
        created_at=datetime.fromtimestamp(created_at).isoformat(),
        description=request.description
    )


@router.get("/auth/keys")
async def list_api_keys(_: str = Depends(authenticate_panel)):
    """
    列出所有 API Keys 及其使用情况
    需要面板密码认证
    """
    storage = await get_storage_adapter()
    all_keys = await storage.list_api_keys()

    keys_list = []
    for api_key, info in all_keys.items():
        keys_list.append({
            "api_key": api_key,
            "total_quota": info["total_quota"],
            "used_quota": info["used_quota"],
            "remaining_quota": info["total_quota"] - info["used_quota"],
            "created_at": datetime.fromtimestamp(info["created_at"]).isoformat(),
            "description": info.get("description"),
            "model_stats": info.get("model_stats", {})
        })

    return JSONResponse(content={
        "total_keys": len(keys_list),
        "keys": keys_list
    })


@router.get("/auth/keys/{api_key}")
async def get_api_key_info(api_key: str, _: str = Depends(authenticate_panel)):
    """
    查看特定 API Key 的详细信息
    需要面板密码认证
    """
    storage = await get_storage_adapter()
    info = await storage.get_api_key(api_key)

    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )

    return JSONResponse(content={
        "api_key": api_key,
        "total_quota": info["total_quota"],
        "used_quota": info["used_quota"],
        "remaining_quota": info["total_quota"] - info["used_quota"],
        "created_at": datetime.fromtimestamp(info["created_at"]).isoformat(),
        "description": info.get("description"),
        "model_stats": info.get("model_stats", {})
    })


@router.delete("/auth/keys/{api_key}")
async def delete_api_key(api_key: str, _: str = Depends(authenticate_panel)):
    """
    删除指定的 API Key
    需要面板密码认证
    """
    storage = await get_storage_adapter()
    success = await storage.delete_api_key(api_key)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )

    log.info(f"Deleted API key: {api_key[:10]}...")

    return JSONResponse(content={
        "message": "API key deleted successfully",
        "api_key": api_key
    })


@router.get("/auth/quota")
async def check_quota(auth_result: dict = Depends(authenticate_api_key)):
    """
    查看当前 API Key 的配额使用情况
    使用 API Key 认证
    """
    api_key = auth_result["api_key"]
    info = auth_result["info"]

    return JSONResponse(content={
        "api_key": api_key[:10] + "...",  # 只显示前10位保护隐私
        "total_quota": info["total_quota"],
        "used_quota": info["used_quota"],
        "remaining_quota": info["total_quota"] - info["used_quota"],
        "model_stats": info.get("model_stats", {})
    })


@router.post("/auth/query")
async def query_api_key_usage(request: Request):
    """
    查询 API Key 的使用情况（无需鉴权）
    用户通过提供完整的 API Key 来查询自己的使用情况
    请求体格式: {"api_key": "sk-xxx"}
    """
    try:
        data = await request.json()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid JSON: {str(e)}"
        )

    api_key = data.get("api_key")
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'api_key' field in request body"
        )

    # 从存储中获取 API Key 信息
    storage = await get_storage_adapter()
    info = await storage.get_api_key(api_key)

    if not info:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found"
        )

    # 返回使用情况
    return JSONResponse(content={
        "api_key": api_key[:10] + "..." + api_key[-4:],  # 显示前10位和后4位
        "total_quota": info["total_quota"],
        "used_quota": info["used_quota"],
        "remaining_quota": info["total_quota"] - info["used_quota"],
        "created_at": datetime.fromtimestamp(info["created_at"]).isoformat(),
        "description": info.get("description"),
        "model_stats": info.get("model_stats", {}),
        "usage_percentage": round(info["used_quota"] / info["total_quota"] * 100, 2) if info["total_quota"] > 0 else 0
    })


@router.get("/auth/models/costs")
async def list_model_costs(_: str = Depends(authenticate_panel)):
    """
    列出所有模型的消耗配置
    需要面板密码认证
    """
    storage = await get_storage_adapter()
    model_costs = await storage.get_config("model_costs")

    # 如果存储中没有配置，使用默认配置
    if not model_costs or not isinstance(model_costs, dict):
        model_costs = DEFAULT_MODEL_COSTS

    return JSONResponse(content={
        "default_cost": DEFAULT_MODEL_COST,
        "model_costs": model_costs
    })


@router.post("/auth/models/costs")
async def update_model_cost(
    request: UpdateModelCostRequest,
    _: str = Depends(authenticate_panel)
):
    """
    更新模型的消耗配置
    需要面板密码认证
    """
    storage = await get_storage_adapter()
    model_costs = await storage.get_config("model_costs")

    # 如果存储中没有配置，先初始化为默认配置
    if not model_costs or not isinstance(model_costs, dict):
        model_costs = DEFAULT_MODEL_COSTS.copy()

    # 更新配置
    model_costs[request.model_name] = request.cost
    await storage.set_config("model_costs", model_costs)

    log.info(f"Updated model cost for {request.model_name}: {request.cost}")

    return JSONResponse(content={
        "message": "Model cost updated successfully",
        "model_name": request.model_name,
        "cost": request.cost
    })


@router.delete("/auth/models/costs/{model_name}")
async def delete_model_cost_config(
    model_name: str,
    _: str = Depends(authenticate_panel)
):
    """
    删除模型的消耗配置（恢复为默认值）
    需要面板密码认证
    """
    storage = await get_storage_adapter()
    model_costs = await storage.get_config("model_costs")

    if not model_costs or not isinstance(model_costs, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model cost configuration not found"
        )

    if model_name not in model_costs:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Model cost configuration not found"
        )

    # 删除配置
    del model_costs[model_name]
    await storage.set_config("model_costs", model_costs)

    log.info(f"Deleted model cost configuration for {model_name}")

    return JSONResponse(content={
        "message": "Model cost configuration deleted successfully",
        "model_name": model_name
    })


@router.get("/auth/models/{model_name}/cost")
async def get_model_cost_info(model_name: str):
    """
    查询特定模型的消耗次数
    公开接口，无需认证
    """
    cost = await get_model_cost(model_name)

    storage = await get_storage_adapter()
    model_costs = await storage.get_config("model_costs")

    # 如果存储中没有配置，使用默认配置
    if not model_costs or not isinstance(model_costs, dict):
        model_costs = DEFAULT_MODEL_COSTS

    return JSONResponse(content={
        "model_name": model_name,
        "cost": cost,
        "is_default": model_name not in model_costs
    })


# 导出给其他模块使用的函数
__all__ = [
    "router",
    "authenticate_api_key",
    "consume_quota",
    "get_model_cost",
]
