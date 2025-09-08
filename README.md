# GeminiCLI to API

**将 GeminiCLI 转换为 OpenAI 和 GEMINI API 接口**

[English](docs/README_EN.md) | 中文

## 🚀 快速部署

[![Deploy on Zeabur](https://zeabur.com/button.svg)](https://zeabur.com/templates/2QLQC2?referralCode=su-kaka)
---

## ⚠️ 许可证声明

**本项目采用 Cooperative Non-Commercial License (CNC-1.0)**

这是一个反商业化的严格开源协议，详情请查看 [LICENSE](LICENSE) 文件。

### ✅ 允许的用途：
- 个人学习、研究、教育用途
- 非营利组织使用
- 开源项目集成（需遵循相同协议）
- 学术研究和论文发表

### ❌ 禁止的用途：
- 任何形式的商业使用
- 年收入超过100万美元的企业使用
- 风投支持或公开交易的公司使用  
- 提供付费服务或产品
- 商业竞争用途

---

## 控制面板演示网址：https://gcli2api-9xbf.onrender.com 密码：pwd

## 核心功能

### 🔄 API 端点和格式支持

**多端点双格式支持**
- **OpenAI 兼容端点**：`/v1/chat/completions` 和 `/v1/models`
  - 支持标准 OpenAI 格式（messages 结构）
  - 支持 Gemini 原生格式（contents 结构）
  - 自动格式检测和转换，无需手动切换
  - 支持多模态输入（文本 + 图像）
- **Gemini 原生端点**：`/v1/models/{model}:generateContent` 和 `streamGenerateContent`
  - 支持完整的 Gemini 原生 API 规范
  - 多种认证方式：Bearer Token、x-goog-api-key 头部、URL 参数 key

### 🔐 认证和安全管理

**灵活的密码管理**
- **分离密码支持**：API 密码（聊天端点）和控制面板密码可独立设置
- **多种认证方式**：支持 Authorization Bearer、x-goog-api-key 头部、URL 参数等
- **JWT Token 认证**：控制面板支持 JWT 令牌认证
- **用户邮箱获取**：自动获取和显示 Google 账户邮箱地址

### 📊 智能凭证管理系统

**高级凭证管理**
- 多个 Google OAuth 凭证自动轮换
- 通过冗余认证增强稳定性
- 负载均衡与并发请求支持
- 自动故障检测和凭证禁用
- 凭证使用统计和配额管理
- 支持手动启用/禁用凭证文件
- 批量凭证文件操作（启用、禁用、删除）

**凭证状态监控**
- 实时凭证健康检查
- 错误码追踪（429、403、500 等）
- 自动封禁机制（可配置）
- 凭证轮换策略（基于调用次数）
- 使用统计和配额监控

### 🌊 流式传输和响应处理

**多种流式支持**
- 真正的实时流式响应
- 假流式模式（用于兼容性）
- 流式抗截断功能（防止回答被截断）
- 异步任务管理和超时处理

**响应优化**
- 思维链（Thinking）内容分离
- 推理过程（reasoning_content）处理
- 多轮对话上下文管理
- 兼容性模式（将 system 消息转换为 user 消息）

### 🎛️ Web 管理控制台

**全功能 Web 界面**
- OAuth 认证流程管理
- 凭证文件上传、下载、管理
- 实时日志查看（WebSocket）
- 系统配置管理
- 使用统计和监控面板
- 移动端适配界面

**批量操作支持**
- ZIP 文件批量上传凭证
- 批量启用/禁用/删除凭证
- 批量获取用户邮箱
- 批量配置管理

### 📈 使用统计和监控

**详细使用统计**
- 按凭证文件统计调用次数
- Gemini 2.5 Pro 模型专项统计
- 每日配额管理（UTC+7 重置）
- 聚合统计和分析
- 自定义每日限制配置

**实时监控**
- WebSocket 实时日志流
- 系统状态监控
- 凭证健康状态
- API 调用成功率统计

### 🔧 高级配置和自定义

**网络和代理配置**
- HTTP/HTTPS 代理支持
- 代理端点配置（OAuth、Google APIs、元数据服务）
- 超时和重试配置
- 网络错误处理和恢复

**性能和稳定性配置**
- 429 错误自动重试（可配置间隔和次数）
- 抗截断最大重试次数
- 凭证轮换策略
- 并发请求管理

**日志和调试**
- 多级日志系统（DEBUG、INFO、WARNING、ERROR）
- 日志文件管理
- 实时日志流
- 日志下载和清空

### 🔄 环境变量和配置管理

**灵活的配置方式**
- TOML 配置文件支持
- 环境变量配置
- 热配置更新（部分配置项）
- 配置锁定（环境变量优先级）

**环境变量凭证支持**
- `GCLI_CREDS_*` 格式环境变量导入
- 自动加载环境变量凭证
- Base64 编码凭证支持
- Docker 容器友好

## 支持的模型

所有模型均具备 1M 上下文窗口容量。每个凭证文件提供 1000 次请求额度。

### 🤖 基础模型
- `gemini-2.5-pro`
- `gemini-2.5-pro-preview-06-05`  
- `gemini-2.5-pro-preview-05-06`

### 🧠 思维模型（Thinking Models）
- `gemini-2.5-pro-maxthinking`：最大思考预算模式
- `gemini-2.5-pro-nothinking`：无思考模式
- 支持自定义思考预算配置
- 自动分离思维内容和最终回答

### 🔍 搜索增强模型
- `gemini-2.5-pro-search`：集成搜索功能的模型

### 🌊 特殊功能变体
- **假流式模式**：在任何模型名称后添加 `-假流式` 后缀
  - 例：`gemini-2.5-pro-假流式`
  - 用于需要流式响应但服务端不支持真流式的场景
- **流式抗截断模式**：在模型名称前添加 `流式抗截断/` 前缀
  - 例：`流式抗截断/gemini-2.5-pro`  
  - 自动检测响应截断并重试，确保完整回答

### 🔧 模型功能自动检测
- 系统自动识别模型名称中的功能标识
- 透明地处理功能模式转换
- 支持功能组合使用

---

## 安装指南

### Termux 环境

**初始安装**
```bash
curl -o termux-install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/termux-install.sh" && chmod +x termux-install.sh && ./termux-install.sh
```

**重启服务**
```bash
cd gcli2api
bash termux-start.sh
```

### Windows 环境

**初始安装**
```powershell
iex (iwr "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/install.ps1" -UseBasicParsing).Content
```

**重启服务**
双击执行 `start.bat`

### Linux 环境

**初始安装**
```bash
curl -o install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/install.sh" && chmod +x install.sh && ./install.sh
```

**重启服务**
```bash
cd gcli2api
bash start.sh
```

### Docker 环境

**Docker 运行命令**
```bash
# 使用通用密码
docker run -d --name gcli2api --network host -e PASSWORD=pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest

# 使用分离密码
docker run -d --name gcli2api --network host -e API_PASSWORD=api_pwd -e PANEL_PASSWORD=panel_pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest
```

**Docker Compose 运行命令**
1. 将以下内容保存为 `docker-compose.yml` 文件：
    ```yaml
    version: '3.8'

    services:
      gcli2api:
        image: ghcr.io/su-kaka/gcli2api:latest
        container_name: gcli2api
        restart: unless-stopped
        network_mode: host
        environment:
          # 使用通用密码（推荐用于简单部署）
          - PASSWORD=pwd
          - PORT=7861
          # 或使用分离密码（推荐用于生产环境）
          # - API_PASSWORD=your_api_password
          # - PANEL_PASSWORD=your_panel_password
        volumes:
          - ./data/creds:/app/creds
        healthcheck:
          test: ["CMD-SHELL", "python -c \"import sys, urllib.request, os; port = os.environ.get('PORT', '7861'); req = urllib.request.Request(f'http://localhost:{port}/v1/models', headers={'Authorization': 'Bearer ' + os.environ.get('PASSWORD', 'pwd')}); sys.exit(0 if urllib.request.urlopen(req, timeout=5).getcode() == 200 else 1)\""]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 40s
    ```
2. 启动服务：
    ```bash
    docker-compose up -d
    ```

---

## ⚠️ 注意事项

- 当前 OAuth 验证流程**仅支持本地主机（localhost）访问**，即须通过 `http://127.0.0.1:7861/auth` 完成认证（默认端口 7861，可通过 PORT 环境变量修改）。
- **如需在云服务器或其他远程环境部署，请先在本地运行服务并完成 OAuth 验证，获得生成的 json 凭证文件（位于 `./geminicli/creds` 目录）后，再在auth面板将该文件上传即可。**
- **请严格遵守使用限制，仅用于个人学习和非商业用途**

---

## 配置说明

1. 访问 `http://127.0.0.1:7861/auth` （默认端口，可通过 PORT 环境变量修改）
2. 完成 OAuth 认证流程（默认密码：`pwd`，可通过环境变量修改）
3. 配置客户端：

**OpenAI 兼容客户端：**
   - **端点地址**：`http://127.0.0.1:7861/v1`
   - **API 密钥**：`pwd`（默认值，可通过 API_PASSWORD 或 PASSWORD 环境变量修改）

**Gemini 原生客户端：**
   - **端点地址**：`http://127.0.0.1:7861`
   - **认证方式**：
     - `Authorization: Bearer your_api_password`
     - `x-goog-api-key: your_api_password` 
     - URL 参数：`?key=your_api_password`

## 💾 分布式存储模式

### 🌟 存储后端优先级

gcli2api 支持多种存储后端，按优先级自动选择：**Redis > Postgres > MongoDB > 本地文件**

### ⚡ Redis 分布式存储模式

### ⚙️ 启用 Redis 模式

**步骤 1: 配置 Redis 连接**
```bash
# 本地 Redis
export REDIS_URI="redis://localhost:6379"

# 带密码的 Redis
export REDIS_URI="redis://:password@localhost:6379"

# SSL 连接（推荐生产环境）
export REDIS_URI="rediss://default:password@host:6380"

# Upstash Redis（免费云服务）
export REDIS_URI="rediss://default:token@your-host.upstash.io:6379"

# 可选：自定义数据库索引（默认: 0）
export REDIS_DATABASE="1"
```

**步骤 2: 启动应用**
```bash
# 应用会自动检测 Redis 配置并优先使用 Redis 存储
python web.py
```

### 🐘 Postgres 分布式存储模式

如果未配置 Redis，或者你希望使用关系型数据库作为主要存储方案，gcli2api 也支持 Postgres（位于 Redis 之后，优先于 MongoDB）。

⚙️ 启用 Postgres 模式

步骤 1: 配置 Postgres 连接
```bash
# 使用标准 DSN（示例）
export POSTGRES_DSN="postgresql://user:password@localhost:5432/gcli2api"

# 也可以使用 socket 或其他 DSN 格式，取决于你的部署方式
```

步骤 2: 启动应用
```bash
# 应用会自动检测 POSTGRES_DSN 并在 Redis 未启用时优先使用 Postgres 存储
python web.py
```

### 🍃 MongoDB 分布式存储模式

### 🌟 备选存储方案

如果未配置 Redis，gcli2api 将尝试使用 **MongoDB 存储模式**，

### ⚙️ 启用 MongoDB 模式

**步骤 1: 配置 MongoDB 连接**
```bash
# 本地 MongoDB
export MONGODB_URI="mongodb://localhost:27017"

# MongoDB Atlas 云服务
export MONGODB_URI="mongodb+srv://username:password@cluster.mongodb.net"

# 带认证的 MongoDB
export MONGODB_URI="mongodb://admin:password@localhost:27017/admin"

# 可选：自定义数据库名称（默认: gcli2api）
export MONGODB_DATABASE="my_gcli_db"
```

**步骤 2: 启动应用**
```bash
# 应用会自动检测 MongoDB 配置并使用 MongoDB 存储
python web.py
```

**Docker 环境使用 MongoDB**
```bash
# 单机 MongoDB 部署
docker run -d --name gcli2api \
  -e MONGODB_URI="mongodb://mongodb:27017" \
  -e API_PASSWORD=your_password \
  --network your_network \
  ghcr.io/su-kaka/gcli2api:latest

# 使用 MongoDB Atlas
docker run -d --name gcli2api \
  -e MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/gcli2api" \
  -e API_PASSWORD=your_password \
  -p 7861:7861 \
  ghcr.io/su-kaka/gcli2api:latest
```

**Docker Compose 示例**
```yaml
version: '3.8'

services:
  mongodb:
    image: mongo:7
    container_name: gcli2api-mongodb
    restart: unless-stopped
    environment:
      MONGO_INITDB_ROOT_USERNAME: admin
      MONGO_INITDB_ROOT_PASSWORD: password123
    volumes:
      - mongodb_data:/data/db
    ports:
      - "27017:27017"

  gcli2api:
    image: ghcr.io/su-kaka/gcli2api:latest
    container_name: gcli2api
    restart: unless-stopped
    depends_on:
      - mongodb
    environment:
      - MONGODB_URI=mongodb://admin:password123@mongodb:27017/admin
      - MONGODB_DATABASE=gcli2api
      - API_PASSWORD=your_api_password
      - PORT=7861
    ports:
      - "7861:7861"

volumes:
  mongodb_data:
```


### 🔧 高级配置

**MongoDB 连接优化**
```bash
# 连接池和超时配置
export MONGODB_URI="mongodb://localhost:27017?maxPoolSize=10&serverSelectionTimeoutMS=5000"

# 副本集配置
export MONGODB_URI="mongodb://host1:27017,host2:27017,host3:27017/gcli2api?replicaSet=myReplicaSet"

# 读写分离配置
export MONGODB_URI="mongodb://localhost:27017/gcli2api?readPreference=secondaryPreferred"
```

## 🏗️ 技术架构

### 核心模块说明

**认证和凭证管理** (`src/auth.py`, `src/credential_manager.py`)
- OAuth 2.0 认证流程管理
- 多凭证文件状态管理和轮换
- 自动故障检测和恢复
- JWT 令牌生成和验证

**API 路由和转换** (`src/openai_router.py`, `src/gemini_router.py`, `src/openai_transfer.py`)
- OpenAI 和 Gemini 格式双向转换
- 多模态输入处理（文本+图像）
- 思维链内容分离和处理
- 流式响应管理

**网络和代理** (`src/httpx_client.py`, `src/google_chat_api.py`)
- 统一 HTTP 客户端管理
- 代理配置和热更新支持
- 超时和重试策略
- 异步请求池管理

**状态管理** (`src/state_manager.py`, `src/usage_stats.py`)
- 原子化状态操作
- 使用统计和配额管理
- 文件锁和并发安全
- 数据持久化（TOML 格式）

**任务管理** (`src/task_manager.py`)
- 全局异步任务生命周期管理
- 资源清理和内存管理
- 优雅关闭和异常处理

**Web 控制台** (`src/web_routes.py`)
- RESTful API 端点
- WebSocket 实时通信
- 移动端适配检测
- 批量操作支持

### 高级特性实现

**流式抗截断机制** (`src/anti_truncation.py`)
- 检测响应截断模式
- 自动重试和状态恢复
- 上下文连接管理

**格式检测和转换** (`src/format_detector.py`)
- 自动检测请求格式（OpenAI vs Gemini）
- 无缝格式转换
- 参数映射和验证

**用户代理模拟** (`src/utils.py`)
- GeminiCLI 格式用户代理生成
- 平台检测和客户端元数据
- API 兼容性保证

### 环境变量配置

**基础配置**
- `PORT`: 服务端口（默认：7861）
- `HOST`: 服务器监听地址（默认：0.0.0.0）

**密码配置**
- `API_PASSWORD`: 聊天 API 访问密码（默认：继承 PASSWORD 或 pwd）
- `PANEL_PASSWORD`: 控制面板访问密码（默认：继承 PASSWORD 或 pwd）  
- `PASSWORD`: 通用密码，设置后覆盖上述两个（默认：pwd）

**性能和稳定性配置**
- `CALLS_PER_ROTATION`: 每个凭证轮换前的调用次数（默认：10）
- `RETRY_429_ENABLED`: 启用 429 错误自动重试（默认：true）
- `RETRY_429_MAX_RETRIES`: 429 错误最大重试次数（默认：3）
- `RETRY_429_INTERVAL`: 429 错误重试间隔，秒（默认：1.0）
- `ANTI_TRUNCATION_MAX_ATTEMPTS`: 抗截断最大重试次数（默认：3）

**网络和代理配置**
- `PROXY`: HTTP/HTTPS 代理地址（格式：`http://host:port`）
- `OAUTH_PROXY_URL`: OAuth 认证代理端点
- `GOOGLEAPIS_PROXY_URL`: Google APIs 代理端点
- `METADATA_SERVICE_URL`: 元数据服务代理端点

**自动化配置**
- `AUTO_BAN`: 启用凭证自动封禁（默认：true）
- `AUTO_LOAD_ENV_CREDS`: 启动时自动加载环境变量凭证（默认：false）

**兼容性配置**
- `COMPATIBILITY_MODE`: 启用兼容性模式，将 system 消息转为 user 消息（默认：false）

**日志配置**
- `LOG_LEVEL`: 日志级别（DEBUG/INFO/WARNING/ERROR，默认：INFO）
- `LOG_FILE`: 日志文件路径（默认：gcli2api.log）

**存储配置（按优先级）**

**Redis 配置（最高优先级）**
- `REDIS_URI`: Redis 连接字符串（设置后启用 Redis 模式）
  - 本地：`redis://localhost:6379`
  - 带密码：`redis://:password@host:6379`
  - SSL：`rediss://default:password@host:6380`
- `REDIS_DATABASE`: Redis 数据库索引（0-15，默认：0）

**MongoDB 配置（第二优先级）**
- `MONGODB_URI`: MongoDB 连接字符串（设置后启用 MongoDB 模式）
- `MONGODB_DATABASE`: MongoDB 数据库名称（默认：gcli2api）

**凭证配置**

支持使用 `GCLI_CREDS_*` 环境变量导入多个凭证：

#### 凭证环境变量使用示例

**方式 1：编号格式**
```bash
export GCLI_CREDS_1='{"client_id":"your-client-id","client_secret":"your-secret","refresh_token":"your-token","token_uri":"https://oauth2.googleapis.com/token","project_id":"your-project"}'
export GCLI_CREDS_2='{"client_id":"...","project_id":"..."}'
```

**方式 2：项目名格式**
```bash
export GCLI_CREDS_myproject='{"client_id":"...","project_id":"myproject",...}'
export GCLI_CREDS_project2='{"client_id":"...","project_id":"project2",...}'
```

**启用自动加载**
```bash
export AUTO_LOAD_ENV_CREDS=true  # 程序启动时自动导入环境变量凭证
```

**Docker 使用示例**
```bash
# 使用通用密码
docker run -d --name gcli2api \
  -e PASSWORD=mypassword \
  -e PORT=8080 \
  -e GOOGLE_CREDENTIALS="$(cat credential.json | base64 -w 0)" \
  ghcr.io/su-kaka/gcli2api:latest

# 使用分离密码
docker run -d --name gcli2api \
  -e API_PASSWORD=my_api_password \
  -e PANEL_PASSWORD=my_panel_password \
  -e PORT=8080 \
  -e GOOGLE_CREDENTIALS="$(cat credential.json | base64 -w 0)" \
  ghcr.io/su-kaka/gcli2api:latest
```

注意：当设置了凭证环境变量时，系统将优先使用环境变量中的凭证，忽略 `creds` 目录中的文件。

### API 使用方式

本服务支持两套完整的 API 端点：

#### 1. OpenAI 兼容端点

**端点：** `/v1/chat/completions`  
**认证：** `Authorization: Bearer your_api_password`

支持两种请求格式，会自动检测并处理：

**OpenAI 格式：**
```json
{
  "model": "gemini-2.5-pro",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant"},
    {"role": "user", "content": "Hello"}
  ],
  "temperature": 0.7,
  "stream": true
}
```

**Gemini 原生格式：**
```json
{
  "model": "gemini-2.5-pro",
  "contents": [
    {"role": "user", "parts": [{"text": "Hello"}]}
  ],
  "systemInstruction": {"parts": [{"text": "You are a helpful assistant"}]},
  "generationConfig": {
    "temperature": 0.7
  }
}
```

#### 2. Gemini 原生端点

**非流式端点：** `/v1/models/{model}:generateContent`  
**流式端点：** `/v1/models/{model}:streamGenerateContent`  
**模型列表：** `/v1/models`

**认证方式（任选一种）：**
- `Authorization: Bearer your_api_password`
- `x-goog-api-key: your_api_password`  
- URL 参数：`?key=your_api_password`

**请求示例：**
```bash
# 使用 x-goog-api-key 头部
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-pro:generateContent" \
  -H "x-goog-api-key: your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'

# 使用 URL 参数
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-pro:streamGenerateContent?key=your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'
```

**说明：**
- OpenAI 端点返回 OpenAI 兼容格式
- Gemini 端点返回 Gemini 原生格式
- 两种端点使用相同的 API 密码

## 📋 完整 API 参考

### Web 控制台 API

**认证端点**
- `POST /auth/login` - 用户登录
- `POST /auth/start` - 开始 OAuth 认证
- `POST /auth/callback` - 处理 OAuth 回调
- `GET /auth/status/{project_id}` - 检查认证状态

**凭证管理端点**
- `GET /creds/status` - 获取所有凭证状态
- `POST /creds/action` - 单个凭证操作（启用/禁用/删除）
- `POST /creds/batch-action` - 批量凭证操作
- `POST /auth/upload` - 批量上传凭证文件（支持 ZIP）
- `GET /creds/download/{filename}` - 下载凭证文件
- `GET /creds/download-all` - 打包下载所有凭证
- `POST /creds/fetch-email/{filename}` - 获取用户邮箱
- `POST /creds/refresh-all-emails` - 批量刷新用户邮箱

**配置管理端点**
- `GET /config/get` - 获取当前配置
- `POST /config/save` - 保存配置

**环境变量凭证端点**
- `POST /auth/load-env-creds` - 加载环境变量凭证
- `DELETE /auth/env-creds` - 清除环境变量凭证
- `GET /auth/env-creds-status` - 获取环境变量凭证状态

**日志管理端点**
- `POST /auth/logs/clear` - 清空日志
- `GET /auth/logs/download` - 下载日志文件
- `WebSocket /auth/logs/stream` - 实时日志流

**使用统计端点**
- `GET /usage/stats` - 获取使用统计
- `GET /usage/aggregated` - 获取聚合统计
- `POST /usage/update-limits` - 更新使用限制
- `POST /usage/reset` - 重置使用统计

### 聊天 API 功能特性

**多模态支持**
```json
{
  "model": "gemini-2.5-pro",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "描述这张图片"},
        {
          "type": "image_url",
          "image_url": {
            "url": "data:image/jpeg;base64,/9j/4AAQSkZJRgABA..."
          }
        }
      ]
    }
  ]
}
```

**思维模式支持**
```json
{
  "model": "gemini-2.5-pro-maxthinking",
  "messages": [
    {"role": "user", "content": "复杂数学问题"}
  ]
}
```

响应将包含分离的思维内容：
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "最终答案",
      "reasoning_content": "详细的思考过程..."
    }
  }]
}
```

**流式抗截断使用**
```json
{
  "model": "流式抗截断/gemini-2.5-pro",
  "messages": [
    {"role": "user", "content": "写一篇长文章"}
  ],
  "stream": true
}
```

**兼容性模式**
```bash
# 启用兼容性模式
export COMPATIBILITY_MODE=true
```
此模式下，所有 `system` 消息会转换为 `user` 消息，提高与某些客户端的兼容性。

---

## 故障排除

**400 错误解决方案**
```bash
npx https://github.com/google-gemini/gemini-cli
```
1. 选择选项 1
2. 按回车确认
3. 完成浏览器中的 Google 账户认证
4. 系统将自动完成授权

---

## 支持项目

如果这个项目对您有帮助，欢迎支持项目的持续发展！

详细捐赠信息请查看：[📖 捐赠说明文档](docs/DONATE.md)

---

## 许可证与免责声明

本项目仅供学习和研究用途。使用本项目表示您同意：
- 不将本项目用于任何商业用途
- 承担使用本项目的所有风险和责任
- 遵守相关的服务条款和法律法规

项目作者对因使用本项目而产生的任何直接或间接损失不承担责任。
