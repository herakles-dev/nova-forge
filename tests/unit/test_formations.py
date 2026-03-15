"""Tests for formations.py — 8 formation definitions + DAAO routing."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from formations import (
    FORMATIONS, get_formation, select_formation, validate_ownership,
    Formation, Role, TOOL_PROFILES,
)


class TestFormationRegistry:
    """All 8 formations are importable and well-formed."""

    def test_eleven_formations_registered(self):
        assert len(FORMATIONS) == 11
        expected = {
            "single-file", "lightweight-feature", "feature-impl",
            "new-project", "bug-investigation", "security-review",
            "perf-optimization", "code-review",
            "recovery", "all-hands-planning", "integration-check",
        }
        assert set(FORMATIONS.keys()) == expected

    def test_each_formation_has_required_fields(self):
        for name, f in FORMATIONS.items():
            assert isinstance(f, Formation), f"{name} is not a Formation"
            assert f.name == name
            assert len(f.roles) >= 1, f"{name} has no roles"
            assert len(f.wave_order) >= 1, f"{name} has no wave_order"
            assert len(f.gate_criteria) >= 1, f"{name} has no gate_criteria"

    def test_all_wave_roles_exist_in_roles_list(self):
        """Every role name in wave_order must exist in the roles list."""
        for name, f in FORMATIONS.items():
            role_names = {r.name for r in f.roles}
            for wave in f.wave_order:
                for role_name in wave:
                    assert role_name in role_names, (
                        f"{name}: wave references role {role_name!r} "
                        f"not in roles {role_names}"
                    )

    def test_roles_have_valid_tool_policies(self):
        for name, f in FORMATIONS.items():
            for role in f.roles:
                assert role.tool_policy in TOOL_PROFILES, (
                    f"{name}/{role.name}: unknown tool_policy {role.tool_policy!r}"
                )


class TestGetFormation:
    def test_get_known_formation(self):
        f = get_formation("feature-impl")
        assert f.name == "feature-impl"
        assert len(f.roles) == 4

    def test_get_unknown_raises_key_error(self):
        with pytest.raises(KeyError, match="Unknown formation"):
            get_formation("nonexistent")


class TestSelectFormation:
    """DAAO routing table maps (complexity, scope) -> formation."""

    @pytest.mark.parametrize("complexity,scope,expected", [
        ("routine", "small", "single-file"),
        ("routine", "medium", "lightweight-feature"),
        ("medium", "small", "lightweight-feature"),
        ("medium", "medium", "lightweight-feature"),
        ("medium", "large", "feature-impl"),
        ("complex", "small", "lightweight-feature"),
        ("complex", "medium", "feature-impl"),
        ("complex", "large", "all-hands-planning"),
        ("novel", "small", "new-project"),
        ("novel", "medium", "new-project"),
        ("novel", "large", "new-project"),
    ])
    def test_daao_routing(self, complexity, scope, expected):
        f = select_formation(complexity, scope)
        assert f.name == expected

    def test_invalid_complexity_raises(self):
        with pytest.raises(ValueError, match="Unknown complexity"):
            select_formation("trivial", "small")

    def test_invalid_scope_raises(self):
        with pytest.raises(ValueError, match="Unknown scope"):
            select_formation("medium", "huge")


class TestValidateOwnership:
    def test_feature_impl_no_overlaps(self):
        f = get_formation("feature-impl")
        warnings = validate_ownership(f)
        assert warnings == [], f"Unexpected overlaps: {warnings}"

    def test_code_review_readonly_no_overlaps(self):
        f = get_formation("code-review")
        warnings = validate_ownership(f)
        assert warnings == []

    def test_synthetic_overlap_detected(self):
        """Two roles sharing the same directory in the same wave = conflict."""
        role_a = Role(
            name="a", model="x", tool_policy="full",
            ownership={"files": [], "directories": ["src/"], "patterns": []},
        )
        role_b = Role(
            name="b", model="x", tool_policy="full",
            ownership={"files": [], "directories": ["src/"], "patterns": []},
        )
        f = Formation(
            name="test", description="", roles=[role_a, role_b],
            wave_order=[["a", "b"]], gate_criteria=["pass"],
            tool_policy_defaults="full",
        )
        warnings = validate_ownership(f)
        assert len(warnings) >= 1
        assert "src/" in warnings[0]


class TestToolProfiles:
    def test_five_profiles_exist(self):
        assert len(TOOL_PROFILES) == 5
        assert "full" in TOOL_PROFILES
        assert "minimal" in TOOL_PROFILES

    def test_full_has_all_tools(self):
        assert TOOL_PROFILES["full"] == {
            "read_file", "write_file", "append_file", "edit_file", "bash", "glob_files", "grep",
            "claim_file", "check_context",
        }

    def test_readonly_no_write(self):
        ro = TOOL_PROFILES["readonly"]
        assert "write_file" not in ro
        assert "edit_file" not in ro
        assert "bash" not in ro
        assert "read_file" in ro

    def test_testing_no_write(self):
        t = TOOL_PROFILES["testing"]
        assert "write_file" not in t
        assert "edit_file" not in t
        assert "bash" in t  # Can run tests

    def test_minimal_empty(self):
        assert len(TOOL_PROFILES["minimal"]) == 0


class TestNewFormations:
    """Tests for recovery and all-hands-planning formations."""

    def test_recovery_formation_structure(self):
        f = get_formation("recovery")
        assert len(f.roles) == 3
        role_names = {r.name for r in f.roles}
        assert role_names == {"investigator", "fixer", "validator"}
        assert len(f.wave_order) == 3  # Sequential: investigate → fix → validate

    def test_recovery_no_ownership_overlaps(self):
        f = get_formation("recovery")
        warnings = validate_ownership(f)
        assert warnings == []

    def test_all_hands_planning_structure(self):
        f = get_formation("all-hands-planning")
        assert len(f.roles) == 5
        role_names = {r.name for r in f.roles}
        assert "synthesizer" in role_names
        assert "arch-reviewer" in role_names
        # First wave has 4 parallel reviewers, second has synthesizer
        assert len(f.wave_order[0]) == 4
        assert f.wave_order[1] == ["synthesizer"]

    def test_all_hands_planning_reviewers_readonly(self):
        f = get_formation("all-hands-planning")
        for role in f.roles:
            if "reviewer" in role.name:
                assert role.tool_policy == "readonly", (
                    f"{role.name} should be readonly, got {role.tool_policy}"
                )

    def test_all_hands_planning_no_ownership_overlaps(self):
        f = get_formation("all-hands-planning")
        warnings = validate_ownership(f)
        assert warnings == []

    def test_daao_routes_complex_large_to_all_hands(self):
        f = select_formation("complex", "large")
        assert f.name == "all-hands-planning"


class TestIntegrationCheckFormation:
    """Tests for the integration-check formation."""

    def test_integration_check_structure(self):
        f = get_formation("integration-check")
        assert len(f.roles) == 3
        role_names = {r.name for r in f.roles}
        assert role_names == {"auditor", "fixer", "verifier"}
        assert len(f.wave_order) == 3  # Sequential: audit -> fix -> verify

    def test_integration_check_auditor_readonly(self):
        f = get_formation("integration-check")
        auditor = next(r for r in f.roles if r.name == "auditor")
        assert auditor.tool_policy == "readonly"

    def test_integration_check_verifier_testing(self):
        f = get_formation("integration-check")
        verifier = next(r for r in f.roles if r.name == "verifier")
        assert verifier.tool_policy == "testing"

    def test_integration_check_no_ownership_overlaps(self):
        f = get_formation("integration-check")
        warnings = validate_ownership(f)
        assert warnings == []

    def test_integration_check_sequential_waves(self):
        f = get_formation("integration-check")
        assert f.wave_order == [["auditor"], ["fixer"], ["verifier"]]


class TestFormationEdgeCases:
    """Edge cases for formation validation and lookup."""

    def test_get_formation_error_message_lists_available(self):
        """Error message should list available formations."""
        with pytest.raises(KeyError) as exc_info:
            get_formation("nonexistent")
        assert "single-file" in str(exc_info.value)
        assert "feature-impl" in str(exc_info.value)

    def test_validate_ownership_empty_ownership_skipped(self):
        """Roles with all-empty ownership lists should not trigger overlaps."""
        role_a = Role(
            name="a", model="x", tool_policy="full",
            ownership={"files": [], "directories": [], "patterns": []},
        )
        role_b = Role(
            name="b", model="x", tool_policy="full",
            ownership={"files": [], "directories": [], "patterns": []},
        )
        f = Formation(
            name="test", description="", roles=[role_a, role_b],
            wave_order=[["a", "b"]], gate_criteria=["pass"],
            tool_policy_defaults="full",
        )
        warnings = validate_ownership(f)
        assert warnings == []

    def test_validate_ownership_cross_wave_no_conflict(self):
        """Roles in DIFFERENT waves sharing directories should NOT conflict."""
        role_a = Role(
            name="a", model="x", tool_policy="full",
            ownership={"files": [], "directories": ["src/"], "patterns": []},
        )
        role_b = Role(
            name="b", model="x", tool_policy="full",
            ownership={"files": [], "directories": ["src/"], "patterns": []},
        )
        f = Formation(
            name="test", description="", roles=[role_a, role_b],
            wave_order=[["a"], ["b"]],  # Different waves
            gate_criteria=["pass"],
            tool_policy_defaults="full",
        )
        warnings = validate_ownership(f)
        assert warnings == []  # No conflict because they're in different waves

    def test_all_formations_have_descriptions(self):
        """Every formation should have a non-empty description."""
        for name, f in FORMATIONS.items():
            assert f.description, f"{name} has empty description"

    def test_coding_profile_matches_full_minus_nothing(self):
        """Coding profile should be identical to full (both have all tools)."""
        assert TOOL_PROFILES["coding"] == TOOL_PROFILES["full"]
