# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Development dependencies configuration in pyproject.toml
- Makefile for common development tasks
- CONTRIBUTING.md with contribution guidelines
- CHANGELOG.md for tracking project changes
- pytest configuration for automated testing
- Code formatting tools (black, flake8, mypy)
- Test coverage reporting configuration

### Changed
- Enhanced pyproject.toml with complete project metadata
- Updated requirements.txt with version constraints

### Fixed
- Missing pypinyin dependency in requirements.txt

## [0.1.0] - Previous Release

### Core Features
- OpenAI-compatible API endpoints (`/v1/chat/completions`, `/v1/models`)
- Gemini native API endpoints support
- Multi-format support (OpenAI and Gemini formats with auto-detection)
- Multiple OAuth credential rotation system
- Web management console with JWT authentication
- Real-time streaming responses with anti-truncation support
- Distributed storage support (Redis, Postgres, MongoDB, File)
- Tool calling implementation (function calling)
- Multi-turn conversation support
- Thinking models support (reasoning content separation)
- Search-enhanced models
- Usage statistics and quota management
- Automatic credential health monitoring
- 429 error retry mechanism
- Proxy support for network requests

### Authentication & Security
- Separate password support (API password vs Panel password)
- JWT token authentication
- Multiple authentication methods (Bearer, x-goog-api-key, URL params)
- Automatic credential ban mechanism
- User email retrieval

### Platform Support
- Docker and Docker Compose deployment
- Linux installation script
- macOS installation script  
- Windows installation script
- Termux environment support
- Multiple deployment platforms (Zeabur, Render, etc.)

### Documentation
- Comprehensive README in Chinese and English
- API documentation with examples
- Environment variable configuration guide
- Docker deployment guide
- Multiple platform installation guides

[Unreleased]: https://github.com/su-kaka/gcli2api/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/su-kaka/gcli2api/releases/tag/v0.1.0
