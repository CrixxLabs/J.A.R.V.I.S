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

    def test_startup_failure_cleans_started_components_and_skips_remaining(self):
        lm = lifecycle.LifecycleManager()
        events = []

        lm.register("first", lambda: events.append("start_first"),
                    lambda: events.append("stop_first"), priority=10)

        def fail():
            events.append("start_failure")
            raise RuntimeError("boom")

        lm.register("failure", fail, lambda: events.append("stop_failure"), priority=20)
        lm.register("later", lambda: events.append("start_later"),
                    lambda: events.append("stop_later"), priority=30)

        assert lm.start_all() is False
        assert events == ["start_first", "start_failure", "stop_first"]

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

    def test_duplicate_start_does_not_start_components_twice(self):
        lm = lifecycle.LifecycleManager()
        start_mock = Mock()
        lm.register("test", start_mock, Mock())
        assert lm.start_all() is True
        assert lm.start_all() is True
        start_mock.assert_called_once()
        lm.shutdown("test complete")

    def test_externally_owned_component_is_not_stopped(self):
        lm = lifecycle.LifecycleManager()
        stop_mock = Mock()
        lm.register("external", Mock(), stop_mock, owned=False, already_started=True)
        assert lm.start_all() is True
        lm.shutdown("UI detached")
        stop_mock.assert_not_called()

    def test_shutdown_is_bounded_and_continues_after_slow_stop(self):
        lm = lifecycle.LifecycleManager(component_stop_timeout=0.05, total_shutdown_timeout=0.2)
        stopped = []
        blocker = threading.Event()
        lm.register("first", Mock(), lambda: stopped.append("first"), priority=10)
        lm.register("slow", Mock(), lambda: blocker.wait(2), priority=20)
        lm.start_all()
        started = time.monotonic()
        clean = lm.shutdown("bounded test")
        elapsed = time.monotonic() - started
        blocker.set()
        assert clean is False
        assert elapsed < 0.5
        assert stopped == ["first"]

    def test_shutdown_signals_worker(self):
        lm = lifecycle.LifecycleManager()
        stop_event = threading.Event()
        worker = threading.Thread(target=stop_event.wait)

        def start_worker():
            worker.start()

        def stop_worker():
            stop_event.set()
            worker.join(timeout=1)

        lm.register("worker", start_worker, stop_worker)
        assert lm.start_all() is True
        assert worker.is_alive()
        assert lm.shutdown("worker test") is True
        assert not worker.is_alive()

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
