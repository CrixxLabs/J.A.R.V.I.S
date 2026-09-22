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
def setup_test_environment(tmp_path):
    """Set up test environment for each test."""
    os.environ.setdefault("TESTING", "1")
    import status_registry
    previous_registry = status_registry._registry_instance
    status_registry._registry_instance = status_registry.RuntimeStatus(
        tmp_path / "status.json", session_id="pytest", pid=os.getpid()
    )
    _reset_provider_state_if_loaded()
    yield
    _reset_provider_state_if_loaded()
    status_registry._registry_instance = previous_registry


def _reset_provider_state_if_loaded():
    """Keep unit tests independent from provider calls made by earlier tests."""
    brain = sys.modules.get("brain")
    if brain is None:
        return
    brain.provider_health.reset()
    brain.reset_ollama_runtime_state()
    brain.reset_last_provider()


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
