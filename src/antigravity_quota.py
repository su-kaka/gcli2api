"""
Antigravity 模型额度查询模块
独立模块，用于获取指定 Antigravity 凭证的模型额度信息
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from config import get_antigravity_api_url
from log import log

from .httpx_client import http_client
from .utils import ANTIGRAVITY_USER_AGENT


def convert_to_beijing_time(utc_time_str: str) -> str:
    """
    将 UTC 时间字符串转换为北京时间显示格式
    
    Args:
        utc_time_str: UTC 时间字符串，如 "2025-01-07T07:27:44Z"
    
    Returns:
        北京时间格式字符串，如 "01-07 15:27"
    """
    if not utc_time_str:
        return "N/A"
    try:
        utc_date = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
        # 转换为北京时间 (UTC+8)
        from datetime import timedelta
        beijing_date = utc_date + timedelta(hours=8)
        return beijing_date.strftime("%m-%d %H:%M")
    except Exception:
        return "N/A"


async def fetch_models_with_quotas(
    credential_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    获取指定凭证的模型额度信息
    
    Args:
        credential_data: 凭证数据，包含 access_token
    
    Returns:
        {
            "success": True/False,
            "data": {
                "lastUpdated": timestamp,
                "models": {
                    "model_id": {
                        "remaining": 0.972,
                        "resetTime": "01-07 15:27",
                        "resetTimeRaw": "2025-01-07T07:27:44Z"
                    }
                }
            },
            "message": "错误信息（如果失败）"
        }
    """
    access_token = credential_data.get("access_token") or credential_data.get("token")
    
    if not access_token:
        return {
            "success": False,
            "message": "凭证中没有 access_token"
        }
    
    # 构建请求头
    headers = {
        'User-Agent': ANTIGRAVITY_USER_AGENT,
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
        'Accept-Encoding': 'gzip'
    }
    
    try:
        antigravity_url = await get_antigravity_api_url()
        
        async with http_client.get_client(timeout=30.0) as client:
            response = await client.post(
                f"{antigravity_url}/v1internal:fetchAvailableModels",
                json={},
                headers=headers,
            )
            
            if response.status_code == 200:
                data = response.json()
                log.debug(f"[ANTIGRAVITY QUOTA] Raw response: {str(data)[:500]}")
                
                # 提取额度信息
                models_with_quotas = {}
                
                if 'models' in data and isinstance(data['models'], dict):
                    for model_id, model_data in data['models'].items():
                        if isinstance(model_data, dict) and 'quotaInfo' in model_data:
                            quota_info = model_data['quotaInfo']
                            remaining = quota_info.get('remainingFraction', 1.0)
                            reset_time_raw = quota_info.get('resetTime', '')
                            
                            models_with_quotas[model_id] = {
                                "remaining": remaining,
                                "resetTime": convert_to_beijing_time(reset_time_raw),
                                "resetTimeRaw": reset_time_raw
                            }
                
                log.info(f"[ANTIGRAVITY QUOTA] Fetched quotas for {len(models_with_quotas)} models")
                
                return {
                    "success": True,
                    "data": {
                        "lastUpdated": int(datetime.now(timezone.utc).timestamp() * 1000),
                        "models": models_with_quotas
                    }
                }
            else:
                error_text = response.text[:500]
                log.error(f"[ANTIGRAVITY QUOTA] API error ({response.status_code}): {error_text}")
                return {
                    "success": False,
                    "message": f"API 错误 ({response.status_code}): {error_text[:100]}"
                }
                
    except Exception as e:
        log.error(f"[ANTIGRAVITY QUOTA] Failed to fetch quotas: {e}")
        return {
            "success": False,
            "message": f"请求失败: {str(e)}"
        }
