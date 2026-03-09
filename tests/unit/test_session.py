"""Tests for forge_session.py — session management + state persistence."""

import json
from pathlib import Path

import pytest

from forge_session import (
    SessionManager, SessionStatus, AutonomyState, FormationState,
)


@pytest.fixture
def project(tmp_path):
    """Create a minimal project directory."""
    return tmp_path


@pytest.fixture
def sm(project):
    """SessionManager for the project."""
    return SessionManager(project)


class TestInit:
    def test_init_creates_forge_dir(self, sm, project):
        sm.init()
        assert (project / ".forge").is_dir()
        assert (project / ".forge" / "state").is_dir()
        assert (project / ".forge" / "audit").is_dir()

    def test_is_initialized_false_by_default(self, sm):
        assert not sm.is_initialized()

    def test_is_initialized_true_after_init(self, sm):
        sm.init()
        assert sm.is_initialized()

    def test_double_init_is_safe(self, sm):
        sm.init()
        sm.init()
        assert sm.is_initialized()


class TestTaskState:
    def test_load_empty_returns_defaults(self, sm):
        sm.init()
        state = sm.load_task_state()
        assert state["total"] == 0
        assert state["completed"] == 0

    def test_save_and_load_roundtrip(self, sm):
        sm.init()
        state = {"project": "test", "total": 5, "completed": 2,
                 "pending": 3, "in_progress": 0, "failed": 0, "blocked": 0}
        sm.save_task_state(state)
        loaded = sm.load_task_state()
        assert loaded["total"] == 5
        assert loaded["completed"] == 2
        assert "last_updated" in loaded


class TestAutonomyState:
    def test_load_empty_returns_a0(self, sm):
        sm.init()
        autonomy = sm.load_autonomy()
        assert autonomy.level == 0

    def test_save_and_load_roundtrip(self, sm):
        sm.init()
        state = AutonomyState(level=2, successful=15, approved_categories=["python", "typescript"])
        sm.save_autonomy(state)
        loaded = sm.load_autonomy()
        assert loaded.level == 2
        assert loaded.successful == 15
        assert "python" in loaded.approved_categories

    def test_to_dict_and_from_dict(self):
        state = AutonomyState(level=3, errors=2, grants=[{"pattern": "src/**"}])
        d = state.to_dict()
        assert d["level"] == 3
        restored = AutonomyState.from_dict(d)
        assert restored.level == 3
        assert restored.errors == 2


class TestFormationState:
    def test_save_and_load(self, sm):
        sm.init()
        formation = FormationState(
            name="feature-impl",
            project="myapp",
            teammates={
                "backend-impl": {
                    "agent": "backend-architect",
                    "agent_id": "abc",
                    "ownership": {"directories": ["src/api/"], "files": [], "patterns": []},
                }
            },
        )
        sm.save_formation(formation)
        loaded = sm.load_formation()
        assert loaded is not None
        assert loaded.name == "feature-impl"
        assert "backend-impl" in loaded.teammates

    def test_clear_formation(self, sm):
        sm.init()
        sm.save_formation(FormationState(name="test", project="test"))
        sm.clear_formation()
        assert sm.load_formation() is None

    def test_load_no_formation(self, sm):
        sm.init()
        assert sm.load_formation() is None


class TestSessionMeta:
    def test_load_empty_returns_dict(self, sm):
        sm.init()
        meta = sm.load_session_meta()
        assert isinstance(meta, dict)

    def test_save_and_load(self, sm):
        sm.init()
        sm.save_session_meta({"sprint": "sprint-01", "phase": "build"})
        meta = sm.load_session_meta()
        assert meta["sprint"] == "sprint-01"
        assert "last_updated" in meta


class TestStatus:
    def test_status_empty_project(self, sm):
        sm.init()
        status = sm.status()
        assert isinstance(status, SessionStatus)
        assert status.total_tasks == 0
        assert status.percent == 0.0

    def test_status_with_tasks(self, sm):
        sm.init()
        sm.save_task_state({"total": 10, "completed": 3, "pending": 5,
                           "in_progress": 2, "failed": 0, "blocked": 0})
        status = sm.status()
        assert status.total_tasks == 10
        assert status.completed == 3
        assert status.percent == 30.0

    def test_status_includes_autonomy(self, sm):
        sm.init()
        sm.save_autonomy(AutonomyState(level=2))
        status = sm.status()
        assert status.autonomy_level == 2

    def test_status_includes_formation(self, sm):
        sm.init()
        sm.save_formation(FormationState(name="feature-impl", project="test"))
        status = sm.status()
        assert status.formation == "feature-impl"


class TestHandoff:
    def test_handoff_produces_markdown(self, sm):
        sm.init()
        sm.save_task_state({"total": 5, "completed": 2, "pending": 3,
                           "in_progress": 0, "failed": 0, "blocked": 0})
        context = sm.handoff()
        assert "# Forge Session Handoff" in context
        assert "2/5" in context

    def test_handoff_includes_formation(self, sm):
        sm.init()
        sm.save_formation(FormationState(
            name="feature-impl", project="test",
            teammates={"backend": {"agent": "arch", "agent_id": "123"}},
        ))
        context = sm.handoff()
        assert "Formation" in context
        assert "feature-impl" in context


class TestArtifacts:
    def test_store_and_load(self, sm):
        sm.init()
        path = sm.store_artifact("task-1", "output.txt", "hello world")
        assert path.exists()
        content = sm.load_artifact("task-1", "output.txt")
        assert content == "hello world"

    def test_load_missing(self, sm):
        sm.init()
        assert sm.load_artifact("task-99", "nope.txt") is None

    def test_list_artifacts(self, sm):
        sm.init()
        sm.store_artifact("task-1", "a.txt", "aa")
        sm.store_artifact("task-1", "b.txt", "bb")
        names = sm.list_artifacts("task-1")
        assert names == ["a.txt", "b.txt"]


class TestCompliance:
    def test_fresh_project_not_compliant(self, sm):
        sm.init()
        assert not sm.is_compliant()

    def test_auto_fix_improves_compliance(self, sm):
        sm.init()
        fixes = sm.auto_fix()
        assert len(fixes) > 0

        gates = sm.check_compliance()
        passed = sum(1 for _, p, _ in gates if p)
        # After auto-fix, most gates should pass
        assert passed >= 6

    def test_check_returns_10_gates(self, sm):
        sm.init()
        gates = sm.check_compliance()
        assert len(gates) == 10

    def test_gate_names(self, sm):
        sm.init()
        gates = sm.check_compliance()
        names = [g for g, _, _ in gates]
        assert "forge_dir" in names
        assert "autonomy_state" in names
        assert "no_legacy" in names
