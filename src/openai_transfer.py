"""
OpenAI Transfer Module - Handles conversion between OpenAI and Gemini API formats
被openai-router调用，负责OpenAI格式与Gemini格式的双向转换
"""
import json
import time
import uuid
from typing import Dict, Any

from config import (
    DEFAULT_SAFETY_SETTINGS,
    get_base_model_name,
    get_thinking_budget,
    is_search_model,
    should_include_thoughts,
    get_compatibility_mode_enabled
)
from log import log
from .models import ChatCompletionRequest

async def openai_request_to_gemini_payload(openai_request: ChatCompletionRequest) -> Dict[str, Any]:
    """
    将OpenAI聊天完成请求直接转换为完整的Gemini API payload格式
    
    Args:
        openai_request: OpenAI格式请求对象
        
    Returns:
        完整的Gemini API payload，包含model和request字段
    """
    contents = []
    system_instructions = []
    
    # 检查是否启用兼容性模式
    compatibility_mode = await get_compatibility_mode_enabled()
    
    # 处理对话中的每条消息
    # 第一阶段：收集连续的system消息到system_instruction中（除非在兼容性模式下）
    collecting_system = True if not compatibility_mode else False
    
    for message in openai_request.messages:
        role = message.role
        
        # 处理系统消息
        if role == "system":
            if compatibility_mode:
                # 兼容性模式：所有system消息转换为user消息
                role = "user"
            elif collecting_system:
                # 正常模式：仍在收集连续的system消息
                if isinstance(message.content, str):
                    system_instructions.append(message.content)
                elif isinstance(message.content, list):
                    # 处理列表格式的系统消息
                    for part in message.content:
                        if part.get("type") == "text" and part.get("text"):
                            system_instructions.append(part["text"])
                continue
            else:
                # 正常模式：后续的system消息转换为user消息
                role = "user"
        else:
            # 遇到非system消息，停止收集system消息
            collecting_system = False
        
        # 将OpenAI角色映射到Gemini角色
        if role == "assistant":
            role = "model"
        
        # 处理工具调用响应消息（role为tool）
        if role == "tool" and message.tool_call_id and message.content:
            # 工具调用结果消息转换为Gemini格式
            # 注意：这里我们需要从message.name中获取函数名，如果没有则跳过
            function_name = getattr(message, 'name', None)
            if not function_name:
                log.error(f"Tool response message missing function name for tool_call_id: {message.tool_call_id}")
                log.error("This will cause a 400 error from Gemini API: 'please ensure that the number of function response parts is equal to the number of function call parts'")
                log.error("Solution: Include 'name' field in tool messages matching the original function name")
                continue
            
            parts = [{
                "functionResponse": {
                    "name": function_name,
                    "response": {"result": message.content}
                }
            }]
            contents.append({"role": "function", "parts": parts})
            log.debug(f"Added tool response to contents: function={function_name}, tool_call_id={message.tool_call_id}")
            continue
        
        # 处理工具调用（从assistant消息中的tool_calls）
        if hasattr(message, 'tool_calls') and message.tool_calls:
            # 如果有工具调用，优先处理工具调用，忽略文本内容
            tool_parts = []
            for tool_call in message.tool_calls:
                if tool_call.type == "function":
                    try:
                        args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    
                    tool_parts.append({
                        "functionCall": {
                            "name": tool_call.function.name,
                            "args": args
                        }
                    })
            
            if tool_parts:
                contents.append({"role": role, "parts": tool_parts})
                log.debug(f"Added tool calls to contents: {[tc.function.name for tc in message.tool_calls]}")
        
        # 处理普通内容（只有在没有工具调用的情况下）
        elif isinstance(message.content, list):
            parts = []
            for part in message.content:
                if part.get("type") == "text":
                    parts.append({"text": part.get("text", "")})
                elif part.get("type") == "image_url":
                    image_url = part.get("image_url", {}).get("url")
                    if image_url:
                        # 解析数据URI: "data:image/jpeg;base64,{base64_image}"
                        try:
                            mime_type, base64_data = image_url.split(";")
                            _, mime_type = mime_type.split(":")
                            _, base64_data = base64_data.split(",")
                            parts.append({
                                "inlineData": {
                                    "mimeType": mime_type,
                                    "data": base64_data
                                }
                            })
                        except ValueError:
                            continue
            contents.append({"role": role, "parts": parts})
            # log.debug(f"Added message to contents: role={role}, parts={parts}")
        elif message.content:
            # 简单文本内容
            contents.append({"role": role, "parts": [{"text": message.content}]})
            # log.debug(f"Added message to contents: role={role}, content={message.content}")

    # 将OpenAI生成参数映射到Gemini格式
    generation_config = {}
    if openai_request.temperature is not None:
        generation_config["temperature"] = openai_request.temperature
    if openai_request.top_p is not None:
        generation_config["topP"] = openai_request.top_p
    if openai_request.max_tokens is not None:
        generation_config["maxOutputTokens"] = openai_request.max_tokens
    if openai_request.stop is not None:
        # Gemini支持停止序列
        if isinstance(openai_request.stop, str):
            generation_config["stopSequences"] = [openai_request.stop]
        elif isinstance(openai_request.stop, list):
            generation_config["stopSequences"] = openai_request.stop
    if openai_request.frequency_penalty is not None:
        generation_config["frequencyPenalty"] = openai_request.frequency_penalty
    if openai_request.presence_penalty is not None:
        generation_config["presencePenalty"] = openai_request.presence_penalty
    if openai_request.n is not None:
        generation_config["candidateCount"] = openai_request.n
    if openai_request.seed is not None:
        generation_config["seed"] = openai_request.seed
    if openai_request.response_format is not None:
        # 处理JSON模式
        if openai_request.response_format.get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"

    # 如果contents为空（只有系统消息的情况），添加一个默认的用户消息以满足Gemini API要求
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "请根据系统指令回答。"}]})
    
    # 构建请求数据
    request_data = {
        "contents": contents,
        "generationConfig": generation_config,
        "safetySettings": DEFAULT_SAFETY_SETTINGS,
    }
    
    # 如果有系统消息且未启用兼容性模式，添加systemInstruction
    if system_instructions and not compatibility_mode:
        combined_system_instruction = "\n\n".join(system_instructions)
        request_data["systemInstruction"] = {"parts": [{"text": combined_system_instruction}]}
    
    log.debug(f"Final request payload contents count: {len(contents)}, system_instruction: {bool(system_instructions and not compatibility_mode)}, compatibility_mode: {compatibility_mode}")
    
    # 为thinking模型添加thinking配置
    thinking_budget = get_thinking_budget(openai_request.model)
    if thinking_budget is not None:
        request_data["generationConfig"]["thinkingConfig"] = {
            "thinkingBudget": thinking_budget,
            "includeThoughts": should_include_thoughts(openai_request.model)
        }

    # 处理工具定义转换（OpenAI tools -> Gemini tools）
    if openai_request.tools:
        function_declarations = []
        for tool in openai_request.tools:
            if tool.type == "function":
                # 清理parameters中的additionalProperties字段，Gemini API不支持
                parameters = tool.function.parameters.copy() if tool.function.parameters else {}
                if "additionalProperties" in parameters:
                    del parameters["additionalProperties"]
                
                # 递归清理嵌套对象中的additionalProperties
                def clean_additional_properties(obj):
                    if isinstance(obj, dict):
                        # 删除当前层级的additionalProperties
                        if "additionalProperties" in obj:
                            del obj["additionalProperties"]
                        # 递归处理嵌套的对象
                        for key, value in obj.items():
                            if isinstance(value, dict):
                                clean_additional_properties(value)
                            elif isinstance(value, list):
                                for item in value:
                                    if isinstance(item, dict):
                                        clean_additional_properties(item)
                
                clean_additional_properties(parameters)
                
                function_def = {
                    "name": tool.function.name,
                    "description": tool.function.description,
                    "parameters": parameters
                }
                function_declarations.append(function_def)
        
        # 所有函数声明放在一个工具对象中
        if function_declarations:
            request_data["tools"] = [{"functionDeclarations": function_declarations}]
    
    # 处理工具选择转换（OpenAI tool_choice -> Gemini toolConfig）
    if openai_request.tool_choice:
        if isinstance(openai_request.tool_choice, str):
            if openai_request.tool_choice == "auto":
                # Gemini默认就是auto模式，无需特殊设置
                pass
            elif openai_request.tool_choice == "required":
                request_data["toolConfig"] = {"functionCallingConfig": {"mode": "ANY"}}
            elif openai_request.tool_choice == "none":
                request_data["toolConfig"] = {"functionCallingConfig": {"mode": "NONE"}}
        elif isinstance(openai_request.tool_choice, dict) and openai_request.tool_choice.get("type") == "function":
            # 强制调用特定函数
            function_name = openai_request.tool_choice.get("function", {}).get("name")
            if function_name:
                request_data["toolConfig"] = {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [function_name]
                    }
                }
    
    # 为搜索模型添加Google Search工具（只有在没有functionDeclarations时才添加）
    if is_search_model(openai_request.model):
        if "tools" not in request_data:
            request_data["tools"] = []
            request_data["tools"].append({"googleSearch": {}})
        elif not any("functionDeclarations" in tool for tool in request_data["tools"]):
            # 只有在没有函数工具时才添加googleSearch
            request_data["tools"].append({"googleSearch": {}})
        else:
            # 如果已有函数工具，不添加googleSearch（Gemini API不支持混合工具类型）
            log.debug("Skipping googleSearch tool because functionDeclarations are present")

    # 移除None值
    request_data = {k: v for k, v in request_data.items() if v is not None}
    
    # 返回完整的Gemini API payload格式
    return {
        "model": get_base_model_name(openai_request.model),
        "request": request_data
    }

def _extract_content_and_reasoning(parts: list) -> tuple:
    """从Gemini响应部件中提取内容、推理内容和函数调用"""
    content = ""
    reasoning_content = ""
    tool_calls = []
    
    for part in parts:
        # 处理文本内容
        if part.get("text"):
            # 检查这个部件是否包含thinking tokens
            if part.get("thought", False):
                reasoning_content += part.get("text", "")
            else:
                content += part.get("text", "")
        
        # 处理函数调用
        elif part.get("functionCall"):
            function_call = part["functionCall"]
            tool_call = {
                "id": f"call_{uuid.uuid4().hex[:8]}",
                "type": "function",
                "function": {
                    "name": function_call.get("name", ""),
                    "arguments": json.dumps(function_call.get("args", {}))
                }
            }
            tool_calls.append(tool_call)
    
    return content, reasoning_content, tool_calls

def _build_message_with_reasoning(role: str, content: str, reasoning_content: str, tool_calls: list = None) -> dict:
    """构建包含可选推理内容和工具调用的消息对象"""
    message = {
        "role": role,
    }
    
    # 如果有工具调用，设置content为None，否则设置文本内容
    if tool_calls:
        message["tool_calls"] = tool_calls
        message["content"] = None if not content.strip() else content
    else:
        message["content"] = content
    
    # 如果有thinking tokens，添加reasoning_content
    if reasoning_content:
        message["reasoning_content"] = reasoning_content
    
    return message

def gemini_response_to_openai(gemini_response: Dict[str, Any], model: str) -> Dict[str, Any]:
    """
    将Gemini API响应转换为OpenAI聊天完成格式
    
    Args:
        gemini_response: 来自Gemini API的响应
        model: 要在响应中包含的模型名称
        
    Returns:
        OpenAI聊天完成格式的字典
    """
    choices = []
    
    for candidate in gemini_response.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")
        
        # 将Gemini角色映射回OpenAI角色
        if role == "model":
            role = "assistant"
        
        # 提取并分离thinking tokens、常规内容和工具调用
        parts = candidate.get("content", {}).get("parts", [])
        content, reasoning_content, tool_calls = _extract_content_and_reasoning(parts)
        
        # 构建消息对象
        message = _build_message_with_reasoning(role, content, reasoning_content, tool_calls)
        
        choices.append({
            "index": candidate.get("index", 0),
            "message": message,
            "finish_reason": _map_finish_reason(candidate.get("finishReason")),
        })
    
    return {
        "id": str(uuid.uuid4()),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

def gemini_stream_chunk_to_openai(gemini_chunk: Dict[str, Any], model: str, response_id: str) -> Dict[str, Any]:
    """
    将Gemini流式响应块转换为OpenAI流式格式
    
    Args:
        gemini_chunk: 来自Gemini流式响应的单个块
        model: 要在响应中包含的模型名称
        response_id: 此流式响应的一致ID
        
    Returns:
        OpenAI流式格式的字典
    """
    choices = []
    
    for candidate in gemini_chunk.get("candidates", []):
        role = candidate.get("content", {}).get("role", "assistant")
        
        # 将Gemini角色映射回OpenAI角色
        if role == "model":
            role = "assistant"
        
        # 提取并分离thinking tokens、常规内容和工具调用
        parts = candidate.get("content", {}).get("parts", [])
        content, reasoning_content, tool_calls = _extract_content_and_reasoning(parts)
        
        # 构建delta对象
        delta = {}
        if content:
            delta["content"] = content
        if reasoning_content:
            delta["reasoning_content"] = reasoning_content
        if tool_calls:
            delta["tool_calls"] = tool_calls
        
        choices.append({
            "index": candidate.get("index", 0),
            "delta": delta,
            "finish_reason": _map_finish_reason(candidate.get("finishReason")),
        })
    
    return {
        "id": response_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
    }

def _map_finish_reason(gemini_reason: str) -> str:
    """
    将Gemini结束原因映射到OpenAI结束原因
    
    Args:
        gemini_reason: 来自Gemini API的结束原因
        
    Returns:
        OpenAI兼容的结束原因
    """
    if gemini_reason == "STOP":
        return "stop"
    elif gemini_reason == "MAX_TOKENS":
        return "length"
    elif gemini_reason in ["SAFETY", "RECITATION"]:
        return "content_filter"
    else:
        return None

def validate_openai_request(request_data: Dict[str, Any]) -> ChatCompletionRequest:
    """
    验证并标准化OpenAI请求数据
    
    Args:
        request_data: 原始请求数据字典
        
    Returns:
        验证后的ChatCompletionRequest对象
        
    Raises:
        ValueError: 当请求数据无效时
    """
    try:
        return ChatCompletionRequest(**request_data)
    except Exception as e:
        raise ValueError(f"Invalid OpenAI request format: {str(e)}")

def normalize_openai_request(request_data: ChatCompletionRequest) -> ChatCompletionRequest:
    """
    标准化OpenAI请求数据，应用默认值和限制
    
    Args:
        request_data: 原始请求对象
        
    Returns:
        标准化后的请求对象
    """
    # 限制max_tokens
    if getattr(request_data, "max_tokens", None) is not None and request_data.max_tokens > 65535:
        request_data.max_tokens = 65535
        
    # 覆写 top_k 为 64
    setattr(request_data, "top_k", 64)

    # 过滤空消息
    filtered_messages = []
    for m in request_data.messages:
        content = getattr(m, "content", None)
        if content:
            if isinstance(content, str) and content.strip():
                filtered_messages.append(m)
            elif isinstance(content, list) and len(content) > 0:
                has_valid_content = False
                for part in content:
                    if isinstance(part, dict):
                        if part.get("type") == "text" and part.get("text", "").strip():
                            has_valid_content = True
                            break
                        elif part.get("type") == "image_url" and part.get("image_url", {}).get("url"):
                            has_valid_content = True
                            break
                if has_valid_content:
                    filtered_messages.append(m)
    
    request_data.messages = filtered_messages
    
    return request_data

def is_health_check_request(request_data: ChatCompletionRequest) -> bool:
    """
    检查是否为健康检查请求
    
    Args:
        request_data: 请求对象
        
    Returns:
        是否为健康检查请求
    """
    return (len(request_data.messages) == 1 and 
            getattr(request_data.messages[0], "role", None) == "user" and
            getattr(request_data.messages[0], "content", None) == "Hi")

def create_health_check_response() -> Dict[str, Any]:
    """
    创建健康检查响应
    
    Returns:
        健康检查响应字典
    """
    return {
        "choices": [{
            "message": {
                "role": "assistant", 
                "content": "gcli2api正常工作中"
            }
        }]
    }

def extract_model_settings(model: str) -> Dict[str, Any]:
    """
    从模型名称中提取设置信息
    
    Args:
        model: 模型名称
        
    Returns:
        包含模型设置的字典
    """
    return {
        "base_model": get_base_model_name(model),
        "use_fake_streaming": model.endswith("-假流式"),
        "thinking_budget": get_thinking_budget(model),
        "include_thoughts": should_include_thoughts(model)
    }