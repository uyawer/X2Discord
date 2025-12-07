from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the repository root is on sys.path so tests can import `app.*`.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# pytest-asyncio configuration
pytest_plugins = ('pytest_asyncio',)

