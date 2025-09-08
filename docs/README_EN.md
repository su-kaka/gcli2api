# GeminiCLI to API

**Convert GeminiCLI to OpenAI and GEMINI API interfaces**

[中文](../README.md) | English

## 🚀 Quick Deploy

[![Deploy on Zeabur](https://zeabur.com/button.svg)](https://zeabur.com/templates/2QLQC2?referralCode=su-kaka)
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

---

## Core Features

### 🔄 API Endpoints and Format Support

**Multi-endpoint Dual Format Support**
- **OpenAI Compatible Endpoints**: `/v1/chat/completions` and `/v1/models`
  - Supports standard OpenAI format (messages structure)
  - Supports Gemini native format (contents structure)
  - Automatic format detection and conversion, no manual switching required
  - Supports multimodal input (text + images)
- **Gemini Native Endpoints**: `/v1/models/{model}:generateContent` and `streamGenerateContent`
  - Supports complete Gemini native API specifications
  - Multiple authentication methods: Bearer Token, x-goog-api-key header, URL parameter key

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
- Credential rotation strategy (based on call count)
- Usage statistics and quota monitoring

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
- OAuth authentication flow management
- Credential file upload, download, and management
- Real-time log viewing (WebSocket)
- System configuration management
- Usage statistics and monitoring dashboard
- Mobile-friendly interface

**Batch Operation Support**
- ZIP file batch credential upload
- Batch enable/disable/delete credentials
- Batch user email retrieval
- Batch configuration management

### 📈 Usage Statistics and Monitoring

**Detailed Usage Statistics**
- Call count statistics by credential file
- Gemini 2.5 Pro model specific statistics
- Daily quota management (UTC+7 reset)
- Aggregated statistics and analysis
- Custom daily limit configuration

**Real-time Monitoring**
- WebSocket real-time log streams
- System status monitoring
- Credential health status
- API call success rate statistics

### 🔧 Advanced Configuration and Customization

**Network and Proxy Configuration**
- HTTP/HTTPS proxy support
- Proxy endpoint configuration (OAuth, Google APIs, metadata service)
- Timeout and retry configuration
- Network error handling and recovery

**Performance and Stability Configuration**
- 429 error automatic retry (configurable interval and attempts)
- Anti-truncation maximum retry attempts
- Credential rotation strategy
- Concurrent request management

**Logging and Debugging**
- Multi-level logging system (DEBUG, INFO, WARNING, ERROR)
- Log file management
- Real-time log streams
- Log download and clearing

### 🔄 Environment Variables and Configuration Management

**Flexible Configuration Methods**
- TOML configuration file support
- Environment variable configuration
- Hot configuration updates (partial configuration items)
- Configuration locking (environment variable priority)

**Environment Variable Credential Support**
- `GCLI_CREDS_*` format environment variable import
- Automatic loading of environment variable credentials
- Base64 encoded credential support
- Docker container friendly

## Supported Models

All models have 1M context window capacity. Each credential file provides 1000 request quota.

### 🤖 Base Models
- `gemini-2.5-pro`
- `gemini-2.5-pro-preview-06-05`  
- `gemini-2.5-pro-preview-05-06`

### 🧠 Thinking Models
- `gemini-2.5-pro-maxthinking`: Maximum thinking budget mode
- `gemini-2.5-pro-nothinking`: No thinking mode
- Supports custom thinking budget configuration
- Automatic separation of thinking content and final answers

### 🔍 Search-Enhanced Models
- `gemini-2.5-pro-search`: Model with integrated search functionality

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

### Docker Environment

**Docker Run Command**
```bash
# Using universal password
docker run -d --name gcli2api --network host -e PASSWORD=pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest

# Using separate passwords
docker run -d --name gcli2api --network host -e API_PASSWORD=api_pwd -e PANEL_PASSWORD=panel_pwd -e PORT=7861 -v $(pwd)/data/creds:/app/creds ghcr.io/su-kaka/gcli2api:latest
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

## ⚠️ Important Notes

- The current OAuth authentication process **only supports localhost access**, meaning authentication must be completed through `http://127.0.0.1:7861/auth` (default port 7861, modifiable via PORT environment variable).
- **For deployment on cloud servers or other remote environments, please first run the service locally and complete OAuth authentication to obtain the generated json credential files (located in the `./geminicli/creds` directory), then upload these files via the auth panel.**
- **Please strictly comply with usage restrictions, only for personal learning and non-commercial purposes**

---

## Configuration Instructions

1. Visit `http://127.0.0.1:7861/auth` (default port, modifiable via PORT environment variable)
2. Complete OAuth authentication flow (default password: `pwd`, modifiable via environment variables)
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

## 💾 Distributed Storage Mode

### 🌟 Storage Backend Priority

gcli2api supports multiple storage backends, automatically selecting by priority: **Redis > Postgres > MongoDB > Local Files**

### ⚡ Redis Distributed Storage Mode

### ⚙️ Enable Redis Mode

**Step 1: Configure Redis Connection**
```bash
# Local Redis
export REDIS_URI="redis://localhost:6379"

# Redis with password
export REDIS_URI="redis://:password@localhost:6379"

# SSL connection (recommended for production)
export REDIS_URI="rediss://default:password@host:6380"

# Upstash Redis (free cloud service)
export REDIS_URI="rediss://default:token@your-host.upstash.io:6379"

# Optional: Custom database index (default: 0)
export REDIS_DATABASE="1"
```

**Step 2: Start Application**
```bash
# Application will automatically detect Redis configuration and prioritize Redis storage
python web.py
```

### 🐘 Postgres Distributed Storage Mode

If Redis is not configured, or you prefer a relational database, gcli2api also supports Postgres (it is checked after Redis and before MongoDB).

⚙️ Enable Postgres Mode

Step 1: Configure Postgres DSN
```bash
# Example DSN:
export POSTGRES_DSN="postgresql://user:password@localhost:5432/gcli2api"
```

Step 2: Start Application
```bash
# Application will detect POSTGRES_DSN and use Postgres when Redis is not available
python web.py
```

### 🍃 MongoDB Distributed Storage Mode

### 🌟 Alternative Storage Solution

If Redis is not configured, gcli2api will attempt to use **MongoDB storage mode**.

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

### 🛠️ Troubleshooting

**Common Issue Solutions**

```bash
# Check MongoDB connection
python mongodb_setup.py check

# View detailed status information
python mongodb_setup.py status

# Verify data migration results
python -c "
import asyncio
from src.storage_adapter import get_storage_adapter

async def test():
    storage = await get_storage_adapter()
    info = await storage.get_backend_info()
    print(f'Current mode: {info[\"backend_type\"]}')
    if info['backend_type'] == 'mongodb':
        print(f'Database: {info.get(\"database_name\", \"Unknown\")}')

asyncio.run(test())
"
```

**Migration Failure Handling**
```bash
# If migration is interrupted, re-run
python mongodb_setup.py migrate

# To rollback to file mode, remove MONGODB_URI environment variable
unset MONGODB_URI
# Then export data from MongoDB
python mongodb_setup.py export
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

## 🏗️ Technical Architecture

### Core Module Description

**Authentication and Credential Management** (`src/auth.py`, `src/credential_manager.py`)
- OAuth 2.0 authentication flow management
- Multi-credential file status management and rotation
- Automatic failure detection and recovery
- JWT token generation and validation

**API Routing and Conversion** (`src/openai_router.py`, `src/gemini_router.py`, `src/openai_transfer.py`)
- OpenAI and Gemini format bidirectional conversion
- Multimodal input processing (text+images)
- Thinking chain content separation and processing
- Streaming response management

**Network and Proxy** (`src/httpx_client.py`, `src/google_chat_api.py`)
- Unified HTTP client management
- Proxy configuration and hot update support
- Timeout and retry strategies
- Asynchronous request pool management

**State Management** (`src/state_manager.py`, `src/usage_stats.py`)
- Atomic state operations
- Usage statistics and quota management
- File locking and concurrency safety
- Data persistence (TOML format)

**Task Management** (`src/task_manager.py`)
- Global asynchronous task lifecycle management
- Resource cleanup and memory management
- Graceful shutdown and exception handling

**Web Console** (`src/web_routes.py`)
- RESTful API endpoints
- WebSocket real-time communication
- Mobile device adaptation detection
- Batch operation support

### Advanced Feature Implementation

**Streaming Anti-truncation Mechanism** (`src/anti_truncation.py`)
- Response truncation pattern detection
- Automatic retry and state recovery
- Context connection management

**Format Detection and Conversion** (`src/format_detector.py`)
- Automatic request format detection (OpenAI vs Gemini)
- Seamless format conversion
- Parameter mapping and validation

**User Agent Simulation** (`src/utils.py`)
- GeminiCLI format user agent generation
- Platform detection and client metadata
- API compatibility guarantee

### Environment Variable Configuration

**Basic Configuration**
- `PORT`: Service port (default: 7861)
- `HOST`: Server listen address (default: 0.0.0.0)

**Password Configuration**
- `API_PASSWORD`: Chat API access password (default: inherits PASSWORD or pwd)
- `PANEL_PASSWORD`: Control panel access password (default: inherits PASSWORD or pwd)  
- `PASSWORD`: Universal password, overrides the above two when set (default: pwd)

**Performance and Stability Configuration**
- `CALLS_PER_ROTATION`: Number of calls before each credential rotation (default: 10)
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
- `LOG_FILE`: Log file path (default: gcli2api.log)

**Storage Configuration (by priority)**

**Redis Configuration (Highest Priority)**
- `REDIS_URI`: Redis connection string (enables Redis mode when set)
  - Local: `redis://localhost:6379`
  - With password: `redis://:password@host:6379`
  - SSL: `rediss://default:password@host:6380`
- `REDIS_DATABASE`: Redis database index (0-15, default: 0)

**MongoDB Configuration (Second Priority)**
- `MONGODB_URI`: MongoDB connection string (enables MongoDB mode when set)
- `MONGODB_DATABASE`: MongoDB database name (default: gcli2api)

**Credential Configuration**

Support importing multiple credentials using `GCLI_CREDS_*` environment variables:

#### Credential Environment Variable Usage Examples

**Method 1: Numbered Format**
```bash
export GCLI_CREDS_1='{"client_id":"your-client-id","client_secret":"your-secret","refresh_token":"your-token","token_uri":"https://oauth2.googleapis.com/token","project_id":"your-project"}'
export GCLI_CREDS_2='{"client_id":"...","project_id":"..."}'
```

**Method 2: Project Name Format**
```bash
export GCLI_CREDS_myproject='{"client_id":"...","project_id":"myproject",...}'
export GCLI_CREDS_project2='{"client_id":"...","project_id":"project2",...}'
```

**Enable Automatic Loading**
```bash
export AUTO_LOAD_ENV_CREDS=true  # Automatically import environment variable credentials at program startup
```

**Docker Usage Example**
```bash
# Using universal password
docker run -d --name gcli2api \
  -e PASSWORD=mypassword \
  -e PORT=8080 \
  -e GOOGLE_CREDENTIALS="$(cat credential.json | base64 -w 0)" \
  ghcr.io/su-kaka/gcli2api:latest

# Using separate passwords
docker run -d --name gcli2api \
  -e API_PASSWORD=my_api_password \
  -e PANEL_PASSWORD=my_panel_password \
  -e PORT=8080 \
  -e GOOGLE_CREDENTIALS="$(cat credential.json | base64 -w 0)" \
  ghcr.io/su-kaka/gcli2api:latest
```

Note: When credential environment variables are set, the system will prioritize using credentials from environment variables and ignore files in the `creds` directory.

### API Usage Methods

This service supports two complete sets of API endpoints:

#### 1. OpenAI Compatible Endpoints

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

#### 2. Gemini Native Endpoints

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

**Notes:**
- OpenAI endpoints return OpenAI-compatible format
- Gemini endpoints return Gemini native format
- Both endpoints use the same API password

## 📋 Complete API Reference

### Web Console API

**Authentication Endpoints**
- `POST /auth/login` - User login
- `POST /auth/start` - Start OAuth authentication
- `POST /auth/callback` - Handle OAuth callback
- `GET /auth/status/{project_id}` - Check authentication status

**Credential Management Endpoints**
- `GET /creds/status` - Get all credential statuses
- `POST /creds/action` - Single credential operation (enable/disable/delete)
- `POST /creds/batch-action` - Batch credential operations
- `POST /auth/upload` - Batch upload credential files (supports ZIP)
- `GET /creds/download/{filename}` - Download credential file
- `GET /creds/download-all` - Package download all credentials
- `POST /creds/fetch-email/{filename}` - Get user email
- `POST /creds/refresh-all-emails` - Batch refresh user emails

**Configuration Management Endpoints**
- `GET /config/get` - Get current configuration
- `POST /config/save` - Save configuration

**Environment Variable Credential Endpoints**
- `POST /auth/load-env-creds` - Load environment variable credentials
- `DELETE /auth/env-creds` - Clear environment variable credentials
- `GET /auth/env-creds-status` - Get environment variable credential status

**Log Management Endpoints**
- `POST /auth/logs/clear` - Clear logs
- `GET /auth/logs/download` - Download log file
- `WebSocket /auth/logs/stream` - Real-time log stream

**Usage Statistics Endpoints**
- `GET /usage/stats` - Get usage statistics
- `GET /usage/aggregated` - Get aggregated statistics
- `POST /usage/update-limits` - Update usage limits
- `POST /usage/reset` - Reset usage statistics

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
  "model": "gemini-2.5-pro-maxthinking",
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

## Troubleshooting

**400 Error Solutions**
```bash
npx https://github.com/google-gemini/gemini-cli
```
1. Select option 1
2. Press Enter to confirm
3. Complete Google account authentication in browser
4. System will automatically complete authorization

---

## Support the Project

If this project has been helpful to you, we welcome your support for the project's continued development!

For detailed donation information, please see: [📖 Donation Documentation](DONATE.md)

---

## License and Disclaimer

This project is for learning and research purposes only. Using this project indicates that you agree to:
- Not use this project for any commercial purposes
- Bear all risks and responsibilities of using this project
- Comply with relevant terms of service and legal regulations

The project authors are not responsible for any direct or indirect losses arising from the use of this project.
