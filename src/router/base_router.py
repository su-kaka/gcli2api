from src.i18n import ts
"""
Base Router - {ts("id_3355")}
{ts("id_3356")}
"""

from typing import List

from src.models import Model, ModelList

def create_openai_model_list(
    model_ids: List[str],
    owned_by: str = "google"
) -> ModelList:
    """
    {ts("id_1029")}OpenAI{ts("id_3343")}
    
    Args:
        model_ids: {ts("id_794")}ID{ts("id_2052")}
        owned_by: {ts("id_3357")}
        
    Returns:
        ModelList{ts("id_1509")}
    """
    from datetime import datetime, timezone
    current_timestamp = int(datetime.now(timezone.utc).timestamp())
    
    models = [
        Model(
            id=model_id,
            object='model',
            created=current_timestamp,
            owned_by=owned_by
        )
        for model_id in model_ids
    ]
    
    return ModelList(data=models)


def create_gemini_model_list(
    model_ids: List[str],
    base_name_extractor=None
) -> dict:
    """
    {ts("id_1029")}Gemini{ts("id_3343")}
    
    Args:
        model_ids: {ts("id_794")}ID{ts("id_2052")}
        base_name_extractor: {ts("id_3358")}
        
    Returns:
        {ts("id_3359")}
    """
    gemini_models = []
    
    for model_id in model_ids:
        base_model = model_id
        if base_name_extractor:
            try:
                base_model = base_name_extractor(model_id)
            except Exception:
                pass
        
        model_info = {
            "name": f"models/{model_id}",
            "baseModelId": base_model,
            "version": "001",
            "displayName": model_id,
            "description": f"Gemini {base_model} model",
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
        }
        gemini_models.append(model_info)
    
    return {"models": gemini_models}