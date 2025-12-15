antigravity怎么弄一个openai格式
一、架构概览
1.1 整体数据流
OpenAI 客户端请求
    ↓
POST /v1/chat/completions
    ↓
[Token 管理器] 获取/刷新 OAuth2 token
    ↓
[请求转换层] OpenAI → Antigravity 格式
    ↓
[XHTTP 客户端]
    ↓
[Google Antigravity API] daily-cloudcode-pa.sandbox.googleapis.com
    ↓
[响应转换层] Antigravity → OpenAI 格式
    ↓
OpenAI 客户端响应

2.1 入口点：接收 OpenAI 请求
文件: src/server/index.js:107-206
app.post('/v1/chat/completions', async (req, res) => {
  const { messages, model, stream = false, tools, ...params} = req.body;

  // 1. 获取 token
  const token = await tokenManager.getToken();

  // 2. 构建 Antigravity 请求体
  const requestBody = generateRequestBody(messages, model, params, tools, token);

  // 3. 发送请求并处理响应
  if (stream) {
    await generateAssistantResponse(requestBody, token, callback);
  } else {
    const { content, toolCalls, usage } = await generateAssistantResponseNoStream(requestBody, token);
  }
});
OpenAI 输入格式:
{
  "model": "claude-sonnet-4-5",
  "messages": [
    {"role": "system", "content": "You are helpful"},
    {"role": "user", "content": "Hello"}
  ],
  "stream": true,
  "temperature": 0.7,
  "max_tokens": 2048,
  "tools": [
    {
      "type": "function",
      "function": {
        "name": "search",
        "description": "Search the web",
        "parameters": {"type": "object", "properties": {...}}
      }
    }
  ]
}
2.2 核心转换函数：generateRequestBody
文件: src/utils/utils.js:206-232
function generateRequestBody(openaiMessages, modelName, parameters, openaiTools, token) {
  const enableThinking = isEnableThinking(modelName);
  const actualModelName = modelMapping(modelName);

  return {
    project: token.projectId,
    requestId: generateRequestId(),
    request: {
      contents: openaiMessageToAntigravity(openaiMessages),        // ①消息转换
      systemInstruction: {
        role: "user",
        parts: [{ text: config.systemInstruction }]
      },
      tools: convertOpenAIToolsToAntigravity(openaiTools),         // ②工具转换
      toolConfig: {
        functionCallingConfig: { mode: "VALIDATED" }
      },
      generationConfig: generateGenerationConfig(parameters, enableThinking, actualModelName), // ③参数转换
      sessionId: token.sessionId
    },
    model: actualModelName,                                         // ④模型名映射
    userAgent: "antigravity"
  };
}
Antigravity 输出格式:
{
  "project": "project-123456",
  "requestId": "req-abc-def-123",
  "model": "claude-sonnet-4-5",
  "userAgent": "antigravity",
  "request": {
    "contents": [...],
    "systemInstruction": {...},
    "tools": [...],
    "toolConfig": {...},
    "generationConfig": {...},
    "sessionId": "session-xyz"
  }
}
2.3 消息格式转换：openaiMessageToAntigravity
文件: src/utils/utils.js:118-132
2.3.1 转换规则
OpenAI 格式	Antigravity 格式
{role: "system", content: "..."}	合并到第一条用户消息中
{role: "user", content: "..."}	{role: "user", parts: [{text: "..."}]}
{role: "assistant", content: "..."}	{role: "model", parts: [{text: "..."}]}
{role: "tool", content: "result", tool_call_id: "x"}	{role: "user", parts: [{functionResponse: {...}}]}
2.3.2 多模态内容处理
文件: src/utils/utils.js:6-41
// OpenAI 多模态输入
{
  role: "user",
  content: [
    {type: "text", text: "What's in this image?"},
    {type: "image_url", image_url: {url: "data:image/png;base64,iVBORw0KG..."}}
  ]
}

// 转换为 Antigravity
{
  role: "user",
  parts: [
    {text: "What's in this image?"},
    {inlineData: {mimeType: "image/png", data: "iVBORw0KG..."}}
  ]
}
提取图片函数:
function extractImagesFromContent(content) {
  const result = { text: '', images: [] };

  if (Array.isArray(content)) {
    for (const item of content) {
      if (item.type === 'text') {
        result.text += item.text;
      } else if (item.type === 'image_url') {
        const match = item.image_url.url.match(/^data:image\/(\w+);base64,(.+)$/);
        if (match) {
          result.images.push({
            inlineData: { mimeType: `image/${match[1]}`, data: match[2] }
          });
        }
      }
    }
  }

  return result;
}
2.3.3 工具调用消息处理
文件: src/utils/utils.js:53-80, 81-117
// OpenAI assistant 消息（带工具调用）
{
  role: "assistant",
  content: "I'll search for that",
  tool_calls: [
    {
      id: "call_abc123",
      type: "function",
      function: {name: "search", arguments: '{"query":"weather"}'}
    }
  ]
}

// 转换为 Antigravity
{
  role: "model",
  parts: [
    {text: "I'll search for that"},
    {
      functionCall: {
        id: "call_abc123",
        name: "search",
        args: {query: '{"query":"weather"}'}
      }
    }
  ]
}

// OpenAI tool 响应
{
  role: "tool",
  content: "Temperature is 20°C",
  tool_call_id: "call_abc123"
}

// 转换为 Antigravity
{
  role: "user",
  parts: [
    {
      functionResponse: {
        id: "call_abc123",
        name: "search",
        response: {output: "Temperature is 20°C"}
      }
    }
  ]
}
2.4 工具定义转换：convertOpenAIToolsToAntigravity
文件: src/utils/utils.js:172-185
// OpenAI 工具定义
{
  type: "function",
  function: {
    name: "get_weather",
    description: "Get current weather",
    parameters: {
      type: "object",
      properties: {
        location: {type: "string", description: "City name"}
      },
      required: ["location"]
    }
  }
}

// 转换为 Antigravity
{
  functionDeclarations: [
    {
      name: "get_weather",
      description: "Get current weather",
      parameters: {
        type: "object",
        properties: {
          location: {type: "string", description: "City name"}
        },
        required: ["location"]
      }
    }
  ]
}
清理不必要的 schema 字段:
const EXCLUDED_KEYS = new Set(['$schema', 'additionalProperties', 'minLength', 'maxLength', 'minItems', 'maxItems', 'uniqueItems']);

function cleanParameters(obj) {
  const cleaned = Array.isArray(obj) ? [] : {};

  for (const [key, value] of Object.entries(obj)) {
    if (EXCLUDED_KEYS.has(key)) continue;
    cleaned[key] = (value && typeof value === 'object') ? cleanParameters(value) : value;
  }

  return cleaned;
}
2.5 生成配置转换：generateGenerationConfig
文件: src/utils/utils.js:133-156
// OpenAI 参数
{
  temperature: 0.7,
  top_p: 0.9,
  max_tokens: 2048
}

// 转换为 Antigravity
{
  temperature: 0.7,
  topP: 0.9,
  topK: 50,                    // 从 config.defaults.top_k 获取
  candidateCount: 1,
  maxOutputTokens: 2048,
  stopSequences: ["<|user|>", "<|bot|>", "<|context_request|>", "<|endoftext|>", "<|end_of_turn|>"],
  thinkingConfig: {
    includeThoughts: true,     // 如果是思考模型
    thinkingBudget: 1024       // 思考 token 预算
  }
}
特殊处理:
思考模型检测: 模型名包含 -thinking 后缀或匹配特定名称（gemini-2.5-pro, gemini-3-pro-* 等）
Claude 思考模型: 删除 topP 参数（src/utils/utils.js:152-154）
默认值: 未提供的参数从 config.json 的 defaults 部分读取
2.6 模型名称映射：modelMapping
文件: src/utils/utils.js:187-196
function modelMapping(modelName) {
  if (modelName === "claude-sonnet-4-5-thinking")
    return "claude-sonnet-4-5";
  else if (modelName === "claude-opus-4-5")
    return "claude-opus-4-5-thinking";
  else if (modelName === "gemini-2.5-flash-thinking")
    return "gemini-2.5-flash";
  return modelName;
}
OpenAI 请求模型	Antigravity 实际模型
claude-sonnet-4-5-thinking	claude-sonnet-4-5
claude-opus-4-5	claude-opus-4-5-thinking
gemini-2.5-flash-thinking	gemini-2.5-flash
其他	保持不变
三、HTTP 请求发送层
3.1 请求头构建
文件: src/api/client.js:25-33
function buildHeaders(token) {
  return {
    'Host': 'daily-cloudcode-pa.sandbox.googleapis.com',
    'User-Agent': 'antigravity/1.11.3 windows/amd64',
    'Authorization': `Bearer ${token.access_token}`,
    'Content-Type': 'application/json',
    'Accept-Encoding': 'gzip'
  };
}

3.3 实际 API 端点
配置文件: config.json:7-12
{
  "api": {
    "url": "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:streamGenerateContent?alt=sse",
    "noStreamUrl": "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:generateContent",
    "modelsUrl": "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:fetchAvailableModels",
    "host": "daily-cloudcode-pa.sandbox.googleapis.com",
    "userAgent": "antigravity/1.11.3 windows/amd64"
  }
}
端点用途	URL
流式响应	/v1internal:streamGenerateContent?alt=sse
非流式响应	/v1internal:generateContent
模型列表	/v1internal:fetchAvailableModels
四、响应转换详解（Antigravity → OpenAI）
4.1 流式响应处理
文件: src/api/client.js:163-206
4.1.1 SSE 事件流解析
文件: src/api/client.js:102-159
function parseAndEmitStreamChunk(line, state, callback) {
  if (!line.startsWith('data: ')) return;

  const data = JSON.parse(line.slice(6));
  const parts = data.response?.candidates?.[0]?.content?.parts;

  if (parts) {
    for (const part of parts) {
      // 1. 思考内容（wrapped in <think></think>）
      if (part.thought === true) {
        if (!state.thinkingStarted) {
          callback({ type: 'thinking', content: '<think>\n' });
          state.thinkingStarted = true;
        }
        callback({ type: 'thinking', content: part.text || '' });
      }
      // 2. 普通文本
      else if (part.text !== undefined) {
        if (state.thinkingStarted) {
          callback({ type: 'thinking', content: '\n</think>\n' });
          state.thinkingStarted = false;
        }
        callback({ type: 'text', content: part.text });
      }
      // 3. 工具调用
      else if (part.functionCall) {
        state.toolCalls.push(convertToToolCall(part.functionCall));
      }
    }
  }

  // 4. 响应结束时发送 usage 和 tool_calls
  if (data.response?.candidates?.[0]?.finishReason) {
    if (state.toolCalls.length > 0) {
      callback({ type: 'tool_calls', tool_calls: state.toolCalls });
    }
    callback({
      type: 'usage',
      usage: {
        prompt_tokens: data.response.usageMetadata.promptTokenCount,
        completion_tokens: data.response.usageMetadata.candidatesTokenCount,
        total_tokens: data.response.usageMetadata.totalTokenCount
      }
    });
  }
}
4.1.2 Antigravity 流式响应格式
data: {"response":{"candidates":[{"content":{"parts":[{"text":"Hello"}]}}]}}

data: {"response":{"candidates":[{"content":{"parts":[{"text":" there"}]}}]}}

data: {"response":{"candidates":[{"content":{"parts":[{"thought":true,"text":"Let me think..."}]}}]}}

data: {"response":{"candidates":[{"content":{"parts":[{"functionCall":{"id":"call_123","name":"search","args":{"query":"test"}}}]}}]}}

data: {"response":{"candidates":[{"finishReason":"STOP","usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":20,"totalTokenCount":30}}]}}
4.1.3 OpenAI 流式输出格式
data: {"id":"chatcmpl-1702300000000","object":"chat.completion.chunk","created":1702300000,"model":"claude-sonnet-4-5","choices":[{"index":0,"delta":{"content":"<think>\nLet me think...\n</think>\nHello there"},"finish_reason":null}]}

data: {"id":"chatcmpl-1702300000000","object":"chat.completion.chunk","created":1702300000,"model":"claude-sonnet-4-5","choices":[{"index":0,"delta":{"tool_calls":[{"id":"call_123","type":"function","function":{"name":"search","arguments":"{\"query\":\"test\"}"}}]},"finish_reason":null}]}

data: {"id":"chatcmpl-1702300000000","object":"chat.completion.chunk","created":1702300000,"model":"claude-sonnet-4-5","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":20,"total_tokens":30}}

data: [DONE]
4.2 非流式响应处理
文件: src/api/client.js:281-343
export async function generateAssistantResponseNoStream(requestBody, token) {
  const headers = buildHeaders(token);

  // 发送请求
  const data = await requester.antigravity_fetch(config.api.noStreamUrl, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestBody)
  }).then(res => res.json());

  // 解析响应
  const parts = data.response?.candidates?.[0]?.content?.parts || [];
  let content = '';
  let thinkingContent = '';
  const toolCalls = [];
  const imageUrls = [];

  for (const part of parts) {
    if (part.thought === true) {
      thinkingContent += part.text || '';
    } else if (part.text !== undefined) {
      content += part.text;
    } else if (part.functionCall) {
      toolCalls.push(convertToToolCall(part.functionCall));
    } else if (part.inlineData) {
      const imageUrl = saveBase64Image(part.inlineData.data, part.inlineData.mimeType);
      imageUrls.push(imageUrl);
    }
  }

  // 拼接思考内容
  if (thinkingContent) {
    content = `<think>\n${thinkingContent}\n</think>\n${content}`;
  }

  // 图片模型：转为 markdown
  if (imageUrls.length > 0) {
    content += '\n\n' + imageUrls.map(url => `![image](${url})`).join('\n\n');
  }

  return { content, toolCalls, usage: {...} };
}
4.2.1 Antigravity 完整响应格式
{
  "response": {
    "candidates": [
      {
        "content": {
          "parts": [
            {"thought": true, "text": "Thinking process..."},
            {"text": "Final answer"},
            {
              "functionCall": {
                "id": "call_abc",
                "name": "search",
                "args": {"query": "test"}
              }
            },
            {
              "inlineData": {
                "mimeType": "image/png",
                "data": "iVBORw0KGgoAAAANSUhEUgAA..."
              }
            }
          ]
        },
        "finishReason": "STOP",
        "usageMetadata": {
          "promptTokenCount": 100,
          "candidatesTokenCount": 50,
          "totalTokenCount": 150
        }
      }
    ]
  }
}
4.2.2 OpenAI 完整响应格式
{
  "id": "chatcmpl-1702300000000",
  "object": "chat.completion",
  "created": 1702300000,
  "model": "claude-sonnet-4-5",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "<think>\nThinking process...\n</think>\nFinal answer\n\n![image](http://localhost:8045/images/1702300000000_abc123.png)",
        "tool_calls": [
          {
            "id": "call_abc",
            "type": "function",
            "function": {
              "name": "search",
              "arguments": "{\"query\":\"test\"}"
            }
          }
        ]
      },
      "finish_reason": "tool_calls"
    }
  ],
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150
  }
}
4.3 工具调用转换：convertToToolCall
文件: src/api/client.js:90-99
function convertToToolCall(functionCall) {
  return {
    id: functionCall.id || generateToolCallId(),
    type: 'function',
    function: {
      name: functionCall.name,
      arguments: JSON.stringify(functionCall.args)  // ⚠️ 注意：args 需要字符串化
    }
  };
}
五、认证与 Token 管理
5.1 Token 数据结构
存储文件: data/accounts.json
{
  "accounts": [
    {
      "email": "user@example.com",
      "access_token": "ya29.a0AfH6SMB...",
      "refresh_token": "1//0gJp8K...",
      "expires_in": 3599,
      "timestamp": 1702300000000,
      "projectId": "project-123456",
      "sessionId": "session-xyz",
      "enable": true
    }
  ]
}
5.2 Token 获取逻辑
文件: src/auth/token_manager.js
async getToken() {
  const enabledAccounts = this.accounts.filter(a => a.enable);
  if (enabledAccounts.length === 0) return null;

  // 轮转到下一个 token
  this.currentIndex = (this.currentIndex + 1) % enabledAccounts.length;
  let token = enabledAccounts[this.currentIndex];

  // 检查是否过期（提前 300 秒刷新）
  if (Date.now() >= token.timestamp + (token.expires_in - 300) * 1000) {
    token = await this.refreshToken(token);
  }

  // 检查是否有 projectId（懒加载）
  if (!token.projectId && !config.other.skipProjectIdFetch) {
    token.projectId = await this.fetchProjectId(token);
  }

  return token;
}
5.3 Token 刷新逻辑
async refreshToken(token) {
  const response = await axios({
    method: 'POST',
    url: 'https://oauth2.googleapis.com/token',
    data: {
      client_id: OAUTH_CONFIG.CLIENT_ID,
      client_secret: OAUTH_CONFIG.CLIENT_SECRET,
      grant_type: 'refresh_token',
      refresh_token: token.refresh_token
    }
  });

  // 更新 token 信息
  token.access_token = response.data.access_token;
  token.expires_in = response.data.expires_in;
  token.timestamp = Date.now();

  await this.saveAccounts();
  return token;
}
5.4 Token 禁用机制
文件: src/api/client.js:62-87
async function handleApiError(error, token) {
  const status = error.response?.status;

  if (status === 403) {
    // 超出上下文
    if (errorBody.includes("The caller does not")) {
      throw new Error(`超出模型最大上下文`);
    }

    // 权限错误：自动禁用该 token
    tokenManager.disableCurrentToken(token);
    throw new Error(`该账号没有使用权限，已自动禁用`);
  }

  throw new Error(`API请求失败 (${status}): ${errorBody}`);
}
六、特殊功能处理
6.1 图片生成模型
识别: 模型名包含 -image 后缀（如 gemini-3-pro-image） 特殊处理 (src/server/index.js:117-131):
if (model.includes('-image')) {
  requestBody.request.generationConfig = {
    candidateCount: 1
  };
  requestBody.requestType = "image_gen";

  // 移除不必要的配置
  delete requestBody.request.systemInstruction;
  delete requestBody.request.tools;
  delete requestBody.request.toolConfig;
}
响应处理 (src/api/client.js:316-320):
if (part.inlineData) {
  const imageUrl = saveBase64Image(part.inlineData.data, part.inlineData.mimeType);
  imageUrls.push(imageUrl);
}

// 转为 markdown
content += imageUrls.map(url => `![image](${url})`).join('\n\n');
图片存储 (src/utils/imageStorage.js):
export function saveBase64Image(base64Data, mimeType) {
  const ext = mimeType.split('/')[1]; // image/png → png
  const filename = `${Date.now()}_${crypto.randomBytes(8).toString('hex')}.${ext}`;
  const filepath = path.join(__dirname, '../../public/images', filename);

  fs.writeFileSync(filepath, Buffer.from(base64Data, 'base64'));

  return `http://localhost:8045/images/${filename}`;
}
6.2 Stable Diffusion API 兼容
路由: src/routes/sd.js
app.post('/sdapi/v1/txt2img', async (req, res) => {
  const { prompt, negative_prompt, steps, width, height } = req.body;

  const messages = [
    {role: "user", content: `Generate image: ${prompt}`}
  ];

  const requestBody = generateRequestBody(messages, 'gemini-3-pro-image', {}, null, token);
  requestBody.requestType = 'image_gen';

  const images = await generateImageForSD(requestBody, token);

  res.json({ images }); // 返回 base64 数组
});