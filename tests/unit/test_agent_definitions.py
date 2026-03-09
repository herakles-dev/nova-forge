"""Tests for agents/*.yml — validate all 20 agent definitions load and have required fields."""

from pathlib import Path

import pytest
import yaml

from forge_registry import AgentRegistry, AgentDefinition

AGENTS_DIR = Path(__file__).parent.parent.parent / "agents"

REQUIRED_FIELDS = {"name", "description", "category", "model_preference", "effort_level", "tool_policy", "system_prompt"}
VALID_CATEGORIES = {"spec", "specialist", "meta"}
VALID_MODEL_PREFS = {"smart", "fast"}
VALID_EFFORT_LEVELS = {"max", "high", "medium", "low"}
VALID_TOOL_POLICIES = {"full", "coding", "testing", "readonly", "minimal"}

EXPECTED_SPEC_AGENTS = [
    "spec-planner", "spec-architect", "spec-implementer", "spec-integrator",
    "spec-tester", "spec-security", "spec-optimizer", "spec-recovery", "spec-reviewer",
]

EXPECTED_SPECIALISTS = [
    "backend-architect", "frontend-specialist", "database-engineer",
    "testing-engineer", "security-engineer", "auth-specialist",
    "ci-cd-architect", "performance-optimizer", "ai-integration-specialist",
    "code-quality-engineer", "real-time-engineer",
]


@pytest.fixture(scope="module")
def registry():
    return AgentRegistry(AGENTS_DIR)


class TestAllDefinitionsLoad:
    def test_20_agents_loaded(self, registry):
        assert registry.count == 20

    def test_all_yaml_files_parse(self):
        for yml in sorted(AGENTS_DIR.glob("*.yml")):
            raw = yaml.safe_load(yml.read_text())
            assert isinstance(raw, dict), f"{yml.name} did not parse as dict"

    def test_required_fields_present(self):
        for yml in sorted(AGENTS_DIR.glob("*.yml")):
            raw = yaml.safe_load(yml.read_text())
            for field in REQUIRED_FIELDS:
                assert field in raw, f"{yml.name} missing required field: {field}"


class TestFieldValues:
    def test_categories_valid(self, registry):
        for agent in registry.list_all():
            assert agent.category in VALID_CATEGORIES, (
                f"{agent.name} has invalid category: {agent.category}"
            )

    def test_model_preferences_valid(self, registry):
        for agent in registry.list_all():
            assert agent.model_preference in VALID_MODEL_PREFS, (
                f"{agent.name} has invalid model_preference: {agent.model_preference}"
            )

    def test_effort_levels_valid(self, registry):
        for agent in registry.list_all():
            assert agent.effort_level in VALID_EFFORT_LEVELS, (
                f"{agent.name} has invalid effort_level: {agent.effort_level}"
            )

    def test_tool_policies_valid(self, registry):
        for agent in registry.list_all():
            assert agent.tool_policy in VALID_TOOL_POLICIES, (
                f"{agent.name} has invalid tool_policy: {agent.tool_policy}"
            )

    def test_system_prompts_nonempty(self, registry):
        for agent in registry.list_all():
            assert len(agent.system_prompt.strip()) > 20, (
                f"{agent.name} has too-short system_prompt"
            )

    def test_descriptions_nonempty(self, registry):
        for agent in registry.list_all():
            assert len(agent.description) > 10, (
                f"{agent.name} has too-short description"
            )


class TestSpecAgents:
    def test_all_9_spec_agents_present(self, registry):
        names = registry.list_names()
        for expected in EXPECTED_SPEC_AGENTS:
            assert expected in names, f"Missing spec agent: {expected}"

    def test_spec_agents_have_category_spec(self, registry):
        for name in EXPECTED_SPEC_AGENTS:
            agent = registry.get(name)
            assert agent.category == "spec", f"{name} should be category=spec"

    def test_spec_agents_have_formation_roles(self, registry):
        for name in EXPECTED_SPEC_AGENTS:
            agent = registry.get(name)
            assert len(agent.formation_roles) >= 1, (
                f"{name} should have at least 1 formation role"
            )


class TestSpecialists:
    def test_all_11_specialists_present(self, registry):
        names = registry.list_names()
        for expected in EXPECTED_SPECIALISTS:
            assert expected in names, f"Missing specialist: {expected}"

    def test_specialists_have_category_specialist(self, registry):
        for name in EXPECTED_SPECIALISTS:
            agent = registry.get(name)
            assert agent.category == "specialist", f"{name} should be category=specialist"


class TestNoDuplicates:
    def test_unique_names(self):
        names = []
        for yml in sorted(AGENTS_DIR.glob("*.yml")):
            raw = yaml.safe_load(yml.read_text())
            names.append(raw.get("name", yml.stem))
        assert len(names) == len(set(names)), f"Duplicate agent names found: {names}"


class TestFormationCoverage:
    """Verify key formations have agents mapped to their roles."""

    def test_feature_impl_has_backend(self, registry):
        agent = registry.route("feature-impl", "backend-impl")
        assert agent is not None

    def test_feature_impl_has_tester(self, registry):
        agent = registry.route("feature-impl", "tester")
        assert agent is not None

    def test_new_project_has_architect(self, registry):
        agent = registry.route("new-project", "architect")
        assert agent is not None

    def test_security_review_has_scanner(self, registry):
        agent = registry.route("security-review", "scanner")
        assert agent is not None
