# AGY2API

**Convert Google Antigravity to OpenAI, Gemini, and Claude API Compatible Interfaces**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: CNC-1.0](https://img.shields.io/badge/License-CNC--1.0-red.svg)](../LICENSE)
[![Docker](https://img.shields.io/badge/docker-available-blue.svg)](https://github.com/su-kaka/agy2api/pkgs/container/agy2api)

[中文](../README.md) | English | [日本語](./README_JA.md)

> **⚠️ Important Change**: The GeminiCLI endpoint has been deprecated by Google. All GeminiCLI-related code has been completely removed; only the Antigravity mode remains.

## Installation Guide

### Termux Environment

**Initial Installation**
```bash
curl -o termux-install.sh "https://raw.githubusercontent.com/su-kaka/agy2api/refs/heads/master/termux-install.sh" && chmod +x termux-install.sh && ./termux-install.sh
```

**Restart Service**
```bash
cd agy2api
bash termux-start.sh
```

### Windows Environment

**Initial Installation**
```powershell
iex (iwr "https://raw.githubusercontent.com/su-kaka/agy2api/refs/heads/master/install.ps1" -UseBasicParsing).Content
```

**Restart Service**
Double-click to execute `start.bat`

### Linux Environment

**Initial Installation**
```bash
curl -o install.sh "https://raw.githubusercontent.com/su-kaka/agy2api/refs/heads/master/install.sh" && chmod +x install.sh && ./install.sh
```

**Restart Service**
```bash
cd agy2api
bash start.sh
```

### macOS Environment

**Initial Installation**
```bash
curl -o darwin-install.sh "https://raw.githubusercontent.com/su-kaka/agy2api/refs/heads/master/darwin-install.sh" && chmod +x darwin-install.sh && ./darwin-install.sh
```

**Restart Service**
```bash
cd agy2api
bash start.sh
```

### Docker Environment

**Docker Run Command**
```bash
docker run -d --name agy2api --network host -e API_PASSWORD=api_pwd -e PANEL_PASSWORD=panel_pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/agy2api:latest
```

**Docker Mac**
```bash
docker run -d \
  --name agy2api \
  -p 7861:7861 \
  -p 8080:8080 \
  -e API_PASSWORD=api_pwd \
  -e PANEL_PASSWORD=panel_pwd \
  -e PORT=7861 \
  -v "$(pwd)/data/creds":/app/creds \
  ghcr.io/su-kaka/agy2api:latest
```

**Docker Compose Run Command**
1. Save the following content as `docker-compose.yml` file:
    ```yaml
    version: '3.8'

    services:
      agy2api:
        image: ghcr.io/su-kaka/agy2api:latest
        container_name: agy2api
        restart: unless-stopped
        network_mode: host
        environment:
          # Password configuration (separate passwords)
          - API_PASSWORD=your_api_password
          - PANEL_PASSWORD=your_panel_password
          - PORT=7861
        volumes:
          - ./data/creds:/app/creds
        healthcheck:
          test: ["CMD-SHELL", "python -c \"import sys, urllib.request, os; port = os.environ.get('PORT', '7861'); req = urllib.request.Request(f'http://localhost:{port}/v1/models', headers={'Authorization': 'Bearer ' + os.environ.get('API_PASSWORD', 'pwd')}); sys.exit(0 if urllib.request.urlopen(req, timeout=5).getcode() == 200 else 1)\""]
          interval: 30s
          timeout: 10s
          retries: 3
          start_period: 40s
    ```
2. Start the service:
    ```bash
    docker-compose up -d
    ```

## Core Features

### 🔄 API Endpoints and Format Support

**Multi-endpoint Multi-format Support**
- **OpenAI Compatible Endpoints**: `/v1/chat/completions` and `/v1/models`
  - Supports standard OpenAI format (messages structure)
  - Supports Gemini native format (contents structure)
  - Automatic format detection and conversion, no manual switching required
  - Supports multimodal input (text + images)
- **Gemini Native Endpoints**: `/v1/models/{model}:generateContent` and `streamGenerateContent`
  - Supports complete Gemini native API specifications
  - Multiple authentication methods: Bearer Token, x-goog-api-key header, URL parameter key
- **Claude Format Compatibility**: Full support for Claude API format
  - Endpoint: `/v1/messages` (follows Claude API specification)
  - Supports Claude standard messages format
  - Supports system parameter and Claude-specific features
  - Automatically converts to backend-supported format
- **Antigravity API Support**: Supports OpenAI, Gemini, and Claude formats
  - OpenAI format endpoint: `/v1/chat/completions`
  - Gemini format endpoint: `/v1/models/{model}:generateContent` and `streamGenerateContent`
  - Claude format endpoint: `/v1/messages`
  - Supports all Antigravity models (Claude, Gemini, etc.)
  - Automatic model name mapping and thinking mode detection

### 🔐 Authentication and Security Management

**Flexible Password Management**
- **Separate Password Support**: API password (chat endpoints) and control panel password can be set independently
- **Multiple Authentication Methods**: Supports Authorization Bearer, x-goog-api-key header, URL parameters, etc.
- **JWT Token Authentication**: Control panel supports JWT token authentication
- **User Email Retrieval**: Automatically retrieves and displays Google account email addresses

### 📊 Intelligent Credential Management System

**Advanced Credential Management**
- Multiple Google OAuth credential automatic rotation
- Enhanced stability through redundant authentication
- Load balancing and concurrent request support
- Automatic failure detection and credential disabling
- Credential usage statistics and quota management
- Credentials keyed by email (unique); re-importing the same email overwrites automatically
- Batch operations (enable, disable, delete)

**Credential Status Monitoring**
- Real-time credential health checks
- Error code tracking (429, 403, 500, etc.)
- Automatic banning mechanism (configurable)

### 🌊 Streaming and Response Processing

**Multiple Streaming Support**
- True real-time streaming responses
- Streaming anti-truncation feature (prevents answer truncation)
- Asynchronous task management and timeout handling

**Response Optimization**
- Thinking chain content separation
- Reasoning process (reasoning_content) handling
- Multi-turn conversation context management

### 🎛️ Web Management Console

**Full-featured Web Interface**
- Antigravity OAuth authentication flow (with callback URL shortcut)
- Credential file upload, download, and management
- System configuration management
- Usage statistics and monitoring dashboard
- Mobile-friendly interface

**Batch Operation Support**
- ZIP/JSON batch credential upload (Antigravity)
- Batch enable/disable/delete credentials
- Batch user email retrieval
- Batch configuration management
- Unified batch upload interface for all credential types

### 🔧 Advanced Configuration and Customization

**Network and Proxy Configuration**
- HTTP/HTTPS proxy support
- Proxy endpoint configuration (OAuth, Google APIs, metadata service)
- Timeout and retry configuration
- Network error handling and recovery

**Performance and Stability Configuration**
- 429 error automatic retry (configurable interval and attempts)
- Anti-truncation maximum retry attempts

**Logging and Debugging**
- Multi-level logging system (DEBUG, INFO, WARNING, ERROR)
- Console-only logging (`ENABLE_LOG=0` for full silence)

### 🔄 Environment Variables and Configuration Management

**Flexible Configuration Methods**
- Environment variable configuration
- Hot configuration updates (partial configuration items)
- Configuration locking (environment variable priority)

## Supported Models

All models have 1M context window capacity. Each credential file provides 1000 request quota.

### 🤖 Base Models
- `gemini-2.5-pro`
- `gemini-3-pro-preview`
- `gemini-3.1-pro-preview`

### 🧠 Thinking Models
- `gemini-2.5-pro-high`: Thinking mode
- `gemini-2.5-pro-low`: Low thinking mode
- Supports custom thinking budget configuration
- Automatic separation of thinking content and final answers

### 🔍 Search-Enhanced Models
- `gemini-2.5-pro-search`: Model with integrated search functionality

### 🖼️ Image Generation Models (Antigravity)
- `gemini-3.1-flash-image`: Base image generation model
- **Resolution Suffixes**:
  - `-2k`: 2K resolution
  - `-4k`: 4K HD resolution
- **Aspect Ratio Suffixes**:
  - `-1x1`: Square (avatar)
  - `-16x9`: Landscape (desktop wallpaper)
  - `-9x16`: Portrait (mobile wallpaper)
  - `-21x9`: Ultra-wide (ultrawide monitor)
  - `-4x3`: Traditional display
  - `-3x4`: Portrait poster
- **Combination Examples**:
  - `gemini-3.1-flash-image-4k-16x9`: 4K landscape
  - `gemini-3.1-flash-image-2k-9x16`: 2K portrait
- When no ratio is specified, the API automatically decides the aspect ratio

### 🌊 Special Feature Variants



- **Anti-truncation Mode**: Add `抗截断/` prefix to model name
  - Example: `抗截断/gemini-2.5-pro`
  - Uses synthetic tool calls to bypass truncation limits and ensure complete answers (works for both streaming and non-streaming)

### 🔧 Automatic Model Feature Detection
- System automatically recognizes feature identifiers in model names
- Transparently handles feature mode transitions
- Supports feature combination usage

---

## Configuration Instructions

1. Visit `http://127.0.0.1:7861` (default port, modifiable via PORT environment variable)
2. Complete OAuth authentication flow (default password: `pwd`, modifiable via environment variables)
3. Configure client:

**OpenAI Compatible Client:**
   - **Endpoint Address**: `http://127.0.0.1:7861/v1`
   - **API Key**: `pwd` (default value, modifiable via API_PASSWORD environment variable)

**Gemini Native Client:**
   - **Endpoint Address**: `http://127.0.0.1:7861`
   - **Authentication Methods**:
     - `Authorization: Bearer your_api_password`
     - `x-goog-api-key: your_api_password`
     - URL parameter: `?key=your_api_password`

## 💾 Data Storage Mode

### 🌟 Storage Backend Support

agy2api supports two storage backends: **Local SQLite (Default)** and **PostgreSQL (Cloud Distributed Storage)**

### 📁 Local SQLite Storage (Default)

**Default Storage Method**
- No configuration required, works out of the box
- Data is stored in a local SQLite database (`creds/credentials.db`)
- Suitable for single-machine deployment and personal use

**Credential Structure**

Credentials are stored as structured database columns (no JSON blobs), keyed uniquely by **email**:
`email`, `access_token`, `refresh_token`, `client_id`, `client_secret`, `project_id`, `expiry`, `tier`

Import/export uses a standardized JSON format: `{"refresh_token": "...", "email": "...", "tier": "...", "project_id": "..."}` (only `refresh_token` is required; the rest are fetched automatically).

### Environment Variable Configuration

**Basic Configuration**
- `PORT`: Service port (default: 7861)
- `HOST`: Server listen address (default: 0.0.0.0)

**Password Configuration**
- `API_PASSWORD`: Chat API access password (default: pwd)
- `PANEL_PASSWORD`: Control panel access password (default: pwd)

**Performance and Stability Configuration**
- `RETRY_429_ENABLED`: Enable 429 error automatic retry (default: true)
- `RETRY_429_MAX_RETRIES`: Maximum retry attempts for 429 errors (default: 5)
- `RETRY_429_INTERVAL`: Retry interval for 429 errors, in seconds (default: 1.0)
- `ANTI_TRUNCATION_MAX_ATTEMPTS`: Maximum retry attempts for anti-truncation (default: 3)

**Network and Proxy Configuration**
- `PROXY`: Proxy address (supports `http://`, `https://`, `socks5://`, `socks5h://`, optionally with credentials `user:pass@host:port`)
- `OAUTH_PROXY_URL`: OAuth authentication proxy endpoint
- `GOOGLEAPIS_PROXY_URL`: Google APIs proxy endpoint

**Automation Configuration**
- `AUTO_BAN`: Enable automatic credential banning (default: false)
- `AUTO_BAN_ERROR_CODES`: Error codes that trigger automatic banning (default: 403)

**Logging Configuration**
- `LOG_LEVEL`: Log level (DEBUG/INFO/WARNING/ERROR, default: INFO)
- `ENABLE_LOG`: Full silence, no log output (set to 0 to enable, default: 1)

**Response Behavior Configuration**
- `RETURN_THOUGHTS_TO_FRONTEND`: Return thinking chain to the frontend (default: true)

**Sensitive Word Filtering**
- `SENSITIVE_WORDS_ENABLED`: Enable sensitive word filtering (default: true)
- `SENSITIVE_WORDS`: Sensitive word list, comma or newline separated (default: empty). Matches are replaced with empty string, case-insensitive, Chinese supported, effective immediately after panel save

**Usage Log**
- `USAGE_LOG_ENABLED`: Enable request logging (default: true). Records every API request (model, credential, token usage, latency, status) to the database
- `USAGE_LOG_RETENTION_DAYS`: Log retention days (default: 30, 0 = keep forever). Expired logs are cleaned up at startup

**Credential Select Mode**
- `CREDENTIAL_SELECT_MODE`: Credential selection mode (default: cache)
  - `random` — Fully random load balancing, each request picks a random credential
  - `cache` — Cache affinity (recommended): hashes "model + system instruction (or first user message)" into a session key; the same session reuses the same credential, hitting Google's upstream prompt cache; automatically switches and rebinds after credential cooldown
  - `sticky` — Sticky: all requests lock onto one credential until it enters cooldown or gets disabled
- `REDIS_URL`: Redis connection string (optional). When set, cache-mode session bindings are stored in Redis for multi-instance sharing; otherwise an in-process memory backend is used

**Storage Configuration**

**SQLite (Default)**
- No configuration required, database file created in `creds/` automatically

**PostgreSQL (Optional Cloud Storage)**
- `POSTGRESQL_URI`: PostgreSQL connection string (enables PostgreSQL mode when set)

**Docker Usage Example**
```bash
docker run -d --name agy2api \
  -e API_PASSWORD=my_api_password \
  -e PANEL_PASSWORD=my_panel_password \
  -e PORT=7861 \
  ghcr.io/su-kaka/agy2api:latest
```

### API Usage Methods

This service supports multiple complete sets of API endpoints:

#### 3. Claude API Format Endpoints

**Endpoint:** `/v1/messages`
**Authentication:** `x-api-key: your_api_password` or `Authorization: Bearer your_api_password`

**Request Example:**
```bash
curl -X POST "http://127.0.0.1:7861/v1/messages" \
  -H "x-api-key: your_api_password" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.5-pro",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello, Claude!"}
    ]
  }'
```

**Support for system parameter:**
```json
{
  "model": "gemini-2.5-pro",
  "max_tokens": 1024,
  "system": "You are a helpful assistant",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

**Notes:**
- Fully compatible with Claude API format specification
- Automatically converts to Gemini format for backend calls
- Supports all Claude standard parameters
- Response format follows Claude API specification

#### 4. Antigravity API Endpoints

**Supports three formats: OpenAI, Gemini, and Claude**

##### Antigravity OpenAI Format Endpoints

**Endpoint:** `/v1/chat/completions`
**Authentication:** `Authorization: Bearer your_api_password`

**Request Example:**
```bash
curl -X POST "http://127.0.0.1:7861/v1/chat/completions" \
  -H "Authorization: Bearer your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "messages": [
      {"role": "user", "content": "Hello"}
    ],
    "stream": true
  }'
```

##### Antigravity Gemini Format Endpoints

**Non-streaming Endpoint:** `/v1/models/{model}:generateContent`
**Streaming Endpoint:** `/v1/models/{model}:streamGenerateContent`

**Authentication Methods (choose one):**
- `Authorization: Bearer your_api_password`
- `x-goog-api-key: your_api_password`
- URL parameter: `?key=your_api_password`

**Request Examples:**
```bash
# Gemini format non-streaming request
curl -X POST "http://127.0.0.1:7861/v1/models/claude-sonnet-4-5:generateContent" \
  -H "x-goog-api-key: your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ],
    "generationConfig": {
      "temperature": 0.7
    }
  }'

# Gemini format streaming request
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-flash:streamGenerateContent?key=your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'
```

##### Antigravity Claude Format Endpoints

**Endpoint:** `/v1/messages`
**Authentication:** `x-api-key: your_api_password`

**Request Example:**
```bash
curl -X POST "http://127.0.0.1:7861/v1/messages" \
  -H "x-api-key: your_api_password" \
  -H "anthropic-version: 2023-06-01" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "Hello"}
    ]
  }'
```

**Supported Antigravity Models:**
- Claude series: `claude-sonnet-4-5`, `claude-opus-4-5`, etc.
- Gemini series: `gemini-2.5-flash`, `gemini-2.5-pro`, etc.
- Automatically supports thinking models

**Gemini Native Example:**
```python
from io import BytesIO
from PIL import Image
from google.genai import Client
from google.genai.types import HttpOptions
from google.genai import types
# The client gets the API key from the environment variable `GEMINI_API_KEY`.

client = Client(
            api_key="pwd",
            http_options=HttpOptions(base_url="http://127.0.0.1:7861"),
        )

prompt = (
    """
    Draw a cat
    """
)

response = client.models.generate_content(
    model="gemini-3.1-flash-image",
    contents=[prompt],
    config=types.GenerateContentConfig(
        image_config=types.ImageConfig(
            aspect_ratio="16:9",
        )
    )
)
for part in response.candidates[0].content.parts:
    if part.text is not None:
        print(part.text)
    elif part.inline_data is not None:
        image = Image.open(BytesIO(part.inline_data.data))
        image.save("generated_image.png")

```

**Notes:**
- OpenAI endpoints return OpenAI-compatible format
- Gemini endpoints return Gemini native format
- Both endpoints use the same API password

## 📋 Complete API Reference

### Web Console API

**Authentication Endpoints**
- `POST /auth/login` - User login
- `POST /auth/start` - Start Antigravity OAuth authentication
- `POST /auth/callback` - Handle OAuth callback
- `POST /auth/callback-url` - Complete authentication directly from callback URL
- `GET /auth/status/{project_id}` - Check authentication status

**Credential Management Endpoints**
- `POST /creds/upload` - Batch upload credential files (supports JSON and ZIP)
- `GET /creds/status` - Get credential status list (supports pagination and filtering)
- `GET /creds/detail/{email}` - Get single credential details
- `POST /creds/action` - Single credential operation (enable/disable/delete)
- `POST /creds/batch-action` - Batch credential operations
- `GET /creds/export/{email}` - Export single credential as standardized JSON
- `GET /creds/download-all` - Package download all credentials (standardized JSON, one file per email)
- `GET /creds/quota/{email}` - Get credential quota information

**Configuration Management Endpoints**
- `GET /config/get` - Get current configuration
- `POST /config/save` - Save configuration

**Version Information Endpoints**
- `GET /version/info` - Get version information (optional `check_update=true` parameter to check for updates)

### Chat API Features

**Multimodal Support**
```json
{
  "model": "gemini-2.5-pro",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this image"},
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

**Thinking Mode Support**
```json
{
  "model": "gemini-2.5-pro-high",
  "messages": [
    {"role": "user", "content": "Complex math problem"}
  ]
}
```

Response will include separated thinking content:
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Final answer",
      "reasoning_content": "Detailed thought process..."
    }
  }]
}
```

**Anti-truncation Usage**
```json
{
  "model": "抗截断/gemini-2.5-pro",
  "messages": [
    {"role": "user", "content": "Write a long article"}
  ],
  "stream": true
}
```

---

## 🛠️ Frontend Development

The panel frontend source lives in `frontend/` (React + Vite + Tailwind CSS):

```bash
cd frontend
npm install
npm run dev     # Development mode, port 3000, proxies backend API
npm run build    # Build to dist/ (used in production)
```

The backend serves the compiled static files under `dist/` directly; no Node.js runtime required.

## 💬 Community

Welcome to join the QQ group for discussion!

**QQ Group: 1083250744**

<img src="qq-group.jpg" width="200" alt="QQ Group QR Code">

---

## License and Disclaimer

This project is for learning and research purposes only. Using this project indicates that you agree to:
- Not use this project for any commercial purposes
- Bear all risks and responsibilities of using this project
- Comply with relevant terms of service and legal regulations

The project authors are not responsible for any direct or indirect losses arising from the use of this project.

## ⚠️ License Declaration

**This project is licensed under the Cooperative Non-Commercial License (CNC-1.0)**

This is a strict anti-commercial open source license. Please refer to the [LICENSE](../LICENSE) file for details.

### ✅ Permitted Uses:
- Personal learning, research, and educational purposes
- Non-profit organization use
- Open source project integration (must comply with the same license)
- Academic research and publication

### ❌ Prohibited Uses:
- Any form of commercial use
- Enterprise use with annual revenue exceeding $1 million
- Venture capital-backed or publicly traded companies
- Providing paid services or products
- Commercial competitive use
