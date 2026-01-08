"""
Antigravity Gemini Router - Handles Gemini format requests and converts to Antigravity API
处理 Gemini 格式请求并转换为 Antigravity API 格式
"""

# 标准库
import json
import uuid
from typing import Any, Dict, List

# 第三方库
from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    is_anti_truncation_model, 
    authenticate_gemini_flexible, 
    get_base_model_from_feature_model
)

# 本地模块 - API客户端
from src.api.antigravity import (
    send_antigravity_request_no_stream,
    send_antigravity_request_stream,
    fetch_available_models,
)

# 本地模块 - 转换器
from src.converter.anti_truncation import apply_anti_truncation_to_stream
from src.converter.gemini_fix import (
    build_antigravity_generation_config,
    build_antigravity_request_body,
    prepare_image_generation_request,
)

# 本地模块 - 基础路由工具
from src.router.base_router import (
    get_credential_manager,
    create_gemini_model_list,
    is_health_check_request,
    create_health_check_response,
    log_request_info,
    extract_base_model_name,
)

# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== 模型名称处理 ====================

def model_mapping(model_name: str) -> str:
    """
    模型名映射到 Antigravity 实际模型名
    
    Args:
        model_name: 原始模型名称
        
    Returns:
        映射后的模型名称
        
    参考映射:
        - claude-sonnet-4-5-thinking -> claude-sonnet-4-5
        - claude-opus-4-5 -> claude-opus-4-5-thinking
        - gemini-2.5-flash-thinking -> gemini-2.5-flash
    """
    mapping = {
        "claude-sonnet-4-5-thinking": "claude-sonnet-4-5",
        "claude-opus-4-5": "claude-opus-4-5-thinking",
        "gemini-2.5-flash-thinking": "gemini-2.5-flash",
    }
    return mapping.get(model_name, model_name)


def is_thinking_model(model_name: str) -> bool:
    """
    检测是否是思考模型
    
    Args:
        model_name: 模型名称
        
    Returns:
        是否是思考模型
    """
    # 检查是否包含 -thinking 后缀
    if "-thinking" in model_name:
        return True

    # 检查是否包含 pro 关键词
    if "pro" in model_name.lower():
        return True

    return False


# ==================== 格式转换函数 ====================

def gemini_contents_to_antigravity_contents(gemini_contents: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    将 Gemini 原生 contents 格式转换为 Antigravity contents 格式
    
    Args:
        gemini_contents: Gemini 格式的 contents
        
    Returns:
        Antigravity 格式的 contents（当前格式一致，直接返回）
    """
    return gemini_contents


def convert_antigravity_response_to_gemini(
    response_data: Dict[str, Any]
) -> Dict[str, Any]:
    """
    将 Antigravity 非流式响应转换为 Gemini 格式
    
    Args:
        response_data: Antigravity 响应数据
        
    Returns:
        Gemini 格式的响应数据
        
    Note:
        Antigravity 响应格式: {"response": {...}}
        Gemini 响应格式: {...}
    """
    return response_data.get("response", response_data)


async def convert_antigravity_stream_to_gemini(
    lines_generator: Any,
    stream_ctx: Any,
    client: Any,
    credential_manager: Any,
    credential_name: str
):
    """
    将 Antigravity 流式响应转换为 Gemini 格式的 SSE 流

    Args:
        lines_generator: 行生成器（已经过滤的 SSE 行）
        stream_ctx: 流上下文
        client: HTTP 客户端
        credential_manager: 凭证管理器
        credential_name: 凭证名称
    """
    success_recorded = False

    try:
        async for line in lines_generator:
            if not line or not line.startswith("data: "):
                continue

            # 记录第一次成功响应
            if not success_recorded:
                if credential_name and credential_manager:
                    await credential_manager.record_api_call_result(credential_name, True, mode="antigravity")
                success_recorded = True

            # 解析 SSE 数据
            try:
                data = json.loads(line[6:])  # 去掉 "data: " 前缀
            except:
                continue

            # Antigravity 流式响应格式: {"response": {...}}
            # Gemini 流式响应格式: {...}
            gemini_data = data.get("response", data)

            # 发送 Gemini 格式的数据
            yield f"data: {json.dumps(gemini_data)}\n\n"

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Streaming error: {e}")
        error_response = {
            "error": {
                "message": str(e),
                "code": 500,
                "status": "INTERNAL"
            }
        }
        yield f"data: {json.dumps(error_response)}\n\n"
    finally:
        # 资源清理
        if stream_ctx:
            try:
                await stream_ctx.__aexit__(None, None, None)
            except Exception as e:
                log.debug(f"[ANTIGRAVITY GEMINI] Error closing stream context: {e}")
        
        if client:
            try:
                await client.aclose()
            except Exception as e:
                log.debug(f"[ANTIGRAVITY GEMINI] Error closing client: {e}")


# ==================== API 路由 ====================

@router.get("/antigravity/v1beta/models")
@router.get("/antigravity/v1/models")
async def gemini_list_models(api_key: str = Depends(authenticate_gemini_flexible)):
    """
    返回 Gemini 格式的模型列表
    
    动态从 Antigravity API 获取可用模型
    """

    try:
        # 获取凭证管理器
        cred_mgr = await get_credential_manager()

        # 从 Antigravity API 获取模型列表（返回 OpenAI 格式的字典列表）
        models = await fetch_available_models(cred_mgr)

        if not models:
            # 如果获取失败，返回空列表
            log.warning("[ANTIGRAVITY GEMINI] Failed to fetch models from API, returning empty list")
            return JSONResponse(content={"models": []})

        # 将 OpenAI 格式转换为 Gemini 格式，同时添加抗截断版本
        gemini_models = []
        for model in models:
            model_id = model.get("id", "")

            # 添加原始模型
            gemini_models.append({
                "name": f"models/{model_id}",
                "version": "001",
                "displayName": model_id,
                "description": f"Antigravity API - {model_id}",
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })

            # 添加流式抗截断版本
            anti_truncation_id = f"流式抗截断/{model_id}"
            gemini_models.append({
                "name": f"models/{anti_truncation_id}",
                "version": "001",
                "displayName": anti_truncation_id,
                "description": f"Antigravity API - {anti_truncation_id} (带流式抗截断功能)",
                "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            })

        return JSONResponse(content={"models": gemini_models})

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Error fetching models: {e}")
        # 返回空列表
        return JSONResponse(content={"models": []})


@router.post("/antigravity/v1beta/models/{model:path}:generateContent")
@router.post("/antigravity/v1/models/{model:path}:generateContent")
async def gemini_generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """处理 Gemini 格式的非流式内容生成请求（通过 Antigravity API）"""
    log.debug(f"[ANTIGRAVITY GEMINI] Non-streaming request for model: {model}")

    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")

    # 健康检查
    if is_health_check_request(request_data, format="gemini"):
        response = create_health_check_response(format="gemini")
        response["candidates"][0]["content"]["parts"][0]["text"] = "antigravity API 正常工作中"
        return JSONResponse(content=response)

    # 获取凭证管理器
    cred_mgr = await get_credential_manager()

    # 提取模型名称
    model = extract_base_model_name(model)

    # 检测并处理抗截断模式
    use_anti_truncation = is_anti_truncation_model(model)
    if use_anti_truncation:
        # 去掉 "流式抗截断/" 前缀
        from src.utils import get_base_model_from_feature_model
        model = get_base_model_from_feature_model(model)

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)
    
    # 获取凭证信息
    cred_result = await cred_mgr.get_valid_credential(mode="antigravity")
    if not cred_result:
        log.error("[ANTIGRAVITY GEMINI] 当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    credential_name, credential_data = cred_result
    log_request_info("ANTIGRAVITY GEMINI", actual_model, credential_name, False)

    # 转换 Gemini contents 为 Antigravity contents
    try:
        contents = gemini_contents_to_antigravity_contents(request_data["contents"])
    except Exception as e:
        log.error(f"Failed to convert Gemini contents: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 提取 Gemini generationConfig
    gemini_config = request_data.get("generationConfig", {})

    # 转换为 Antigravity generation_config
    parameters = {
        "temperature": gemini_config.get("temperature"),
        "top_p": gemini_config.get("topP"),
        "top_k": gemini_config.get("topK"),
        "max_tokens": gemini_config.get("maxOutputTokens"),
        # 图片生成相关参数
        "response_modalities": gemini_config.get("response_modalities"),
        "image_config": gemini_config.get("image_config"),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    generation_config = build_antigravity_generation_config(parameters, enable_thinking, actual_model)

    # 获取凭证信息（用于 project_id 和 session_id）
    cred_result = await cred_mgr.get_valid_credential(mode="antigravity")
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("project_id", "default-project")
    session_id = credential_data.get("session_id", f"session-{uuid.uuid4().hex}")

    # 处理 systemInstruction
    system_instruction = None
    if "systemInstruction" in request_data:
        system_instruction = request_data["systemInstruction"]

    # 处理 tools
    antigravity_tools = None
    if "tools" in request_data:
        # Gemini 和 Antigravity 的 tools 格式基本一致
        antigravity_tools = request_data["tools"]

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        project_id=project_id,
        session_id=session_id,
        system_instruction=system_instruction,
        tools=antigravity_tools,
        generation_config=generation_config,
    )

    # 图像生成模型特殊处理
    if "-image" in model:
        request_body = prepare_image_generation_request(request_body, model)

    # 发送非流式请求
    try:
        response_data, cred_name, cred_data = await send_antigravity_request_no_stream(
            request_body, cred_mgr
        )

        # 转换并返回 Gemini 格式响应
        gemini_response = convert_antigravity_response_to_gemini(response_data)
        return JSONResponse(content=gemini_response)

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {str(e)}")


@router.post("/antigravity/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/antigravity/v1/models/{model:path}:streamGenerateContent")
async def gemini_stream_generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible),
):
    """处理 Gemini 格式的流式内容生成请求（通过 Antigravity API）"""
    log.debug(f"[ANTIGRAVITY GEMINI] Streaming request for model: {model}")

    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")

    # 获取凭证管理器
    from src.credential_manager import get_credential_manager
    cred_mgr = await get_credential_manager()

    # 提取模型名称（移除 "models/" 前缀）
    if model.startswith("models/"):
        model = model[7:]

    # 检测并处理抗截断模式
    use_anti_truncation = is_anti_truncation_model(model)
    if use_anti_truncation:
        # 去掉 "流式抗截断/" 前缀
        model = get_base_model_from_feature_model(model)

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    log.info(f"[ANTIGRAVITY GEMINI] Stream request: model={model} -> {actual_model}, thinking={enable_thinking}, anti_truncation={use_anti_truncation}")

    # 转换 Gemini contents 为 Antigravity contents
    try:
        contents = gemini_contents_to_antigravity_contents(request_data["contents"])
    except Exception as e:
        log.error(f"Failed to convert Gemini contents: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 提取 Gemini generationConfig
    gemini_config = request_data.get("generationConfig", {})

    # 转换为 Antigravity generation_config
    parameters = {
        "temperature": gemini_config.get("temperature"),
        "top_p": gemini_config.get("topP"),
        "top_k": gemini_config.get("topK"),
        "max_tokens": gemini_config.get("maxOutputTokens"),
        # 图片生成相关参数
        "response_modalities": gemini_config.get("response_modalities"),
        "image_config": gemini_config.get("image_config"),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    generation_config = build_antigravity_generation_config(parameters, enable_thinking, actual_model)

    # 获取凭证信息（用于 project_id 和 session_id）
    cred_result = await cred_mgr.get_valid_credential(mode="antigravity")
    if not cred_result:
        log.error("当前无可用 antigravity 凭证")
        raise HTTPException(status_code=500, detail="当前无可用 antigravity 凭证")

    _, credential_data = cred_result
    project_id = credential_data.get("project_id", "default-project")
    session_id = credential_data.get("session_id", f"session-{uuid.uuid4().hex}")

    # 处理 systemInstruction
    system_instruction = None
    if "systemInstruction" in request_data:
        system_instruction = request_data["systemInstruction"]

    # 处理 tools
    antigravity_tools = None
    if "tools" in request_data:
        # Gemini 和 Antigravity 的 tools 格式基本一致
        antigravity_tools = request_data["tools"]

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        project_id=project_id,
        session_id=session_id,
        system_instruction=system_instruction,
        tools=antigravity_tools,
        generation_config=generation_config,
    )

    # 图像生成模型特殊处理
    if "-image" in model:
        request_body = prepare_image_generation_request(request_body, model)

    # 发送流式请求
    try:
        # 处理抗截断功能（仅流式传输时有效）
        if use_anti_truncation:
            log.info("[ANTIGRAVITY GEMINI] 启用流式抗截断功能")
            max_attempts = await get_anti_truncation_max_attempts()

            # 包装请求函数以适配抗截断处理器
            async def antigravity_gemini_request_func(payload):
                resources, cred_name, cred_data = await send_antigravity_request_stream(
                    payload, cred_mgr
                )
                response, stream_ctx, client = resources
                return StreamingResponse(
                    convert_antigravity_stream_to_gemini(
                        response, stream_ctx, client, cred_mgr, cred_name
                    ),
                    media_type="text/event-stream"
                )

            return await apply_anti_truncation_to_stream(
                antigravity_gemini_request_func, request_body, max_attempts
            )

        # 流式请求（无抗截断）
        resources, cred_name, cred_data = await send_antigravity_request_stream(
            request_body, cred_mgr
        )
        # resources 是一个元组: (response, stream_ctx, client)
        response, stream_ctx, client = resources

        # 转换并返回流式响应
        # response 现在是 filtered_lines 生成器
        return StreamingResponse(
            convert_antigravity_stream_to_gemini(
                response, stream_ctx, client, cred_mgr, cred_name
            ),
            media_type="text/event-stream"
        )

    except Exception as e:
        log.error(f"[ANTIGRAVITY GEMINI] Stream request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {str(e)}")
