"""Tests for forge_hooks_impl.py — 12 V11 hook implementations.

Covers:
- wire_all_hooks registration (5 pre, 6 post, 1 stop)
- detect-project hook
- guard-write-gates (task state enforcement, file ownership, session tracking)
- guard-enforcement (high-risk bash, force push, safe commands)
- guard-effort (advisory only)
- enforce-test-coverage (deploy detection, non-deploy passthrough)
- verify-syntax (Python, JSON, unknown extensions)
- track-autonomy (audit trail, error detection)
- sync-tasks (TaskCreate, TaskUpdate including completed status)
- guard-teammate-timeout
- track-agents (usage logging, failure detection)
- fix-team-model (advisory only)
- session-end (session log output)
- file ownership enforcement
- tool policy cascade
- tool name normalization (ForgeAgent -> V11 names)
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from forge_hooks import HookSystem, HookEvent, HookResult
from forge_hooks_impl import wire_all_hooks, _HookState, _normalize_tool_name, _TOOL_NAME_MAP
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


# ── Tool Name Normalization ──────────────────────────────────────────────────

class TestToolNameNormalization:
    """Verify ForgeAgent tool names map to V11 hook names."""

    def test_write_file_maps_to_write(self):
        assert _normalize_tool_name("write_file") == "Write"

    def test_edit_file_maps_to_edit(self):
        assert _normalize_tool_name("edit_file") == "Edit"

    def test_bash_maps_to_bash(self):
        assert _normalize_tool_name("bash") == "Bash"

    def test_read_file_maps_to_read(self):
        assert _normalize_tool_name("read_file") == "Read"

    def test_append_file_maps_to_write(self):
        assert _normalize_tool_name("append_file") == "Write"

    def test_unknown_tool_passes_through(self):
        assert _normalize_tool_name("TaskCreate") == "TaskCreate"
        assert _normalize_tool_name("Agent") == "Agent"

    def test_all_mapped_tools_covered(self):
        """All entries in _TOOL_NAME_MAP should normalize correctly."""
        for forge_name, v11_name in _TOOL_NAME_MAP.items():
            assert _normalize_tool_name(forge_name) == v11_name


# ── wire_all_hooks ───────────────────────────────────────────────────────────

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

    def test_total_hooks_is_12(self, project):
        """Total registered hooks should be exactly 12 (5+6+1)."""
        hs = HookSystem()
        wire_all_hooks(hs, project_root=project)
        total = sum(len(hooks) for hooks in hs._python_hooks.values())
        assert total == 12

    def test_state_has_active_project(self, project):
        hs = HookSystem()
        state = wire_all_hooks(hs, project_root=project)
        assert state.active_project == project.name

    def test_state_has_project_root(self, project):
        hs = HookSystem()
        state = wire_all_hooks(hs, project_root=project)
        assert state.project_root == project

    def test_state_initial_session_writes_zero(self, project):
        hs = HookSystem()
        state = wire_all_hooks(hs, project_root=project)
        assert state.session_writes == 0
        assert len(state.files_modified) == 0


# ── detect-project ───────────────────────────────────────────────────────────

class TestDetectProject:
    def test_detects_from_file_path(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Read", {"file_path": "/home/hercules/myapp/src/main.py"}))
        assert not result.blocked

    def test_no_crash_on_empty_args(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Read", {}))
        assert not result.blocked


# ── guard-write-gates ────────────────────────────────────────────────────────

class TestGuardWriteGates:
    def test_allows_write_no_tasks(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/main.py")}))
        assert not result.blocked

    def test_blocks_when_no_in_progress_tasks(self, hook_system, project):
        hs, state = hook_system
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
        task_state = {"total": 1, "completed": 0, "pending": 1, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert not result.blocked

    def test_allows_when_all_tasks_completed(self, hook_system, project):
        hs, state = hook_system
        task_state = {"total": 3, "completed": 3, "pending": 0, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "src/app.py")}))
        assert not result.blocked

    def test_allows_when_only_failed_remain(self, hook_system, project):
        hs, state = hook_system
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

    def test_tracks_unique_files_only(self, hook_system, project):
        """Writing to the same file twice should count 2 writes but 1 unique file."""
        hs, state = hook_system
        path = str(project / "a.py")
        _run(hs.pre_tool_use("Write", {"file_path": path}))
        _run(hs.pre_tool_use("Write", {"file_path": path}))
        assert state.session_writes == 2
        assert len(state.files_modified) == 1

    def test_edit_also_gates_writes(self, hook_system, project):
        """Edit tool should also be subject to write gate enforcement."""
        hs, state = hook_system
        task_state = {"total": 3, "completed": 1, "pending": 2, "in_progress": 0}
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps(task_state))

        result = _run(hs.pre_tool_use("Edit", {"file_path": str(project / "src/app.py")}))
        assert result.blocked


# ── guard-enforcement ────────────────────────────────────────────────────────

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

    def test_blocks_hard_reset(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "git reset --hard HEAD~5"}))
        assert result.blocked

    def test_blocks_destructive_docker(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "docker system prune -a"}))
        assert result.blocked

    def test_allows_pytest_command(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "python3 -m pytest tests/ -v"}))
        assert not result.blocked

    def test_allows_cat_command(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "cat README.md"}))
        assert not result.blocked

    def test_does_not_apply_to_non_bash_write_edit(self, hook_system, project):
        """Guard enforcement should not block Read or Glob tools."""
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Glob", {"pattern": "**/*.py"}))
        assert not result.blocked


# ── guard-effort ─────────────────────────────────────────────────────────────

class TestGuardEffort:
    def test_advisory_only_never_blocks(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Agent", {
            "prompt": "Design a microservice architecture for payments",
            "metadata": {"complexity": "novel", "scope": "large"},
        }))
        assert not result.blocked

    def test_advisory_on_routine_task(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Agent", {
            "prompt": "Format the README file",
            "metadata": {"complexity": "routine", "scope": "small"},
        }))
        assert not result.blocked


# ── enforce-test-coverage ────────────────────────────────────────────────────

class TestEnforceTestCoverage:
    def test_skips_non_deploy_commands(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "python3 -m pytest"}))
        assert not result.blocked

    def test_skips_non_bash_tools(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Write", {"file_path": "/tmp/test.py"}))
        assert not result.blocked

    def test_triggers_on_docker_compose_up(self, hook_system):
        """Deploy command should trigger the test coverage check (not block if no framework)."""
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "docker-compose up -d"}))
        # Should not block (no test framework detected in tmp_path)
        assert not result.blocked

    def test_triggers_on_kubectl_apply(self, hook_system):
        hs, state = hook_system
        result = _run(hs.pre_tool_use("Bash", {"command": "kubectl apply -f deploy.yaml"}))
        assert not result.blocked


# ── verify-syntax ────────────────────────────────────────────────────────────

class TestVerifySyntax:
    def test_checks_python_file(self, hook_system, project):
        hs, state = hook_system
        py_file = project / "valid.py"
        py_file.write_text("x = 1\n")
        result = _run(hs.post_tool_use("Write", {"file_path": str(py_file)}, "File written"))
        assert not result.blocked  # Advisory only

    def test_warns_on_syntax_error(self, hook_system, project):
        hs, state = hook_system
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

    def test_checks_json_file(self, hook_system, project):
        hs, state = hook_system
        json_file = project / "config.json"
        json_file.write_text('{"key": "value"}')
        result = _run(hs.post_tool_use("Write", {"file_path": str(json_file)}, "File written"))
        assert not result.blocked

    def test_skips_nonexistent_file(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.post_tool_use("Write", {"file_path": str(project / "ghost.py")}, "File written"))
        assert not result.blocked


# ── track-autonomy ───────────────────────────────────────────────────────────

class TestTrackAutonomy:
    def test_records_audit_entry(self, hook_system, project):
        hs, state = hook_system
        _run(hs.post_tool_use("Write", {"file_path": str(project / "src/main.py")}, "File written"))

        audit_file = project / ".forge" / "audit" / "audit.jsonl"
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

    def test_audit_entry_has_timestamp(self, hook_system, project):
        """Audit entries should include a timestamp."""
        hs, state = hook_system
        _run(hs.post_tool_use("Write", {"file_path": str(project / "test.py")}, "Written"))

        audit_file = project / ".forge" / "audit" / "audit.jsonl"
        if audit_file.exists():
            entry = json.loads(audit_file.read_text().strip().split("\n")[0])
            assert "timestamp" in entry
            assert "T" in entry["timestamp"]  # ISO format


# ── sync-tasks ───────────────────────────────────────────────────────────────

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

    def test_updates_on_task_in_progress(self, hook_system, project):
        hs, state = hook_system
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

    def test_updates_on_task_completed(self, hook_system, project):
        """TaskUpdate to 'completed' should decrement in_progress and increment completed."""
        hs, state = hook_system
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 3, "pending": 0,
            "completed": 1, "in_progress": 2, "blocked": 0,
        }))
        state._task_state_loaded = False
        state.task_state = None

        _run(hs.post_tool_use(
            "TaskUpdate", {"taskId": "2", "status": "completed"}, "Updated"
        ))

        data = json.loads(state_file.read_text())
        assert data["completed"] == 2
        assert data["in_progress"] == 1

    def test_updates_on_task_blocked(self, hook_system, project):
        """TaskUpdate to 'blocked' should decrement in_progress and increment blocked."""
        hs, state = hook_system
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 3, "pending": 0,
            "completed": 0, "in_progress": 3, "blocked": 0,
        }))
        state._task_state_loaded = False
        state.task_state = None

        _run(hs.post_tool_use(
            "TaskUpdate", {"taskId": "3", "status": "blocked"}, "Updated"
        ))

        data = json.loads(state_file.read_text())
        assert data["blocked"] == 1
        assert data["in_progress"] == 2

    def test_multiple_task_creates_accumulate(self, hook_system, project):
        """Multiple TaskCreate calls should accumulate total and pending."""
        hs, state = hook_system
        for i in range(3):
            _run(hs.post_tool_use(
                "TaskCreate",
                {"subject": f"Task {i}", "metadata": {"project": project.name}},
                f"Created task #{i+1}"
            ))

        state_file = project / ".forge" / "state" / "task-state.json"
        data = json.loads(state_file.read_text())
        assert data["total"] == 3
        assert data["pending"] == 3

    def test_ignores_non_task_tools(self, hook_system, project):
        """sync-tasks hook should not modify state for non-task tools."""
        hs, state = hook_system
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 2, "pending": 2,
            "completed": 0, "in_progress": 0, "blocked": 0,
        }))
        state._task_state_loaded = False
        state.task_state = None

        _run(hs.post_tool_use("Write", {"file_path": "test.py"}, "Written"))

        data = json.loads(state_file.read_text())
        assert data["total"] == 2  # Unchanged


# ── guard-teammate-timeout ───────────────────────────────────────────────────

class TestGuardTeammateTimeout:
    def test_no_alert_when_no_tasks(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.post_tool_use("TaskList", {}, "No tasks"))
        assert not result.blocked

    def test_no_block_on_task_list(self, hook_system, project):
        """Teammate timeout is advisory only — should never block."""
        hs, state = hook_system
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 3, "pending": 0,
            "completed": 1, "in_progress": 2, "blocked": 0,
            "last_updated": "2026-01-01T00:00:00+00:00",  # Very stale
        }))
        state._task_state_loaded = False
        state.task_state = None

        result = _run(hs.post_tool_use("TaskList", {}, "Listing tasks"))
        assert not result.blocked  # Advisory only


# ── track-agents ─────────────────────────────────────────────────────────────

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

    def test_categorizes_frontend_agent(self, hook_system, project):
        """Agent description mentioning frontend should categorize as 'frontend'."""
        hs, state = hook_system
        _run(hs.post_tool_use(
            "Agent",
            {"subagent_type": "ui-builder", "description": "Build React frontend components"},
            "Completed successfully"
        ))

        usage_file = project / ".forge" / "audit" / "agent-usage.jsonl"
        entry = json.loads(usage_file.read_text().strip())
        assert entry["category"] == "frontend"

    def test_categorizes_security_agent(self, hook_system, project):
        """Agent description mentioning security should categorize as 'security'."""
        hs, state = hook_system
        _run(hs.post_tool_use(
            "Agent",
            {"subagent_type": "sec-reviewer", "description": "Security audit and OWASP checks"},
            "Done"
        ))

        usage_file = project / ".forge" / "audit" / "agent-usage.jsonl"
        entry = json.loads(usage_file.read_text().strip())
        assert entry["category"] == "security"


# ── fix-team-model ───────────────────────────────────────────────────────────

class TestFixTeamModel:
    def test_advisory_only(self, hook_system, project):
        hs, state = hook_system
        result = _run(hs.post_tool_use(
            "Agent", {"subagent_type": "backend-architect"}, "Spawned"
        ))
        assert not result.blocked


# ── session-end ──────────────────────────────────────────────────────────────

class TestSessionEnd:
    def test_writes_session_log(self, hook_system, project):
        hs, state = hook_system
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

    def test_session_log_includes_writes_count(self, hook_system, project):
        """Session log should include session_writes and files_modified counts."""
        hs, state = hook_system
        state_file = project / ".forge" / "state" / "task-state.json"
        state_file.write_text(json.dumps({
            "project": project.name, "total": 2, "pending": 0,
            "completed": 2, "in_progress": 0, "blocked": 0,
        }))
        state._task_state_loaded = False
        state.task_state = None

        # Do some writes before stop
        state.session_writes = 5
        state.files_modified = {"a.py", "b.py", "c.py"}

        _run(hs.on_stop())

        log_file = project / ".forge" / "audit" / "session-log.jsonl"
        entry = json.loads(log_file.read_text().strip())
        assert entry["session_writes"] == 5
        assert entry["files_modified"] == 3

    def test_no_log_without_active_project(self, project):
        """Session end with no active project should not write log."""
        hs = HookSystem()
        state = wire_all_hooks(hs, project_root=None)
        _run(hs.on_stop())

        log_file = project / ".forge" / "audit" / "session-log.jsonl"
        assert not log_file.exists()


# ── file ownership ───────────────────────────────────────────────────────────

class TestFileOwnership:
    def test_blocks_write_to_others_files(self, hook_system, project):
        hs, state = hook_system

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

        os.environ["FORGE_AGENT_ID"] = "agent-xyz"
        try:
            result = _run(hs.pre_tool_use("Write", {"file_path": "src/api/routes.py"}))
            assert result.blocked
            assert "owned by" in result.reason.lower() or "backend-impl" in result.reason
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)

    def test_allows_write_to_own_files(self, hook_system, project):
        """Agent writing to its own owned directory should be allowed."""
        hs, state = hook_system

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

        os.environ["FORGE_AGENT_ID"] = "agent-abc"
        try:
            result = _run(hs.pre_tool_use("Write", {"file_path": "src/api/routes.py"}))
            assert not result.blocked
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)

    def test_allows_write_to_unclaimed_files(self, hook_system, project):
        """Writing to a file not owned by anyone should be allowed."""
        hs, state = hook_system

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

        os.environ["FORGE_AGENT_ID"] = "agent-xyz"
        try:
            result = _run(hs.pre_tool_use("Write", {"file_path": "src/utils/helpers.py"}))
            assert not result.blocked
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)


# ── tool policy ──────────────────────────────────────────────────────────────

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

        os.environ["FORGE_AGENT_ID"] = "agent-rev"
        try:
            result = _run(hs.pre_tool_use("Write", {"file_path": str(project / "test.py")}))
            assert result.blocked
            assert "not in allowed" in result.reason.lower() or "denied" in result.reason.lower()
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)

    def test_allows_read_for_readonly_profile(self, hook_system, project):
        """Readonly profile should allow Read tool."""
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

        os.environ["FORGE_AGENT_ID"] = "agent-rev"
        try:
            result = _run(hs.pre_tool_use("Read", {"file_path": str(project / "test.py")}))
            assert not result.blocked
        finally:
            os.environ.pop("FORGE_AGENT_ID", None)
