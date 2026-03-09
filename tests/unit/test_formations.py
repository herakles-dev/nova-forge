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

    def test_eight_formations_registered(self):
        assert len(FORMATIONS) == 8
        expected = {
            "single-file", "lightweight-feature", "feature-impl",
            "new-project", "bug-investigation", "security-review",
            "perf-optimization", "code-review",
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
        ("complex", "large", "feature-impl"),
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

    def test_full_has_all_six_tools(self):
        assert TOOL_PROFILES["full"] == {
            "read_file", "write_file", "edit_file", "bash", "glob_files", "grep"
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
