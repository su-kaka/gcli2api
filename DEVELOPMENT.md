# Development Guide

This guide provides comprehensive information for developers working on gcli2api.

## Table of Contents

- [Quick Start](#quick-start)
- [Project Architecture](#project-architecture)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Code Style](#code-style)
- [Debugging](#debugging)
- [Common Tasks](#common-tasks)

## Quick Start

### Prerequisites

- Python 3.12 or higher
- pip or uv package manager
- Git

### Setting Up Development Environment

```bash
# Clone the repository
git clone https://github.com/su-kaka/gcli2api.git
cd gcli2api

# Run the automated setup script
./setup-dev.sh

# Or manually:
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env with your configuration
```

### Running the Application

```bash
# Activate virtual environment if not already active
source venv/bin/activate

# Run the application
python web.py

# Or using make
make run
```

The application will start on `http://127.0.0.1:7861` by default.

## Project Architecture

### Directory Structure

```
gcli2api/
├── src/                          # Main source code
│   ├── auth.py                   # OAuth authentication and user management
│   ├── credential_manager.py    # Credential rotation and health monitoring
│   ├── openai_router.py          # OpenAI-compatible API endpoints
│   ├── gemini_router.py          # Gemini native API endpoints
│   ├── openai_transfer.py        # Format conversion (OpenAI ↔ Gemini)
│   ├── format_detector.py        # Auto-detect request format
│   ├── google_chat_api.py        # Google AI API client
│   ├── google_oauth_api.py       # Google OAuth API client
│   ├── httpx_client.py           # HTTP client with proxy support
│   ├── models.py                 # Pydantic data models
│   ├── state_manager.py          # Application state management
│   ├── usage_stats.py            # Usage tracking and quota management
│   ├── task_manager.py           # Async task lifecycle management
│   ├── utils.py                  # Utility functions
│   ├── web_routes.py             # Web console API endpoints
│   ├── anti_truncation.py        # Stream anti-truncation logic
│   ├── storage_adapter.py        # Storage backend abstraction
│   └── storage/                  # Storage implementations
│       ├── file_storage_manager.py   # Local file storage
│       ├── redis_manager.py          # Redis distributed storage
│       ├── mongodb_manager.py        # MongoDB distributed storage
│       ├── postgres_manager.py       # PostgreSQL storage
│       └── cache_manager.py          # In-memory cache
├── front/                        # Frontend static files
├── tests/                        # Test files (organized)
├── web.py                        # Main application entry point
├── config.py                     # Configuration management
├── log.py                        # Logging configuration
├── test_*.py                     # Test files (root level)
├── requirements.txt              # Production dependencies
├── requirements-dev.txt          # Development dependencies
└── pyproject.toml                # Project metadata and tool config
```

### Core Components

#### 1. API Routers

**OpenAI Router** (`src/openai_router.py`)
- Handles `/v1/chat/completions` endpoint
- Supports both OpenAI and Gemini request formats
- Auto-detects format and converts as needed
- Returns OpenAI-compatible responses

**Gemini Router** (`src/gemini_router.py`)
- Handles native Gemini endpoints
- `/v1/models/{model}:generateContent` (non-streaming)
- `/v1/models/{model}:streamGenerateContent` (streaming)
- Returns Gemini native format responses

#### 2. Authentication System

**Auth Module** (`src/auth.py`)
- OAuth 2.0 flow management
- JWT token generation and validation
- User email retrieval
- Credential file upload/download

**Credential Manager** (`src/credential_manager.py`)
- Multiple credential rotation
- Health monitoring and auto-ban
- Usage tracking per credential
- Background worker for state persistence

#### 3. Format Conversion

**OpenAI Transfer** (`src/openai_transfer.py`)
- Converts OpenAI messages to Gemini contents
- Converts Gemini responses to OpenAI format
- Handles tool calling conversion
- Manages thinking/reasoning content separation

**Format Detector** (`src/format_detector.py`)
- Auto-detects request format
- Determines if request is OpenAI or Gemini format
- Enables seamless dual-format support

#### 4. Storage Backends

The application supports multiple storage backends with automatic priority selection:

**Priority Order:** Redis > Postgres > MongoDB > File Storage

**Redis Manager** (`src/storage/redis_manager.py`)
- Distributed storage using Redis
- Fastest performance
- Best for cloud deployments

**PostgreSQL Manager** (`src/storage/postgres_manager.py`)
- Relational database storage
- Good for complex queries
- ACID compliance

**MongoDB Manager** (`src/storage/mongodb_manager.py`)
- Document-based storage
- Flexible schema
- Good for JSON-heavy data

**File Storage Manager** (`src/storage/file_storage_manager.py`)
- Local file system storage
- Default fallback option
- Best for single-instance deployments

#### 5. Special Features

**Anti-Truncation** (`src/anti_truncation.py`)
- Detects response truncation
- Automatically retries with continuation
- Ensures complete responses

**Task Manager** (`src/task_manager.py`)
- Global async task tracking
- Graceful shutdown handling
- Resource cleanup

## Development Workflow

### 1. Making Changes

```bash
# Create a new branch
git checkout -b feature/my-feature

# Make your changes
# ...

# Format code
make format

# Run linters
make lint

# Run tests
make test
```

### 2. Pre-commit Hooks

Install pre-commit hooks to automatically check your code before committing:

```bash
pre-commit install
```

This will run:
- Code formatting checks (black, isort)
- Linting (flake8)
- YAML/JSON validation
- Large file detection
- Private key detection

### 3. Commit Guidelines

Follow these commit message conventions:

```
<type>: <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc.)
- `refactor`: Code refactoring
- `test`: Adding or updating tests
- `chore`: Maintenance tasks

## Testing

### Running Tests

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run specific test file
python -m pytest test_tool_calling.py -v

# Run tests matching a pattern
python -m pytest -k "test_convert" -v
```

### Writing Tests

Tests should be placed in the `tests/` directory or as `test_*.py` files in the root.

Example test structure:

```python
import pytest
from src.openai_transfer import convert_openai_tools_to_gemini

def test_convert_tools():
    """Test tool conversion from OpenAI to Gemini format"""
    openai_tools = [...]
    result = convert_openai_tools_to_gemini(openai_tools)
    assert len(result) == 1
    assert "functionDeclarations" in result[0]
```

### Test Coverage

Aim for at least 70% test coverage. Check coverage report:

```bash
make test-cov
# Open htmlcov/index.html in browser
```

## Code Style

### Python Style Guide

We follow PEP 8 with some modifications:

- **Line length**: 100 characters (not 79)
- **Imports**: Sorted with isort, grouped by standard library, third-party, local
- **Formatting**: Automated with black
- **Type hints**: Encouraged but not required

### Formatting Your Code

```bash
# Format all code
make format

# Check formatting without changes
make format-check
```

### Linting

```bash
# Run all linters
make lint

# Individual linters
flake8 src/
mypy src/ --ignore-missing-imports
```

### Import Sorting

Imports should be organized as:

```python
# Standard library
import asyncio
import json
from typing import Optional

# Third-party
import httpx
from fastapi import FastAPI
from pydantic import BaseModel

# Local
from src.models import ChatCompletionRequest
from src.utils import get_user_agent
```

## Debugging

### Local Debugging

Set log level to DEBUG in `.env`:

```bash
LOG_LEVEL=debug
```

### Using Debugger

```python
# Add breakpoint
import pdb; pdb.set_trace()

# Or use built-in breakpoint()
breakpoint()
```

### Viewing Logs

Logs are written to `log.txt` by default. Watch logs in real-time:

```bash
tail -f log.txt
```

### Testing with curl

```bash
# Test OpenAI endpoint
curl -X POST http://localhost:7861/v1/chat/completions \
  -H "Authorization: Bearer pwd" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gemini-2.5-pro",
    "messages": [{"role": "user", "content": "Hello"}]
  }'

# Test Gemini endpoint
curl -X POST http://localhost:7861/v1/models/gemini-2.5-pro:generateContent \
  -H "x-goog-api-key: pwd" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"role": "user", "parts": [{"text": "Hello"}]}]
  }'
```

## Common Tasks

### Adding a New Storage Backend

1. Create new file in `src/storage/`
2. Implement the storage interface
3. Add to `src/storage_adapter.py` priority list
4. Update documentation

### Adding a New Model

Models are configured dynamically. No code changes needed for basic models.

For models with special features:
1. Add detection logic in request processing
2. Update format conversion if needed
3. Document in README

### Updating Dependencies

```bash
# Update requirements.txt
pip install --upgrade package_name
pip freeze > requirements.txt

# Test changes
make test

# Verify security
safety check -r requirements.txt
```

### Running in Docker

```bash
# Build image
make docker-build

# Run container
make docker-run

# Or use docker-compose
make docker-compose-up

# View logs
docker logs -f gcli2api

# Stop services
make docker-compose-down
```

### Environment Variables

Key environment variables for development:

```bash
# Server
PORT=7861
HOST=0.0.0.0

# Authentication
PASSWORD=pwd
API_PASSWORD=your_api_password
PANEL_PASSWORD=your_panel_password

# Logging
LOG_LEVEL=debug
LOG_FILE=log.txt

# Storage (optional)
REDIS_URI=redis://localhost:6379
MONGODB_URI=mongodb://localhost:27017
POSTGRES_DSN=postgresql://user:pass@localhost:5432/gcli2api

# Development
PYTHONUNBUFFERED=1
PYTHONDONTWRITEBYTECODE=1
```

## Troubleshooting

### Common Issues

**Issue: Tests failing with import errors**
```bash
# Solution: Install package in development mode
pip install -e .
```

**Issue: Black formatting conflicts with flake8**
```bash
# Solution: Use our config which ignores conflicting rules
make format
make lint
```

**Issue: Port already in use**
```bash
# Solution: Change port in .env
PORT=7862
```

**Issue: Credential files not loading**
```bash
# Solution: Check directory and permissions
ls -la creds/
# Ensure files are readable
chmod 644 creds/*.json
```

## Additional Resources

- [Contributing Guide](CONTRIBUTING.md)
- [Security Policy](SECURITY.md)
- [Changelog](CHANGELOG.md)
- [README](README.md)
- [English README](docs/README_EN.md)

## Getting Help

- **Issues**: Open a GitHub issue
- **Discussions**: Use GitHub Discussions
- **Documentation**: Check README files

## License

This project is licensed under CNC-1.0. See [LICENSE](LICENSE) for details.

Remember: This project is for educational and research purposes only. No commercial use permitted.
