"""
Antigravity Model List Router - Handles model list requests
Antigravity 模型列表路由 - 处理模型列表请求
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
    get_base_model_from_feature_model,
    authenticate_flexible
)

# 本地模块 - API
from src.api.antigravity import fetch_available_models

# 本地模块 - 基础路由工具
from src.router.base_router import create_gemini_model_list, create_openai_model_list
from src.models import model_to_dict
from log import log


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== 辅助函数 ====================

async def get_antigravity_models_with_features():
    """
    获取 Antigravity 模型列表并添加功能前缀
    
    Returns:
        带有功能前缀的模型列表
    """
    # 从 API 获取基础模型列表
    base_models_data = await fetch_available_models()
    
    if not base_models_data:
        log.warning("[ANTIGRAVITY MODEL LIST] 无法获取模型列表，返回空列表")
        return []
    
    # 提取模型 ID
    base_model_ids = [model['id'] for model in base_models_data if 'id' in model]
    
    # 添加功能前缀
    models = []
    for base_model in base_model_ids:
        # 基础模型
        models.append(base_model)
        
        # 假流式模型 (前缀格式)
        models.append(f"假流式/{base_model}")
        
        # 流式抗截断模型 (仅在流式传输时有效，前缀格式)
        models.append(f"流式抗截断/{base_model}")
    
    log.info(f"[ANTIGRAVITY MODEL LIST] 生成了 {len(models)} 个模型（包含功能前缀）")
    return models


# ==================== API 路由 ====================

@router.get("/antigravity/v1beta/models")
async def list_gemini_models(token: str = Depends(authenticate_flexible)):
    """
    返回 Gemini 格式的模型列表
    
    从 src.api.antigravity.fetch_available_models 动态获取模型列表
    并添加假流式和流式抗截断前缀
    """
    models = await get_antigravity_models_with_features()
    log.info("[ANTIGRAVITY MODEL LIST] 返回 Gemini 格式")
    return JSONResponse(content=create_gemini_model_list(
        models,
        base_name_extractor=get_base_model_from_feature_model
    ))


@router.get("/antigravity/v1/models")
async def list_openai_models(token: str = Depends(authenticate_flexible)):
    """
    返回 OpenAI 格式的模型列表
    
    从 src.api.antigravity.fetch_available_models 动态获取模型列表
    并添加假流式和流式抗截断前缀
    """
    models = await get_antigravity_models_with_features()
    log.info("[ANTIGRAVITY MODEL LIST] 返回 OpenAI 格式")
    model_list = create_openai_model_list(models, owned_by="google")
    return JSONResponse(content={
        "object": "list",
        "data": [model_to_dict(model) for model in model_list.data]
    })
