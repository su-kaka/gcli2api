# Contributing to agy2api

First off, thank you for considering contributing to agy2api! It's people like you that make agy2api such a great tool.

## Code of Conduct

This project is intended for personal learning and research purposes only. By participating, you are expected to uphold this code and respect the CNC-1.0 license restrictions on commercial use.

## How Can I Contribute?

### Reporting Bugs

Before creating bug reports, please check the existing issues to avoid duplicates. When you create a bug report, include as many details as possible:

* **Use a clear and descriptive title**
* **Describe the exact steps to reproduce the problem**
* **Provide specific examples** - Include code snippets, configuration files, or log outputs
* **Describe the behavior you observed** and what you expected to see
* **Include environment details**: OS, Python version, Docker version (if applicable)

### Suggesting Enhancements

Enhancement suggestions are tracked as GitHub issues. When creating an enhancement suggestion, include:

* **Use a clear and descriptive title**
* **Provide a detailed description** of the suggested enhancement
* **Explain why this enhancement would be useful**
* **List any alternative solutions** you've considered

### Pull Requests

1. Fork the repo and create your branch from `master`
2. If you've added code that should be tested, add tests
3. If you've changed APIs, update the documentation
4. Ensure the test suite passes
5. Make sure your code follows the existing style
6. Write a clear commit message

## Development Setup

### Prerequisites

* Python 3.12 or higher
* pip or uv package manager

### Setting Up Your Development Environment

```bash
# Clone your fork
git clone https://github.com/YOUR_USERNAME/agy2api.git
cd agy2api

# Install development dependencies
make install-dev
# or
pip install -e ".[dev]"

# Copy environment example
cp .env.example .env
# Edit .env with your configuration
```

### Development Workflow

```bash
# Run tests
make test

# Format code
make format

# Run linters
make lint

# Run the application locally
make run
```

### Testing

We use pytest for testing. All new features should include appropriate tests.

```bash
# Run all tests
make test

# Run with coverage
make test-cov

# Run specific test file
python -m pytest test_tool_calling.py -v
```

### Code Style

* We use [Black](https://black.readthedocs.io/) for code formatting (line length: 100)
* We use [flake8](https://flake8.pycqa.org/) for linting
* We use [mypy](http://mypy-lang.org/) for type checking (optional, but encouraged)

```bash
# Format your code before committing
make format

# Check if code is properly formatted
make format-check

# Run linters
make lint
```

## Project Structure

```
agy2api/
├── src/                    # Main source code
│   ├── auth.py            # Authentication and OAuth
│   ├── credential_manager.py  # Credential rotation
│   ├── openai_router.py   # OpenAI-compatible endpoints
│   ├── gemini_router.py   # Gemini native endpoints
│   ├── openai_transfer.py # Format conversion
│   ├── storage/           # Storage backends (Redis, MongoDB, Postgres, File)
│   └── ...
├── front/                 # Frontend static files
├── tests/                 # Test directory (to be created)
├── test_*.py             # Test files (root level)
├── web.py                # Main application entry point
├── config.py             # Configuration management
└── requirements.txt      # Production dependencies
```

## Coding Guidelines

### Python Style

* Follow PEP 8 guidelines
* Use type hints where appropriate
* Write docstrings for classes and functions
* Keep functions focused and concise

### Commit Messages

* Use the present tense ("Add feature" not "Added feature")
* Use the imperative mood ("Move cursor to..." not "Moves cursor to...")
* Limit the first line to 72 characters or less
* Reference issues and pull requests liberally after the first line

### Documentation

* Update the README.md if you change functionality
* Comment your code where necessary
* Update the .env.example if you add new configuration options

## License

By contributing to agy2api, you agree that your contributions will be licensed under the CNC-1.0 license. This is a strict anti-commercial license - see [LICENSE](LICENSE) for details.

### Important License Restrictions

* ❌ No commercial use
* ❌ No use by companies with revenue > $1M USD
* ❌ No use by VC-backed or publicly traded companies
* ✅ Personal learning, research, and educational use only
* ✅ Open source integration (must follow same license)

## Questions?

Feel free to open an issue with your question or reach out to the maintainers.

Thank you for contributing! 🎉
