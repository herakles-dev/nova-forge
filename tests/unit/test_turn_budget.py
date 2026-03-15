"""Unit tests for compute_turn_budget() and ConvergenceTracker."""
import pytest
from config import compute_turn_budget
from forge_agent import ConvergenceTracker


# ── compute_turn_budget tests ─────────────────────────────────────────────


class TestComputeTurnBudget:
    """Adaptive turn budget scales with task complexity."""

    def test_zero_files_gets_discovery_budget(self):
        budget = compute_turn_budget({"files": []})
        assert budget["soft_limit"] == 12
        assert budget["hard_limit"] <= 16
        assert budget["verify_budget"] == 3

    def test_one_file_budget(self):
        budget = compute_turn_budget({"files": ["models.py"]})
        assert budget["soft_limit"] == 15
        assert budget["hard_limit"] == 19  # max(15+4, int(15*1.3)) = 19

    def test_two_files_budget(self):
        budget = compute_turn_budget({"files": ["app.py", "models.py"]})
        assert budget["soft_limit"] == 18

    def test_three_files_budget(self):
        budget = compute_turn_budget({"files": ["a.py", "b.py", "c.py"]})
        assert budget["soft_limit"] == 24  # 12 + 3*4

    def test_many_files_caps_at_30(self):
        files = [f"file_{i}.py" for i in range(10)]
        budget = compute_turn_budget({"files": files})
        assert budget["soft_limit"] == 30  # min(12+10*4, 30) = 30

    def test_ceiling_limits_budget(self):
        budget = compute_turn_budget({"files": ["a.py", "b.py", "c.py"]}, max_turns_ceiling=15)
        assert budget["soft_limit"] == 15  # ceiling caps it

    def test_curl_acceptance_adds_budget(self):
        meta = {"files": ["app.py"], "acceptance_criteria": ["curl http://localhost:5000"]}
        budget = compute_turn_budget(meta)
        assert budget["soft_limit"] == 19  # 15 + 4

    def test_blocked_by_adds_budget(self):
        meta = {"files": ["app.py"], "blocked_by": ["task-1"]}
        budget = compute_turn_budget(meta)
        assert budget["soft_limit"] == 17  # 15 + 2

    def test_both_modifiers_stack(self):
        meta = {
            "files": ["app.py"],
            "acceptance_criteria": ["curl localhost:5000"],
            "blocked_by": ["task-1"],
        }
        budget = compute_turn_budget(meta)
        assert budget["soft_limit"] == 21  # 15 + 4 + 2

    def test_escalation_turns_is_half_soft(self):
        budget = compute_turn_budget({"files": ["a.py", "b.py"]})
        assert budget["escalation_turns"] == max(8, budget["soft_limit"] // 2)

    def test_hard_limit_formula(self):
        budget = compute_turn_budget({"files": ["app.py"]})
        soft = budget["soft_limit"]
        expected_hard = max(soft + 4, int(soft * 1.3))
        assert budget["hard_limit"] == expected_hard

    def test_empty_metadata(self):
        budget = compute_turn_budget({})
        assert budget["soft_limit"] == 12  # 0 files = discovery budget


# ── ConvergenceTracker tests ─────────────────────────────────────────────


class TestConvergenceTracker:
    """Convergence detection for read-edit loops."""

    def test_should_not_stop_before_window(self):
        ct = ConvergenceTracker(window=5)
        for _ in range(3):
            ct.end_turn()
        assert not ct.should_stop()

    def test_should_stop_after_zero_write_window(self):
        ct = ConvergenceTracker(window=5)
        ct.record_write(100)
        ct.end_turn()
        for _ in range(5):
            ct.end_turn()  # 5 zero-write turns
        assert ct.should_stop()

    def test_should_not_stop_with_recent_writes(self):
        ct = ConvergenceTracker(window=5)
        for _ in range(5):
            ct.record_write(50)
            ct.end_turn()
        assert not ct.should_stop()

    def test_should_stop_on_diminishing_returns(self):
        ct = ConvergenceTracker(window=5, min_change_ratio=0.02)
        ct.record_write(1000)
        ct.end_turn()
        for _ in range(5):
            ct.record_write(1)  # ~0.1% of initial — below 2% threshold
            ct.end_turn()
        assert ct.should_stop()

    def test_mixed_writes_no_convergence(self):
        ct = ConvergenceTracker(window=5)
        ct.record_write(100)
        ct.end_turn()
        for _ in range(5):
            ct.record_write(80)
            ct.end_turn()
        assert not ct.should_stop()

    def test_multiple_writes_per_turn(self):
        ct = ConvergenceTracker(window=5)
        ct.record_write(100)
        ct.record_write(200)
        ct.end_turn()
        assert ct._turn_writes == [300]

    def test_initial_write_tracked(self):
        ct = ConvergenceTracker()
        ct.record_write(500)
        assert ct._initial_write == 500
        ct.record_write(100)
        assert ct._initial_write == 500  # unchanged

    def test_negative_bytes_clamped_to_zero(self):
        """Negative byte values are clamped to zero (never negative)."""
        ct = ConvergenceTracker()
        ct.record_write(-50)
        ct.end_turn()
        assert ct._turn_writes == [0]

    def test_zero_initial_write_does_not_trigger_ratio_stop(self):
        """When initial_write is 0, ratio check is skipped (no division by zero)."""
        ct = ConvergenceTracker(window=3)
        for _ in range(5):
            ct.end_turn()
        # All zeros, initial_write=0 — should trigger the all-zero check
        assert ct.should_stop() is True

    def test_exact_window_boundary(self):
        """At exactly window turns of zero writes, should_stop returns True."""
        ct = ConvergenceTracker(window=3)
        ct.record_write(100)
        ct.end_turn()
        # Exactly 3 zero-write turns
        for _ in range(3):
            ct.end_turn()
        assert ct.should_stop() is True

    def test_custom_min_change_ratio(self):
        """Custom min_change_ratio works — higher threshold triggers earlier."""
        ct = ConvergenceTracker(window=3, min_change_ratio=0.5)
        ct.record_write(1000)
        ct.end_turn()
        for _ in range(3):
            ct.record_write(100)  # 10% of 1000 — below 50% threshold
            ct.end_turn()
        assert ct.should_stop() is True


# ── compute_turn_budget edge cases ───────────────────────────────────────────


class TestComputeTurnBudgetEdgeCases:
    """Additional edge case tests for compute_turn_budget."""

    def test_verify_budget_scales_with_soft_limit(self):
        """verify_budget is at least 2 and scales as soft_limit // 4."""
        budget = compute_turn_budget({"files": ["a.py", "b.py", "c.py"]})
        assert budget["verify_budget"] == max(2, budget["soft_limit"] // 4)

    def test_ceiling_caps_hard_limit(self):
        """hard_limit never wildly exceeds ceiling."""
        budget = compute_turn_budget(
            {"files": [f"f{i}.py" for i in range(10)]},
            max_turns_ceiling=15,
        )
        assert budget["hard_limit"] <= 15 + 4

    def test_acceptance_criteria_without_server_keyword_no_bonus(self):
        """Acceptance criteria without server keywords do not add bonus."""
        meta = {"files": ["app.py"], "acceptance_criteria": ["check output format"]}
        budget = compute_turn_budget(meta)
        assert budget["soft_limit"] == 15  # No bonus

    def test_multiple_blocked_by_still_adds_2(self):
        """Multiple blocked_by items still add only +2."""
        meta = {"files": ["app.py"], "blocked_by": ["t1", "t2", "t3"]}
        budget = compute_turn_budget(meta)
        assert budget["soft_limit"] == 17  # 15 + 2, not 15 + 6
