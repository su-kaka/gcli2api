"""
Base Router - 共用的路由基础功能
提供模型列表处理、通用响应等共同功能
"""

from typing import List, Optional

from src.models import Model, ModelList


# ==================== 模型列表处理 ====================

def expand_models_with_features(
    base_models: List[str],
    features: Optional[List[str]] = None
) -> List[str]:
    """
    使用特性前缀扩展模型列表
    
    Args:
        base_models: 基础模型列表
        features: 特性前缀列表，如 ["流式抗截断", "假流式"]
        
    Returns:
        扩展后的模型列表（包含原始模型和特性变体）
    """
    if not features:
        return base_models.copy()
    
    expanded = []
    for model in base_models:
        # 添加原始模型
        expanded.append(model)
        
        # 添加特性变体
        for feature in features:
            expanded.append(f"{feature}/{model}")
    
    return expanded


def create_openai_model_list(
    model_ids: List[str],
    owned_by: str = "google"
) -> ModelList:
    """
    创建OpenAI格式的模型列表
    
    Args:
        model_ids: 模型ID列表
        owned_by: 模型所有者
        
    Returns:
        ModelList对象
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
    创建Gemini格式的模型列表
    
    Args:
        model_ids: 模型ID列表
        base_name_extractor: 可选的基础模型名提取函数
        
    Returns:
        包含模型列表的字典
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