# PROJECT_GUIDE

## 概览

### 项目简述与目标
本项目名为"GCLI2API"，核心目标是将Google Gemini API转换为OpenAI兼容的API接口，提供稳定的AI服务代理。项目采用Python + FastAPI架构，支持多端点双格式（OpenAI兼容和Gemini原生），具备流式响应、智能凭证轮换和Web管理面板等特性。

### 本次任务摘要与范围
**任务类型**：缺陷修复（DevOps/部署问题）。  
**问题描述**：通过docker-compose部署的Docker服务启动后自动关闭，影响日常调试。可能原因包括端口冲突、凭证初始化失败、网络模式冲突或其他系统级问题。  
**范围**：仅限于Docker部署和启动流程的诊断与修复，不涉及代码逻辑修改。  
**验收标准**：服务稳定运行至少30分钟，无自动关闭现象；日志无错误输出；healthcheck通过。

## 架构与技术栈

### 架构图/文字化拓扑
```
[客户端] → [FastAPI应用 (端口8080)]
    ↓
[路由层]
├── OpenAI兼容路由 (/v1/chat/completions)
├── Gemini原生路由 (/v1/models/{model}:generateContent)
└── Web管理路由 (/auth, /panel)
    ↓
[业务逻辑层]
├── 凭证管理器 (CredentialManager)
├── 格式检测器 (FormatDetector)
├── Google API客户端 (GoogleApiClient)
└── 工具模块 (AntiTruncation, Utils)
    ↓
[数据层]
├── 凭证文件 (./data/creds/*.json)
├── 配置 (环境变量 + config.toml)
└── 日志 (log.txt)
```

架构风格：单体应用，采用路由分离的微服务风格设计。进程边界清晰，模块职责明确。

### 技术栈与关键依赖及版本
- **语言与运行时**：Python 3.13 (Docker镜像python:3.13-slim)
- **Web框架**：FastAPI 0.104+ (异步API框架)
- **服务器**：Hypercorn (ASGI服务器)
- **HTTP客户端**：httpx[socks] (支持代理)
- **数据验证**：Pydantic (类型安全)
- **认证**：google-auth, google-auth-oauthlib
- **配置管理**：python-dotenv, toml
- **容器化**：Docker, Docker Compose
- **包管理**：pip (requirements.txt)

### 进程/模块边界与数据流
- **主进程**：web.py启动FastAPI应用，初始化CredentialManager
- **路由模块**：src/openai_router.py, src/gemini_router.py, src/web_routes.py
- **数据流**：客户端请求 → 路由 → 凭证管理 → Google API → 格式转换 → 响应

## 代码组织与约定

### 目录结构说明（含关键目录与职责）
```
/Users/liujie/Library/Mobile Documents/com~apple~CloudDocs/001-Inbox---中转站/001-Inbox---收件箱，先到碗里来/📦收集箱/docker/gcli2api/
├── config.py              # 全局配置管理
├── web.py                 # 主应用入口
├── log.py                 # 日志工具
├── requirements.txt       # Python依赖
├── docker-compose.yml     # Docker编排配置
├── Dockerfile             # 容器构建脚本
├── .env.example           # 环境变量示例
├── src/                   # 源代码目录
│   ├── openai_router.py   # OpenAI兼容API路由
│   ├── gemini_router.py   # Gemini原生API路由
│   ├── web_routes.py      # Web管理界面路由
│   ├── credential_manager.py # 凭证管理核心
│   ├── google_api_client.py # Google API客户端
│   ├── auth_api.py        # 认证API
│   ├── format_detector.py # 请求格式检测
│   ├── openai_transfer.py # OpenAI格式转换
│   ├── anti_truncation.py # 防截断处理
│   ├── models.py          # 数据模型定义
│   ├── usage_stats.py     # 使用统计
│   └── utils.py           # 工具函数
├── data/                  # 数据目录
│   └── creds/             # 凭证文件存储
├── front/                 # 前端代码
└── start.sh               # 本地启动脚本
```

### 代码风格、Lint、测试约定
- **编码风格**：PEP 8，4空格缩进，行长88字符
- **命名约定**：snake_case (函数/变量)，PascalCase (类)，UPPER_CASE (常量)
- **异步编程**：使用async/await模式，FastAPI原生支持
- **类型注解**：使用typing模块，Pydantic进行运行时验证
- **错误处理**：try/except块，日志记录异常
- **测试策略**：无内置测试框架，主要依赖手动验证和healthcheck

### 配置与环境管理
- **环境变量**：PORT(8080), PASSWORD(pwd), API_PASSWORD, PANEL_PASSWORD
- **配置文件**：config.toml (动态加载)
- **凭证管理**：./data/creds目录，JSON格式凭证文件
- **Secrets管理**：环境变量优先，TOML文件备用

## 运行与开发

### 本地运行步骤与关键命令
1. **环境准备**：
   ```bash
   cd /Users/liujie/Library/Mobile Documents/com~apple~CloudDocs/001-Inbox---中转站/001-Inbox---收件箱，先到碗里来/📦收集箱/docker/gcli2api
   pip install -r requirements.txt
   ```

2. **启动服务**：
   ```bash
   python web.py
   # 或使用脚本
   bash start.sh
   ```

3. **Docker运行**：
   ```bash
   docker-compose up -d
   ```

### 调试方法与样例数据/Mock
- **日志调试**：查看log.txt文件，包含启动日志和API调用记录
- **健康检查**：访问http://localhost:8080/v1/models (需要Authorization头)
- **凭证验证**：检查./data/creds目录下JSON文件完整性
- **网络调试**：使用curl测试API端点

### 常见问题与排错清单
- **端口冲突**：检查8080端口是否被占用 (`lsof -i :8080`)
- **凭证问题**：验证JSON文件格式和权限
- **网络问题**：检查host网络模式是否与本地服务冲突
- **依赖问题**：确认Python版本和包安装

## 接口与数据契约

### API 定义与示例
- **OpenAI兼容端点**：`/v1/chat/completions`
  - 方法：POST
  - 认证：`Authorization: Bearer {API_PASSWORD}`
  - 请求体：OpenAI标准格式或Gemini格式（自动检测）

- **Gemini原生端点**：`/v1/models/{model}:generateContent`
  - 方法：POST
  - 认证：Bearer/API密钥/URL参数
  - 请求体：Gemini原生格式

### 数据模型/Schema/迁移策略
- **凭证格式**：Google OAuth JSON (client_id, client_secret, refresh_token等)
- **配置格式**：TOML文件，支持动态重载
- **日志格式**：结构化文本，包含时间戳和级别

### 兼容性与版本化策略
- **API版本**：v1 (当前版本)
- **向后兼容**：支持旧版OpenAI格式
- **格式检测**：自动识别请求格式，无需手动切换

## 安全与合规

### 认证/鉴权/权限模型
- **API认证**：Bearer Token (API_PASSWORD)
- **面板认证**：独立密码 (PANEL_PASSWORD)
- **OAuth流程**：Google OAuth 2.0，支持多凭证轮换

### 数据隐私、密钥管理、依赖漏洞策略
- **密钥存储**：本地文件系统 (./data/creds)
- **传输安全**：HTTPS推荐 (当前HTTP)
- **凭证轮换**：自动检测失效凭证并切换
- **漏洞管理**：依赖requirements.txt，定期更新

## 可观测性与质量保证

### 日志/指标/Tracing
- **日志级别**：INFO (默认)，可配置DEBUG/ERROR
- **日志位置**：log.txt
- **健康检查**：内置healthcheck端点，检查API可用性

### 测试金字塔与覆盖目标
- **单元测试**：无 (主要依赖集成测试)
- **集成测试**：手动API测试
- **端到端测试**：通过healthcheck验证

### 性能基线与回归检查
- **响应时间**：<30秒 (HTTP_TIMEOUT配置)
- **并发连接**：100 (MAX_CONNECTIONS配置)
- **错误率**：<5% (429重试机制)

## 任务剧本（本次任务的可执行计划）

### 分解为步骤清单（每步说明目标、入口文件、修改点、命令、预期产出）
1. **诊断端口冲突**
   - 目标：检查8080端口占用情况
   - 入口文件：docker-compose.yml
   - 修改点：PORT环境变量
   - 命令：`lsof -i :8080` 和 `docker-compose logs gcli2api`
   - 预期产出：确认端口状态和容器日志

2. **验证凭证初始化**
   - 目标：检查凭证文件和初始化过程
   - 入口文件：src/credential_manager.py, web.py
   - 修改点：lifespan函数中的初始化逻辑
   - 命令：`docker-compose exec gcli2api ls -la /app/creds`
   - 预期产出：凭证文件存在且格式正确

3. **检查网络模式冲突**
   - 目标：验证host网络模式是否导致冲突
   - 入口文件：docker-compose.yml
   - 修改点：network_mode配置
   - 命令：`docker-compose down && docker-compose up -d`
   - 预期产出：服务稳定运行

4. **分析healthcheck失败**
   - 目标：调试健康检查逻辑
   - 入口文件：docker-compose.yml (healthcheck配置)
   - 修改点：healthcheck测试命令
   - 命令：`docker-compose ps` 和 `docker inspect gcli2api`
   - 预期产出：healthcheck状态正常

5. **日志分析与修复**
   - 目标：收集详细错误信息
   - 入口文件：log.py, web.py
   - 修改点：日志配置和错误处理
   - 命令：`docker-compose logs -f gcli2api`
   - 预期产出：明确错误原因和修复方案

### 影响分析与回滚策略
- **影响范围**：仅Docker部署配置，不影响代码逻辑
- **风险评估**：低风险，主要涉及配置调整
- **回滚策略**：保留原docker-compose.yml备份，失败时恢复

### 验收标准（功能/性能/安全/文档/发布）
- **功能**：服务稳定运行30分钟，无自动关闭
- **性能**：响应时间正常，healthcheck通过
- **安全**：认证机制正常，凭证安全
- **文档**：更新README.md的故障排除部分
- **发布**：提交配置变更到Git

## 交付产物清单

### 代码变更点列表（文件路径级）
- docker-compose.yml：网络模式和端口配置优化
- config.py：环境变量默认值调整
- README.md：故障排除指南更新

### 文档与脚本更新项
- README.md：添加Docker故障排除章节
- start.sh：添加调试选项

### 变更日志（遵循 Conventional Commits）
- fix: resolve Docker auto-shutdown issue by optimizing network config
- docs: update troubleshooting guide for Docker deployment

## 上线与发布

### 环境推广流程（dev → staging → prod）
1. 本地测试修复效果
2. 提交变更到Git分支
3. 合并到主分支后重新部署

### 灰度/开关旗标/回滚
- **开关**：通过环境变量控制调试模式
- **回滚**：保留docker-compose.yml备份文件

### 风险与应急预案
- **风险**：配置变更导致新问题
- **预案**：立即回滚配置，分析日志定位问题

## 附录

### 术语表
- **GCLI2API**：项目名称，Gemini CLI to API的缩写
- **凭证轮换**：自动切换多个Google OAuth凭证以提高稳定性
- **格式检测**：自动识别OpenAI或Gemini请求格式

### 参考链接
- [FastAPI文档](https://fastapi.tiangolo.com/)
- [Google Gemini API](https://ai.google.dev/docs)
- [Docker Compose](https://docs.docker.com/compose/)

### 关键代码片段定位
- **主入口**：web.py:67 (serve函数调用)
- **凭证管理**：src/credential_manager.py:CredentialManager类
- **路由定义**：src/openai_router.py:chat_completions函数