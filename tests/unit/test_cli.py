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
