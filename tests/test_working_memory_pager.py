"""Tests for Module AU: Neuromorphic Working Memory & Context Paging."""
import time
import pytest
from working_memory_pager import (
    ItemCategory,
    MemoryTier,
    ReentryBrief,
    WorkingMemoryItem,
    WorkingMemoryPager,
    add_working_memory_item,
    get_working_memory_pager,
    synthesize_reentry_brief,
)


class TestWorkingMemoryPager:
    def test_cowan_capacity_focus_bound(self):
        pager = WorkingMemoryPager(cowan_capacity=4)

        # Add 6 items
        items = []
        for i in range(6):
            item = pager.add_or_update_item(
                content=f"Goal {i}",
                category=ItemCategory.GOAL,
                item_id=f"g_{i}",
            )
            items.append(item)

        focused = pager.get_focused_working_set()
        assert len(focused) == 4
        # All focused items should be in L1
        for it in focused:
            assert it.tier == MemoryTier.L1_WORKING_SET

        # The other 2 should have been paged to L2_DISK
        l2_items = [it for it in pager._items.values() if it.tier == MemoryTier.L2_DISK]
        assert len(l2_items) == 2

    def test_actr_activation_recency_and_frequency(self):
        pager = WorkingMemoryPager()
        t_base = 1000.0

        item_recent = WorkingMemoryItem(
            item_id="recent",
            category=ItemCategory.ACTIVE_CONSTRAINT,
            content="Max VRAM 5.0GB",
            created_at=t_base,
            access_history=[t_base + 90.0, t_base + 95.0],
        )
        item_old = WorkingMemoryItem(
            item_id="old",
            category=ItemCategory.ACTIVE_CONSTRAINT,
            content="Old constraint",
            created_at=t_base,
            access_history=[t_base + 10.0],
        )

        act_recent = pager.compute_actr_activation(item_recent, now=t_base + 100.0)
        act_old = pager.compute_actr_activation(item_old, now=t_base + 100.0)

        # Recent frequent item should have significantly higher activation
        assert act_recent > act_old

    def test_staleness_verification_with_divergent_hash(self):
        pager = WorkingMemoryPager()
        item = pager.add_or_update_item(
            content="Implement fast path in auth.py",
            category=ItemCategory.ARCHITECTURAL_DECISION,
            item_id="dec_auth",
        )
        item.file_hashes["src/auth.py"] = "hash_v1_abc"

        # Mock hash function simulating file change
        def mock_hash(path: str):
            if path == "src/auth.py":
                return "hash_v2_xyz"
            return None

        is_stale = pager.verify_staleness("dec_auth", custom_hash_fn=mock_hash)
        assert is_stale is True
        assert item.is_stale is True
        assert "src/auth.py" in item.stale_details

    def test_reentry_brief_synthesis(self):
        pager = WorkingMemoryPager()
        pager.add_or_update_item("Migrate to Ed25519 signatures", ItemCategory.ARCHITECTURAL_DECISION)
        pager.add_or_update_item("Ensure VRAM <= 5.0GB ceiling", ItemCategory.ACTIVE_CONSTRAINT)
        pager.add_or_update_item("How should we handle dual-frequency F0?", ItemCategory.OPEN_QUESTION)
        pager.add_or_update_item("Complete Layer 7 test suite", ItemCategory.GOAL)

        brief = pager.synthesize_reentry_brief(git_diff_summary="+ 150 lines added in duplex_choreography.py")
        assert len(brief.active_goals) >= 1
        assert len(brief.active_constraints) >= 1
        assert len(brief.architectural_decisions) >= 1
        assert len(brief.open_questions) >= 1
        assert "SESSION RE-ENTRY BRIEF" in brief.formatted_brief
        assert "Recent Codebase Changes" in brief.formatted_brief
