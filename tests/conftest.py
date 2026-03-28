"""
Shared pytest fixtures for TD5-Dash test suite.

Adds backend/ to sys.path so tests can import backend modules directly
(backend/ is not a Python package — it has no __init__.py).
"""

import sys
import os

# Insert backend/ onto the import path so `from obd.decoder import ...` works,
# as well as `from ws_hub import ...` and `from mock_service import ...`.
_BACKEND = os.path.join(os.path.dirname(__file__), os.pardir, "backend")
sys.path.insert(0, os.path.abspath(_BACKEND))

import pytest
from ws_hub import ConnectionManager


@pytest.fixture
def manager():
    """Fresh ConnectionManager instance for each test (works for both sync and async tests)."""
    return ConnectionManager()
