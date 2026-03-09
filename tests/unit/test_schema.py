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
