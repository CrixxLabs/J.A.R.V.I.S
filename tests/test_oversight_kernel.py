"""Tests for Module AJ: Oversight, Provenance & Reversibility Layer."""
import time
import pytest
from oversight_kernel import (
    OversightKernel,
    TIER_FULL_AUTONOMY,
    TIER_HUMAN_IN_THE_LOOP,
    TIER_READ_ONLY,
    TIER_SUPERVISED,
    check_deadman_switch,
    detect_rubber_stamp,
    get_oversight_kernel,
    record_audit_event,
    update_spc_metric,
    verify_chain_integrity,
)


class TestOversightKernel:
    def test_audit_chain_integrity(self, tmp_path):
        db_path = tmp_path / "test_audit.db"
        kernel = OversightKernel(db_path=db_path)

        # Record multiple events
        e1 = kernel.record_event("SYS_INIT", {"version": "8.0.0"})
        e2 = kernel.record_event("ACTION_EXEC", {"tool": "file_write", "target": "main.py"})
        e3 = kernel.record_event("USER_INTERACT", {"msg": "hello"})

        assert e1.event_id == 1
        assert e2.event_id == 2
        assert e3.event_id == 3
        assert e2.prev_hash == e1.current_hash
        assert e3.prev_hash == e2.current_hash

        valid, err = kernel.verify_chain_integrity()
        assert valid is True
        assert err is None

    def test_audit_chain_tamper_detection(self, tmp_path):
        db_path = tmp_path / "test_tamper.db"
        kernel = OversightKernel(db_path=db_path)

        kernel.record_event("E1", {"a": 1})
        kernel.record_event("E2", {"b": 2})

        # Tamper with database row
        conn = kernel._get_connection()
        with conn:
            conn.execute("UPDATE audit_chain SET payload_json = '{\"tampered\": true}' WHERE event_id = 1")
        conn.close()

        valid, err = kernel.verify_chain_integrity()
        assert valid is False
        assert "Corrupted hash" in err

    def test_spc_cusum_drift_and_auto_demotion(self, tmp_path):
        db_path = tmp_path / "test_spc.db"
        kernel = OversightKernel(db_path=db_path)
        assert kernel.current_autonomy_tier == TIER_FULL_AUTONOMY

        # Baseline observations (normal)
        for _ in range(10):
            kernel.update_spc_metric("file_mutations", 2.0, target_mean=2.0, target_std=0.5, h=4.0)

        assert kernel.current_autonomy_tier == TIER_FULL_AUTONOMY

        # Single spike to trigger CUSUM breach
        res = kernel.update_spc_metric("file_mutations", 10.0, target_mean=2.0, target_std=0.5, h=4.0)

        assert res.out_of_control is True
        assert kernel.current_autonomy_tier == TIER_SUPERVISED

        # Further spikes demote further
        kernel.update_spc_metric("file_mutations", 10.0, target_mean=2.0, target_std=0.5, h=4.0)
        assert kernel.current_autonomy_tier == TIER_HUMAN_IN_THE_LOOP

    def test_deadman_switch(self, tmp_path):
        db_path = tmp_path / "test_deadman.db"
        kernel = OversightKernel(db_path=db_path, deadman_timeout_sec=10.0)

        now = time.time()
        kernel.last_user_checkin_ts = now

        # Within timeout
        triggered, tier = kernel.check_deadman_switch(current_ts=now + 5.0)
        assert triggered is False
        assert tier == TIER_FULL_AUTONOMY

        # Lapsed check-in
        triggered, tier = kernel.check_deadman_switch(current_ts=now + 20.0)
        assert triggered is True
        assert tier == TIER_READ_ONLY
        assert kernel.current_autonomy_tier == TIER_READ_ONLY

    def test_rubber_stamp_defense(self, tmp_path):
        db_path = tmp_path / "test_rubber.db"
        kernel = OversightKernel(db_path=db_path)

        long_proposal = " ".join(["execute_high_risk_migration_plan"] * 100) # 100 words
        # 100 words @ 300 wpm = 20 seconds minimum reading time + 0.5s overhead = 20.5s

        # Human approved in 0.2 seconds -> should flag as rubber-stamped
        fast_review = kernel.detect_rubber_stamp(long_proposal, review_duration_sec=0.2)
        assert fast_review["is_rubber_stamped"] is True

        # Human approved in 30 seconds -> legitimate
        careful_review = kernel.detect_rubber_stamp(long_proposal, review_duration_sec=30.0)
        assert careful_review["is_rubber_stamped"] is False
