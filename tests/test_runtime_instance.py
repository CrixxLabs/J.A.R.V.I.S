import pytest

from runtime_instance import DuplicateRuntimeError, RuntimeInstanceGuard


def test_duplicate_runtime_guard_fails_clearly_and_releases():
    first = RuntimeInstanceGuard(name="Local\\JARVIS_MARK_VII_TEST_RUNTIME", fallback_port=18764)
    second = RuntimeInstanceGuard(name="Local\\JARVIS_MARK_VII_TEST_RUNTIME", fallback_port=18764)
    first.acquire()
    try:
        with pytest.raises(DuplicateRuntimeError):
            second.acquire()
    finally:
        first.release()

    replacement = RuntimeInstanceGuard(name="Local\\JARVIS_MARK_VII_TEST_RUNTIME", fallback_port=18764)
    replacement.acquire()
    replacement.release()
