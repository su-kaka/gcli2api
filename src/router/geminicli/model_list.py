"""
Gemini CLI Model List Router - Handles model list requests
Gemini CLI 模型列表路由 - 处理模型列表请求
"""

import sys
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# 第三方库
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# 本地模块 - 工具和认证
from src.utils import (
    get_available_models,
    get_base_model_from_feature_model,
    authenticate_flexible
)

# 本地模块 - 基础路由工具
from src.router.base_router import create_gemini_model_list, create_openai_model_list
from src.models import model_to_dict
from log import log


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== API 路由 ====================

@router.get("/v1beta/models")
async def list_gemini_models(token: str = Depends(authenticate_flexible)):
    """
    返回 Gemini 格式的模型列表

    使用 create_gemini_model_list 工具函数创建标准格式
    """
    models = get_available_models("gemini")
    log.info("[GEMINICLI MODEL LIST] 返回 Gemini 格式")
    return JSONResponse(content=create_gemini_model_list(
        models,
        base_name_extractor=get_base_model_from_feature_model
    ))


@router.get("/v1/models")
async def list_openai_models(token: str = Depends(authenticate_flexible)):
    """
    返回 OpenAI 格式的模型列表

    使用 create_openai_model_list 工具函数创建标准格式
    """
    models = get_available_models("gemini")
    log.info("[GEMINICLI MODEL LIST] 返回 OpenAI 格式")
    model_list = create_openai_model_list(models, owned_by="google")
    return JSONResponse(content={
        "object": "list",
        "data": [model_to_dict(model) for model in model_list.data]
    })
