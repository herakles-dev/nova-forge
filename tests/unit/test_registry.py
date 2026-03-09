"""Tests for forge_registry.py — Agent registry: load, discover, route."""

import tempfile
from pathlib import Path

import pytest

from forge_registry import AgentRegistry, AgentDefinition, FormationRoleMapping


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

    def test_empty_dir(self, tmp_path):
        reg = AgentRegistry(tmp_path)
        assert reg.count == 0

    def test_nonexistent_dir(self, tmp_path):
        reg = AgentRegistry(tmp_path / "nope")
        assert reg.count == 0


class TestGet:
    def test_get_by_name(self, registry):
        a = registry.get("impl-agent")
        assert isinstance(a, AgentDefinition)
        assert a.name == "impl-agent"
        assert a.category == "spec"
        assert a.model_preference == "fast"
        assert a.tool_policy == "coding"

    def test_get_unknown_raises(self, registry):
        with pytest.raises(KeyError, match="Unknown agent"):
            registry.get("nonexistent")

    def test_formation_roles_parsed(self, registry):
        a = registry.get("impl-agent")
        assert len(a.formation_roles) == 2
        assert a.formation_roles[0].formation == "feature-impl"
        assert a.formation_roles[0].role == "backend-impl"

    def test_ownership_parsed(self, registry):
        a = registry.get("impl-agent")
        assert "src/" in a.ownership.directories
        assert "*.py" in a.ownership.patterns

    def test_system_prompt_loaded(self, registry):
        a = registry.get("impl-agent")
        assert "implementation agent" in a.system_prompt


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


class TestCategoryFilter:
    def test_list_by_spec(self, registry):
        specs = registry.list_by_category("spec")
        assert len(specs) == 2

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

    def test_properties(self, registry):
        a = registry.get("impl-agent")
        assert a.is_spec_agent is True
        assert a.is_specialist is False
        b = registry.get("arch-agent")
        assert b.is_specialist is True
