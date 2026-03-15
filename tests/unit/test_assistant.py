"""Tests for Sprint 9 — ForgeAssistant smart session helper.

Validates:
- Skill level detection (beginner / intermediate / expert)
- Autonomy recommendations per skill level
- Autonomy explanations for all levels
- Formation recommendations by project description
- Model recommendations based on credentials
- Contextual hints (shown once, skill-gated)
- Welcome messages (adaptive verbosity)
- Post-plan and post-build guidance
- Autonomy bar visual formatting
- Autonomy level read/write via project state files
"""

import json
import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    from forge_assistant import (
        ForgeAssistant,
        _LEVEL_NAMES,
        _LEVEL_DESCRIPTIONS,
        _LEVEL_CAPABILITIES,
        _FORMATION_DESCRIPTIONS,
        _HINTS,
    )
except ImportError:
    pytest.skip("forge_assistant not yet implemented", allow_module_level=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_shell(
    builds_completed: int = 0,
    first_run: bool = True,
    recent_projects: list | None = None,
    default_model: str = "",
    project_path: str | None = None,
) -> MagicMock:
    """Build a MagicMock that mimics ForgeShell for assistant tests."""
    shell = MagicMock()
    shell.state = {
        "first_run": first_run,
        "builds_completed": builds_completed,
        "recent_projects": recent_projects or [],
    }
    shell.config = {"default_model": default_model} if default_model else {}
    shell.project_path = project_path
    return shell


# ═══════════════════════════════════════════════════════════════════════════════
# Skill Level Detection
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillDetection:
    """Validate the signal-based skill detection algorithm."""

    def test_detect_beginner_on_first_run(self):
        """First-time user with zero builds should be detected as beginner."""
        shell = _make_shell(builds_completed=0, first_run=True)
        assistant = ForgeAssistant(shell)
        level = assistant.detect_skill_level()
        assert level == "beginner"

    def test_detect_intermediate_after_builds(self):
        """User with 2+ builds gets at least one signal bump."""
        shell = _make_shell(builds_completed=3, first_run=False, recent_projects=["a", "b"])
        assistant = ForgeAssistant(shell)
        level = assistant.detect_skill_level()
        assert level in ("intermediate", "expert")

    def test_detect_expert_with_many_builds_and_projects(self):
        """User with 10+ builds, 5+ recent projects, and custom model is expert."""
        shell = _make_shell(
            builds_completed=15,
            first_run=False,
            recent_projects=["a", "b", "c", "d", "e"],
            default_model="nova-pro",
        )
        assistant = ForgeAssistant(shell)
        level = assistant.detect_skill_level()
        assert level == "expert"

    def test_set_skill_level_explicit(self):
        """set_skill_level should override detection."""
        shell = _make_shell(builds_completed=0)
        assistant = ForgeAssistant(shell)
        assistant.set_skill_level("expert")
        assert assistant.skill_level == "expert"

    def test_set_skill_level_rejects_invalid(self):
        """Invalid skill level should not change state."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.set_skill_level("god_mode")
        assert assistant.skill_level == "beginner"  # Default unchanged

    def test_detect_updates_skill_detected_flag(self):
        """After detection, _skill_detected should be True."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assert not assistant._skill_detected
        assistant.detect_skill_level()
        assert assistant._skill_detected

    def test_builds_completed_5_gets_two_signals(self):
        """5+ builds should contribute 2 signals (bumps past beginner)."""
        shell = _make_shell(builds_completed=5, first_run=False)
        assistant = ForgeAssistant(shell)
        level = assistant.detect_skill_level()
        assert level in ("intermediate", "expert")


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomy Recommendations
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutonomyRecommendation:
    """Validate autonomy level suggestions based on skill."""

    def test_beginner_gets_low_autonomy(self):
        """Beginner should get A1 recommendation."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "beginner"
        level, reason = assistant.get_autonomy_recommendation()
        assert level <= 2
        assert isinstance(reason, str)
        assert len(reason) > 10

    def test_intermediate_gets_a2(self):
        """Intermediate should get A2."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "intermediate"
        level, reason = assistant.get_autonomy_recommendation()
        assert level == 2

    def test_expert_gets_high_autonomy(self):
        """Expert should get A3+."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        level, reason = assistant.get_autonomy_recommendation()
        assert level >= 3

    def test_recommendation_returns_tuple(self):
        """Return type must be (int, str)."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        result = assistant.get_autonomy_recommendation()
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], int)
        assert isinstance(result[1], str)


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomy Explanations
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutonomyExplanations:
    """Validate human-friendly autonomy level descriptions."""

    def test_explain_all_defined_levels(self):
        """All levels in _LEVEL_DESCRIPTIONS should have explanations."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        for level in _LEVEL_DESCRIPTIONS:
            explanation = assistant.explain_autonomy(level)
            assert len(explanation) > 20, f"A{level} explanation too short"
            assert f"A{level}" in explanation

    def test_explain_a0_mentions_manual(self):
        """A0 explanation should mention 'Manual' or 'ask'."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        explanation = assistant.explain_autonomy(0)
        assert "manual" in explanation.lower() or "ask" in explanation.lower()

    def test_explain_unknown_level(self):
        """Unknown level should return a fallback string."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        explanation = assistant.explain_autonomy(99)
        assert "99" in explanation or "unknown" in explanation.lower()

    def test_explain_a2_mentions_read_or_write(self):
        """A2 explanation should mention read/write capabilities."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        explanation = assistant.explain_autonomy(2)
        lower = explanation.lower()
        assert "read" in lower or "write" in lower

    def test_explain_all_levels_multi_line(self):
        """explain_all_autonomy_levels should return all levels on separate lines."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        text = assistant.explain_all_autonomy_levels()
        assert "A0" in text
        assert "A4" in text
        lines = text.strip().split("\n")
        assert len(lines) >= 5

    def test_get_capabilities_returns_lists(self):
        """get_autonomy_capabilities should return (can, asks) lists."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        for level in range(6):
            can, asks = assistant.get_autonomy_capabilities(level)
            assert isinstance(can, list)
            assert isinstance(asks, list)

    def test_a0_asks_everything(self):
        """A0 should have empty 'can' and full 'asks'."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        can, asks = assistant.get_autonomy_capabilities(0)
        assert len(can) == 0
        assert len(asks) > 0

    def test_a4_can_everything(self):
        """A4 should have non-empty 'can' and empty 'asks'."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        can, asks = assistant.get_autonomy_capabilities(4)
        assert len(can) > 0
        assert len(asks) == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Formation Recommendations
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormationRecommendation:
    """Validate formation selection from project descriptions."""

    def test_simple_script_recommends_single_file(self):
        """'a simple CLI tool' should recommend single-file."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, reason = assistant.get_formation_recommendation("a simple CLI tool")
        assert name == "single-file"
        assert isinstance(reason, str)

    def test_fullstack_project_recommends_new_project(self):
        """Project with both backend and frontend keywords gets new-project."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, reason = assistant.get_formation_recommendation(
            "a full-stack e-commerce platform with API backend and React dashboard"
        )
        assert name == "new-project"

    def test_debug_task_recommends_bug_investigation(self):
        """Bug-related description should get bug-investigation."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("debug the login bug")
        assert name == "bug-investigation"

    def test_security_task_recommends_security_review(self):
        """Security review should get security-review."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("audit our auth vulnerability")
        assert name == "security-review"

    def test_backend_only_recommends_feature_impl(self):
        """API-only project should get feature-impl."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("a REST API for user management")
        assert name == "feature-impl"

    def test_generic_project_defaults_to_feature_impl(self):
        """Vague description should default to feature-impl."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("something cool with data")
        assert name == "feature-impl"

    def test_recommendation_returns_tuple(self):
        """Return type must be (str, str)."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        result = assistant.get_formation_recommendation("anything")
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert all(isinstance(x, str) for x in result)


# ═══════════════════════════════════════════════════════════════════════════════
# Contextual Hints
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextualHints:
    """Validate hint delivery (shown once, skill-gated)."""

    def test_first_hint_returns_string(self):
        """First call for a context should return a non-empty string."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "beginner"
        hint = assistant.contextual_hint("after_plan")
        assert isinstance(hint, str)
        assert len(hint) > 0

    def test_same_hint_not_repeated(self):
        """Second call for same context should return None."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "beginner"
        hint1 = assistant.contextual_hint("after_plan")
        hint2 = assistant.contextual_hint("after_plan")
        assert hint1 is not None
        assert hint2 is None

    def test_unknown_context_returns_none(self):
        """Non-existent context key should return None."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assert assistant.contextual_hint("nonexistent_context") is None

    def test_expert_skips_most_hints(self):
        """Expert should get None for basic tutorial hints."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        # Experts skip informational hints
        hint = assistant.contextual_hint("after_plan")
        assert hint is None

    def test_expert_still_gets_error_hint(self):
        """Expert should still get after_build_fail hint."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        hint = assistant.contextual_hint("after_build_fail")
        assert hint is not None

    def test_all_hint_keys_in_dict(self):
        """All documented hint contexts should exist in _HINTS."""
        expected_keys = [
            "after_plan", "after_build_pass", "after_build_fail",
            "first_build", "no_credentials", "model_choice",
            "formation_intro", "autonomy_intro", "after_preview",
            "returning_expert",
        ]
        for key in expected_keys:
            assert key in _HINTS, f"Missing hint key: {key}"


# ═══════════════════════════════════════════════════════════════════════════════
# Model Recommendations
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelRecommendation:
    """Validate model selection logic."""

    def test_returns_valid_tuple(self):
        """Model recommendation should return (model_name, reason)."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        model, reason = assistant.get_model_recommendation()
        assert isinstance(model, str)
        assert isinstance(reason, str)
        assert len(model) > 0

    def test_default_when_no_credentials(self):
        """Without any credentials, should recommend nova-lite."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        with patch.dict(os.environ, {}, clear=True):
            model, _ = assistant.get_model_recommendation()
            assert model == "nova-lite"


# ═══════════════════════════════════════════════════════════════════════════════
# Welcome Messages
# ═══════════════════════════════════════════════════════════════════════════════


class TestWelcomeMessage:
    """Validate skill-adaptive welcome messages."""

    def test_beginner_welcome_is_detailed(self):
        """Beginner welcome should explain what Nova Forge is."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "beginner"
        msg = assistant.welcome_message()
        assert len(msg) > 200
        assert "build" in msg.lower() or "forge" in msg.lower()

    def test_expert_welcome_is_concise(self):
        """Expert welcome should be brief."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        msg = assistant.welcome_message()
        assert len(msg) < 200

    def test_intermediate_welcome_mentions_new_features(self):
        """Intermediate welcome should mention /autonomy or /guide."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "intermediate"
        msg = assistant.welcome_message()
        assert "/autonomy" in msg or "/guide" in msg

    def test_all_levels_return_nonempty(self):
        """All skill levels should produce non-empty welcome messages."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        for level in ("beginner", "intermediate", "expert"):
            assistant.skill_level = level
            msg = assistant.welcome_message()
            assert isinstance(msg, str)
            assert len(msg) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomy Bar Visual
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutonomyBar:
    """Validate visual autonomy bar formatting."""

    def test_bar_contains_level_indicator(self):
        """Bar should include A{level} text."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        for level in range(6):
            bar = assistant.format_autonomy_bar(level)
            assert f"A{level}" in bar

    def test_bar_contains_level_name(self):
        """Bar should include the level name."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        bar = assistant.format_autonomy_bar(2)
        assert "Supervised" in bar

    def test_bar_has_visual_blocks(self):
        """Bar should contain filled/empty block characters."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        bar = assistant.format_autonomy_bar(3)
        # Should have some visual progress indicator
        assert "\u2588" in bar or "\u2591" in bar or "#" in bar or "=" in bar


# ═══════════════════════════════════════════════════════════════════════════════
# Post-Event Guidance
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostEventGuidance:
    """Validate post-plan and post-build guidance messages."""

    def test_post_plan_beginner_explains_waves(self):
        """Beginner post-plan should explain what a wave is."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "beginner"
        msg = assistant.post_plan_guidance(10, 3)
        assert "wave" in msg.lower()
        assert "10" in msg

    def test_post_plan_expert_is_terse(self):
        """Expert post-plan should be a short summary."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        msg = assistant.post_plan_guidance(10, 3)
        assert len(msg) < 100

    def test_post_build_all_passed(self):
        """All-pass build should mention /preview."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "intermediate"
        msg = assistant.post_build_guidance(5, 0, 5)
        assert "/preview" in msg

    def test_post_build_partial_failure(self):
        """Partial failure should mention /build to retry."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "intermediate"
        msg = assistant.post_build_guidance(3, 2, 5)
        assert "/build" in msg

    def test_post_build_all_failed(self):
        """Total failure should mention /tasks."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        msg = assistant.post_build_guidance(0, 5, 5)
        assert "/tasks" in msg


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomy State Read/Write
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutonomyStateReadWrite:
    """Validate reading and writing autonomy state through the assistant."""

    def test_read_default_level_no_project(self):
        """Without a project path, default level should be 2."""
        shell = _make_shell(project_path=None)
        assistant = ForgeAssistant(shell)
        assert assistant.read_autonomy_level() == 2

    def test_read_level_from_state_file(self, tmp_path):
        """Should read level from .forge/state/autonomy.json (canonical path)."""
        state_dir = tmp_path / ".forge" / "state"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "autonomy.json"
        state_file.write_text(json.dumps({"level": 3}))
        shell = _make_shell(project_path=str(tmp_path))
        assistant = ForgeAssistant(shell)
        assert assistant.read_autonomy_level() == 3

    def test_read_level_corrupt_file(self, tmp_path):
        """Corrupt JSON should return default level 2."""
        state_dir = tmp_path / ".forge" / "state"
        state_dir.mkdir(parents=True)
        state_file = state_dir / "autonomy.json"
        state_file.write_text("NOT JSON")
        shell = _make_shell(project_path=str(tmp_path))
        assistant = ForgeAssistant(shell)
        assert assistant.read_autonomy_level() == 2

    def test_set_level_creates_file(self, tmp_path):
        """set_autonomy_level should create the state file at canonical path."""
        shell = _make_shell(project_path=str(tmp_path))
        assistant = ForgeAssistant(shell)
        result = assistant.set_autonomy_level(3)
        assert result is True
        # Canonical path is .forge/state/autonomy.json
        state_file = tmp_path / ".forge" / "state" / "autonomy.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["level"] == 3

    def test_set_level_no_project_returns_false(self):
        """Without a project path, set should return False."""
        shell = _make_shell(project_path=None)
        assistant = ForgeAssistant(shell)
        assert assistant.set_autonomy_level(3) is False


# ═══════════════════════════════════════════════════════════════════════════════
# Formation Explanations
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormationExplanations:
    """Validate human-friendly formation descriptions."""

    def test_known_formations_have_descriptions(self):
        """All formations in _FORMATION_DESCRIPTIONS should return text."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        for name in _FORMATION_DESCRIPTIONS:
            desc = assistant.explain_formation(name)
            assert isinstance(desc, str)
            assert len(desc) > 20

    def test_unknown_formation_fallback(self):
        """Unknown formation should return a fallback message."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        desc = assistant.explain_formation("nonexistent-formation")
        assert "nonexistent-formation" in desc


# ═══════════════════════════════════════════════════════════════════════════════
# Module-Level Constants
# ═══════════════════════════════════════════════════════════════════════════════


class TestModuleConstants:
    """Validate module-level constants are correctly defined."""

    def test_level_names_has_six_entries(self):
        """_LEVEL_NAMES should have 6 entries (A0-A5)."""
        assert len(_LEVEL_NAMES) >= 6

    def test_level_descriptions_match_names(self):
        """Every level in _LEVEL_NAMES should have a description."""
        for level in _LEVEL_NAMES:
            assert level in _LEVEL_DESCRIPTIONS, f"A{level} missing from _LEVEL_DESCRIPTIONS"

    def test_level_capabilities_match_names(self):
        """Every level in _LEVEL_NAMES should have capabilities."""
        for level in _LEVEL_NAMES:
            assert level in _LEVEL_CAPABILITIES, f"A{level} missing from _LEVEL_CAPABILITIES"

    def test_hints_are_all_nonempty_strings(self):
        """All hint values should be non-empty strings."""
        for key, value in _HINTS.items():
            assert isinstance(value, str), f"Hint '{key}' is not a string"
            assert len(value) > 10, f"Hint '{key}' too short"

    def test_formation_descriptions_are_nonempty(self):
        """All formation descriptions should be non-empty."""
        for name, desc in _FORMATION_DESCRIPTIONS.items():
            assert isinstance(desc, str)
            assert len(desc) > 20, f"Formation '{name}' description too short"

    def test_formation_descriptions_match_formations_registry(self):
        """All formation description keys must exist in formations.FORMATIONS."""
        from formations import FORMATIONS
        for name in _FORMATION_DESCRIPTIONS:
            assert name in FORMATIONS, (
                f"_FORMATION_DESCRIPTIONS has '{name}' but it's not in FORMATIONS. "
                f"Valid names: {list(FORMATIONS.keys())}"
            )

    def test_formation_recommendations_use_valid_names(self):
        """All formation names returned by get_formation_recommendation must exist in FORMATIONS."""
        from formations import FORMATIONS
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        test_goals = [
            "a simple CLI tool", "debug the login bug", "audit auth vulnerability",
            "a REST API for users", "full-stack app with Flask backend and React UI",
            "something vague", "optimize database performance",
        ]
        for goal in test_goals:
            name, _ = assistant.get_formation_recommendation(goal)
            assert name in FORMATIONS, (
                f"Recommendation for '{goal}' returned '{name}' which is not in FORMATIONS. "
                f"Valid: {list(FORMATIONS.keys())}"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# Skill Detection — Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestSkillDetectionEdgeCases:
    """Edge cases and boundary conditions for skill detection."""

    def test_set_skill_level_idempotent(self):
        """Setting the same level twice doesn't break state."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.set_skill_level("expert")
        assert assistant.skill_level == "expert"
        assert assistant._skill_detected is True
        assistant.set_skill_level("expert")
        assert assistant.skill_level == "expert"

    def test_set_skill_level_rejects_all_invalid_values(self):
        """Various invalid strings are all rejected."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        for invalid in ["god_mode", "", "EXPERT", "Beginner", "pro", "123"]:
            assistant.set_skill_level(invalid)
            assert assistant.skill_level == "beginner", f"'{invalid}' should not change level"

    def test_detect_first_run_no_builds_no_projects(self):
        """Zero signals: first_run=True, no builds, no projects -> beginner."""
        shell = _make_shell(builds_completed=0, first_run=True, recent_projects=[])
        assistant = ForgeAssistant(shell)
        level = assistant.detect_skill_level()
        assert level == "beginner"
        assert assistant._skill_detected is True

    def test_detect_exactly_2_builds_gives_signal(self):
        """2 builds contributes 1 signal (below intermediate threshold alone)."""
        shell = _make_shell(builds_completed=2, first_run=False)
        assistant = ForgeAssistant(shell)
        level = assistant.detect_skill_level()
        # 1 signal from builds + potentially 1 from env file = intermediate or beginner
        assert level in ("beginner", "intermediate")


# ═══════════════════════════════════════════════════════════════════════════════
# Formation Recommendation — Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestFormationRecommendationEdgeCases:
    """Edge cases for formation keyword matching."""

    def test_performance_keyword_triggers_perf(self):
        """'performance' keyword triggers perf-optimization."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("optimize the database performance")
        assert name == "perf-optimization"

    def test_fix_crash_triggers_bug(self):
        """'fix' + 'crash' triggers bug-investigation."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("fix the crash in login")
        assert name == "bug-investigation"

    def test_security_before_bug(self):
        """'security' keyword takes priority over 'bug' keywords."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("debug the security vulnerability")
        assert name == "security-review"

    def test_empty_description_defaults(self):
        """Empty string defaults to feature-impl."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, reason = assistant.get_formation_recommendation("")
        assert name == "feature-impl"
        assert isinstance(reason, str)

    def test_full_stack_keyword(self):
        """Explicit 'full-stack' phrase triggers new-project."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        name, _ = assistant.get_formation_recommendation("a full-stack application")
        assert name == "new-project"


# ═══════════════════════════════════════════════════════════════════════════════
# Contextual Hints — Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestContextualHintsEdgeCases:
    """Additional edge cases for hint delivery."""

    def test_intermediate_gets_tutorial_hints(self):
        """Intermediate users still get tutorial hints (unlike experts)."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "intermediate"
        hint = assistant.contextual_hint("after_plan")
        assert hint is not None
        assert len(hint) > 0

    def test_expert_gets_after_preview_hint(self):
        """Experts get after_preview hint (it's actionable)."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        hint = assistant.contextual_hint("after_preview")
        assert hint is not None

    def test_expert_gets_returning_expert_hint(self):
        """Experts get the returning_expert hint."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "expert"
        hint = assistant.contextual_hint("returning_expert")
        assert hint is not None
        assert "/status" in hint

    def test_beginner_gets_all_hints_once(self):
        """Beginner can get every hint key exactly once."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        assistant.skill_level = "beginner"
        delivered = set()
        for key in _HINTS:
            hint = assistant.contextual_hint(key)
            if hint is not None:
                delivered.add(key)
        # Beginner should see all hints
        assert delivered == set(_HINTS.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomy State — Edge Cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutonomyStateEdgeCases:
    """Edge cases for autonomy state read/write."""

    def test_overwrite_level(self, tmp_path):
        """Setting level twice overwrites the first value."""
        shell = _make_shell(project_path=str(tmp_path))
        assistant = ForgeAssistant(shell)
        assistant.set_autonomy_level(3)
        assistant.set_autonomy_level(1)
        assert assistant.read_autonomy_level() == 1

    def test_read_missing_level_key(self, tmp_path):
        """State file with empty JSON returns default 2."""
        state_dir = tmp_path / ".forge" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "autonomy.json").write_text("{}")
        shell = _make_shell(project_path=str(tmp_path))
        assistant = ForgeAssistant(shell)
        assert assistant.read_autonomy_level() == 2

    def test_a5_capabilities_same_as_a4_plus_logging(self):
        """A5 should have everything A4 has plus enhanced audit logging."""
        shell = _make_shell()
        assistant = ForgeAssistant(shell)
        can4, asks4 = assistant.get_autonomy_capabilities(4)
        can5, asks5 = assistant.get_autonomy_capabilities(5)
        assert len(can5) >= len(can4)
        assert len(asks5) == 0
        assert len(asks4) == 0
