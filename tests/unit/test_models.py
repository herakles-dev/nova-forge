"""Tests for forge_models — model intelligence layer."""

import pytest
from forge_models import (
    MODEL_CAPABILITIES,
    MODEL_PRESETS,
    PHASE_DEFAULTS,
    CostTracker,
    ModelCapability,
    apply_preset,
    estimate_cost,
    format_cost,
    get_active_preset,
    get_capability,
    get_escalation_model,
)


class TestModelCapabilities:
    def test_all_seven_models_present(self):
        assert len(MODEL_CAPABILITIES) == 7
        expected = {"nova-lite", "nova-pro", "nova-premier", "gemini-flash", "gemini-pro", "claude-sonnet", "claude-haiku"}
        assert set(MODEL_CAPABILITIES.keys()) == expected

    def test_all_entries_are_model_capability(self):
        for alias, cap in MODEL_CAPABILITIES.items():
            assert isinstance(cap, ModelCapability)
            assert cap.alias == alias
            assert cap.model_id
            assert cap.provider in ("bedrock", "openai", "anthropic")
            assert cap.cost_per_1k_input >= 0
            assert cap.cost_per_1k_output >= 0
            assert cap.context_window > 0
            assert len(cap.strengths) > 0

    def test_escalation_targets_are_valid(self):
        for alias, cap in MODEL_CAPABILITIES.items():
            if cap.escalation_target is not None:
                assert cap.escalation_target in MODEL_CAPABILITIES, (
                    f"{alias} escalation target '{cap.escalation_target}' not in registry"
                )


class TestEstimateCost:
    def test_known_input(self):
        # nova-lite: $0.00006 per 1K in, $0.00024 per 1K out
        cost = estimate_cost("nova-lite", 1000, 1000)
        assert abs(cost - 0.00030) < 1e-8

    def test_zero_tokens(self):
        assert estimate_cost("nova-lite", 0, 0) == 0.0

    def test_large_tokens(self):
        cost = estimate_cost("nova-lite", 10_000, 5_000)
        expected = 10 * 0.00006 + 5 * 0.00024
        assert abs(cost - expected) < 1e-8

    def test_unknown_model_returns_zero(self):
        assert estimate_cost("unknown-model-xyz", 1000, 1000) == 0.0

    def test_by_full_model_id(self):
        cost = estimate_cost("bedrock/us.amazon.nova-2-lite-v1:0", 1000, 1000)
        assert cost > 0


class TestFormatCost:
    def test_zero(self):
        assert format_cost(0) == "$0.00"
        assert format_cost(0.0) == "$0.00"

    def test_small_cost(self):
        result = format_cost(0.0012)
        assert result == "$0.0012"

    def test_normal_cost(self):
        assert format_cost(1.23) == "$1.23"

    def test_large_cost(self):
        assert format_cost(42.5) == "$42.50"

    def test_penny(self):
        assert format_cost(0.01) == "$0.01"

    def test_sub_penny(self):
        result = format_cost(0.005)
        assert result == "$0.0050"


class TestGetEscalationModel:
    def test_nova_lite_escalates_to_pro(self):
        result = get_escalation_model("nova-lite")
        assert result == "bedrock/us.amazon.nova-pro-v1:0"

    def test_nova_pro_escalates_to_premier(self):
        result = get_escalation_model("nova-pro")
        assert result == "bedrock/us.amazon.nova-premier-v1:0"

    def test_nova_premier_no_escalation(self):
        assert get_escalation_model("nova-premier") is None

    def test_gemini_flash_escalates_to_pro(self):
        result = get_escalation_model("gemini-flash")
        assert result == "openrouter/google/gemini-2.5-pro-preview"

    def test_unknown_model_returns_none(self):
        assert get_escalation_model("nonexistent-model") is None

    def test_by_full_model_id(self):
        result = get_escalation_model("bedrock/us.amazon.nova-2-lite-v1:0")
        assert result == "bedrock/us.amazon.nova-pro-v1:0"

    def test_claude_haiku_escalates_to_sonnet(self):
        result = get_escalation_model("claude-haiku")
        assert result == "anthropic/claude-sonnet-4-6-20250514"


class TestGetCapability:
    def test_by_alias(self):
        cap = get_capability("nova-lite")
        assert cap is not None
        assert cap.alias == "nova-lite"

    def test_by_full_model_id(self):
        cap = get_capability("bedrock/us.amazon.nova-pro-v1:0")
        assert cap is not None
        assert cap.alias == "nova-pro"

    def test_unknown_returns_none(self):
        assert get_capability("does-not-exist") is None


class TestCostTracker:
    def test_record_accumulates(self):
        tracker = CostTracker()
        c1 = tracker.record("nova-lite", 1000, 500)
        c2 = tracker.record("nova-lite", 2000, 1000)
        assert tracker.total_cost == pytest.approx(c1 + c2)

    def test_per_model_tracking(self):
        tracker = CostTracker()
        tracker.record("nova-lite", 1000, 500)
        tracker.record("gemini-flash", 1000, 500)
        assert "nova-lite" in tracker.per_model
        assert "gemini-flash" in tracker.per_model

    def test_per_task_tracking(self):
        tracker = CostTracker()
        tracker.record("nova-lite", 1000, 500, task_id=1)
        tracker.record("nova-lite", 2000, 1000, task_id=2)
        assert 1 in tracker.per_task
        assert 2 in tracker.per_task

    def test_unknown_model_records_zero(self):
        tracker = CostTracker()
        cost = tracker.record("unknown-xyz", 1000, 1000)
        assert cost == 0.0
        assert tracker.total_cost == 0.0

    def test_summary(self):
        tracker = CostTracker()
        tracker.record("nova-lite", 1000, 1000)
        s = tracker.summary()
        assert "total_cost" in s
        assert "total_cost_formatted" in s
        assert "per_model" in s
        assert "per_task" in s

    def test_format_summary(self):
        tracker = CostTracker()
        tracker.record("nova-lite", 1000, 1000, task_id=1)
        text = tracker.format_summary()
        assert "Total:" in text
        assert "nova-lite" in text


class TestPhaseDefaults:
    def test_all_four_phases(self):
        assert set(PHASE_DEFAULTS.keys()) == {"planning", "coding", "review", "escalation"}

    def test_defaults_are_valid_aliases(self):
        for phase, alias in PHASE_DEFAULTS.items():
            assert alias in MODEL_CAPABILITIES, f"Phase '{phase}' default '{alias}' not in MODEL_CAPABILITIES"


class TestModelPresets:
    def test_all_presets_exist(self):
        assert set(MODEL_PRESETS.keys()) == {"nova", "mixed", "premium"}

    def test_preset_structure(self):
        for name, preset in MODEL_PRESETS.items():
            assert "description" in preset
            assert "default_model" in preset
            assert "phases" in preset
            assert "formation_fast" in preset
            assert "formation_smart" in preset
            assert set(preset["phases"].keys()) == {"planning", "coding", "review", "escalation"}

    def test_preset_models_are_valid(self):
        for name, preset in MODEL_PRESETS.items():
            assert preset["default_model"] in MODEL_CAPABILITIES
            assert preset["formation_fast"] in MODEL_CAPABILITIES
            assert preset["formation_smart"] in MODEL_CAPABILITIES
            for phase, alias in preset["phases"].items():
                assert alias in MODEL_CAPABILITIES, f"Preset '{name}' phase '{phase}' uses unknown model '{alias}'"

    def test_nova_preset_is_aws_only(self):
        preset = MODEL_PRESETS["nova"]
        for phase, alias in preset["phases"].items():
            cap = MODEL_CAPABILITIES[alias]
            assert cap.provider == "bedrock", f"Nova preset phase '{phase}' uses non-AWS model '{alias}'"
        assert MODEL_CAPABILITIES[preset["formation_fast"]].provider == "bedrock"
        assert MODEL_CAPABILITIES[preset["formation_smart"]].provider == "bedrock"

    def test_apply_preset_updates_phase_defaults(self):
        # Save originals
        original = dict(PHASE_DEFAULTS)
        try:
            apply_preset("nova")
            assert PHASE_DEFAULTS["coding"] == "nova-lite"
            assert get_active_preset() == "nova"

            apply_preset("mixed")
            assert PHASE_DEFAULTS["coding"] == "gemini-flash"
            assert get_active_preset() == "mixed"
        finally:
            # Restore
            PHASE_DEFAULTS.update(original)

    def test_apply_unknown_preset_raises(self):
        with pytest.raises(KeyError):
            apply_preset("nonexistent")

    def test_premium_preset_uses_pro(self):
        preset = MODEL_PRESETS["premium"]
        assert preset["default_model"] == "nova-pro"
        assert preset["phases"]["coding"] == "nova-pro"
        assert preset["phases"]["escalation"] == "nova-premier"


# ── compute_turn_budget tests ──────────────────────────────────────────────


class TestComputeTurnBudget:
    """Tests for config.compute_turn_budget() — adaptive turn budget by task complexity."""

    def setup_method(self):
        from config import compute_turn_budget
        self.compute = compute_turn_budget

    def test_zero_files_returns_base_12(self):
        result = self.compute({"files": []})
        assert result["soft_limit"] == 12

    def test_one_file_returns_base_15(self):
        result = self.compute({"files": ["app.py"]})
        assert result["soft_limit"] == 15

    def test_two_files_returns_base_18(self):
        result = self.compute({"files": ["app.py", "models.py"]})
        assert result["soft_limit"] == 18

    def test_three_files_returns_scaled(self):
        result = self.compute({"files": ["a.py", "b.py", "c.py"]})
        # 3 files: min(12 + 3*4, 30) = min(24, 30) = 24
        assert result["soft_limit"] == 24

    def test_many_files_capped_at_30(self):
        files = [f"f{i}.py" for i in range(10)]
        result = self.compute({"files": files})
        # 10 files: min(12 + 10*4, 30) = min(52, 30) = 30
        assert result["soft_limit"] == 30

    def test_server_acceptance_criteria_adds_budget(self):
        result_plain = self.compute({"files": ["app.py"]})
        result_server = self.compute({
            "files": ["app.py"],
            "acceptance_criteria": ["curl http://localhost:5000/api/tasks"],
        })
        assert result_server["soft_limit"] == result_plain["soft_limit"] + 4

    def test_blocked_by_adds_budget(self):
        result_plain = self.compute({"files": ["app.py"]})
        result_blocked = self.compute({
            "files": ["app.py"],
            "blocked_by": [1, 2],
        })
        assert result_blocked["soft_limit"] == result_plain["soft_limit"] + 2

    def test_hard_limit_exceeds_soft(self):
        result = self.compute({"files": ["app.py"]})
        assert result["hard_limit"] > result["soft_limit"]

    def test_hard_limit_formula(self):
        result = self.compute({"files": ["app.py"]})
        soft = result["soft_limit"]
        expected_hard = max(soft + 4, int(soft * 1.3))
        assert result["hard_limit"] == expected_hard

    def test_verify_budget_is_quarter_of_soft(self):
        result = self.compute({"files": ["app.py", "models.py"]})
        assert result["verify_budget"] == max(2, result["soft_limit"] // 4)

    def test_escalation_turns_is_half_of_soft(self):
        result = self.compute({"files": ["app.py", "models.py"]})
        assert result["escalation_turns"] == max(8, result["soft_limit"] // 2)

    def test_ceiling_caps_soft_limit(self):
        files = [f"f{i}.py" for i in range(10)]
        result = self.compute({"files": files}, max_turns_ceiling=20)
        assert result["soft_limit"] <= 20

    def test_ceiling_caps_hard_limit(self):
        files = [f"f{i}.py" for i in range(10)]
        result = self.compute({"files": files}, max_turns_ceiling=20)
        # hard_limit never wildly exceeds ceiling: max(soft+4, soft*1.3) capped at ceiling+4
        assert result["hard_limit"] <= 24  # ceiling + 4

    def test_missing_files_key_uses_zero(self):
        result = self.compute({})
        assert result["soft_limit"] == 12

    def test_returns_all_required_keys(self):
        result = self.compute({"files": ["app.py"]})
        assert set(result.keys()) == {"soft_limit", "hard_limit", "verify_budget", "escalation_turns"}
