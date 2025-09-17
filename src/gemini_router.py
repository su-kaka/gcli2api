"""
Gemini Router - Handles native Gemini format API requests
处理原生Gemini格式请求的路由模块
"""
import asyncio
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request, Path, Query, status, Header
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import get_available_models, is_fake_streaming_model, is_anti_truncation_model, get_base_model_from_feature_model, get_anti_truncation_max_attempts, get_base_model_name
from log import log
from .anti_truncation import apply_anti_truncation_to_stream
from .credential_manager import CredentialManager
from .google_chat_api import send_gemini_request, build_gemini_payload_from_native
from .openai_transfer import _extract_content_and_reasoning
from .task_manager import create_managed_task
# 创建路由器
router = APIRouter()
security = HTTPBearer()

# 全局凭证管理器实例
credential_manager = None

@asynccontextmanager
async def get_credential_manager():
    """获取全局凭证管理器实例"""
    global credential_manager
    if not credential_manager:
        credential_manager = CredentialManager()
        await credential_manager.initialize()
    yield credential_manager

async def authenticate(credentials: HTTPAuthorizationCredentials = Depends(security)) -> str:
    """验证用户密码（Bearer Token方式）"""
    from config import get_api_password
    password = await get_api_password()
    token = credentials.credentials
    if token != password:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="密码错误")
    return token

async def authenticate_gemini_flexible(
    request: Request,
    x_goog_api_key: Optional[str] = Header(None, alias="x-goog-api-key"),
    key: Optional[str] = Query(None),
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(lambda: None)
) -> str:
    """灵活验证：支持x-goog-api-key头部、URL参数key或Authorization Bearer"""
    from config import get_api_password
    password = await get_api_password()
    
    # 尝试从URL参数key获取（Google官方标准方式）
    if key:
        log.debug(f"Using URL parameter key authentication")
        if key == password:
            return key
    
    # 尝试从Authorization头获取（兼容旧方式）
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]  # 移除 "Bearer " 前缀
        log.debug(f"Using Bearer token authentication")
        if token == password:
            return token
    
    # 尝试从x-goog-api-key头获取（新标准方式）
    if x_goog_api_key:
        log.debug(f"Using x-goog-api-key authentication")
        if x_goog_api_key == password:
            return x_goog_api_key
    
    log.error(f"Authentication failed. Headers: {dict(request.headers)}, Query params: key={key}")
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, 
        detail="Missing or invalid authentication. Use 'key' URL parameter, 'x-goog-api-key' header, or 'Authorization: Bearer <token>'"
    )

@router.get("/v1/v1beta/models")
@router.get("/v1/v1/models")
@router.get("/v1beta/models")
@router.get("/v1/models")
async def list_gemini_models():
    """返回Gemini格式的模型列表"""
    models = get_available_models("gemini")
    
    # 构建符合Gemini API格式的模型列表
    gemini_models = []
    for model_name in models:
        # 获取基础模型名
        base_model = get_base_model_from_feature_model(model_name)
        
        model_info = {
            "name": f"models/{model_name}",
            "baseModelId": base_model,
            "version": "001",
            "displayName": model_name,
            "description": f"Gemini {base_model} model",
            "inputTokenLimit": 1000000,
            "outputTokenLimit": 8192,
            "supportedGenerationMethods": ["generateContent", "streamGenerateContent"],
            "temperature": 1.0,
            "maxTemperature": 2.0,
            "topP": 0.95,
            "topK": 64
        }
        gemini_models.append(model_info)
    
    return JSONResponse(content={
        "models": gemini_models
    })

@router.post("/v1/v1beta/models/{model:path}:generateContent")
@router.post("/v1/v1/models/{model:path}:generateContent")
@router.post("/v1beta/models/{model:path}:generateContent")
@router.post("/v1/models/{model:path}:generateContent")
async def generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible)
):
    """处理Gemini格式的内容生成请求（非流式）"""
    
    
    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")
    
    # 请求预处理：限制参数
    if "generationConfig" in request_data and request_data["generationConfig"]:
        generation_config = request_data["generationConfig"]
        
        # 限制max_tokens (在Gemini中叫maxOutputTokens)
        if "maxOutputTokens" in generation_config and generation_config["maxOutputTokens"] is not None:
            if generation_config["maxOutputTokens"] > 65535:
                generation_config["maxOutputTokens"] = 65535
                
        # 覆写 top_k 为 64 (在Gemini中叫topK)
        generation_config["topK"] = 64
    else:
        # 如果没有generationConfig，创建一个并设置topK
        request_data["generationConfig"] = {"topK": 64}
    
    # 处理模型名称和功能检测
    use_anti_truncation = is_anti_truncation_model(model)
    
    # 获取基础模型名
    real_model = get_base_model_from_feature_model(model)
    
    # 对于假流式模型，如果是流式端点才返回假流式响应
    # 注意：这是generateContent端点，不应该触发假流式
    
    # 对于抗截断模型的非流式请求，给出警告
    if use_anti_truncation:
        log.warning("抗截断功能仅在流式传输时有效，非流式请求将忽略此设置")
    
    # 健康检查
    if (len(request_data["contents"]) == 1 and 
        request_data["contents"][0].get("role") == "user" and
        request_data["contents"][0].get("parts", [{}])[0].get("text") == "Hi"):
        return JSONResponse(content={
            "candidates": [{
                "content": {
                    "parts": [{"text": "gcli2api工作中"}],
                    "role": "model"
                },
                "finishReason": "STOP",
                "index": 0
            }]
        })
    
    # 获取凭证管理器
    from src.credential_manager import get_credential_manager
    cred_mgr = await get_credential_manager()
    
    # 获取有效凭证
    credential_result = await cred_mgr.get_valid_credential()
    if not credential_result:
        log.error("当前无可用凭证，请去控制台获取")
        raise HTTPException(status_code=500, detail="当前无可用凭证，请去控制台获取")
    
    # 增加调用计数
    cred_mgr.increment_call_count()
    
    # 构建Google API payload
    try:
        api_payload = build_gemini_payload_from_native(request_data, real_model)
    except Exception as e:
        log.error(f"Gemini payload build failed: {e}")
        raise HTTPException(status_code=500, detail="Request processing failed")
    
    # 发送请求（429重试已在google_api_client中处理）
    response = await send_gemini_request(api_payload, False, cred_mgr)
    
    # 处理响应
    try:
        if hasattr(response, 'body'):
            response_data = json.loads(response.body.decode() if isinstance(response.body, bytes) else response.body)
        elif hasattr(response, 'content'):
            response_data = json.loads(response.content.decode() if isinstance(response.content, bytes) else response.content)
        else:
            response_data = json.loads(str(response))
        
        return JSONResponse(content=response_data)
        
    except Exception as e:
        log.error(f"Response processing failed: {e}")
        # 返回原始响应
        if hasattr(response, 'content'):
            return JSONResponse(content=json.loads(response.content))
        else:
            raise HTTPException(status_code=500, detail="Response processing failed")

@router.post("/v1/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/v1/v1/models/{model:path}:streamGenerateContent")
@router.post("/v1beta/models/{model:path}:streamGenerateContent")
@router.post("/v1/models/{model:path}:streamGenerateContent")
async def stream_generate_content(
    model: str = Path(..., description="Model name"),
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible)
):
    """处理Gemini格式的流式内容生成请求"""
    log.debug(f"Stream request received for model: {model}")
    log.debug(f"Request headers: {dict(request.headers)}")
    log.debug(f"API key received: {api_key[:10] if api_key else None}...")
    try:
        body = await request.body()
        log.debug(f"request body: {body.decode() if isinstance(body, bytes) else body}")
    except Exception as e:
        log.error(f"Failed to read request body: {e}")


    # 获取原始请求数据
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # 验证必要字段
    if "contents" not in request_data or not request_data["contents"]:
        raise HTTPException(status_code=400, detail="Missing required field: contents")
    
    # 请求预处理：限制参数
    if "generationConfig" in request_data and request_data["generationConfig"]:
        generation_config = request_data["generationConfig"]
        
        # 限制max_tokens (在Gemini中叫maxOutputTokens)
        if "maxOutputTokens" in generation_config and generation_config["maxOutputTokens"] is not None:
            if generation_config["maxOutputTokens"] > 65535:
                generation_config["maxOutputTokens"] = 65535
                
        # 覆写 top_k 为 64 (在Gemini中叫topK)
        generation_config["topK"] = 64
    else:
        # 如果没有generationConfig，创建一个并设置topK
        request_data["generationConfig"] = {"topK": 64}
    
    # 处理模型名称和功能检测
    use_fake_streaming = is_fake_streaming_model(model)
    use_anti_truncation = is_anti_truncation_model(model)
    
    # 获取基础模型名
    real_model = get_base_model_from_feature_model(model)
    
    # 对于假流式模型，返回假流式响应
    if use_fake_streaming:
        return await fake_stream_response_gemini(request_data, real_model)
    
    # 获取凭证管理器
    from src.credential_manager import get_credential_manager
    cred_mgr = await get_credential_manager()
    
    # 获取有效凭证
    credential_result = await cred_mgr.get_valid_credential()
    if not credential_result:
        log.error("当前无可用凭证，请去控制台获取")
        raise HTTPException(status_code=500, detail="当前无可用凭证，请去控制台获取")
    
    # 增加调用计数
    cred_mgr.increment_call_count()
    
    # 构建Google API payload
    try:
        api_payload = build_gemini_payload_from_native(request_data, real_model)
    except Exception as e:
        log.error(f"Gemini payload build failed: {e}")
        raise HTTPException(status_code=500, detail="Request processing failed")
    
    # 处理抗截断功能（仅流式传输时有效）
    if use_anti_truncation:
        log.info("启用流式抗截断功能")
        # 使用流式抗截断处理器
        max_attempts = await get_anti_truncation_max_attempts()
        return await apply_anti_truncation_to_stream(
            lambda payload: send_gemini_request(payload, True, cred_mgr),
            api_payload,
            max_attempts
        )
    
    # 常规流式请求（429重试已在google_api_client中处理）
    response = await send_gemini_request(api_payload, True, cred_mgr)
    
    # 直接返回流式响应
    return response
    
@router.post("/v1/v1beta/models/{model:path}:countTokens")
@router.post("/v1/v1/models/{model:path}:countTokens")
@router.post("/v1beta/models/{model:path}:countTokens")
@router.post("/v1/models/{model:path}:countTokens")
async def count_tokens(
    request: Request = None,
    api_key: str = Depends(authenticate_gemini_flexible)
):
    """模拟Gemini格式的token计数"""
    
    try:
        request_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    
    # 简单的token计数模拟 - 基于文本长度估算
    total_tokens = 0
    
    # 如果有contents字段
    if "contents" in request_data:
        for content in request_data["contents"]:
            if "parts" in content:
                for part in content["parts"]:
                    if "text" in part:
                        # 简单估算：大约4字符=1token
                        text_length = len(part["text"])
                        total_tokens += max(1, text_length // 4)
    
    # 如果有generateContentRequest字段
    elif "generateContentRequest" in request_data:
        gen_request = request_data["generateContentRequest"]
        if "contents" in gen_request:
            for content in gen_request["contents"]:
                if "parts" in content:
                    for part in content["parts"]:
                        if "text" in part:
                            text_length = len(part["text"])
                            total_tokens += max(1, text_length // 4)
    
    # 返回Gemini格式的响应
    return JSONResponse(content={
        "totalTokens": total_tokens
    })

@router.get("/v1/v1beta/models/{model:path}")
@router.get("/v1/v1/models/{model:path}")
@router.get("/v1beta/models/{model:path}")
@router.get("/v1/models/{model:path}")
async def get_model_info(
    model: str = Path(..., description="Model name"),
    api_key: str = Depends(authenticate_gemini_flexible)
):
    """获取特定模型的信息"""
    
    # 获取基础模型名称
    base_model = get_base_model_name(model)
    
    # 模拟模型信息
    model_info = {
        "name": f"models/{base_model}",
        "baseModelId": base_model,
        "version": "001",
        "displayName": base_model,
        "description": f"Gemini {base_model} model",
        "inputTokenLimit": 128000,
        "outputTokenLimit": 8192,
        "supportedGenerationMethods": [
            "generateContent",
            "streamGenerateContent"
        ],
        "temperature": 1.0,
        "maxTemperature": 2.0,
        "topP": 0.95,
        "topK": 64
    }
    
    return JSONResponse(content=model_info)

async def fake_stream_response_gemini(request_data: dict, model: str):
    """处理Gemini格式的假流式响应"""
    
    async def gemini_stream_generator():
        try:
            # 获取凭证管理器
            from src.credential_manager import get_credential_manager
            cred_mgr = await get_credential_manager()
            
            # 获取有效凭证
            credential_result = await cred_mgr.get_valid_credential()
            if not credential_result:
                log.error("当前无可用凭证，请去控制台获取")
                error_chunk = {
                    "error": {
                        "message": "当前无凭证，请去控制台获取",
                        "type": "authentication_error",
                        "code": 500
                    }
                }
                yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return
            
            # 增加调用计数
            cred_mgr.increment_call_count()
            
            # 构建Google API payload
            try:
                api_payload = build_gemini_payload_from_native(request_data, model)
            except Exception as e:
                log.error(f"Gemini payload build failed: {e}")
                error_chunk = {
                    "error": {
                        "message": f"Request processing failed: {str(e)}",
                        "type": "api_error",
                        "code": 500
                    }
                }
                yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                yield "data: [DONE]\n\n".encode()
                return
            
            # 发送心跳
            heartbeat = {
                "candidates": [{
                    "content": {
                        "parts": [{"text": ""}],
                        "role": "model"
                    },
                    "finishReason": None,
                    "index": 0
                }]
            }
            yield f"data: {json.dumps(heartbeat)}\n\n".encode()
            
            # 异步发送实际请求
            async def get_response():
                return await send_gemini_request(api_payload, False, cred_mgr)
            
            # 创建请求任务
            response_task = create_managed_task(get_response(), name="gemini_fake_stream_request")
            
            try:
                # 每3秒发送一次心跳，直到收到响应
                while not response_task.done():
                    await asyncio.sleep(3.0)
                    if not response_task.done():
                        yield f"data: {json.dumps(heartbeat)}\n\n".encode()
                
                # 获取响应结果
                response = await response_task

            except asyncio.CancelledError:
                # 取消任务并传播取消
                response_task.cancel()
                try:
                    await response_task
                except asyncio.CancelledError:
                    pass
                raise
            except Exception as e:
                # 取消任务并处理其他异常
                response_task.cancel()
                try:
                    await response_task
                except asyncio.CancelledError:
                    pass
                log.error(f"Fake streaming request failed: {e}")
                raise

            # 发送实际请求
            # response 已在上面获取

            # 处理结果
            try:
                if hasattr(response, 'body'):
                    response_data = json.loads(response.body.decode() if isinstance(response.body, bytes) else response.body)
                elif hasattr(response, 'content'):
                    response_data = json.loads(response.content.decode() if isinstance(response.content, bytes) else response.content)
                else:
                    response_data = json.loads(str(response))
                
                log.debug(f"Gemini fake stream response data: {response_data}")
                
                # 发送完整内容作为单个chunk，使用思维链分离
                if "candidates" in response_data and response_data["candidates"]:
                    candidate = response_data["candidates"][0]
                    if "content" in candidate and "parts" in candidate["content"]:
                        parts = candidate["content"]["parts"]
                        content, reasoning_content = _extract_content_and_reasoning(parts)
                        log.debug(f"Gemini extracted content: {content}")
                        log.debug(f"Gemini extracted reasoning: {reasoning_content[:100] if reasoning_content else 'None'}...")
                        
                        # 如果没有正常内容但有思维内容
                        if not content and reasoning_content:
                            log.warning(f"Gemini fake stream contains only thinking content: {reasoning_content[:100]}...")
                            content = "[模型正在思考中，请稍后再试或重新提问]"
                        
                        if content:
                            # 构建包含分离内容的响应
                            parts_response = [{"text": content}]
                            if reasoning_content:
                                parts_response.append({"text": reasoning_content, "thought": True})
                            
                            content_chunk = {
                                "candidates": [{
                                    "content": {
                                        "parts": parts_response,
                                        "role": "model"
                                    },
                                    "finishReason": candidate.get("finishReason", "STOP"),
                                    "index": 0
                                }]
                            }
                            yield f"data: {json.dumps(content_chunk)}\n\n".encode()
                        else:
                            log.warning(f"No content found in Gemini candidate: {candidate}")
                            # 提供默认回复
                            error_chunk = {
                                "candidates": [{
                                    "content": {
                                        "parts": [{"text": "[响应为空，请重新尝试]"}],
                                        "role": "model"
                                    },
                                    "finishReason": "STOP",
                                    "index": 0
                                }]
                            }
                            yield f"data: {json.dumps(error_chunk)}\n\n".encode()
                    else:
                        log.warning(f"No content/parts found in Gemini candidate: {candidate}")
                        # 返回原始响应
                        yield f"data: {json.dumps(response_data)}\n\n".encode()
                else:
                    log.warning(f"No candidates found in Gemini response: {response_data}")
                    yield f"data: {json.dumps(response_data)}\n\n".encode()
                
            except Exception as e:
                log.error(f"Response parsing failed: {e}")
                error_chunk = {
                    "candidates": [{
                        "content": {
                            "parts": [{"text": f"Response parsing error: {str(e)}"}],
                            "role": "model"
                        },
                        "finishReason": "ERROR",
                        "index": 0
                    }]
                }
                yield f"data: {json.dumps(error_chunk)}\n\n".encode()
            
            yield "data: [DONE]\n\n".encode()
                
        except Exception as e:
            log.error(f"Fake streaming error: {e}")
            error_chunk = {
                "error": {
                    "message": f"Fake streaming error: {str(e)}",
                    "type": "api_error", 
                    "code": 500
                }
            }
            yield f"data: {json.dumps(error_chunk)}\n\n".encode()
            yield "data: [DONE]\n\n".encode()

    return StreamingResponse(gemini_stream_generator(), media_type="text/event-stream")
