from src.i18n import ts
"""
Antigravity Model List Router - Handles model list requests
Antigravity {ts("id_3330")} - {ts("id_3329")}
"""

import sys
from pathlib import Path

# {ts("id_1599")}Python{ts("id_796")}
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# {ts("id_3199")}
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# {ts("id_3201")} - {ts("id_3202")}
from src.utils import (
    get_base_model_from_feature_model,
    authenticate_flexible
)

# {ts("id_3201")} - API
from src.api.antigravity import fetch_available_models

# {ts("id_3201")} - {ts("id_3204")}
from src.router.base_router import create_gemini_model_list, create_openai_model_list
from src.models import model_to_dict
from log import log


# ==================== {ts("id_3207")} ====================

router = APIRouter()


# ==================== {ts("id_1473")} ====================

async def get_antigravity_models_with_features():
    """
    {ts("id_712")} Antigravity {ts("id_3331")}
    
    Returns:
        {ts("id_3332")}
    """
    # {ts("id_1731")} API {ts("id_3333")}
    base_models_data = await fetch_available_models()
    
    if not base_models_data:
        log.warning(f"[ANTIGRAVITY MODEL LIST] {ts("id_3334")}")
        return []
    
    # {ts("id_3335")} ID
    base_model_ids = [model['id'] for model in base_models_data if 'id' in model]
    
    # {ts("id_3336")}
    models = []
    for base_model in base_model_ids:
        # {ts("id_117")}
        models.append(base_model)
        
        # {ts("id_3337")} ({ts("id_3338")})
        models.append(ff"{ts("id_121")}/{base_model}")
        
        # {ts("id_3340")} ({ts("id_3339")})
        models.append(ff"{ts("id_80")}/{base_model}")
    
    log.info(ff"[ANTIGRAVITY MODEL LIST] {ts("id_3342")} {len(models)} {ts("id_3341")}")
    return models


# ==================== API {ts("id_3208")} ====================

@router.get("/antigravity/v1beta/models")
async def list_gemini_models(token: str = Depends(authenticate_flexible)):
    """
    {ts("id_1530")} Gemini {ts("id_3343")}
    
    {ts("id_1731")} src.api.antigravity.fetch_available_models {ts("id_3344")}
    {ts("id_3345")}
    """
    models = await get_antigravity_models_with_features()
    log.info(f"[ANTIGRAVITY MODEL LIST] {ts("id_1530")} Gemini {ts("id_57")}")
    return JSONResponse(content=create_gemini_model_list(
        models,
        base_name_extractor=get_base_model_from_feature_model
    ))


@router.get("/antigravity/v1/models")
async def list_openai_models(token: str = Depends(authenticate_flexible)):
    """
    {ts("id_1530")} OpenAI {ts("id_3343")}
    
    {ts("id_1731")} src.api.antigravity.fetch_available_models {ts("id_3344")}
    {ts("id_3345")}
    """
    models = await get_antigravity_models_with_features()
    log.info(f"[ANTIGRAVITY MODEL LIST] {ts("id_1530")} OpenAI {ts("id_57")}")
    model_list = create_openai_model_list(models, owned_by="google")
    return JSONResponse(content={
        "object": "list",
        "data": [model_to_dict(model) for model in model_list.data]
    })
