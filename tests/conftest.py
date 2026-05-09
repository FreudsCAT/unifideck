"""Shared test fixtures for the Unifideck backend test suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tests.helpers import MockConfig

# Ensure py_modules is on sys.path for all tests
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_PY_MODULES = _PROJECT_ROOT / "py_modules"
if str(_PY_MODULES) not in sys.path:
    sys.path.insert(0, str(_PY_MODULES))


@pytest.fixture
def mock_config() -> MockConfig:
    """Return a simple dict-backed config stub."""
    return MockConfig()
