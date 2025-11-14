.PHONY: help install install-dev test lint format clean run docker-build docker-run docker-compose-up docker-compose-down

help:
	@echo "gcli2api - Development Commands"
	@echo ""
	@echo "Available commands:"
	@echo "  make install            - Install production dependencies"
	@echo "  make install-dev        - Install development dependencies"
	@echo "  make test               - Run tests"
	@echo "  make test-cov           - Run tests with coverage report"
	@echo "  make lint               - Run linters (flake8, mypy)"
	@echo "  make format             - Format code with black"
	@echo "  make format-check       - Check code formatting without making changes"
	@echo "  make clean              - Clean build artifacts and cache"
	@echo "  make run                - Run the application"
	@echo "  make docker-build       - Build Docker image"
	@echo "  make docker-run         - Run Docker container"
	@echo "  make docker-compose-up  - Start services with docker-compose"
	@echo "  make docker-compose-down - Stop services with docker-compose"

install:
	pip install -r requirements.txt

install-dev:
	pip install -e ".[dev]"
	pip install -r requirements-dev.txt

test:
	python -m pytest -v

test-cov:
	python -m pytest --cov=src --cov-report=term-missing --cov-report=html

lint:
	python -m flake8 src/ web.py config.py log.py --max-line-length=100 --extend-ignore=E203,W503
	python -m mypy src/ --ignore-missing-imports

format:
	python -m black src/ web.py config.py log.py test_*.py

format-check:
	python -m black --check src/ web.py config.py log.py test_*.py

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.log" -delete
	rm -rf .pytest_cache .mypy_cache .coverage htmlcov/ build/ dist/ *.egg-info

run:
	python web.py

docker-build:
	docker build -t gcli2api:latest .

docker-run:
	docker run -d --name gcli2api --network host -e PASSWORD=pwd -e PORT=7861 -v $$(pwd)/data/creds:/app/creds gcli2api:latest

docker-compose-up:
	docker-compose up -d

docker-compose-down:
	docker-compose down
