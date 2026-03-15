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
