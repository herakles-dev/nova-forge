"""Tests for forge_registry.py — Agent registry: load, discover, route."""

import tempfile
from pathlib import Path

import pytest

from forge_registry import AgentRegistry, AgentDefinition, FormationRoleMapping, OwnershipSpec


@pytest.fixture
def agents_dir():
    """Create a temp dir with test agent definitions."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "impl.yml").write_text(
            "name: impl-agent\n"
            "description: Implementation agent\n"
            "category: spec\n"
            "model_preference: fast\n"
            "effort_level: high\n"
            "tool_policy: coding\n"
            "formation_roles:\n"
            "  - formation: feature-impl\n"
            "    role: backend-impl\n"
            "  - formation: lightweight-feature\n"
            "    role: implementer\n"
            "ownership_patterns:\n"
            "  directories:\n"
            "    - src/\n"
            "  patterns:\n"
            "    - '*.py'\n"
            "system_prompt: |\n"
            "  You are an implementation agent.\n"
        )
        (d / "tester.yml").write_text(
            "name: tester-agent\n"
            "description: Testing agent for validation\n"
            "category: spec\n"
            "model_preference: fast\n"
            "effort_level: medium\n"
            "tool_policy: testing\n"
            "formation_roles:\n"
            "  - formation: feature-impl\n"
            "    role: tester\n"
            "system_prompt: |\n"
            "  You are a testing agent.\n"
        )
        (d / "architect.yml").write_text(
            "name: arch-agent\n"
            "description: Security architecture specialist\n"
            "category: specialist\n"
            "model_preference: smart\n"
            "effort_level: max\n"
            "tool_policy: full\n"
            "formation_roles:\n"
            "  - formation: new-project\n"
            "    role: architect\n"
            "system_prompt: |\n"
            "  You are an architect agent.\n"
        )
        yield d


@pytest.fixture
def registry(agents_dir):
    return AgentRegistry(agents_dir)


class TestLoading:
    def test_loads_all_agents(self, registry):
        assert registry.count == 3

    def test_list_names(self, registry):
        names = registry.list_names()
        assert "impl-agent" in names
        assert "tester-agent" in names
        assert "arch-agent" in names

    def test_list_names_sorted(self, registry):
        names = registry.list_names()
        assert names == sorted(names)

    def test_empty_dir(self, tmp_path):
        reg = AgentRegistry(tmp_path)
        assert reg.count == 0
        assert reg.list_names() == []

    def test_nonexistent_dir(self, tmp_path):
        reg = AgentRegistry(tmp_path / "nope")
        assert reg.count == 0

    def test_list_all_returns_definitions(self, registry):
        all_agents = registry.list_all()
        assert len(all_agents) == 3
        assert all(isinstance(a, AgentDefinition) for a in all_agents)

    def test_invalid_yaml_skipped(self, tmp_path):
        (tmp_path / "bad.yml").write_text("not: valid: yaml: {{{")
        (tmp_path / "good.yml").write_text(
            "name: good-agent\n"
            "description: A good agent\n"
            "category: spec\n"
            "model_preference: fast\n"
            "effort_level: high\n"
            "tool_policy: coding\n"
            "system_prompt: You are good.\n"
        )
        reg = AgentRegistry(tmp_path)
        assert reg.count >= 1
        assert "good-agent" in reg.list_names()


class TestGet:
    def test_get_by_name(self, registry):
        a = registry.get("impl-agent")
        assert isinstance(a, AgentDefinition)
        assert a.name == "impl-agent"
        assert a.description == "Implementation agent"
        assert a.category == "spec"
        assert a.model_preference == "fast"
        assert a.effort_level == "high"
        assert a.tool_policy == "coding"

    def test_get_unknown_raises(self, registry):
        with pytest.raises(KeyError, match="Unknown agent"):
            registry.get("nonexistent")

    def test_get_unknown_error_lists_available(self, registry):
        with pytest.raises(KeyError) as exc_info:
            registry.get("nonexistent")
        assert "impl-agent" in str(exc_info.value)

    def test_formation_roles_parsed(self, registry):
        a = registry.get("impl-agent")
        assert len(a.formation_roles) == 2
        assert a.formation_roles[0].formation == "feature-impl"
        assert a.formation_roles[0].role == "backend-impl"
        assert a.formation_roles[1].formation == "lightweight-feature"
        assert a.formation_roles[1].role == "implementer"

    def test_ownership_parsed(self, registry):
        a = registry.get("impl-agent")
        assert isinstance(a.ownership, OwnershipSpec)
        assert "src/" in a.ownership.directories
        assert "*.py" in a.ownership.patterns
        assert a.ownership.files == []

    def test_system_prompt_loaded(self, registry):
        a = registry.get("impl-agent")
        assert "implementation agent" in a.system_prompt

    def test_source_file_tracked(self, registry, agents_dir):
        a = registry.get("impl-agent")
        assert a.source_file is not None
        assert a.source_file.name == "impl.yml"

    def test_agent_without_ownership(self, registry):
        a = registry.get("tester-agent")
        assert a.ownership.directories == []
        assert a.ownership.patterns == []
        assert a.ownership.files == []


class TestDiscover:
    def test_discover_by_name(self, registry):
        results = registry.discover("impl")
        assert len(results) >= 1
        assert results[0].name == "impl-agent"

    def test_discover_by_description(self, registry):
        results = registry.discover("security")
        assert any(a.name == "arch-agent" for a in results)

    def test_discover_by_role(self, registry):
        results = registry.discover("tester")
        assert any(a.name == "tester-agent" for a in results)

    def test_discover_no_match(self, registry):
        results = registry.discover("zzzzzzzzz")
        assert len(results) == 0

    def test_discover_max_results(self, registry):
        results = registry.discover("agent", max_results=2)
        assert len(results) <= 2

    def test_discover_returns_best_first(self, registry):
        results = registry.discover("impl-agent")
        assert results[0].name == "impl-agent"

    def test_discover_case_insensitive(self, registry):
        results = registry.discover("IMPL")
        assert len(results) >= 1
        assert any(a.name == "impl-agent" for a in results)


class TestRoute:
    def test_route_exact_match(self, registry):
        a = registry.route("feature-impl", "backend-impl")
        assert a is not None
        assert a.name == "impl-agent"

    def test_route_tester(self, registry):
        a = registry.route("feature-impl", "tester")
        assert a is not None
        assert a.name == "tester-agent"

    def test_route_no_match(self, registry):
        a = registry.route("unknown-formation", "unknown-role")
        assert a is None

    def test_route_or_default(self, registry):
        a = registry.route_or_default("unknown", "unknown", default_name="impl-agent")
        assert a.name == "impl-agent"

    def test_route_or_default_falls_through(self, registry):
        # When default name also doesn't exist, should get first available
        a = registry.route_or_default("unknown", "unknown", default_name="also-unknown")
        assert a is not None  # Falls back to first available agent

    def test_route_or_default_empty_registry(self, tmp_path):
        reg = AgentRegistry(tmp_path)
        with pytest.raises(KeyError, match="No agents loaded"):
            reg.route_or_default("unknown", "unknown", default_name="nope")

    def test_route_partial_role_match(self, registry):
        # "backend" partially matches "backend-impl" role
        a = registry.route("feature-impl", "backend")
        assert a is not None


class TestCategoryFilter:
    def test_list_by_spec(self, registry):
        specs = registry.list_by_category("spec")
        assert len(specs) == 2
        assert all(a.category == "spec" for a in specs)

    def test_list_by_specialist(self, registry):
        specialists = registry.list_by_category("specialist")
        assert len(specialists) == 1
        assert specialists[0].name == "arch-agent"

    def test_list_by_unknown(self, registry):
        assert registry.list_by_category("meta") == []


class TestReload:
    def test_reload_returns_count(self, registry):
        count = registry.reload()
        assert count == 3

    def test_reload_clears_and_reloads(self, registry, agents_dir):
        # Add a new agent file
        (agents_dir / "new.yml").write_text(
            "name: new-agent\n"
            "description: New agent\n"
            "category: spec\n"
            "model_preference: fast\n"
            "effort_level: low\n"
            "tool_policy: minimal\n"
            "system_prompt: You are new.\n"
        )
        count = registry.reload()
        assert count == 4
        assert "new-agent" in registry.list_names()

    def test_properties(self, registry):
        a = registry.get("impl-agent")
        assert a.is_spec_agent is True
        assert a.is_specialist is False
        b = registry.get("arch-agent")
        assert b.is_specialist is True
        assert b.is_spec_agent is False


class TestFormationRoleMapping:
    def test_creation(self):
        frm = FormationRoleMapping(formation="feature-impl", role="backend-impl")
        assert frm.formation == "feature-impl"
        assert frm.role == "backend-impl"


class TestOwnershipSpec:
    def test_defaults(self):
        o = OwnershipSpec()
        assert o.directories == []
        assert o.files == []
        assert o.patterns == []

    def test_with_values(self):
        o = OwnershipSpec(directories=["src/"], patterns=["*.py"])
        assert o.directories == ["src/"]
        assert o.patterns == ["*.py"]
