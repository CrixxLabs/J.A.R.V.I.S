"""
Pytest configuration for JARVIS tests.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


def pytest_configure(config):
    """Configure pytest."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "windows: mark test as Windows-specific"
    )
    # Set asyncio fixture loop scope
    config.option.asyncio_default_fixture_loop_scope = "function"


@pytest.fixture(autouse=True)
def setup_test_environment():
    """Set up test environment for each test."""
    # Set up any global test state
    os.environ.setdefault("TESTING", "1")
    yield
    # Cleanup after each test


@pytest.fixture
def temp_memory_file():
    """Provide a temporary memory file for testing."""
    import tempfile
    temp_dir = tempfile.mkdtemp()
    memory_file = os.path.join(temp_dir, "memory.json")
    yield memory_file
    # Cleanup
    if os.path.exists(memory_file):
        os.remove(memory_file)
    os.rmdir(temp_dir)


@pytest.fixture
def mock_speak():
    """Mock speak function for testing."""
    from unittest.mock import Mock
    return Mock()


@pytest.fixture
def mock_ask():
    """Mock ask function for testing."""
    from unittest.mock import Mock
    mock = Mock()
    mock.return_value = (None, "test response")
    return mock