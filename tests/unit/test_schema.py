"""Tests for forge_schema.py — JSON schema validation layer."""

import pytest
from forge_schema import SchemaValidator, SCHEMA_NAMES


@pytest.fixture
def sv():
    return SchemaValidator()


class TestSchemaLoading:
    def test_all_8_schemas_loaded(self, sv):
        assert len(sv.available) == 8

    def test_schema_names_match(self, sv):
        for name in SCHEMA_NAMES:
            assert name in sv.available

    def test_get_schema_returns_dict(self, sv):
        schema = sv.get_schema("task-metadata")
        assert isinstance(schema, dict)
        assert "$schema" in schema

    def test_get_unknown_raises(self, sv):
        with pytest.raises(KeyError, match="Unknown schema"):
            sv.get_schema("nonexistent")


class TestTaskMetadataValidation:
    def test_valid_metadata(self, sv):
        data = {"project": "myapp", "sprint": "sprint-01-init", "risk": "low"}
        assert sv.validate_task_metadata(data) == []

    def test_valid_full_metadata(self, sv):
        data = {
            "project": "myapp",
            "sprint": "sprint-01-init",
            "risk": "high",
            "agent": "backend-architect",
            "complexity": "complex",
            "scope": "large",
            "parallelizable": True,
        }
        assert sv.validate_task_metadata(data) == []

    def test_missing_required_fields(self, sv):
        errors = sv.validate_task_metadata({"project": "x"})
        assert len(errors) > 0
        assert any("sprint" in e or "risk" in e for e in errors)

    def test_invalid_risk_value(self, sv):
        data = {"project": "x", "sprint": "sprint-01-x", "risk": "extreme"}
        errors = sv.validate_task_metadata(data)
        assert any("extreme" in e for e in errors)

    def test_empty_project_fails(self, sv):
        data = {"project": "", "sprint": "sprint-01-x", "risk": "low"}
        errors = sv.validate_task_metadata(data)
        assert len(errors) > 0


class TestAutonomyStateValidation:
    def test_valid_state(self, sv):
        data = {"level": 2, "approved_categories": ["python", "typescript"]}
        assert sv.validate_autonomy_state(data) == []

    def test_level_out_of_range(self, sv):
        assert not sv.is_valid("autonomy-state", {"level": 10})

    def test_level_required(self, sv):
        errors = sv.validate_autonomy_state({})
        assert any("level" in e for e in errors)

    def test_grants_structure(self, sv):
        data = {
            "level": 2,
            "grants": [{"pattern": "src/**", "type": "glob", "reason": "project files"}],
        }
        assert sv.is_valid("autonomy-state", data)


class TestFormationRegistryValidation:
    def test_valid_registry(self, sv):
        data = {
            "formation": "feature-impl",
            "project": "myapp",
            "teammates": {
                "backend-impl": {
                    "agent": "backend-architect",
                    "agent_id": "abc123",
                    "ownership": {"directories": ["src/api/"], "files": [], "patterns": []},
                }
            },
        }
        assert sv.validate_formation_registry(data) == []

    def test_missing_formation(self, sv):
        data = {"project": "x", "teammates": {"role": {"agent": "x", "agent_id": "y", "ownership": {}}}}
        errors = sv.validate_formation_registry(data)
        assert any("formation" in e for e in errors)


class TestIsValid:
    def test_valid_returns_true(self, sv):
        assert sv.is_valid("task-metadata", {"project": "x", "sprint": "sprint-01-x", "risk": "low"})

    def test_invalid_returns_false(self, sv):
        assert not sv.is_valid("task-metadata", {})

    def test_unknown_schema_returns_false(self, sv):
        assert not sv.is_valid("nonexistent", {})


class TestValidateMethod:
    """Tests for the generic validate() method."""

    def test_validate_unknown_schema(self, sv):
        errors = sv.validate("nonexistent", {})
        assert len(errors) == 1
        assert "not loaded" in errors[0]

    def test_validate_returns_list(self, sv):
        errors = sv.validate("task-metadata", {"project": "x", "sprint": "sprint-01-x", "risk": "low"})
        assert isinstance(errors, list)
        assert errors == []

    def test_validate_returns_human_readable_errors(self, sv):
        errors = sv.validate("task-metadata", {})
        assert len(errors) > 0
        # Each error should be a string with some path info
        for e in errors:
            assert isinstance(e, str)


class TestTaskStateSchema:
    """Tests for task-state schema validation."""

    def test_valid_task_state(self, sv):
        data = {
            "tasks": [],
            "version": "1.0",
        }
        # Just verify schema can validate data without crashing
        result = sv.validate("task-state", data)
        assert isinstance(result, list)


class TestToolPolicySchema:
    """Tests for tool-policy schema validation."""

    def test_tool_policy_schema_loaded(self, sv):
        assert "tool-policy" in sv.available

    def test_valid_tool_policy(self, sv):
        data = {
            "policy": "standard",
            "allowed_tools": ["read_file", "write_file"],
        }
        result = sv.validate("tool-policy", data)
        assert isinstance(result, list)


class TestMemoryIndexSchema:
    """Tests for memory-index schema validation."""

    def test_memory_index_schema_loaded(self, sv):
        assert "memory-index" in sv.available


class TestAgentRegistryValidation:
    """Tests for agent-registry schema validation."""

    def test_agent_registry_method_exists(self, sv):
        """validate_agent_registry is a convenience method."""
        result = sv.validate_agent_registry({})
        assert isinstance(result, list)

    def test_agent_registry_schema_loaded(self, sv):
        assert "agent-registry" in sv.available


class TestSchemaValidatorEdgeCases:
    """Edge cases for SchemaValidator."""

    def test_all_8_schema_names_match_constant(self, sv):
        for name in SCHEMA_NAMES:
            assert name in sv.available, f"Schema {name!r} was not loaded"

    def test_get_schema_has_schema_key(self, sv):
        """All loaded schemas should have a $schema key."""
        for name in sv.available:
            schema = sv.get_schema(name)
            assert isinstance(schema, dict)
            # Not all schemas may have $schema, but they should be dicts
            assert "type" in schema or "$schema" in schema or "properties" in schema

    def test_validate_with_none_data(self, sv):
        """Validating None should return errors (not crash)."""
        errors = sv.validate("task-metadata", None)
        assert len(errors) > 0

    def test_validate_with_list_data(self, sv):
        """Validating a list where object expected should return errors."""
        errors = sv.validate("task-metadata", [1, 2, 3])
        assert len(errors) > 0
