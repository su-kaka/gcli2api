# Quick Reference Guide

Quick reference for common gcli2api commands and operations.

## Installation

```bash
# Automated setup
./setup-dev.sh

# Manual setup
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
```

## Development Commands

```bash
make help              # Show all available commands
make install           # Install production dependencies
make install-dev       # Install development dependencies
make test              # Run tests
make test-cov          # Run tests with coverage
make lint              # Run linters
make format            # Format code
make format-check      # Check formatting without changes
make clean             # Clean cache and artifacts
make run               # Run the application
```

## Docker Commands

```bash
make docker-build           # Build Docker image
make docker-run             # Run Docker container
make docker-compose-up      # Start with docker-compose
make docker-compose-down    # Stop docker-compose services
```

## Testing

```bash
# Run all tests
python -m pytest -v

# Run specific test file
python test_tool_calling.py
python -m pytest test_tool_calling.py -v

# Run with coverage
python -m pytest --cov=src --cov-report=html

# Run tests matching pattern
python -m pytest -k "test_convert" -v
```

## Code Quality

```bash
# Format code
black src/ web.py config.py log.py test_*.py --line-length=100

# Check formatting
black --check src/ web.py --line-length=100

# Lint code
flake8 src/ web.py config.py log.py --max-line-length=100

# Type check
mypy src/ --ignore-missing-imports

# Sort imports
isort src/ web.py config.py --profile=black --line-length=100
```

## Git Workflow

```bash
# Create feature branch
git checkout -b feature/my-feature

# Stage changes
git add .

# Commit with descriptive message
git commit -m "feat: add new feature"

# Push to remote
git push origin feature/my-feature
```

## Running the Application

```bash
# Basic run
python web.py

# With custom port
PORT=8080 python web.py

# With debug logging
LOG_LEVEL=debug python web.py

# Using make
make run
```

## API Testing

```bash
# Test OpenAI endpoint
curl -X POST http://localhost:7861/v1/chat/completions \
  -H "Authorization: Bearer pwd" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-2.5-pro","messages":[{"role":"user","content":"Hello"}]}'

# Test Gemini endpoint
curl -X POST http://localhost:7861/v1/models/gemini-2.5-pro:generateContent \
  -H "x-goog-api-key: pwd" \
  -d '{"contents":[{"role":"user","parts":[{"text":"Hello"}]}]}'

# List models
curl http://localhost:7861/v1/models -H "Authorization: Bearer pwd"

# Test streaming
curl -X POST http://localhost:7861/v1/chat/completions \
  -H "Authorization: Bearer pwd" \
  -H "Content-Type: application/json" \
  -d '{"model":"gemini-2.5-pro","messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

## Configuration

```bash
# Copy example config
cp .env.example .env

# Edit configuration
nano .env  # or vim, code, etc.

# Common environment variables
export PORT=7861
export PASSWORD=your_password
export LOG_LEVEL=info
export REDIS_URI=redis://localhost:6379
```

## Docker Operations

```bash
# Build custom image
docker build -t gcli2api:dev .

# Run with environment variables
docker run -d --name gcli2api \
  -e PASSWORD=mypassword \
  -e PORT=7861 \
  -p 7861:7861 \
  -v $(pwd)/data/creds:/app/creds \
  gcli2api:dev

# View logs
docker logs -f gcli2api

# Stop container
docker stop gcli2api

# Remove container
docker rm gcli2api

# Start with docker-compose
docker-compose up -d

# View docker-compose logs
docker-compose logs -f

# Stop docker-compose
docker-compose down
```

## Debugging

```bash
# View logs
tail -f log.txt

# Clear logs
> log.txt

# Run with debug logging
LOG_LEVEL=debug python web.py

# Python debugger
python -m pdb web.py

# Interactive Python shell
python -i web.py
```

## Security Checks

```bash
# Check dependency vulnerabilities
safety check -r requirements.txt

# Security analysis with Bandit
bandit -r src/ -ll

# Check for secrets in code
git secrets --scan
```

## Pre-commit Hooks

```bash
# Install hooks
pre-commit install

# Run manually on all files
pre-commit run --all-files

# Update hook versions
pre-commit autoupdate

# Skip hooks for a commit (not recommended)
git commit --no-verify -m "message"
```

## Credential Management

```bash
# Check credential files
ls -la creds/

# Set permissions
chmod 644 creds/*.json

# Load from environment (if configured)
export AUTO_LOAD_ENV_CREDS=true
export GCLI_CREDS_1='{"client_id":"...","project_id":"..."}'
```

## Common Paths

```bash
# Configuration
.env                      # Environment variables
creds/                    # Credential files
pyproject.toml            # Project metadata

# Logs
log.txt                   # Application logs

# Source code
src/                      # Main source directory
web.py                    # Application entry point
config.py                 # Configuration loader

# Tests
tests/                    # Organized tests
test_*.py                 # Root-level tests

# Documentation
README.md                 # Main documentation
CONTRIBUTING.md           # Contribution guide
SECURITY.md              # Security policy
DEVELOPMENT.md           # Development guide
```

## Useful Links

- Repository: https://github.com/su-kaka/gcli2api
- Issues: https://github.com/su-kaka/gcli2api/issues
- Docker Image: https://github.com/su-kaka/gcli2api/pkgs/container/gcli2api

## Environment Variable Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 7861 | Server port |
| `HOST` | 0.0.0.0 | Server host |
| `PASSWORD` | pwd | Common password |
| `API_PASSWORD` | pwd | API access password |
| `PANEL_PASSWORD` | pwd | Panel access password |
| `LOG_LEVEL` | info | Logging level |
| `LOG_FILE` | log.txt | Log file path |
| `REDIS_URI` | - | Redis connection string |
| `MONGODB_URI` | - | MongoDB connection string |
| `POSTGRES_DSN` | - | PostgreSQL connection string |
| `PROXY` | - | HTTP/HTTPS proxy |
| `AUTO_BAN` | false | Auto-ban on errors |
| `CALLS_PER_ROTATION` | 100 | Calls before credential rotation |

## Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| Port already in use | Change `PORT` in .env |
| Import errors in tests | Run `pip install -e .` |
| Credentials not loading | Check file permissions in `creds/` |
| Test failures | Run `pip install -r requirements-dev.txt` |
| Docker build fails | Check `.dockerignore` and Dockerfile |
| Format conflicts | Run `make format` then `make lint` |
| Module not found | Activate virtual environment |

## Tips

- Always activate virtual environment: `source venv/bin/activate`
- Use `make help` to see all available commands
- Run tests before committing: `make test`
- Format code automatically: `make format`
- Check logs for errors: `tail -f log.txt`
- Use pre-commit hooks to catch issues early
- Keep dependencies updated but test thoroughly
- Document your changes in CHANGELOG.md
