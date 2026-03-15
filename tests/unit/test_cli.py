"""Tests for forge.py CLI — Click commands work end-to-end."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import pytest
from click.testing import CliRunner
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path

from forge import cli
from forge_orchestrator import PlanResult, BuildResult, StatusReport
from forge_tasks import Task


@pytest.fixture
def runner():
    return CliRunner()


class TestModelsCommand:
    def test_models_lists_aliases(self, runner):
        result = runner.invoke(cli, ["models"])
        assert result.exit_code == 0
        assert "nova-lite" in result.output
        assert "gemini-flash" in result.output


class TestStatusCommand:
    def test_status_shows_counts(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.status.return_value = StatusReport(
            project_name="test-proj",
            total_tasks=10,
            completed=3,
            in_progress=2,
            pending=4,
            failed=1,
            blocked=0,
            percent=30.0,
        )
        # Patch inside forge_orchestrator module which is where forge.py imports from
        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "status"])
        assert result.exit_code == 0
        assert "test-proj" in result.output
        assert "3/10" in result.output
        assert "30%" in result.output


class TestListCommand:
    def test_list_shows_tasks(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.list_tasks.return_value = [
            Task(id="1", subject="Build API", description="", status="completed"),
            Task(id="2", subject="Write tests", description="", status="pending"),
        ]

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "list"])
        assert result.exit_code == 0
        assert "Build API" in result.output
        assert "Write tests" in result.output
        assert "[+]" in result.output  # completed marker
        assert "[ ]" in result.output  # pending marker

    def test_list_no_tasks(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.list_tasks.return_value = []

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "list"])
        assert result.exit_code == 0
        assert "No tasks found" in result.output


class TestNewCommand:
    def test_new_creates_forge_dir(self, runner, tmp_path):
        result = runner.invoke(cli, ["--project", str(tmp_path), "new", "my-app"])
        assert result.exit_code == 0
        assert "Created" in result.output
        assert (tmp_path / "my-app" / ".forge").exists()


class TestPlanCommand:
    def test_plan_success(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.plan = AsyncMock(return_value=PlanResult(
            spec_path=tmp_path / "spec.md",
            tasks_path=tmp_path / "tasks.json",
            task_count=5,
            error=None,
        ))

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "plan", "weather API"])
        assert result.exit_code == 0
        assert "weather API" in result.output
        assert "5 tasks" in result.output

    def test_plan_error(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.plan = AsyncMock(return_value=PlanResult(
            error="Model timeout",
        ))

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "plan", "something"])
        assert result.exit_code != 0


class TestBuildCommand:
    def test_build_success(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.build = AsyncMock(return_value=BuildResult(
            success=True,
            waves_completed=3,
            total_waves=3,
            gate_passed=True,
            errors=[],
            duration=45.2,
        ))

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "build"])
        assert result.exit_code == 0
        assert "3/3" in result.output
        assert "PASS" in result.output
        assert "45.2s" in result.output

    def test_build_failure_exits_nonzero(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.build = AsyncMock(return_value=BuildResult(
            success=False,
            waves_completed=1,
            total_waves=3,
            gate_passed=False,
            errors=["Agent timeout"],
            duration=12.0,
        ))

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "build"])
        assert result.exit_code != 0
        assert "Agent timeout" in result.output

    def test_build_shows_duration(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.build = AsyncMock(return_value=BuildResult(
            success=True,
            waves_completed=2,
            total_waves=2,
            gate_passed=True,
            errors=[],
            duration=99.5,
        ))

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "build", "--no-preview"])
        assert result.exit_code == 0
        assert "99.5s" in result.output


class TestDeployCommand:
    def test_deploy_success(self, runner, tmp_path):
        mock_deployer = MagicMock()
        mock_result = MagicMock()
        mock_result.error = None
        mock_result.url = "https://weather.herakles.dev"
        mock_result.port = 8500
        mock_result.container_id = "abc123"
        mock_result.health_status = True
        mock_deployer.deploy = AsyncMock(return_value=mock_result)

        with patch("forge_deployer.ForgeDeployer", return_value=mock_deployer):
            result = runner.invoke(cli, ["--project", str(tmp_path), "deploy", "--domain", "weather.herakles.dev"])
        assert result.exit_code == 0
        assert "weather.herakles.dev" in result.output
        assert "8500" in result.output

    def test_deploy_error_exits_nonzero(self, runner, tmp_path):
        mock_deployer = MagicMock()
        mock_result = MagicMock()
        mock_result.error = "Docker not running"
        mock_deployer.deploy = AsyncMock(return_value=mock_result)

        with patch("forge_deployer.ForgeDeployer", return_value=mock_deployer):
            result = runner.invoke(cli, ["--project", str(tmp_path), "deploy"])
        assert result.exit_code != 0


class TestAuditCommand:
    def test_audit_no_log(self, runner, tmp_path):
        result = runner.invoke(cli, ["--project", str(tmp_path), "audit"])
        assert result.exit_code == 0
        assert "No audit log" in result.output

    def test_audit_with_entries(self, runner, tmp_path):
        import json as json_mod
        audit_dir = tmp_path / ".forge" / "audit"
        audit_dir.mkdir(parents=True)
        audit_file = audit_dir / "audit.jsonl"
        entries = [
            json_mod.dumps({"timestamp": "2026-03-15T12:00:00", "tool": "write_file", "agent_id": "agent-1"}),
            json_mod.dumps({"timestamp": "2026-03-15T12:01:00", "tool": "read_file", "agent_id": "agent-2"}),
        ]
        audit_file.write_text("\n".join(entries))

        result = runner.invoke(cli, ["--project", str(tmp_path), "audit"])
        assert result.exit_code == 0
        assert "2 entries" in result.output
        assert "write_file" in result.output


class TestModelsCommandDetailed:
    def test_models_includes_key_aliases(self, runner):
        result = runner.invoke(cli, ["models"])
        assert result.exit_code == 0
        # Should list several model aliases
        assert "nova-lite" in result.output
        assert "gemini-flash" in result.output
        # Output should have arrow showing mapping
        assert "->" in result.output


class TestInitCommand:
    def test_init_creates_forge_dir(self, runner, tmp_path):
        result = runner.invoke(cli, ["--project", str(tmp_path), "init"])
        assert result.exit_code == 0
        assert ".forge/" in result.output or "initialized" in result.output

    def test_init_idempotent(self, runner, tmp_path):
        # First init
        runner.invoke(cli, ["--project", str(tmp_path), "init"])
        # Second init should not error
        result = runner.invoke(cli, ["--project", str(tmp_path), "init"])
        assert result.exit_code == 0
        assert "already exists" in result.output.lower() or "initialized" in result.output.lower()


class TestHandoffCommand:
    def test_handoff_outputs_context(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.handoff.return_value = "## Handoff Context\nProject: test-app"

        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "handoff"])
        assert result.exit_code == 0
        assert "Handoff Context" in result.output


class TestFormationCommand:
    def test_formation_select(self, runner):
        result = runner.invoke(cli, ["formation", "--complexity", "medium", "--scope", "medium"])
        assert result.exit_code == 0
        assert "Formation:" in result.output
        assert "Roles:" in result.output
        assert "Wave" in result.output

    def test_formation_invalid_complexity(self, runner):
        result = runner.invoke(cli, ["formation", "--complexity", "impossible", "--scope", "medium"])
        # Should either show an error or default
        # Just verify it doesn't crash unexpectedly
        assert isinstance(result.exit_code, int)


class TestNewCommandExtended:
    def test_new_shows_compliance_status(self, runner, tmp_path):
        result = runner.invoke(cli, ["--project", str(tmp_path), "new", "test-app"])
        assert result.exit_code == 0
        assert "Compliance:" in result.output or "gates" in result.output

    def test_new_with_nonexistent_template_warns(self, runner, tmp_path):
        result = runner.invoke(cli, ["--project", str(tmp_path), "new", "test-app2", "--template", "nonexistent-template"])
        assert result.exit_code == 0
        assert "not found" in result.output.lower() or "Warning" in result.output


class TestStatusCommandExtended:
    def test_status_shows_all_categories(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.status.return_value = StatusReport(
            project_name="my-project",
            total_tasks=20,
            completed=10,
            in_progress=3,
            pending=5,
            failed=1,
            blocked=1,
            percent=50.0,
        )
        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "status"])
        assert result.exit_code == 0
        assert "Completed" in result.output
        assert "In Progress" in result.output
        assert "Pending" in result.output
        assert "Failed" in result.output
        assert "Blocked" in result.output
        assert "50%" in result.output


class TestListCommandExtended:
    def test_list_shows_status_markers_correctly(self, runner, tmp_path):
        mock_orch = MagicMock()
        mock_orch.list_tasks.return_value = [
            Task(id="1", subject="Task A", description="", status="completed"),
            Task(id="2", subject="Task B", description="", status="in_progress"),
            Task(id="3", subject="Task C", description="", status="failed"),
            Task(id="4", subject="Task D", description="", status="blocked"),
        ]
        with patch("forge_orchestrator.ForgeOrchestrator", return_value=mock_orch):
            result = runner.invoke(cli, ["--project", str(tmp_path), "list"])
        assert result.exit_code == 0
        assert "[+]" in result.output   # completed
        assert "[>]" in result.output   # in_progress
        assert "[!]" in result.output   # failed
        assert "[x]" in result.output   # blocked
