"""
Pytest configuration and fixtures for gcli2api tests.
"""
import sys
from pathlib import Path

# Add the parent directory to sys.path to allow importing from src
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
