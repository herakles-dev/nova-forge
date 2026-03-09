"""Nova Forge schema validation — loads V11 JSON schemas and validates data.

Provides a SchemaValidator that loads all 8 schemas from the schemas/ directory
and exposes validate() for any schema by name. Used by TaskStore (task-metadata),
formations (formation-registry, formation-config), AutonomyManager (autonomy-state),
and AgentRegistry (agent-registry).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator, ValidationError

logger = logging.getLogger(__name__)

# ── Schema names ────────────────────────────────────────────────────────────

SCHEMA_NAMES = (
    "task-metadata",
    "task-state",
    "agent-registry",
    "autonomy-state",
    "formation-config",
    "formation-registry",
    "memory-index",
    "tool-policy",
)

# ── Schema directory resolution ─────────────────────────────────────────────

_SCHEMAS_DIR = Path(__file__).parent / "schemas"


def _load_schema(name: str, schemas_dir: Path | None = None) -> dict:
    """Load a single JSON schema by name."""
    base = schemas_dir or _SCHEMAS_DIR
    path = base / f"{name}.schema.json"
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


# ── SchemaValidator ─────────────────────────────────────────────────────────


class SchemaValidator:
    """Validates data against V11/Nova Forge JSON schemas.

    Usage::

        sv = SchemaValidator()
        errors = sv.validate("task-metadata", {"project": "myapp", "sprint": "sprint-01-init", "risk": "low"})
        if errors:
            print(f"Validation failed: {errors}")
    """

    def __init__(self, schemas_dir: Path | None = None) -> None:
        self._schemas: dict[str, dict] = {}
        self._validators: dict[str, Draft202012Validator] = {}
        self._schemas_dir = schemas_dir or _SCHEMAS_DIR
        self._load_all()

    def _load_all(self) -> None:
        """Load all schemas from the schemas directory."""
        for name in SCHEMA_NAMES:
            try:
                schema = _load_schema(name, self._schemas_dir)
                self._schemas[name] = schema
                self._validators[name] = Draft202012Validator(schema)
                logger.debug("Loaded schema: %s", name)
            except FileNotFoundError:
                logger.warning("Schema not found: %s — skipping", name)
            except json.JSONDecodeError as exc:
                logger.warning("Invalid JSON in schema %s: %s", name, exc)

    @property
    def available(self) -> list[str]:
        """Return names of successfully loaded schemas."""
        return sorted(self._schemas.keys())

    def get_schema(self, name: str) -> dict:
        """Return the raw schema dict for *name*.

        Raises:
            KeyError: If the schema was not loaded.
        """
        if name not in self._schemas:
            raise KeyError(
                f"Unknown schema {name!r}. Available: {', '.join(self.available)}"
            )
        return self._schemas[name]

    def validate(self, schema_name: str, data: Any) -> list[str]:
        """Validate *data* against the named schema.

        Returns:
            Empty list if valid, or a list of human-readable error strings.
        """
        if schema_name not in self._validators:
            return [f"Schema {schema_name!r} not loaded"]

        validator = self._validators[schema_name]
        errors: list[str] = []
        for error in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
            path = ".".join(str(p) for p in error.absolute_path) or "(root)"
            errors.append(f"{path}: {error.message}")
        return errors

    def is_valid(self, schema_name: str, data: Any) -> bool:
        """Quick boolean check — is *data* valid against the named schema?"""
        if schema_name not in self._validators:
            return False
        return self._validators[schema_name].is_valid(data)

    def validate_task_metadata(self, metadata: dict) -> list[str]:
        """Convenience: validate task metadata dict."""
        return self.validate("task-metadata", metadata)

    def validate_autonomy_state(self, state: dict) -> list[str]:
        """Convenience: validate autonomy state dict."""
        return self.validate("autonomy-state", state)

    def validate_formation_registry(self, registry: dict) -> list[str]:
        """Convenience: validate formation registry dict."""
        return self.validate("formation-registry", registry)

    def validate_agent_registry(self, registry: dict) -> list[str]:
        """Convenience: validate agent registry dict."""
        return self.validate("agent-registry", registry)
