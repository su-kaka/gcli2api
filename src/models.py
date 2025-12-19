from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field


# Pydantic v1/v2 兼容性辅助函数
def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    """
    兼容 Pydantic v1 和 v2 的模型转字典方法
    - v1: model.dict()
    - v2: model.model_dump()
    """
    if hasattr(model, 'model_dump'):
        # Pydantic v2
        return model.model_dump()
    else:
        # Pydantic v1
        return model.dict()


# Common Models
class Model(BaseModel):
    id: str
    object: str = "model"
    created: Optional[int] = None
    owned_by: Optional[str] = "google"


class ModelList(BaseModel):
    object: str = "list"
    data: List[Model]


# OpenAI Models
class OpenAIToolFunction(BaseModel):
    name: str
    arguments: str  # JSON string


class OpenAIToolCall(BaseModel):
    id: str
    type: str = "function"
    function: OpenAIToolFunction


class OpenAITool(BaseModel):
    type: str = "function"
    function: Dict[str, Any]


class OpenAIChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]], None] = None
    reasoning_content: Optional[str] = None
    name: Optional[str] = None
    tool_calls: Optional[List[OpenAIToolCall]] = None
    tool_call_id: Optional[str] = None  # for role="tool"


class OpenAIChatCompletionRequest(BaseModel):
    model: str
    messages: List[OpenAIChatMessage]
    stream: bool = False
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(None, ge=1)
    stop: Optional[Union[str, List[str]]] = None
    frequency_penalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    presence_penalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    n: Optional[int] = Field(1, ge=1, le=128)
    seed: Optional[int] = None
    response_format: Optional[Dict[str, Any]] = None
    top_k: Optional[int] = Field(None, ge=1)
    enable_anti_truncation: Optional[bool] = False
    tools: Optional[List[OpenAITool]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None

    class Config:
        extra = "allow"  # Allow additional fields not explicitly defined


# 通用的聊天完成请求模型（兼容OpenAI和其他格式）
ChatCompletionRequest = OpenAIChatCompletionRequest


class OpenAIChatCompletionChoice(BaseModel):
    index: int
    message: OpenAIChatMessage
    finish_reason: Optional[str] = None
    logprobs: Optional[Dict[str, Any]] = None


class OpenAIChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[OpenAIChatCompletionChoice]
    usage: Optional[Dict[str, int]] = None
    system_fingerprint: Optional[str] = None


class OpenAIDelta(BaseModel):
    role: Optional[str] = None
    content: Optional[str] = None
    reasoning_content: Optional[str] = None


class OpenAIChatCompletionStreamChoice(BaseModel):
    index: int
    delta: OpenAIDelta
    finish_reason: Optional[str] = None
    logprobs: Optional[Dict[str, Any]] = None


class OpenAIChatCompletionStreamResponse(BaseModel):
    id: str
    object: str = "chat.completion.chunk"
    created: int
    model: str
    choices: List[OpenAIChatCompletionStreamChoice]
    system_fingerprint: Optional[str] = None


# Gemini Models
class GeminiPart(BaseModel):
    text: Optional[str] = None
    inlineData: Optional[Dict[str, Any]] = None
    fileData: Optional[Dict[str, Any]] = None
    thought: Optional[bool] = False


class GeminiContent(BaseModel):
    role: str
    parts: List[GeminiPart]


class GeminiSystemInstruction(BaseModel):
    parts: List[GeminiPart]


class GeminiImageConfig(BaseModel):
    """图片生成配置"""
    aspect_ratio: Optional[str] = None  # "1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"
    image_size: Optional[str] = None  # "1K", "2K", "4K"


class GeminiGenerationConfig(BaseModel):
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0)
    topP: Optional[float] = Field(None, ge=0.0, le=1.0)
    topK: Optional[int] = Field(None, ge=1)
    maxOutputTokens: Optional[int] = Field(None, ge=1)
    stopSequences: Optional[List[str]] = None
    responseMimeType: Optional[str] = None
    responseSchema: Optional[Dict[str, Any]] = None
    candidateCount: Optional[int] = Field(None, ge=1, le=8)
    seed: Optional[int] = None
    frequencyPenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    presencePenalty: Optional[float] = Field(None, ge=-2.0, le=2.0)
    thinkingConfig: Optional[Dict[str, Any]] = None
    # 图片生成相关参数
    response_modalities: Optional[List[str]] = None  # ["TEXT", "IMAGE"]
    image_config: Optional[GeminiImageConfig] = None


class GeminiSafetySetting(BaseModel):
    category: str
    threshold: str


class GeminiRequest(BaseModel):
    contents: List[GeminiContent]
    systemInstruction: Optional[GeminiSystemInstruction] = None
    generationConfig: Optional[GeminiGenerationConfig] = None
    safetySettings: Optional[List[GeminiSafetySetting]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    toolConfig: Optional[Dict[str, Any]] = None
    cachedContent: Optional[str] = None
    enable_anti_truncation: Optional[bool] = False

    class Config:
        extra = "allow"  # 允许透传未定义的字段


class GeminiCandidate(BaseModel):
    content: GeminiContent
    finishReason: Optional[str] = None
    index: int = 0
    safetyRatings: Optional[List[Dict[str, Any]]] = None
    citationMetadata: Optional[Dict[str, Any]] = None
    tokenCount: Optional[int] = None


class GeminiUsageMetadata(BaseModel):
    promptTokenCount: Optional[int] = None
    candidatesTokenCount: Optional[int] = None
    totalTokenCount: Optional[int] = None


class GeminiResponse(BaseModel):
    candidates: List[GeminiCandidate]
    usageMetadata: Optional[GeminiUsageMetadata] = None
    modelVersion: Optional[str] = None


# Error Models
class APIError(BaseModel):
    message: str
    type: str = "api_error"
    code: Optional[int] = None


class ErrorResponse(BaseModel):
    error: APIError


# Control Panel Models
class SystemStatus(BaseModel):
    status: str
    timestamp: str
    credentials: Dict[str, int]
    config: Dict[str, Any]
    current_credential: str


class CredentialInfo(BaseModel):
    filename: str
    project_id: Optional[str] = None
    status: Dict[str, Any]
    size: Optional[int] = None
    modified_time: Optional[str] = None
    error: Optional[str] = None


class LogEntry(BaseModel):
    timestamp: str
    level: str
    message: str
    module: Optional[str] = None


class ConfigValue(BaseModel):
    key: str
    value: Any
    env_locked: bool = False
    description: Optional[str] = None


# Authentication Models
class AuthRequest(BaseModel):
    project_id: Optional[str] = None
    user_session: Optional[str] = None


class AuthResponse(BaseModel):
    success: bool
    auth_url: Optional[str] = None
    state: Optional[str] = None
    error: Optional[str] = None
    credentials: Optional[Dict[str, Any]] = None
    file_path: Optional[str] = None
    requires_manual_project_id: Optional[bool] = None
    requires_project_selection: Optional[bool] = None
    available_projects: Optional[List[Dict[str, str]]] = None


class CredentialStatus(BaseModel):
    disabled: bool = False
    error_codes: List[int] = []
    last_success: Optional[str] = None


# Web Routes Models
class LoginRequest(BaseModel):
    password: str


class AuthStartRequest(BaseModel):
    project_id: Optional[str] = None  # 现在是可选的
    use_antigravity: Optional[bool] = False  # 是否使用antigravity模式


class AuthCallbackRequest(BaseModel):
    project_id: Optional[str] = None  # 现在是可选的
    use_antigravity: Optional[bool] = False  # 是否使用antigravity模式


class AuthCallbackUrlRequest(BaseModel):
    callback_url: str  # OAuth回调完整URL
    project_id: Optional[str] = None  # 可选的项目ID
    use_antigravity: Optional[bool] = False  # 是否使用antigravity模式


class CredFileActionRequest(BaseModel):
    filename: str
    action: str  # enable, disable, delete


class CredFileBatchActionRequest(BaseModel):
    action: str  # "enable", "disable", "delete"
    filenames: List[str]  # 批量操作的文件名列表


class ConfigSaveRequest(BaseModel):
    config: dict
