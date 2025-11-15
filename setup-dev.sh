#!/bin/bash
# Development setup script for gcli2api
# This script sets up the development environment

set -e

echo "=========================================="
echo "gcli2api Development Setup"
echo "=========================================="
echo

# Check Python version
echo "Checking Python version..."
python_version=$(python --version 2>&1 | awk '{print $2}')
required_version="3.12"

if ! python -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)"; then
    echo "❌ Error: Python 3.12 or higher is required. Found: $python_version"
    exit 1
fi
echo "✅ Python $python_version"
echo

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python -m venv venv
    echo "✅ Virtual environment created"
else
    echo "✅ Virtual environment already exists"
fi
echo

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate
echo "✅ Virtual environment activated"
echo

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip -q
echo "✅ pip upgraded"
echo

# Install production dependencies
echo "Installing production dependencies..."
pip install -r requirements.txt -q
echo "✅ Production dependencies installed"
echo

# Install development dependencies
echo "Installing development dependencies..."
pip install -r requirements-dev.txt -q
echo "✅ Development dependencies installed"
echo

# Copy .env.example to .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env file from .env.example..."
    cp .env.example .env
    echo "✅ .env file created"
    echo "⚠️  Please edit .env file with your configuration"
else
    echo "✅ .env file already exists"
fi
echo

# Install pre-commit hooks
echo "Installing pre-commit hooks..."
pre-commit install
echo "✅ Pre-commit hooks installed"
echo

echo "=========================================="
echo "✅ Development setup complete!"
echo "=========================================="
echo
echo "Next steps:"
echo "  1. Edit .env with your configuration"
echo "  2. Run 'make test' to verify setup"
echo "  3. Run 'make run' to start the application"
echo
echo "Available commands:"
echo "  make help      - Show all available commands"
echo "  make test      - Run tests"
echo "  make lint      - Run linters"
echo "  make format    - Format code"
echo "  make run       - Run the application"
echo
echo "To activate the virtual environment in the future:"
echo "  source venv/bin/activate"
echo
