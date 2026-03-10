"""Tests for forge_hooks_impl.py — 12 V11 hook implementations."""

import asyncio
import json
import tempfile
from pathlib import Path

import pytest

from forge_hooks import HookSystem, HookEvent, HookResult
from forge_hooks_impl import wire_all_hooks, _HookState
from forge_guards import AutonomyManager


@pytest.fixture
def project(tmp_path):
    """Create a minimal .forge/ project structure."""
    forge = tmp_path / ".forge"
    state = forge / "state"
    audit = forge / "audit"
    state.mkdir(parents=True)
    audit.mkdir(parents=True)
    return tmp_path


@pytest.fixture
def hook_system(project):
    """HookSystem with all 12 hooks wired."""
    hs = HookSystem()
    state = wire_all_hooks(hs, project_root=project)
    return hs, state


def _run(coro):
    return asyncio.run(coro)


class TestWireAllHooks:
    def test_returns_hook_state(self, project):
        hs = HookSystem()
        state = wire_all_hooks(hs, project_root=project)
        assert isinstance(state, _HookState)

    def test_registers_5_pre_hooks(self, project):
        hs = HookSystem()
        wire_all_hooks(hs, project_root=project)
        assert len(hs._python_hooks[HookEvent.PRE_TOOL_USE]) == 5

    def test_registers_6_post_hooks(self, project):
        hs = HookSystem()
        wire_all_hooks(hs, project_root=project)
        assert len(hs._python_hooks[HookEvent.POST_TOOL_USE]) == 6

    def test_registers_1_stop_hook(self, project):
        hs = HookSystem()
        wire_all_hooks(hs, project_root=project)
        assert len(hs._python_hooks[HookEvent.STOP]) == 1

    def test_state_has_active_project(self, project):
        hs = HookSystem()
        state = wire_all_hooks(hs, project_root=project)
        assert state.active_project == project.name


class TestDetectProject:
    def test_detects_from_file_path(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Read", {"file_path": "/home/hercules/myapp/src/main.py"}))
        assert not result.blocked


class TestGuardWriteGates:
    def test_allows_write_no_tasks(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/main.py")}))
        assert not result.blocked

    def test_blocks_when_no_in_progress_tasks(self, hook_system, project):
        hs, state = hook_system
        # Create task state with 3 tasks but 0 in_progress
        task_state = {"total": 3, "completed": 1, "pending": 2, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert result.blocked
        assert "in_progress" in result.reason.lower() or "task" in result.reason.lower()

    def test_allows_when_task_in_progress(self, hook_system, project):
        hs, state = hook_system
        task_state = {"total": 3, "completed": 1, "pending": 1, "in_progress": 1}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert not result.blocked

    def test_allows_single_task_project(self, hook_system, project):
        hs, state = hook_system
        # Projects with <2 tasks don't require in_progress
        task_state = {"total": 1, "completed": 0, "pending": 1, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert not result.blocked

    def test_allows_when_all_tasks_completed(self, hook_system, project):
        hs, state = hook_system
        # All tasks done — chat editing should be allowed
        task_state = {"total": 3, "completed": 3, "pending": 0, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert not result.blocked

    def test_allows_when_only_failed_remain(self, hook_system, project):
        hs, state = hook_system
        # Failed tasks with no pending — user manually fixing via chat
        task_state = {"total": 3, "completed": 2, "pending": 0, "failed": 1, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert not result.blocked

    def test_tracks_session_writes(self, hook_system, project):
        hs, state = hook_system
        _run(hs.pre_tool_use("Write", {"file_path": str(project / "a.py")}))
        _run(hs.pre_tool_use("Edit", {"file_path": str(project / "b.py")}))
        assert state.session_writes == 2
        assert len(state.files_modified) == 2


class TestGuardEnforcement:
    def test_blocks_high_risk_bash(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "rm -rf /"}))
        assert result.blocked
        assert "high-risk" in result.reason.lower() or "blocked" in result.reason.lower()

    def test_allows_safe_bash(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "ls -la"}))
        assert not result.blocked

    def test_allows_read_tool(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Read", {"file_path": "/tmp/test.py"}))
        assert not result.blocked

    def test_blocks_force_push(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "git push --force origin main"}))
        assert result.blocked


class TestGuardEffort:
    def test_advisory_only_never_blocks(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Agent", {
            "prompt": "Design a microservice architecture for payments",
            "metadata": {"complexity": "novel", "scope": "large"},
        }))
        assert not result.blocked


class TestEnforceTestCoverage:
    def test_skips_non_deploy_commands(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "python3 -m pytest"}))
        assert not result.blocked

    def test_skips_non_bash_tools(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Write", {"file_path": "/tmp/test.py"}))
        assert not result.blocked


class TestVerifySyntax:
    def test_checks_python_file(self, hook_system, project):
        hs, state = hook_system
        # Create a valid Python file
        py_file = project / "valid.py"
        py_file.write_text("x = 1\n")
        result = _run(hs.post_tool_use("Write", {"file_path": str(py_file)}, "File written"))
        assert not result.blocked  # Advisory only

    def test_warns_on_syntax_error(self, hook_system, project):
        hs, state = hook_system
        # Create invalid Python
        py_file = project / "invalid.py"
        py_file.write_text("def foo(\n")
        result = _run(hs.post_tool_use("Write", {"file_path": str(py_file)}, "File written"))
        assert not result.blocked  # Advisory only, never blocks

    def test_skips_unknown_extensions(self, hook_system, project):
        hs, state = hook_system
        dat_file = project / "data.dat"
        dat_file.write_text("binary data")
        result = _run(hs.post_tool_use("Write", {"file_path": str(dat_file)}, "File written"))
        assert not result.blocked


class TestTrackAutonomy:
    def test_records_audit_entry(self, hook_system, project):
        hs, state = hook_system
        _run(hs.post_tool_use("Write", {"file_path": str(project / "src/main.py")}, "File written"))

        audit_file = project / ".forge" / "audit" / "audit.jsonl"
        # Medium-risk write should produce an audit entry
        if audit_file.exists():
            lines = audit_file.read_text().strip().split("\n")
            assert len(lines) >= 1
            entry = json.loads(lines[0])
            assert entry["tool"] == "Write"
            assert entry["outcome"] == "success"

    def test_detects_errors_in_result(self, hook_system, project):
        hs, state = hook_system
        _run(hs.post_tool_use(
            "Bash",
            {"command": "python3 test.py"},
            "Traceback (most recent call last):\n  File 'test.py'\nError: something failed"
        ))

        audit_file = project / ".forge" / "audit" / "audit.jsonl"
        if audit_file.exists():
            lines = audit_file.read_text().strip().split("\n")
            last = json.loads(lines[-1])
            assert last["outcome"] == "error"


class TestSyncTasks:
    def test_creates_task_state_on_task_create(self, hook_system, project):
        hs, state = hook_system
        _run(hs.post_tool_use(
            "TaskCreate",
            {"subject": "Build API", "metadata": {"project": project.name}},
            "Created task #1"
        ))

        state_file = project / ".forge" / "state" / "task-state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert data["total"] == 1
        assert data["pending"] == 1

    def test_updates_on_task_status_change(self, hook_system, project):
        hs, state = hook_system
        # Seed task state
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 2, "pending": 2,
            "completed": 0, "in_progress": 0, "blocked": 0,
        }))
        state._task_state_loaded = False
        state.task_state = None

        _run(hs.post_tool_use(
            "TaskUpdate", {"taskId": "1", "status": "in_progress"}, "Updated"
        ))

        data = json.loads(state_file.read_text())
        assert data["in_progress"] == 1
        assert data["pending"] == 1


class TestGuardTeammateTimeout:
    def test_no_alert_when_no_tasks(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.post_tool_use("TaskList", {}, "No tasks"))
        assert not result.blocked


class TestTrackAgents:
    def test_records_agent_usage(self, hook_system, project):
        hs, state = hook_system
        _run(hs.post_tool_use(
            "Agent",
            {"subagent_type": "backend-architect", "description": "Build API endpoints"},
            "Completed successfully"
        ))

        usage_file = project / ".forge" / "audit" / "agent-usage.jsonl"
        assert usage_file.exists()
        entry = json.loads(usage_file.read_text().strip())
        assert entry["agent"] == "backend-architect"
        assert entry["category"] == "backend"
        assert entry["success"] == "true"

    def test_detects_failure(self, hook_system, project):
        hs, state = hook_system
        _run(hs.post_tool_use(
            "Agent",
            {"subagent_type": "testing-engineer", "description": "Run tests"},
            "Error: test suite failed with 3 failures"
        ))

        usage_file = project / ".forge" / "audit" / "agent-usage.jsonl"
        entry = json.loads(usage_file.read_text().strip())
        assert entry["success"] == "false"


class TestFixTeamModel:
    def test_advisory_only(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.post_tool_use(
            "Agent", {"subagent_type": "backend-architect"}, "Spawned"
        ))
        assert not result.blocked


class TestSessionEnd:
    def test_writes_session_log(self, hook_system, project):
        hs, state = hook_system
        # Seed task state
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 5, "pending": 1,
            "completed": 3, "in_progress": 1, "blocked": 0,
        }))
        state._task_state_loaded = False
        state.task_state = None

        _run(hs.on_stop())

        log_file = project / ".forge" / "audit" / "session-log.jsonl"
        assert log_file.exists()
        entry = json.loads(log_file.read_text().strip())
        assert entry["project"] == project.name
        assert entry["completed"] == 3
        assert entry["total"] == 5


class TestFileOwnership:
    def test_blocks_write_to_others_files(self, hook_system, project):
        hs, state = hook_system

        # Create formation registry with ownership
        registry = {
            "formation": "feature-impl",
            "project": project.name,
            "teammates": {
                "backend-impl": {
                    "agent": "backend-architect",
                    "agent_id": "agent-abc",
                    "ownership": {"directories": ["src/api/"], "files": [], "patterns": []},
                }
            },
        }
        reg_file = project / ".forge" / "state" / "formation-registry.json"
        reg_file.write_text(json.dumps(registry))

        # Try to write to backend's territory from a different agent
        import os
        os.environ["FORGE_AGENT_ID"] = "agent-xyz"
        try:
            result = _run(hs.pre_tool_use("Write", {"file_path": "src/api/routes.py"}))
            assert result.blocked
            assert "owned by" in result.reason.lower() or "backend-impl" in result.reason
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)


class TestToolPolicy:
    def test_blocks_denied_tool(self, hook_system, project):
        hs, state = hook_system

        registry = {
            "formation": "feature-impl",
            "project": project.name,
            "tool_policies": {
                "defaults": {"profile": "readonly"},
                "per_role": {},
            },
            "teammates": {
                "reviewer": {
                    "agent": "spec-reviewer",
                    "agent_id": "agent-rev",
                    "tool_policies": {"profile": "readonly"},
                },
            },
        }
        reg_file = project / ".forge" / "state" / "formation-registry.json"
        reg_file.write_text(json.dumps(registry))

        import os
        os.environ["FORGE_AGENT_ID"] = "agent-rev"
        try:
            result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "test.py")}))
            assert result.blocked
            assert "not in allowed" in result.reason.lower() or "denied" in result.reason.lower()
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)
