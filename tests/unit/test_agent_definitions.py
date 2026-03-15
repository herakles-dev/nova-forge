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
        yml_files = sorted(AGENTS_DIR.glob("*.yml"))
        assert len(yml_files) == 20
        for yml in yml_files:
            raw = yaml.safe_load(yml.read_text())
            assert isinstance(raw, dict), f"{yml.name} did not parse as dict"

    def test_required_fields_present(self):
        for yml in sorted(AGENTS_DIR.glob("*.yml")):
            raw = yaml.safe_load(yml.read_text())
            for field in REQUIRED_FIELDS:
                assert field in raw, f"{yml.name} missing required field: {field}"

    def test_yaml_files_are_utf8(self):
        for yml in sorted(AGENTS_DIR.glob("*.yml")):
            content = yml.read_text(encoding="utf-8")
            assert len(content) > 0, f"{yml.name} is empty"


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

    def test_names_are_kebab_case(self, registry):
        import re
        for agent in registry.list_all():
            assert re.match(r'^[a-z][a-z0-9-]*$', agent.name), (
                f"{agent.name} is not kebab-case"
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

    def test_spec_agents_have_model_preference(self, registry):
        for name in EXPECTED_SPEC_AGENTS:
            agent = registry.get(name)
            assert agent.model_preference in VALID_MODEL_PREFS, (
                f"{name} has invalid model_preference"
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

    def test_specialists_have_system_prompts(self, registry):
        for name in EXPECTED_SPECIALISTS:
            agent = registry.get(name)
            assert len(agent.system_prompt.strip()) > 20, (
                f"{name} has too-short system_prompt"
            )


class TestNoDuplicates:
    def test_unique_names(self):
        names = []
        for yml in sorted(AGENTS_DIR.glob("*.yml")):
            raw = yaml.safe_load(yml.read_text())
            names.append(raw.get("name", yml.stem))
        assert len(names) == len(set(names)), f"Duplicate agent names found: {names}"

    def test_unique_filenames(self):
        files = [yml.stem for yml in sorted(AGENTS_DIR.glob("*.yml"))]
        assert len(files) == len(set(files))


class TestFormationCoverage:
    """Verify key formations have agents mapped to their roles."""

    def test_feature_impl_has_backend(self, registry):
        agent = registry.route("feature-impl", "backend-impl")
        assert agent is not None
        assert isinstance(agent, AgentDefinition)

    def test_feature_impl_has_tester(self, registry):
        agent = registry.route("feature-impl", "tester")
        assert agent is not None

    def test_new_project_has_architect(self, registry):
        agent = registry.route("new-project", "architect")
        assert agent is not None

    def test_security_review_has_scanner(self, registry):
        agent = registry.route("security-review", "scanner")
        assert agent is not None

    def test_each_spec_agent_has_at_least_one_formation(self, registry):
        for name in EXPECTED_SPEC_AGENTS:
            agent = registry.get(name)
            formations = {fr.formation for fr in agent.formation_roles}
            assert len(formations) >= 1, f"{name} has no formation assignments"
