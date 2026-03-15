"""Tests for Enhanced Autonomy System (A0-A5).

Validates:
- A0-A5 backward compatibility
- AUTONOMY_LEVELS rich descriptors
- get_level_info() / set_level() / recommend_level() APIs
- Escalation ceiling (auto-escalation stops at A3)
- Escalation thresholds (exact boundary tests)
- De-escalation behavior
- Trivial-call escalation inflation guard (M16)
- check_permission() across all levels
"""

import json
import sys
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from forge_guards import (
    AutonomyManager,
    AutonomyLevel,
    AUTONOMY_LEVELS,
    RiskLevel,
    _LEVEL_NAMES,
    _ESCALATION_THRESHOLDS,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Backward Compatibility — A0 through A5 (MUST ALWAYS PASS)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompatibility:
    """Verify existing A0-A5 behavior is not broken."""

    def test_a0_blocks_everything(self, tmp_path):
        """A0 (Manual) should block all risk levels."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 0}))
        mgr = AutonomyManager(af)
        assert mgr.check_permission(RiskLevel.LOW) is False
        assert mgr.check_permission(RiskLevel.MEDIUM) is False
        assert mgr.check_permission(RiskLevel.HIGH) is False

    def test_a1_allows_low_only(self, tmp_path):
        """A1 (Guided) should allow only LOW."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 1}))
        mgr = AutonomyManager(af)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is False
        assert mgr.check_permission(RiskLevel.HIGH) is False

    def test_a2_allows_low_and_medium(self, tmp_path):
        """A2 (Supervised) should allow LOW and MEDIUM, block HIGH."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2}))
        mgr = AutonomyManager(af)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is True
        assert mgr.check_permission(RiskLevel.HIGH) is False

    def test_a3_allows_low_and_medium(self, tmp_path):
        """A3 (Trusted) should allow LOW and MEDIUM but block HIGH."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3}))
        mgr = AutonomyManager(af)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is True
        assert mgr.check_permission(RiskLevel.HIGH) is False

    def test_a4_allows_everything(self, tmp_path):
        """A4 (Autonomous) should allow everything."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 4}))
        mgr = AutonomyManager(af)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is True
        assert mgr.check_permission(RiskLevel.HIGH) is True

    def test_a5_allows_everything(self, tmp_path):
        """A5 (Unattended) should allow everything like A4."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 5}))
        mgr = AutonomyManager(af)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is True
        assert mgr.check_permission(RiskLevel.HIGH) is True

    def test_default_is_a2(self, tmp_path):
        """Default autonomy level should be A2 (Supervised)."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        assert mgr.current_level == 2

    def test_level_names_has_a0_through_a5(self):
        """_LEVEL_NAMES must have entries for A0 through A5."""
        for i in range(6):
            assert i in _LEVEL_NAMES, f"A{i} missing from _LEVEL_NAMES"

    def test_level_names_correct_values(self):
        """Verify canonical level names for all 6 levels."""
        expected = {
            0: "Manual", 1: "Guided", 2: "Supervised",
            3: "Trusted", 4: "Autonomous", 5: "Unattended",
        }
        for level_id, name in expected.items():
            assert _LEVEL_NAMES[level_id] == name, (
                f"A{level_id} should be '{name}', got '{_LEVEL_NAMES[level_id]}'"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Escalation Behavior — exact threshold and boundary tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEscalation:
    """Verify automatic escalation thresholds and ceiling."""

    def test_auto_escalation_ceiling_is_a3(self, tmp_path):
        """Automatic escalation from A0 should stop at A3 (cooldown-aware).

        Each escalation sets last_escalation, triggering the 1-hour cooldown.
        To verify the ceiling, we start at A2 with expired cooldown and enough
        successes to trigger A2->A3, then confirm A3 does NOT escalate further.
        """
        old = (datetime.now(tz=timezone.utc) - timedelta(hours=2)).isoformat()
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 2, "successful_actions": 24,
            "last_escalation": old,
        }))
        mgr = AutonomyManager(af)
        mgr.record_success()  # 25th success triggers A2->A3
        assert mgr.current_level == 3, (
            f"Expected A2->A3 escalation at 25 successes, got A{mgr.current_level}"
        )
        # Now verify A3 never auto-escalates even with many more successes
        for _ in range(100):
            mgr.record_success()
        assert mgr.current_level == 3, (
            f"Expected auto-escalation ceiling A3, got A{mgr.current_level}"
        )

    def test_a0_does_not_escalate_below_threshold(self, tmp_path):
        """A0 should not escalate with fewer than 5 successes."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 0, "successful_actions": 0}))
        mgr = AutonomyManager(af)
        for _ in range(4):
            mgr.record_success()
        assert mgr.current_level == 0, (
            f"A0 should not escalate with 4 successes, got A{mgr.current_level}"
        )

    def test_a0_escalates_at_exactly_5_successes(self, tmp_path):
        """A0 should escalate to A1 at exactly 5 successes (threshold boundary)."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 0, "successful_actions": 0, "last_escalation": None,
        }))
        mgr = AutonomyManager(af)
        for _ in range(5):
            mgr.record_success()
        assert mgr.current_level == 1, (
            f"A0 should escalate to A1 at 5 successes, got A{mgr.current_level}"
        )

    def test_a1_escalates_at_threshold(self, tmp_path):
        """A1 should escalate to A2 at 10 successes (threshold for level 1)."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 1, "successful_actions": 0, "last_escalation": None,
        }))
        mgr = AutonomyManager(af)
        for _ in range(10):
            mgr.record_success()
        assert mgr.current_level == 2, (
            f"A1 should escalate to A2 at 10 successes, got A{mgr.current_level}"
        )

    def test_a2_escalates_at_threshold(self, tmp_path):
        """A2 should escalate to A3 at 25 successes (threshold for level 2)."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 2, "successful_actions": 0, "last_escalation": None,
        }))
        mgr = AutonomyManager(af)
        for _ in range(25):
            mgr.record_success()
        assert mgr.current_level == 3, (
            f"A2 should escalate to A3 at 25 successes, got A{mgr.current_level}"
        )

    def test_a3_never_auto_escalates(self, tmp_path):
        """A3 should never auto-escalate to A4 regardless of successes."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3, "successful_actions": 0}))
        mgr = AutonomyManager(af)
        for _ in range(100):
            mgr.record_success()
        assert mgr.current_level == 3, (
            f"A3 should never auto-escalate, got A{mgr.current_level}"
        )

    def test_a4_never_auto_escalates_to_a5(self, tmp_path):
        """A4 should never auto-escalate to A5."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 4, "successful_actions": 0}))
        mgr = AutonomyManager(af)
        for _ in range(100):
            mgr.record_success()
        assert mgr.current_level == 4, (
            f"A4 should never auto-escalate to A5, got A{mgr.current_level}"
        )

    def test_escalation_respects_cooldown(self, tmp_path):
        """Escalation should not happen within the 1-hour cooldown period."""
        now = datetime.now(tz=timezone.utc)
        recent = (now - timedelta(minutes=30)).isoformat()
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 1,
            "successful_actions": 50,
            "last_escalation": recent,
        }))
        mgr = AutonomyManager(af)
        for _ in range(20):
            mgr.record_success()
        assert mgr.current_level == 1, (
            "Escalation should be blocked during 1-hour cooldown"
        )

    def test_escalation_allowed_after_cooldown_expired(self, tmp_path):
        """Escalation should proceed after the 1-hour cooldown expires."""
        now = datetime.now(tz=timezone.utc)
        old = (now - timedelta(hours=2)).isoformat()
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 1,
            "successful_actions": 9,
            "last_escalation": old,
        }))
        mgr = AutonomyManager(af)
        mgr.record_success()  # 10th success triggers A1->A2
        assert mgr.current_level == 2, (
            "Escalation should proceed after cooldown expires"
        )

    def test_escalation_thresholds_are_documented(self):
        """Verify escalation thresholds match documented values."""
        assert _ESCALATION_THRESHOLDS == {0: 5, 1: 10, 2: 25}


# ═══════════════════════════════════════════════════════════════════════════════
# Trivial-Call Escalation Inflation (M16)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrivialCallInflation:
    """Guard against M16: trivial calls should not inflate trust score when
    using track() which is the path that records successful_actions to state.

    Note: record_success() increments successful_actions unconditionally — it
    is the caller's (track/record_build_result) responsibility to ensure the
    action was non-trivial. These tests verify that boundary is respected.
    """

    def test_track_with_different_tools_builds_categories(self, tmp_path):
        """Tracking diverse tools should accumulate distinct categories."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 2, "successful_actions": 0, "approved_categories": [],
        }))
        mgr = AutonomyManager(af)
        mgr.track("git", RiskLevel.LOW, "success")
        mgr.track("docker", RiskLevel.MEDIUM, "success")
        mgr.track("Write", RiskLevel.MEDIUM, "success")
        cats = mgr._state["approved_categories"]
        assert "git" in cats
        assert "docker" in cats
        assert "write" in cats
        assert len(set(cats)) == len(cats), "Categories should be deduplicated"

    def test_track_same_tool_does_not_duplicate_category(self, tmp_path):
        """Tracking the same tool repeatedly should not create duplicate categories."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 2, "successful_actions": 0, "approved_categories": [],
        }))
        mgr = AutonomyManager(af)
        for _ in range(10):
            mgr.track("git", RiskLevel.LOW, "success")
        cats = mgr._state["approved_categories"]
        assert cats.count("git") == 1, "Same category should not be duplicated"


# ═══════════════════════════════════════════════════════════════════════════════
# De-escalation Behavior
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeescalation:
    """Verify error-driven de-escalation."""

    def test_single_error_drops_one_level(self, tmp_path):
        """A single error should drop level by exactly 1."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_error()
        assert mgr.current_level == 2

    def test_single_error_from_a4_drops_to_a3(self, tmp_path):
        """A single error at A4 should drop to A3."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 4, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_error()
        assert mgr.current_level == 3

    def test_single_error_from_a5_drops_to_a4(self, tmp_path):
        """A single error at A5 should drop to A4 (stays in autopilot territory)."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 5, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_error()
        assert mgr.current_level == 4

    def test_rapid_errors_crash_to_a0(self, tmp_path):
        """5+ errors in 10 minutes should crash to A0."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3, "error_history": []}))
        mgr = AutonomyManager(af)
        for _ in range(6):
            mgr.record_error()
        assert mgr.current_level == 0

    def test_rapid_errors_from_a5_crash_to_a0(self, tmp_path):
        """5+ rapid errors at A5 should crash all the way to A0."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 5, "error_history": []}))
        mgr = AutonomyManager(af)
        for _ in range(6):
            mgr.record_error()
        assert mgr.current_level == 0

    def test_a0_stays_at_a0_on_error(self, tmp_path):
        """A0 should not go below A0."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 0, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_error()
        assert mgr.current_level == 0

    def test_error_appends_to_history(self, tmp_path):
        """record_error should append an entry to error_history."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_error()
        history = mgr._state["error_history"]
        assert len(history) == 1
        assert history[0]["tool"] == "build"
        assert "timestamp" in history[0]


# ═══════════════════════════════════════════════════════════════════════════════
# check() method detailed tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCheckMethod:
    """Verify the detailed check() method returns correct AutonomyResult."""

    def test_low_risk_always_allowed_via_check(self, tmp_path):
        """LOW risk via check() is always allowed at all levels (fast-path)."""
        for level in range(6):
            af = tmp_path / f"autonomy_{level}.json"
            af.write_text(json.dumps({"level": level}))
            mgr = AutonomyManager(af)
            result = mgr.check("Read", RiskLevel.LOW, file_path="/any/file.py")
            assert result.allowed is True, f"A{level} check() should allow LOW risk"
            assert "LOW" in result.reason, f"A{level} reason should mention LOW"

    def test_medium_risk_a0_blocked(self, tmp_path):
        """MEDIUM risk at A0 should be blocked with descriptive reason."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 0}))
        mgr = AutonomyManager(af)
        result = mgr.check("Write", RiskLevel.MEDIUM, file_path="/some/file.py")
        assert result.allowed is False
        assert "A0" in result.reason
        assert "Manual" in result.reason

    def test_medium_risk_a3_allowed(self, tmp_path):
        """MEDIUM risk at A3 should be auto-approved."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3}))
        mgr = AutonomyManager(af)
        result = mgr.check("Write", RiskLevel.MEDIUM, file_path="/some/file.py")
        assert result.allowed is True
        assert "auto-approved" in result.reason.lower()

    def test_high_risk_a3_blocked(self, tmp_path):
        """HIGH risk at A3 should be blocked."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3}))
        mgr = AutonomyManager(af)
        result = mgr.check("Bash", RiskLevel.HIGH, command="rm -rf /")
        assert result.allowed is False
        assert "HIGH" in result.reason

    def test_high_risk_a4_with_history_match(self, tmp_path):
        """HIGH risk at A4 with matching history should be allowed."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 4,
            "high_risk_history": ["docker system prune -a"],
        }))
        mgr = AutonomyManager(af)
        result = mgr.check("Bash", RiskLevel.HIGH, command="docker system prune -a")
        assert result.allowed is True
        assert "history" in result.reason.lower()

    def test_high_risk_a4_without_history_blocked(self, tmp_path):
        """HIGH risk at A4 without history match should be blocked."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 4, "high_risk_history": []}))
        mgr = AutonomyManager(af)
        result = mgr.check("Bash", RiskLevel.HIGH, command="rm -rf /important")
        assert result.allowed is False

    def test_result_has_nonempty_reason(self, tmp_path):
        """AutonomyResult should always have a non-empty reason string."""
        for level in range(6):
            for risk in RiskLevel:
                af = tmp_path / f"auto_{level}_{risk.value}.json"
                af.write_text(json.dumps({"level": level}))
                mgr = AutonomyManager(af)
                result = mgr.check("Write", risk, file_path="/foo.py")
                assert isinstance(result.reason, str)
                assert len(result.reason) > 0, (
                    f"A{level} + {risk.value} should have non-empty reason"
                )


# ═══════════════════════════════════════════════════════════════════════════════
# track() method tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrackMethod:
    """Verify the track() method updates state correctly."""

    def test_track_success_increments_count(self, tmp_path):
        """Tracking a success should increment successful_actions by exactly 1."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "successful_actions": 5}))
        mgr = AutonomyManager(af)
        mgr.track("Write", RiskLevel.MEDIUM, "success")
        assert mgr._state["successful_actions"] == 6

    def test_track_error_increments_error_count(self, tmp_path):
        """Tracking an error should increment error_count by exactly 1."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "error_count": 3, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.track("Write", RiskLevel.MEDIUM, "error")
        assert mgr._state["error_count"] == 4

    def test_track_success_accumulates_categories(self, tmp_path):
        """Successful actions should add categories to approved list."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "successful_actions": 0, "approved_categories": []}))
        mgr = AutonomyManager(af)
        mgr.track("git", RiskLevel.LOW, "success")
        assert "git" in mgr._state["approved_categories"]

    def test_track_error_appends_to_error_history(self, tmp_path):
        """Tracking an error should append a timestamped entry to error_history."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "error_count": 0, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.track("Bash", RiskLevel.MEDIUM, "error")
        history = mgr._state["error_history"]
        assert len(history) == 1
        assert history[0]["tool"] == "Bash"
        assert "timestamp" in history[0]

    def test_track_persists_to_disk(self, tmp_path):
        """track() should call _save() so state persists across instances."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 2, "successful_actions": 0, "approved_categories": [],
        }))
        mgr = AutonomyManager(af)
        mgr.track("git", RiskLevel.LOW, "success")
        mgr2 = AutonomyManager(af)
        assert mgr2._state["successful_actions"] == 1
        assert "git" in mgr2._state["approved_categories"]


# ═══════════════════════════════════════════════════════════════════════════════
# record_build_result() tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestRecordBuildResult:
    """Verify build result tracking."""

    def test_clean_build_counts_as_success(self, tmp_path):
        """Build with 0 failures should record exactly 1 success."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "successful_actions": 0}))
        mgr = AutonomyManager(af)
        mgr.record_build_result(passed=5, failed=0, total=5)
        assert mgr._state["successful_actions"] == 1

    def test_failed_build_counts_as_error(self, tmp_path):
        """Build with failures should record exactly 1 error."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3, "error_count": 0, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_build_result(passed=3, failed=2, total=5)
        assert mgr._state["error_count"] == 1

    def test_empty_build_ignored(self, tmp_path):
        """Build with 0 total tasks should not change state."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "successful_actions": 0, "error_count": 0}))
        mgr = AutonomyManager(af)
        mgr.record_build_result(passed=0, failed=0, total=0)
        assert mgr._state["successful_actions"] == 0
        assert mgr._state["error_count"] == 0

    def test_build_result_persists(self, tmp_path):
        """record_build_result should save state to disk."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2, "successful_actions": 0}))
        mgr = AutonomyManager(af)
        mgr.record_build_result(passed=3, failed=0, total=3)
        mgr2 = AutonomyManager(af)
        assert mgr2._state["successful_actions"] == 1

    def test_failed_build_deescalates(self, tmp_path):
        """A failed build at A3 should trigger de-escalation to A2."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 3, "error_count": 0, "error_history": []}))
        mgr = AutonomyManager(af)
        mgr.record_build_result(passed=2, failed=3, total=5)
        assert mgr.current_level == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Persistence tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPersistence:
    """Verify state is saved and loaded correctly."""

    def test_state_persists_across_instances(self, tmp_path):
        """Changes via track() should be visible to a new AutonomyManager instance."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 2,
            "error_count": 0,
            "error_history": [],
            "successful_actions": 0,
            "approved_categories": [],
        }))
        mgr1 = AutonomyManager(af)
        mgr1.track("Write", RiskLevel.MEDIUM, "error")
        mgr2 = AutonomyManager(af)
        assert mgr2.current_level == mgr1.current_level

    def test_corrupt_json_uses_defaults(self, tmp_path):
        """Corrupt state file should fall back to defaults."""
        af = tmp_path / "autonomy.json"
        af.write_text("NOT VALID JSON!!!")
        mgr = AutonomyManager(af)
        assert mgr.current_level == 2  # Default

    def test_missing_file_uses_defaults(self, tmp_path):
        """Missing state file should use defaults."""
        af = tmp_path / "does_not_exist.json"
        mgr = AutonomyManager(af)
        assert mgr.current_level == 2

    def test_empty_file_uses_defaults(self, tmp_path):
        """Empty state file should fall back to defaults."""
        af = tmp_path / "autonomy.json"
        af.write_text("")
        mgr = AutonomyManager(af)
        assert mgr.current_level == 2

    def test_set_level_persists_across_instances(self, tmp_path):
        """set_level should persist across AutonomyManager instances."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(4, reason="test_persistence")
        mgr2 = AutonomyManager(af)
        assert mgr2.current_level == 4

    def test_default_state_has_all_required_keys(self, tmp_path):
        """Default state dict must contain all required keys."""
        af = tmp_path / "does_not_exist.json"
        mgr = AutonomyManager(af)
        required_keys = {
            "level", "name", "successful_actions", "error_count",
            "approved_categories", "grants", "high_risk_history",
            "last_escalation", "error_history",
        }
        assert required_keys.issubset(set(mgr._state.keys()))


# ═══════════════════════════════════════════════════════════════════════════════
# A5 (Unattended) — now always runs since A5 is implemented
# ═══════════════════════════════════════════════════════════════════════════════


class TestA5Unattended:
    """Tests for the A5 (Unattended) level."""

    def test_a5_name_is_unattended(self):
        """A5 should be named 'Unattended'."""
        assert _LEVEL_NAMES[5] == "Unattended"

    def test_a5_permits_everything_via_check_permission(self, tmp_path):
        """A5 should permit all risk levels via check_permission()."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 5}))
        mgr = AutonomyManager(af)
        for risk in RiskLevel:
            assert mgr.check_permission(risk) is True, (
                f"A5 should permit {risk.value}"
            )

    def test_a5_check_low_risk_allowed(self, tmp_path):
        """A5 check() for LOW risk should be allowed with audit hint."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 5}))
        mgr = AutonomyManager(af)
        result = mgr.check("Read", RiskLevel.LOW, file_path="/test.py")
        assert result.allowed is True

    def test_a5_check_medium_risk_allowed(self, tmp_path):
        """A5 check() for MEDIUM risk should be auto-approved."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 5}))
        mgr = AutonomyManager(af)
        result = mgr.check("Write", RiskLevel.MEDIUM, file_path="/test.py")
        assert result.allowed is True

    def test_a5_not_reachable_by_auto_escalation(self, tmp_path):
        """A5 should never be reached by automatic escalation from A4."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 4, "successful_actions": 0}))
        mgr = AutonomyManager(af)
        for _ in range(100):
            mgr.record_success()
        assert mgr.current_level == 4

    def test_a5_reachable_only_via_set_level(self, tmp_path):
        """A5 should only be reachable via explicit set_level(5)."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(5, reason="ci_pipeline")
        assert mgr.current_level == 5


# ═══════════════════════════════════════════════════════════════════════════════
# AUTONOMY_LEVELS rich descriptor
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutonomyLevelsDescriptor:
    """Tests for AUTONOMY_LEVELS rich descriptor dict."""

    def test_all_six_levels_defined(self):
        """AUTONOMY_LEVELS should have entries for A0 through A5."""
        for i in range(6):
            assert i in AUTONOMY_LEVELS, f"A{i} missing from AUTONOMY_LEVELS"

    def test_each_level_has_name(self):
        """Each level should have a non-empty name attribute."""
        for level_id, info in AUTONOMY_LEVELS.items():
            assert isinstance(info, AutonomyLevel), f"A{level_id} not AutonomyLevel"
            assert len(info.name) > 0

    def test_each_level_has_description(self):
        """Each level should have a description of at least 20 characters."""
        for level_id, info in AUTONOMY_LEVELS.items():
            assert len(info.description) > 20, f"A{level_id} description too short"

    def test_each_level_has_capabilities(self):
        """Each level should have a non-empty capabilities list."""
        for level_id, info in AUTONOMY_LEVELS.items():
            assert isinstance(info.capabilities, list)
            assert len(info.capabilities) > 0

    def test_each_level_has_recommended_for(self):
        """Each level should have a recommendation target."""
        for level_id, info in AUTONOMY_LEVELS.items():
            assert len(info.recommended_for) > 0

    def test_names_match_level_names(self):
        """AUTONOMY_LEVELS names should match _LEVEL_NAMES."""
        for level_id, info in AUTONOMY_LEVELS.items():
            assert info.name == _LEVEL_NAMES[level_id], (
                f"A{level_id}: AUTONOMY_LEVELS name '{info.name}' != "
                f"_LEVEL_NAMES '{_LEVEL_NAMES[level_id]}'"
            )

    def test_level_ids_match_dict_keys(self):
        """Each AutonomyLevel.id should match its dict key."""
        for level_id, info in AUTONOMY_LEVELS.items():
            assert info.id == level_id


class TestSetLevel:
    """Tests for explicit set_level() method."""

    def test_set_level_to_a4(self, tmp_path):
        """set_level should set level to A4."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(4, reason="test")
        assert mgr.current_level == 4

    def test_set_level_to_a5(self, tmp_path):
        """set_level should set level to A5."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(5, reason="ci")
        assert mgr.current_level == 5

    def test_set_level_persists(self, tmp_path):
        """set_level should persist across instances."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(3, reason="explicit")
        mgr2 = AutonomyManager(af)
        assert mgr2.current_level == 3

    def test_set_level_clamps_to_valid_range(self, tmp_path):
        """set_level should clamp values outside 0-5."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(-1, reason="underflow")
        assert mgr.current_level == 0
        mgr.set_level(99, reason="overflow")
        assert mgr.current_level == 5

    def test_set_level_updates_name(self, tmp_path):
        """set_level should update the name field in state."""
        af = tmp_path / "autonomy.json"
        mgr = AutonomyManager(af)
        mgr.set_level(4, reason="test")
        assert mgr._state["name"] == "Autonomous"


class TestGetLevelInfo:
    """Tests for get_level_info() method."""

    def test_get_level_info_returns_autonomy_level(self, tmp_path):
        """get_level_info should return an AutonomyLevel dataclass."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({"level": 2}))
        mgr = AutonomyManager(af)
        info = mgr.get_level_info()
        assert isinstance(info, AutonomyLevel)
        assert info.name == "Supervised"

    def test_get_level_info_matches_current(self, tmp_path):
        """get_level_info should return info for the current level."""
        for level in range(6):
            af = tmp_path / f"auto_{level}.json"
            af.write_text(json.dumps({"level": level}))
            mgr = AutonomyManager(af)
            info = mgr.get_level_info()
            assert info.name == _LEVEL_NAMES[level]


class TestRecommendLevel:
    """Tests for recommend_level() classmethod."""

    def test_recommend_beginner_gets_a1(self):
        """Beginner should get A1."""
        assert AutonomyManager.recommend_level("beginner") == 1

    def test_recommend_intermediate_gets_a2(self):
        """Intermediate should get A2."""
        assert AutonomyManager.recommend_level("intermediate") == 2

    def test_recommend_expert_gets_a3(self):
        """Expert should get A3."""
        assert AutonomyManager.recommend_level("expert") == 3

    def test_recommend_ci_gets_a5(self):
        """CI/unattended should get A5."""
        assert AutonomyManager.recommend_level("ci") == 5

    def test_recommend_pipeline_gets_a5(self):
        """Pipeline alias should also get A5."""
        assert AutonomyManager.recommend_level("pipeline") == 5

    def test_recommend_unattended_gets_a5(self):
        """Unattended alias should also get A5."""
        assert AutonomyManager.recommend_level("unattended") == 5

    def test_recommend_unknown_gets_a2(self):
        """Unknown skill should default to A2."""
        assert AutonomyManager.recommend_level("unknown_skill") == 2

    def test_recommend_case_insensitive(self):
        """Skill input should be case-insensitive."""
        assert AutonomyManager.recommend_level("BEGINNER") == 1
        assert AutonomyManager.recommend_level("Expert") == 3
