"""
Antigravity OpenAI Router - OpenAI格式API路由
通过Antigravity处理OpenAI格式的聊天完成请求
"""

# 标准库
import json
import time
from typing import Any, Dict

# 第三方库
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# 本地模块 - 配置和日志
from config import get_anti_truncation_max_attempts
from log import log

# 本地模块 - 工具和认证
from src.utils import (
    is_anti_truncation_model,
    get_base_model_from_feature_model,
    authenticate_bearer,
)

# 本地模块 - 模型和API客户端
from src.models import (
    ChatCompletionRequest,
    Model,
    ModelList,
    model_to_dict,
    OpenAIChatCompletionChoice,
    OpenAIChatCompletionResponse,
    OpenAIChatMessage,
)
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
from src.converter.openai2gemini import (
    convert_openai_tools_to_gemini,
    extract_tool_calls_from_parts,
    openai_messages_to_gemini_contents,
    gemini_stream_chunk_to_openai,
)

# 本地模块 - 基础路由工具
# 已不需要从 base_router 导入 get_credential_manager
from src.router.hi_check import is_health_check_request, create_health_check_response


# ==================== 路由器初始化 ====================

router = APIRouter()


# ==================== 模型名称映射 ====================

def model_mapping(model_name: str) -> str:
    """
    OpenAI模型名映射到Antigravity实际模型名
    
    映射规则：
    - claude-sonnet-4-5-thinking -> claude-sonnet-4-5
    - claude-opus-4-5 -> claude-opus-4-5-thinking
    - gemini-2.5-flash-thinking -> gemini-2.5-flash
    
    Args:
        model_name: OpenAI格式的模型名
        
    Returns:
        Antigravity实际模型名
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
    
    检测规则：
    - 包含 -thinking 后缀
    - 包含 pro 关键词
    
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


# ==================== 辅助函数 ====================

async def convert_antigravity_stream_to_openai(
    lines_generator: Any,
    stream_ctx: Any,
    client: Any,
    model: str,
    request_id: str,
):
    """
    将Antigravity流式响应转换为OpenAI格式的SSE流
    
    使用openai2gemini模块的gemini_stream_chunk_to_openai函数进行格式转换
    
    Args:
        lines_generator: SSE行生成器（已过滤）
        stream_ctx: 流上下文管理器
        client: HTTP客户端
        model: 模型名称
        request_id: 请求ID
        
    Yields:
        OpenAI格式的SSE数据块
    """
    try:
        async for line in lines_generator:
            # 处理 bytes 类型
            if isinstance(line, bytes):
                if not line.startswith(b"data: "):
                    continue
                # 解码 bytes 后再解析
                line_str = line.decode('utf-8', errors='ignore')
            else:
                line_str = str(line)
                if not line_str.startswith("data: "):
                    continue

            # 解析 SSE 数据
            try:
                data = json.loads(line_str[6:])  # 去掉 "data: " 前缀
            except:
                continue

            # Antigravity 响应格式: {"response": {src.}}
            # 提取内层的 Gemini 格式数据
            gemini_chunk = data.get("response", data)

            # 使用 openai2gemini 模块的函数转换为 OpenAI 格式
            openai_chunk = gemini_stream_chunk_to_openai(gemini_chunk, model, request_id)

            # 发送 OpenAI 格式的 chunk
            yield f"data: {json.dumps(openai_chunk)}\n\n"

        # 发送结束标记
        yield "data: [DONE]\n\n"

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Streaming error: {e}")
        error_response = {
            "error": {
                "message": str(e),
                "type": "api_error",
                "code": 500
            }
        }
        yield f"data: {json.dumps(error_response)}\n\n"
    finally:
        # 确保清理所有资源
        try:
            await stream_ctx.__aexit__(None, None, None)
        except Exception as e:
            log.debug(f"[ANTIGRAVITY] Error closing stream context: {e}")
        try:
            await client.aclose()
        except Exception as e:
            log.debug(f"[ANTIGRAVITY] Error closing client: {e}")


def convert_antigravity_response_to_openai(
    response_data: Dict[str, Any],
    model: str,
    request_id: str
) -> Dict[str, Any]:
    """
    将Antigravity非流式响应转换为OpenAI格式
    
    处理内容包括：
    - 工具调用提取
    - 思考内容处理
    - 图片数据转换
    - 使用统计提取
    
    Args:
        response_data: Antigravity响应数据（已unwrap，无"response"包装）
        model: 模型名称
        request_id: 请求ID
        
    Returns:
        OpenAI格式的响应字典
    """
    # 提取 parts（response_data 已经是 unwrap 后的数据，直接访问）
    parts = response_data.get("candidates", [{}])[0].get("content", {}).get("parts", [])

    # 使用 openai2gemini 模块函数提取工具调用和文本内容
    tool_calls_list, text_content = extract_tool_calls_from_parts(parts, is_streaming=False)
    
    thinking_content = ""
    content = text_content  # 使用提取的文本内容作为基础

    for part in parts:
        # 处理思考内容（extract_tool_calls_from_parts 不处理思考内容）
        if part.get("thought") is True:
            thinking_content += part.get("text", "")

        # 处理图片数据 (inlineData)
        elif "inlineData" in part:
            inline_data = part["inlineData"]
            mime_type = inline_data.get("mimeType", "image/png")
            base64_data = inline_data.get("data", "")
            # 转换为 Markdown 格式的图片（需要额外添加到 content，因为 extract_tool_calls_from_parts 不处理图片）
            content += f"\n\n![生成的图片](data:{mime_type};base64,{base64_data})\n\n"

    # 使用 OpenAIChatMessage 模型构建消息
    message = OpenAIChatMessage(
        role="assistant",
        content=content,
        reasoning_content=thinking_content if thinking_content else None,
        tool_calls=tool_calls_list if tool_calls_list else None
    )

    # 确定 finish_reason
    finish_reason = "stop"
    if tool_calls_list:
        finish_reason = "tool_calls"

    finish_reason_raw = response_data.get("candidates", [{}])[0].get("finishReason")
    if finish_reason_raw == "MAX_TOKENS":
        finish_reason = "length"

    # 提取使用统计
    usage_metadata = response_data.get("usageMetadata", {})
    usage = {
        "prompt_tokens": usage_metadata.get("promptTokenCount", 0),
        "completion_tokens": usage_metadata.get("candidatesTokenCount", 0),
        "total_tokens": usage_metadata.get("totalTokenCount", 0)
    }

    # 使用 OpenAIChatCompletionChoice 模型
    choice = OpenAIChatCompletionChoice(
        index=0,
        message=message,
        finish_reason=finish_reason
    )

    # 使用 OpenAIChatCompletionResponse 模型
    response = OpenAIChatCompletionResponse(
        id=request_id,
        object="chat.completion",
        created=int(time.time()),
        model=model,
        choices=[choice],
        usage=usage
    )

    return model_to_dict(response)


# ==================== API 路由 ====================

@router.get("/antigravity/v1/models", 
            response_model=ModelList,
            dependencies=[Depends(authenticate_bearer)])
async def list_models():
    """
    返回OpenAI格式的模型列表
    
    动态从Antigravity API获取可用模型，并自动扩展抗截断版本
    
    Returns:
        ModelList: 可用模型列表（包含原始模型和抗截断版本）
    """
    try:
        # 从 Antigravity API 获取模型列表（返回 OpenAI 格式的字典列表）
        models = await fetch_available_models()

        if not models:
            # 如果获取失败，直接返回空列表
            log.warning("[ANTIGRAVITY] Failed to fetch models from API, returning empty list")
            return ModelList(data=[])

        # models 已经是 OpenAI 格式的字典列表，扩展为包含抗截断版本
        expanded_models = []
        for model in models:
            # 添加原始模型
            expanded_models.append(Model(**model))

            # 添加流式抗截断版本
            anti_truncation_model = model.copy()
            anti_truncation_model["id"] = f"流式抗截断/{model['id']}"
            expanded_models.append(Model(**anti_truncation_model))

        return ModelList(data=expanded_models)

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Error fetching models: {e}")
        # 返回空列表
        return ModelList(data=[])


@router.post("/antigravity/v1/chat/completions")
async def chat_completions(
    request: Request,
    token: str = Depends(authenticate_bearer)
):
    """
    处理OpenAI格式的聊天完成请求
    
    转换为Antigravity API格式并支持：
    - 健康检查
    - 流式抗截断
    - 思考模型
    - 工具调用
    - 图像生成
    
    Args:
        request: FastAPI请求对象
        token: Bearer认证令牌
        
    Returns:
        JSONResponse或StreamingResponse
    """
    # 获取原始请求数据
    try:
        raw_data = await request.json()
    except Exception as e:
        log.error(f"Failed to parse JSON request: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # 创建请求对象
    try:
        request_data = ChatCompletionRequest(**raw_data)
    except Exception as e:
        log.error(f"Request validation failed: {e}")
        raise HTTPException(status_code=400, detail=f"Request validation error: {str(e)}")

    # 健康检查 - 使用统一的 hi_check 模块
    if is_health_check_request(raw_data, format="openai"):
        return JSONResponse(
            content=create_health_check_response(format="openai")
        )

    # 提取参数
    model = request_data.model
    messages = request_data.messages
    stream = getattr(request_data, "stream", False)
    tools = getattr(request_data, "tools", None)

    # 检测并处理抗截断模式
    use_anti_truncation = is_anti_truncation_model(model)
    if use_anti_truncation:
        # 去掉 "流式抗截断/" 前缀
        model = get_base_model_from_feature_model(model)

    # 模型名称映射
    actual_model = model_mapping(model)
    enable_thinking = is_thinking_model(model)

    log.info(f"[ANTIGRAVITY] Request: model={model} -> {actual_model}, stream={stream}, thinking={enable_thinking}, anti_truncation={use_anti_truncation}")

    # 转换消息格式（使用 openai2gemini 模块的通用函数）
    try:
        contents, system_instructions = openai_messages_to_gemini_contents(
            messages, compatibility_mode=False
        )
    except Exception as e:
        log.error(f"Failed to convert messages: {e}")
        raise HTTPException(status_code=500, detail=f"Message conversion failed: {str(e)}")

    # 转换工具定义
    antigravity_tools = convert_openai_tools_to_gemini(tools)

    # 生成配置参数
    parameters = {
        "temperature": getattr(request_data, "temperature", None),
        "top_p": getattr(request_data, "top_p", None),
        "max_tokens": getattr(request_data, "max_tokens", None),
    }
    # 过滤 None 值
    parameters = {k: v for k, v in parameters.items() if v is not None}

    generation_config = build_antigravity_generation_config(parameters, enable_thinking, actual_model)

    # 构建 Antigravity 请求体
    request_body = build_antigravity_request_body(
        contents=contents,
        model=actual_model,
        tools=antigravity_tools,
        generation_config=generation_config,
    )

    # 图像生成模型特殊处理
    if "-image" in model:
        request_body = prepare_image_generation_request(request_body, model)

    # 生成请求 ID
    request_id = f"chatcmpl-{int(time.time() * 1000)}"

    # 发送请求
    try:
        if stream:
            # 处理抗截断功能（仅流式传输时有效）
            if use_anti_truncation:
                log.info("[ANTIGRAVITY] 启用流式抗截断功能")
                max_attempts = await get_anti_truncation_max_attempts()

                # 包装请求函数以适配抗截断处理器
                async def antigravity_request_func(payload):
                    resources, _, _ = await send_antigravity_request_stream(
                        payload
                    )
                    response, stream_ctx, client = resources
                    return StreamingResponse(
                        convert_antigravity_stream_to_openai(
                            response, stream_ctx, client, model, request_id
                        ),
                        media_type="text/event-stream"
                    )

                return await apply_anti_truncation_to_stream(
                    antigravity_request_func, request_body, max_attempts
                )

            # 流式请求（无抗截断）
            resources, _, _ = await send_antigravity_request_stream(
                request_body
            )
            # resources 是一个元组: (response, stream_ctx, client)
            response, stream_ctx, client = resources

            # 转换并返回流式响应,传递资源管理对象
            # response 现在是 filtered_lines 生成器
            return StreamingResponse(
                convert_antigravity_stream_to_openai(
                    response, stream_ctx, client, model, request_id
                ),
                media_type="text/event-stream"
            )
        else:
            # 非流式请求
            response_data, _, _ = await send_antigravity_request_no_stream(
                request_body
            )

            # 转换并返回响应
            openai_response = convert_antigravity_response_to_openai(response_data, model, request_id)
            return JSONResponse(content=openai_response)

    except Exception as e:
        log.error(f"[ANTIGRAVITY] Request failed: {e}")
        raise HTTPException(status_code=500, detail=f"Antigravity API request failed: {str(e)}")
