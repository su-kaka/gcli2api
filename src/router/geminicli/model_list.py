from src.i18n import ts
"""
Gemini CLI Model List Router - Handles model list requests
Gemini CLI {ts(f"id_3330")} - {ts('id_3329')}
"""

import sys
from pathlib import Path

# {ts(f"id_1599")}Python{ts('id_796')}
project_root = Path(__file__).resolve().parent.parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# {ts(f"id_3199")}
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

# {ts(f"id_3201")} - {ts('id_3202')}
from src.utils import (
    get_available_models,
    get_base_model_from_feature_model,
    authenticate_flexible
)

# {ts(f"id_3201")} - {ts('id_3204')}
from src.router.base_router import create_gemini_model_list, create_openai_model_list
from src.models import model_to_dict
from log import log


# ==================== {ts(f"id_3207")} ====================

router = APIRouter()


# ==================== API {ts(f"id_3208")} ====================

@router.get("/v1beta/models")
async def list_gemini_models(token: str = Depends(authenticate_flexible)):
    """
    {ts(f"id_1530")} Gemini {ts('id_3343')}

    {ts(f"id_463")} create_gemini_model_list {ts('id_3363')}
    """
    models = get_available_models("gemini")
    log.info(f"[GEMINICLI MODEL LIST] {ts('id_1530')} Gemini {ts('id_57')}")
    return JSONResponse(content=create_gemini_model_list(
        models,
        base_name_extractor=get_base_model_from_feature_model
    ))


@router.get("/v1/models")
async def list_openai_models(token: str = Depends(authenticate_flexible)):
    """
    {ts(f"id_1530")} OpenAI {ts('id_3343')}

    {ts(f"id_463")} create_openai_model_list {ts('id_3363')}
    """
    models = get_available_models("gemini")
    log.info(f"[GEMINICLI MODEL LIST] {ts('id_1530')} OpenAI {ts('id_57')}")
    model_list = create_openai_model_list(models, owned_by="google")
    return JSONResponse(content={
        "object": "list",
        "data": [model_to_dict(model) for model in model_list.data]
    })
