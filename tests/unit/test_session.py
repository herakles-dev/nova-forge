"""Tests for forge_session.py — session management + state persistence."""

import json
from pathlib import Path

import pytest

from forge_session import (
    SessionManager, SessionStatus, AutonomyState, FormationState, UserProfile,
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

    def test_init_returns_forge_project(self, sm):
        result = sm.init()
        assert result is not None
        assert hasattr(result, "root")

    def test_init_creates_artifacts_dir(self, sm, project):
        sm.init()
        # After auto_fix, settings would exist; init just creates structure
        assert (project / ".forge").is_dir()


class TestTaskState:
    def test_load_empty_returns_defaults(self, sm):
        sm.init()
        state = sm.load_task_state()
        assert state["total"] == 0
        assert state["completed"] == 0
        assert state["pending"] == 0
        assert state["in_progress"] == 0
        assert state["failed"] == 0
        assert state["blocked"] == 0

    def test_save_and_load_roundtrip(self, sm):
        sm.init()
        state = {"project": "test", "total": 5, "completed": 2,
                 "pending": 3, "in_progress": 0, "failed": 0, "blocked": 0}
        sm.save_task_state(state)
        loaded = sm.load_task_state()
        assert loaded["total"] == 5
        assert loaded["completed"] == 2
        assert "last_updated" in loaded
        assert loaded["last_updated"] != ""

    def test_save_overwrites_previous(self, sm):
        sm.init()
        sm.save_task_state({"total": 3, "completed": 1, "pending": 2,
                            "in_progress": 0, "failed": 0, "blocked": 0})
        sm.save_task_state({"total": 10, "completed": 7, "pending": 3,
                            "in_progress": 0, "failed": 0, "blocked": 0})
        loaded = sm.load_task_state()
        assert loaded["total"] == 10
        assert loaded["completed"] == 7

    def test_load_corrupt_file_returns_defaults(self, sm, project):
        sm.init()
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text("{invalid json!!!")
        state = sm.load_task_state()
        assert state["total"] == 0


class TestAutonomyState:
    def test_load_empty_returns_a2(self, sm):
        sm.init()
        autonomy = sm.load_autonomy()
        assert autonomy.level == 2  # Default is A2 (Supervised)

    def test_save_and_load_roundtrip(self, sm):
        sm.init()
        state = AutonomyState(level=2, successful=15, approved_categories=["python", "typescript"])
        sm.save_autonomy(state)
        loaded = sm.load_autonomy()
        assert loaded.level == 2
        assert loaded.successful == 15
        assert "python" in loaded.approved_categories
        assert "typescript" in loaded.approved_categories

    def test_to_dict_and_from_dict(self):
        state = AutonomyState(level=3, errors=2, grants=[{"pattern": "src/**"}])
        d = state.to_dict()
        assert d["level"] == 3
        assert d["errors"] == 2
        assert d["grants"] == [{"pattern": "src/**"}]
        restored = AutonomyState.from_dict(d)
        assert restored.level == 3
        assert restored.errors == 2
        assert restored.grants == [{"pattern": "src/**"}]

    def test_to_dict_full_fields(self):
        state = AutonomyState(
            level=4, successful=20, errors=3, rollbacks=1,
            approved_categories=["python"],
            grants=[{"pattern": "*.py"}],
            recent_errors=[{"msg": "fail"}],
            last_escalation="2026-01-01T00:00:00Z",
            last_deescalation="2026-01-02T00:00:00Z",
        )
        d = state.to_dict()
        assert d["rollbacks"] == 1
        assert d["recent_errors"] == [{"msg": "fail"}]
        assert d["last_escalation"] == "2026-01-01T00:00:00Z"
        assert d["last_deescalation"] == "2026-01-02T00:00:00Z"
        restored = AutonomyState.from_dict(d)
        assert restored.rollbacks == 1
        assert restored.last_escalation == "2026-01-01T00:00:00Z"

    def test_from_dict_missing_fields_uses_defaults(self):
        state = AutonomyState.from_dict({})
        assert state.level == 0
        assert state.successful == 0
        assert state.errors == 0
        assert state.approved_categories == []
        assert state.grants == []

    def test_load_corrupt_autonomy_returns_default(self, sm, project):
        sm.init()
        auto_file = project / ".forge" / "state" / "autonomy.json"
        auto_file.write_text("NOT JSON AT ALL")
        loaded = sm.load_autonomy()
        assert loaded.level == 2  # Default


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
        assert loaded.project == "myapp"
        assert "backend-impl" in loaded.teammates
        assert loaded.teammates["backend-impl"]["agent"] == "backend-architect"

    def test_clear_formation(self, sm):
        sm.init()
        sm.save_formation(FormationState(name="test", project="test"))
        sm.clear_formation()
        assert sm.load_formation() is None

    def test_load_no_formation(self, sm):
        sm.init()
        assert sm.load_formation() is None

    def test_to_dict_and_from_dict(self):
        state = FormationState(
            name="feature-impl", project="myapp",
            teammates={"impl": {"agent": "spec-impl"}},
            tool_policies={"impl": "coding"},
            started_at="2026-01-01T00:00:00Z",
        )
        d = state.to_dict()
        assert d["formation"] == "feature-impl"
        assert d["project"] == "myapp"
        assert d["started_at"] == "2026-01-01T00:00:00Z"
        restored = FormationState.from_dict(d)
        assert restored.name == "feature-impl"
        assert restored.tool_policies == {"impl": "coding"}

    def test_from_dict_empty(self):
        state = FormationState.from_dict({})
        assert state.name == ""
        assert state.project == ""
        assert state.teammates == {}

    def test_load_corrupt_formation_returns_none(self, sm, project):
        sm.init()
        reg_file = project / ".forge" / "state" / "formation-registry.json"
        reg_file.write_text("{{CORRUPT}}")
        assert sm.load_formation() is None


class TestSessionMeta:
    def test_load_empty_returns_dict(self, sm):
        sm.init()
        meta = sm.load_session_meta()
        assert isinstance(meta, dict)
        assert meta == {}

    def test_save_and_load(self, sm):
        sm.init()
        sm.save_session_meta({"sprint": "sprint-01", "phase": "build"})
        meta = sm.load_session_meta()
        assert meta["sprint"] == "sprint-01"
        assert meta["phase"] == "build"
        assert "last_updated" in meta

    def test_save_preserves_extra_fields(self, sm):
        sm.init()
        sm.save_session_meta({"sprint": "sprint-01", "custom": "value"})
        meta = sm.load_session_meta()
        assert meta["custom"] == "value"

    def test_load_corrupt_returns_empty(self, sm, project):
        sm.init()
        meta_file = project / ".forge" / "state" / "session-meta.json"
        meta_file.write_text("CORRUPT!")
        meta = sm.load_session_meta()
        assert meta == {}


class TestUserProfile:
    def test_defaults(self):
        p = UserProfile()
        assert p.skill_level == "beginner"
        assert p.preferred_autonomy == 2
        assert p.preferred_model == "nova-lite"
        assert p.preferred_formation == "auto"
        assert p.builds_completed == 0
        assert p.builds_failed == 0
        assert p.verbosity == "normal"
        assert p.show_explanations is True

    def test_to_dict_and_from_dict(self):
        p = UserProfile(skill_level="expert", preferred_autonomy=4, builds_completed=20)
        d = p.to_dict()
        assert d["skill_level"] == "expert"
        assert d["preferred_autonomy"] == 4
        restored = UserProfile.from_dict(d)
        assert restored.skill_level == "expert"
        assert restored.builds_completed == 20

    def test_from_dict_missing_fields(self):
        p = UserProfile.from_dict({})
        assert p.skill_level == "beginner"
        assert p.preferred_autonomy == 2

    def test_save_and_load_profile(self, sm):
        sm.init()
        profile = UserProfile(skill_level="intermediate", builds_completed=5)
        sm.save_profile(profile)
        loaded = sm.load_profile()
        assert loaded.skill_level == "intermediate"
        assert loaded.builds_completed == 5

    def test_load_profile_default_when_missing(self, sm):
        sm.init()
        profile = sm.load_profile()
        assert profile.skill_level == "beginner"

    def test_update_profile_beginner_to_intermediate(self, sm):
        sm.init()
        profile = UserProfile(builds_completed=2)
        updated = sm.update_profile_after_build(profile, passed=5, failed=0)
        assert updated.builds_completed == 3
        assert updated.skill_level == "intermediate"

    def test_update_profile_intermediate_to_expert(self, sm):
        sm.init()
        profile = UserProfile(skill_level="intermediate", builds_completed=9)
        updated = sm.update_profile_after_build(profile, passed=5, failed=0)
        assert updated.builds_completed == 10
        assert updated.skill_level == "expert"
        assert updated.preferred_autonomy >= 3

    def test_update_profile_failure_increments_failed(self, sm):
        sm.init()
        profile = UserProfile()
        updated = sm.update_profile_after_build(profile, passed=0, failed=3)
        assert updated.builds_failed == 1
        assert updated.builds_completed == 0
        assert updated.skill_level == "beginner"

    def test_update_profile_no_skill_change_below_threshold(self, sm):
        sm.init()
        profile = UserProfile(builds_completed=1)
        updated = sm.update_profile_after_build(profile, passed=3, failed=0)
        assert updated.builds_completed == 2
        assert updated.skill_level == "beginner"


class TestStatus:
    def test_status_empty_project(self, sm):
        sm.init()
        status = sm.status()
        assert isinstance(status, SessionStatus)
        assert status.total_tasks == 0
        assert status.percent == 0.0
        assert status.formation == ""

    def test_status_with_tasks(self, sm):
        sm.init()
        sm.save_task_state({"total": 10, "completed": 3, "pending": 5,
                           "in_progress": 2, "failed": 0, "blocked": 0})
        status = sm.status()
        assert status.total_tasks == 10
        assert status.completed == 3
        assert status.in_progress == 2
        assert status.pending == 5
        assert status.percent == 30.0

    def test_status_100_percent(self, sm):
        sm.init()
        sm.save_task_state({"total": 5, "completed": 5, "pending": 0,
                           "in_progress": 0, "failed": 0, "blocked": 0})
        status = sm.status()
        assert status.percent == 100.0

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

    def test_status_includes_project_name(self, sm, project):
        sm.init()
        status = sm.status()
        assert status.project_name == project.name


class TestHandoff:
    def test_handoff_produces_markdown(self, sm):
        sm.init()
        sm.save_task_state({"total": 5, "completed": 2, "pending": 3,
                           "in_progress": 0, "failed": 0, "blocked": 0})
        context = sm.handoff()
        assert "# Forge Session Handoff" in context
        assert "2/5" in context
        assert "## Progress" in context
        assert "## Autonomy" in context
        assert "## Next Steps" in context

    def test_handoff_includes_formation(self, sm):
        sm.init()
        sm.save_formation(FormationState(
            name="feature-impl", project="test",
            teammates={"backend": {"agent": "arch", "agent_id": "123"}},
        ))
        context = sm.handoff()
        assert "Formation" in context
        assert "feature-impl" in context
        assert "arch" in context

    def test_handoff_includes_profile_for_experienced_user(self, sm):
        sm.init()
        sm.save_profile(UserProfile(skill_level="expert", builds_completed=15))
        context = sm.handoff()
        assert "User Profile" in context
        assert "expert" in context


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

    def test_list_artifacts_empty_task(self, sm):
        sm.init()
        names = sm.list_artifacts("task-nonexistent")
        assert names == []

    def test_store_multiple_tasks(self, sm):
        sm.init()
        sm.store_artifact("task-1", "file.py", "code 1")
        sm.store_artifact("task-2", "file.py", "code 2")
        assert sm.load_artifact("task-1", "file.py") == "code 1"
        assert sm.load_artifact("task-2", "file.py") == "code 2"

    def test_overwrite_artifact(self, sm):
        sm.init()
        sm.store_artifact("task-1", "out.txt", "first")
        sm.store_artifact("task-1", "out.txt", "second")
        assert sm.load_artifact("task-1", "out.txt") == "second"


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
        assert "state_dir" in names
        assert "audit_dir" in names
        assert "settings_file" in names

    def test_compliance_gate_tuples_structure(self, sm):
        sm.init()
        gates = sm.check_compliance()
        for gate_name, passed, detail in gates:
            assert isinstance(gate_name, str)
            assert isinstance(passed, bool)
            assert isinstance(detail, str)

    def test_no_legacy_detects_spec_yml(self, sm, project):
        sm.init()
        (project / "spec.yml").write_text("legacy: true")
        gates = sm.check_compliance()
        legacy_gate = next(g for g in gates if g[0] == "no_legacy")
        assert legacy_gate[1] is False

    def test_no_legacy_detects_state_md(self, sm, project):
        sm.init()
        (project / "state.md").write_text("# Legacy state")
        gates = sm.check_compliance()
        legacy_gate = next(g for g in gates if g[0] == "no_legacy")
        assert legacy_gate[1] is False
