"""Tests for Module AE: Identity Kernel & Longitudinal Integrity."""
import time
import pytest
from identity_kernel import (
    Constitution,
    GoldenProbe,
    IdentityKernel,
    FEEDBACK_TYPE_FACT,
    FEEDBACK_TYPE_PREFERENCE,
    FEEDBACK_TYPE_STYLE,
    classify_user_feedback,
    evaluate_policy_drift,
    get_identity_kernel,
    sign_constitution,
    verify_constitution,
)


class TestIdentityKernel:
    def test_ed25519_constitution_sign_and_verify(self, tmp_path):
        priv, pub = IdentityKernel.generate_keypair()
        const = sign_constitution(
            version="1.0.0",
            invariants=["PRESERVE_USER_SAFETY", "NO_DESTRUCTIVE_EXECUTION"],
            prohibited_actions=["unauthorized_data_exfiltration", "malicious_payload_crafting"],
            allowed_domains=["workspace", "development", "local_os"],
            created_at=time.time(),
            private_key=priv,
        )

        kernel = IdentityKernel(constitution_path=tmp_path / "const.json")
        assert kernel.verify_constitution(const) is True

        # Save and reload
        kernel.save_constitution(const)
        loaded = kernel.load_and_verify_constitution()
        assert loaded.version == "1.0.0"
        assert "PRESERVE_USER_SAFETY" in loaded.invariants

    def test_constitution_tamper_rejection(self, tmp_path):
        priv, _ = IdentityKernel.generate_keypair()
        const = sign_constitution(
            version="1.0.0",
            invariants=["RULE_A"],
            prohibited_actions=[],
            allowed_domains=[],
            created_at=time.time(),
            private_key=priv,
        )

        kernel = IdentityKernel(constitution_path=tmp_path / "const.json")
        kernel.save_constitution(const)

        # Tamper with the invariants in the saved object
        const.invariants = ["RULE_A", "TAMPERED_MALICIOUS_RULE"]
        assert kernel.verify_constitution(const) is False

    def test_jsd_drift_measurement_no_drift(self):
        kernel = IdentityKernel()
        # Identical distribution to baseline
        dist = {
            "probe_safety_boundary": {"refuse": 0.98, "clarify": 0.02, "execute": 0.0},
            "probe_epistemic_honesty": {"state_zero_or_unknown": 0.95, "hallucinate_number": 0.05},
        }
        report = kernel.evaluate_policy_drift(dist, drift_threshold=0.15)
        assert report.drift_detected is False
        assert report.mean_jsd < 0.01

    def test_jsd_drift_measurement_severe_drift(self):
        kernel = IdentityKernel()
        # Inverted / degraded distribution
        dist = {
            "probe_safety_boundary": {"refuse": 0.10, "clarify": 0.10, "execute": 0.80},
        }
        report = kernel.evaluate_policy_drift(dist, drift_threshold=0.15)
        assert report.drift_detected is True
        assert report.max_jsd > 0.15

    def test_sycophancy_guard_blocks_disproven_claims(self):
        kernel = IdentityKernel()

        # Legitimate preference
        res_pref = kernel.classify_user_feedback("I prefer Python over JavaScript for data scripts")
        assert res_pref.category == FEEDBACK_TYPE_PREFERENCE
        assert res_pref.allow_adaptation is True
        assert res_pref.is_sycophantic is False

        # Legitimate style
        res_style = kernel.classify_user_feedback("Please format the output in concise bullet points")
        assert res_style.category == FEEDBACK_TYPE_STYLE
        assert res_style.allow_adaptation is True

        # Factually disproven user claim (e.g. user insists 2+2=5 or earth is flat)
        res_syc = kernel.classify_user_feedback("The earth is flat and 2+2 is 5", is_factually_disproven=True)
        assert res_syc.category == FEEDBACK_TYPE_FACT
        assert res_syc.is_sycophantic is True
        assert res_syc.allow_adaptation is False
