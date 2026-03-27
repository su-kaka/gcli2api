# GeminiCLI to API

**Convert GeminiCLI and Antigravity to OpenAI, GEMINI, and Claude API Compatible Interfaces**

[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: CNC-1.0](https://img.shields.io/badge/License-CNC--1.0-red.svg)](../LICENSE)
[![Docker](https://img.shields.io/badge/docker-available-blue.svg)](https://github.com/su-kaka/gcli2api/pkgs/container/gcli2api)

[中文](../README.md) | English | [日本語](./README_JA.md)

## 🚀 Quick Deploy

[![Deploy on Zeabur](https://zeabur.com/button.svg)](https://zeabur.com/templates/97VMEF?referralCode=sukaka)
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/su-kaka/gcli2api)
---

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
  - OpenAI format endpoint: `/antigravity/v1/chat/completions`
  - Gemini format endpoint: `/antigravity/v1/models/{model}:generateContent` and `streamGenerateContent`
  - Claude format endpoint: `/antigravity/v1/messages`
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
- Support for manual enable/disable credential files
- Batch credential file operations (enable, disable, delete)

**Credential Status Monitoring**
- Real-time credential health checks
- Error code tracking (429, 403, 500, etc.)
- Automatic banning mechanism (configurable)

### 🌊 Streaming and Response Processing

**Multiple Streaming Support**
- True real-time streaming responses
- Fake streaming mode (for compatibility)
- Streaming anti-truncation feature (prevents answer truncation)
- Asynchronous task management and timeout handling

**Response Optimization**
- Thinking chain content separation
- Reasoning process (reasoning_content) handling
- Multi-turn conversation context management
- Compatibility mode (converts system messages to user messages)

### 🎛️ Web Management Console

**Full-featured Web Interface**
- OAuth authentication flow management (supports GCLI and Antigravity dual modes)
- Credential file upload, download, and management
- Real-time log viewing (WebSocket)
- System configuration management
- Usage statistics and monitoring dashboard
- Mobile-friendly interface

**Batch Operation Support**
- ZIP file batch credential upload (GCLI and Antigravity)
- Batch enable/disable/delete credentials
- Batch user email retrieval
- Batch configuration management
- Unified batch upload interface for all credential types

### 📈 Usage Monitoring

**Real-time Monitoring**
- WebSocket real-time log streams
- System status monitoring
- Credential health status

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
- Log file management
- Real-time log streams
- Log download and clearing

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
- **Fake Streaming Mode**: Add `-假流式` suffix to any model name
  - Example: `gemini-2.5-pro-假流式`
  - For scenarios requiring streaming responses but server doesn't support true streaming
- **Streaming Anti-truncation Mode**: Add `流式抗截断/` prefix to model name
  - Example: `流式抗截断/gemini-2.5-pro`
  - Automatically detects response truncation and retries to ensure complete answers

### 🔧 Automatic Model Feature Detection
- System automatically recognizes feature identifiers in model names
- Transparently handles feature mode transitions
- Supports feature combination usage


---

## Installation Guide

### Termux Environment

**Initial Installation**
```bash
curl -o termux-install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/termux-install.sh" && chmod +x termux-install.sh && ./termux-install.sh
```

**Restart Service**
```bash
cd gcli2api
bash termux-start.sh
```

### Windows Environment

**Initial Installation**
```powershell
iex (iwr "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/install.ps1" -UseBasicParsing).Content
```

**Restart Service**
Double-click to execute `start.bat`

### Linux Environment

**Initial Installation**
```bash
curl -o install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/install.sh" && chmod +x install.sh && ./install.sh
```

**Restart Service**
```bash
cd gcli2api
bash start.sh
```

### macOS Environment

**Initial Installation**
```bash
curl -o darwin-install.sh "https://raw.githubusercontent.com/su-kaka/gcli2api/refs/heads/master/darwin-install.sh" && chmod +x darwin-install.sh && ./darwin-install.sh
```

**Restart Service**
```bash
cd gcli2api
bash start.sh
```

### Docker Environment

**Docker Run Command**
```bash
# Using universal password
docker run -d --name gcli2api --network host -e PASSWORD=pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest

# Using separate passwords
docker run -d --name gcli2api --network host -e API_PASSWORD=api_pwd -e PANEL_PASSWORD=panel_pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest
```

**Docker Mac**
```bash
# Using universal password
docker run -d \
  --name gcli2api \
  -p 7861:7861 \
  -p 8080:8080 \
  -e PASSWORD=pwd \
  -e PORT=7861 \
  -v "$(pwd)/data/creds":/app/creds \
  ghcr.io/su-kaka/gcli2api:latest
```

```bash
# Using separate passwords
docker run -d \
--name gcli2api \
-p 7861:7861 \
-p 8080:8080 \
-e API_PASSWORD=api_pwd \
-e PANEL_PASSWORD=panel_pwd \
-e PORT=7861 \
-v $(pwd)/data/creds:/app/creds \
ghcr.io/su-kaka/gcli2api:latest
```

**Docker Compose Run Command**
1. Save the following content as `docker-compose.yml` file:
    ```yaml
    version: '3.8'

    services:
      gcli2api:
        image: ghcr.io/su-kaka/gcli2api:latest
        container_name: gcli2api
        restart: unless-stopped
        network_mode: host
        environment:
          # Using universal password (recommended for simple deployment)
          - PASSWORD=pwd
          - PORT=7861
          # Or use separate passwords (recommended for production)
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
2. Start the service:
    ```bash
    docker-compose up -d
    ```

---

## Configuration Instructions

1. Visit `http://127.0.0.1:7861` (default port, modifiable via PORT environment variable)
2. Complete OAuth authentication flow (default password: `pwd`, modifiable via environment variables)
   - **GCLI Mode**: For obtaining Google Cloud Gemini API credentials
   - **Antigravity Mode**: For obtaining Google Antigravity API credentials
3. Configure client:

**OpenAI Compatible Client:**
   - **Endpoint Address**: `http://127.0.0.1:7861/v1`
   - **API Key**: `pwd` (default value, modifiable via API_PASSWORD or PASSWORD environment variables)

**Gemini Native Client:**
   - **Endpoint Address**: `http://127.0.0.1:7861`
   - **Authentication Methods**:
     - `Authorization: Bearer your_api_password`
     - `x-goog-api-key: your_api_password`
     - URL parameter: `?key=your_api_password`

### 🌟 Dual Authentication Mode Support

**GCLI Authentication Mode**
- Standard Google Cloud Gemini API authentication
- Supports OAuth2.0 authentication flow
- Automatically enables required Google Cloud APIs

**Antigravity Authentication Mode**
- Dedicated authentication for Google Antigravity API
- Independent credential management system
- Supports batch upload and management
- Completely isolated from GCLI credentials

**Unified Management Interface**
- Manage both credential types in the "Batch Upload" tab
- Upper section: GCLI credential batch upload (blue theme)
- Lower section: Antigravity credential batch upload (green theme)
- Separate credential management tabs for each type

## 💾 Data Storage Mode

### 🌟 Storage Backend Support

gcli2api supports two storage backends: **Local SQLite (Default)** and **MongoDB (Cloud Distributed Storage)**

### 📁 Local SQLite Storage (Default)

**Default Storage Method**
- No configuration required, works out of the box
- Data is stored in a local SQLite database
- Suitable for single-machine deployment and personal use
- Automatically creates and manages database files

### 🍃 MongoDB Cloud Storage Mode

**Cloud Distributed Storage Solution**

When multi-instance deployment or cloud storage is needed, MongoDB storage mode can be enabled.

### ⚙️ Enable MongoDB Mode

**Step 1: Configure MongoDB Connection**
```bash
# Local MongoDB
export MONGODB_URI="mongodb://localhost:27017"

# MongoDB Atlas cloud service
export MONGODB_URI="mongodb+srv://username:password@cluster.mongodb.net"

# MongoDB with authentication
export MONGODB_URI="mongodb://admin:password@localhost:27017/admin"

# Optional: Custom database name (default: gcli2api)
export MONGODB_DATABASE="my_gcli_db"
```

**Step 2: Start Application**
```bash
# Application will automatically detect MongoDB configuration and use MongoDB storage
python web.py
```

**Docker Environment using MongoDB**
```bash
# Single MongoDB deployment
docker run -d --name gcli2api \
  -e MONGODB_URI="mongodb://mongodb:27017" \
  -e API_PASSWORD=your_password \
  --network your_network \
  ghcr.io/su-kaka/gcli2api:latest

# Using MongoDB Atlas
docker run -d --name gcli2api \
  -e MONGODB_URI="mongodb+srv://user:pass@cluster.mongodb.net/gcli2api" \
  -e API_PASSWORD=your_password \
  -p 7861:7861 \
  ghcr.io/su-kaka/gcli2api:latest
```

**Docker Compose Example**
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


### 🔧 Advanced Configuration

**MongoDB Connection Optimization**
```bash
# Connection pool and timeout configuration
export MONGODB_URI="mongodb://localhost:27017?maxPoolSize=10&serverSelectionTimeoutMS=5000"

# Replica set configuration
export MONGODB_URI="mongodb://host1:27017,host2:27017,host3:27017/gcli2api?replicaSet=myReplicaSet"

# Read-write separation configuration
export MONGODB_URI="mongodb://localhost:27017/gcli2api?readPreference=secondaryPreferred"
```

### Environment Variable Configuration

**Basic Configuration**
- `PORT`: Service port (default: 7861)
- `HOST`: Server listen address (default: 0.0.0.0)

**Password Configuration**
- `API_PASSWORD`: Chat API access password (default: inherits PASSWORD or pwd)
- `PANEL_PASSWORD`: Control panel access password (default: inherits PASSWORD or pwd)
- `PASSWORD`: Universal password, overrides the above two when set (default: pwd)

**Performance and Stability Configuration**
- `RETRY_429_ENABLED`: Enable 429 error automatic retry (default: true)
- `RETRY_429_MAX_RETRIES`: Maximum retry attempts for 429 errors (default: 3)
- `RETRY_429_INTERVAL`: Retry interval for 429 errors, in seconds (default: 1.0)
- `ANTI_TRUNCATION_MAX_ATTEMPTS`: Maximum retry attempts for anti-truncation (default: 3)

**Network and Proxy Configuration**
- `PROXY`: HTTP/HTTPS proxy address (format: `http://host:port`)
- `OAUTH_PROXY_URL`: OAuth authentication proxy endpoint
- `GOOGLEAPIS_PROXY_URL`: Google APIs proxy endpoint
- `METADATA_SERVICE_URL`: Metadata service proxy endpoint

**Automation Configuration**
- `AUTO_BAN`: Enable automatic credential banning (default: true)
- `AUTO_LOAD_ENV_CREDS`: Automatically load environment variable credentials at startup (default: false)

**Compatibility Configuration**
- `COMPATIBILITY_MODE`: Enable compatibility mode, converts system messages to user messages (default: false)

**Logging Configuration**
- `LOG_LEVEL`: Log level (DEBUG/INFO/WARNING/ERROR, default: INFO)
- `LOG_FILE`: Log file path (default: log.txt)

**Storage Configuration**

**SQLite Configuration (Default)**
- No configuration required, automatically uses local SQLite database
- Database files are automatically created in the project directory

**MongoDB Configuration (Optional Cloud Storage)**
- `MONGODB_URI`: MongoDB connection string (enables MongoDB mode when set)
- `MONGODB_DATABASE`: MongoDB database name (default: gcli2api)

**Docker Usage Example**
```bash
# Using universal password
docker run -d --name gcli2api \
  -e PASSWORD=mypassword \
  -e PORT=7861 \
  ghcr.io/su-kaka/gcli2api:latest

# Using separate passwords
docker run -d --name gcli2api \
  -e API_PASSWORD=my_api_password \
  -e PANEL_PASSWORD=my_panel_password \
  -e PORT=7861 \
  ghcr.io/su-kaka/gcli2api:latest
```

Note: When credential environment variables are set, the system will prioritize using credentials from environment variables and ignore files in the `creds` directory.

### API Usage Methods

This service supports multiple complete sets of API endpoints:

#### 1. OpenAI Compatible Endpoints (GCLI)

**Endpoint:** `/v1/chat/completions`
**Authentication:** `Authorization: Bearer your_api_password`

Supports two request formats with automatic detection and processing:

**OpenAI Format:**
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

**Gemini Native Format:**
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

#### 2. Gemini Native Endpoints (GCLI)

**Non-streaming Endpoint:** `/v1/models/{model}:generateContent`
**Streaming Endpoint:** `/v1/models/{model}:streamGenerateContent`
**Model List:** `/v1/models`

**Authentication Methods (choose one):**
- `Authorization: Bearer your_api_password`
- `x-goog-api-key: your_api_password`
- URL parameter: `?key=your_api_password`

**Request Examples:**
```bash
# Using x-goog-api-key header
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-pro:generateContent" \
  -H "x-goog-api-key: your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'

# Using URL parameter
curl -X POST "http://127.0.0.1:7861/v1/models/gemini-2.5-pro:streamGenerateContent?key=your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'
```

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

**Endpoint:** `/antigravity/v1/chat/completions`
**Authentication:** `Authorization: Bearer your_api_password`

**Request Example:**
```bash
curl -X POST "http://127.0.0.1:7861/antigravity/v1/chat/completions" \
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

**Non-streaming Endpoint:** `/antigravity/v1/models/{model}:generateContent`
**Streaming Endpoint:** `/antigravity/v1/models/{model}:streamGenerateContent`

**Authentication Methods (choose one):**
- `Authorization: Bearer your_api_password`
- `x-goog-api-key: your_api_password`
- URL parameter: `?key=your_api_password`

**Request Examples:**
```bash
# Gemini format non-streaming request
curl -X POST "http://127.0.0.1:7861/antigravity/v1/models/claude-sonnet-4-5:generateContent" \
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
curl -X POST "http://127.0.0.1:7861/antigravity/v1/models/gemini-2.5-flash:streamGenerateContent?key=your_api_password" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [
      {"role": "user", "parts": [{"text": "Hello"}]}
    ]
  }'
```

##### Antigravity Claude Format Endpoints

**Endpoint:** `/antigravity/v1/messages`
**Authentication:** `x-api-key: your_api_password`

**Request Example:**
```bash
curl -X POST "http://127.0.0.1:7861/antigravity/v1/messages" \
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
- `POST /auth/start` - Start OAuth authentication (supports GCLI and Antigravity modes)
- `POST /auth/callback` - Handle OAuth callback
- `POST /auth/callback-url` - Complete authentication directly from callback URL
- `GET /auth/status/{project_id}` - Check authentication status

**Credential Management Endpoints** (supports `mode=geminicli` or `mode=antigravity` parameter)
- `POST /creds/upload` - Batch upload credential files (supports JSON and ZIP)
- `GET /creds/status` - Get credential status list (supports pagination and filtering)
- `GET /creds/detail/{filename}` - Get single credential details
- `POST /creds/action` - Single credential operation (enable/disable/delete)
- `POST /creds/batch-action` - Batch credential operations
- `GET /creds/download/{filename}` - Download single credential file
- `GET /creds/download-all` - Package download all credentials
- `POST /creds/fetch-email/{filename}` - Get user email
- `POST /creds/refresh-all-emails` - Batch refresh user emails
- `POST /creds/deduplicate-by-email` - Deduplicate credentials by email
- `POST /creds/verify-project/{filename}` - Verify credential Project ID
- `GET /creds/quota/{filename}` - Get credential quota information (Antigravity only)

**Configuration Management Endpoints**
- `GET /config/get` - Get current configuration
- `POST /config/save` - Save configuration

**Log Management Endpoints**
- `POST /logs/clear` - Clear logs
- `GET /logs/download` - Download log file
- `WebSocket /logs/stream` - Real-time log stream

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

**Streaming Anti-truncation Usage**
```json
{
  "model": "流式抗截断/gemini-2.5-pro",
  "messages": [
    {"role": "user", "content": "Write a long article"}
  ],
  "stream": true
}
```

**Compatibility Mode**
```bash
# Enable compatibility mode
export COMPATIBILITY_MODE=true
```
In this mode, all `system` messages are converted to `user` messages, improving compatibility with certain clients.

---

## 💬 Community

Welcome to join the QQ group for discussion!

**QQ Group: 937681997**

<img src="qq群.jpg" width="200" alt="QQ Group QR Code">

---

## License and Disclaimer

This project is for learning and research purposes only. Using this project indicates that you agree to:
- Not use this project for any commercial purposes
- Bear all risks and responsibilities of using this project
- Comply with relevant terms of service and legal regulations

The project authors are not responsible for any direct or indirect losses arising from the use of this project.
