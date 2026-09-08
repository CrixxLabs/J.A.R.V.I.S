"""
Test suite for JARVIS lifecycle module.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import threading
import time

import lifecycle


class TestLifecycle:
    """Test lifecycle manager."""

    def test_register_and_start_component(self):
        """Test registering and starting a component."""
        lm = lifecycle.LifecycleManager()
        
        start_mock = Mock()
        stop_mock = Mock()
        
        lm.register("test_comp", start_mock, stop_mock, priority=10)
        success = lm.start_all()
        
        assert success is True
        start_mock.assert_called_once()
        assert lm.is_started() is True

    def test_component_failure_stops_startup(self):
        """Test that component failure stops startup."""
        lm = lifecycle.LifecycleManager()
        
        def fail_start():
            raise RuntimeError("Start failed")
        
        lm.register("fail_comp", fail_start, Mock(), priority=10)
        success = lm.start_all()
        
        assert success is False

    def test_shutdown_stops_components_in_reverse_order(self):
        """Test shutdown stops components in reverse priority order.
        
        Lower priority number = higher priority = started earlier, stopped LATER.
        Higher priority number = lower priority = started later, stopped EARLIER.
        """
        lm = lifecycle.LifecycleManager()
        
        order = []
        
        def make_start(name):
            def start():
                order.append(f"start_{name}")
            return start
        
        def make_stop(name):
            def stop():
                order.append(f"stop_{name}")
            return stop
        
        lm.register("low_priority", make_start("low"), make_stop("low"), priority=50)
        lm.register("high_priority", make_start("high"), make_stop("high"), priority=10)
        
        lm.start_all()
        lm.shutdown("Test")
        
        # Check shutdown order: low_priority (priority=50) stopped first,
        # then high_priority (priority=10) stopped last
        stop_events = [e for e in order if e.startswith("stop_")]
        assert stop_events == ["stop_low", "stop_high"]

    def test_shutdown_idempotent(self):
        """Test that multiple shutdown calls are safe."""
        lm = lifecycle.LifecycleManager()
        
        start_mock = Mock()
        stop_mock = Mock()
        lm.register("test", start_mock, stop_mock)
        lm.start_all()
        
        lm.shutdown("First")
        lm.shutdown("Second")  # Should not error
        
        # stop should only be called once
        assert stop_mock.call_count == 1

    def test_is_shutting_down(self):
        """Test is_shutting_down flag."""
        lm = lifecycle.LifecycleManager()
        
        assert lm.is_shutting_down() is False
        
        lm.register("test", Mock(), Mock())
        lm.start_all()
        lm.shutdown("Test")
        
        assert lm.is_shutting_down() is True

    def test_wait_for_shutdown(self):
        """Test wait_for_shutdown blocks until shutdown."""
        lm = lifecycle.LifecycleManager()
        
        lm.register("test", Mock(), Mock())
        lm.start_all()
        
        def trigger_shutdown():
            time.sleep(0.1)
            lm.shutdown("Triggered")
        
        thread = threading.Thread(target=trigger_shutdown)
        thread.start()
        
        lm.wait_for_shutdown(timeout=1.0)
        
        assert lm.is_shutting_down() is True
        thread.join()


class TestLifecycleConvenience:
    """Test convenience functions."""

    def test_get_lifecycle_singleton(self):
        """Test get_lifecycle returns singleton."""
        lm1 = lifecycle.get_lifecycle()
        lm2 = lifecycle.get_lifecycle()
        assert lm1 is lm2

    def test_register_component(self):
        """Test register_component convenience function."""
        lifecycle._lifecycle = None  # Reset singleton for test
        
        lifecycle.register_component("test", Mock(), Mock(), priority=10)
        lm = lifecycle.get_lifecycle()
        
        assert len(lm._components) == 1

    def test_start_all_shutdown(self):
        """Test start_all and shutdown convenience functions."""
        lifecycle._lifecycle = None  # Reset
        
        lifecycle.register_component("test", Mock(), Mock())
        success = lifecycle.start_all()
        assert success is True
        
        lifecycle.shutdown("Test")


class TestManagedLifecycle:
    """Test managed_lifecycle context manager."""

    def test_context_manager(self):
        """Test managed_lifecycle context manager."""
        with lifecycle.managed_lifecycle() as lm:
            lm.register("test", Mock(), Mock())
            assert lm.is_started() is True
        
        # Should have shutdown automatically


if __name__ == "__main__":
    pytest.main([__file__, "-v"])