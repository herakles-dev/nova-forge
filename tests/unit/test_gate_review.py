"""Unit tests for ForgeShell._run_gate_review and gate review wiring in _cmd_build."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from forge_agent import AgentResult
from forge_tasks import Task


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_shell(tmp_path: Path):
    """Construct a ForgeShell pointing at tmp_path without a live CLI session."""
    from forge_cli import ForgeShell
    shell = ForgeShell.__new__(ForgeShell)
    shell.project_path = tmp_path
    shell.model = "bedrock/us.amazon.nova-2-lite-v1:0"
    shell.state = {}
    return shell


def make_store_with_tasks(tasks: list[Task]) -> MagicMock:
    """Return a mock TaskStore whose list() returns the given tasks."""
    store = MagicMock()
    store.list.return_value = tasks
    return store


def run(coro):
    return asyncio.run(coro)


# ── Tests for _run_gate_review ────────────────────────────────────────────────

class TestRunGateReview:
    def test_gate_review_pass(self, tmp_path):
        """Agent output containing GATE: PASS returns status='pass'."""
        shell = make_shell(tmp_path)
        task = Task(id="t1", subject="Write server", description="", status="completed",
                    artifacts={"app.py": {}})
        store = make_store_with_tasks([task])

        mock_result = AgentResult(output="Reviewed all files.\nGATE: PASS", turns=2)
        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.run = AsyncMock(return_value=mock_result)
            result = run(shell._run_gate_review(store, "Build a web app"))

        assert result["status"] == "pass"
        assert result["issues"] == []
        assert result["summary"] == "All checks passed"

    def test_gate_review_fail(self, tmp_path):
        """Agent output containing GATE: FAIL returns status='fail' with reason."""
        shell = make_shell(tmp_path)
        store = make_store_with_tasks([])

        mock_result = AgentResult(
            output="Missing imports in routes.py.\nGATE: FAIL - missing imports",
            turns=3,
        )
        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.run = AsyncMock(return_value=mock_result)
            result = run(shell._run_gate_review(store, "Build an API"))

        assert result["status"] == "fail"
        assert "missing imports" in result["issues"][0]
        assert "missing imports" in result["summary"]

    def test_gate_review_conditional(self, tmp_path):
        """Agent output containing GATE: CONDITIONAL returns status='conditional'."""
        shell = make_shell(tmp_path)
        store = make_store_with_tasks([])

        mock_result = AgentResult(
            output="Routes missing auth middleware.\nGATE: CONDITIONAL - incomplete routes",
            turns=4,
        )
        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.run = AsyncMock(return_value=mock_result)
            result = run(shell._run_gate_review(store, "Build an API"))

        assert result["status"] == "conditional"
        assert "incomplete routes" in result["issues"][0]
        assert "incomplete routes" in result["summary"]

    def test_gate_review_unparseable(self, tmp_path):
        """When agent output lacks any GATE marker, returns conditional with fallback summary."""
        shell = make_shell(tmp_path)
        store = make_store_with_tasks([])

        mock_result = AgentResult(
            output="The code looks okay but I am not sure about the imports.",
            turns=2,
        )
        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.run = AsyncMock(return_value=mock_result)
            result = run(shell._run_gate_review(store, "spec"))

        assert result["status"] == "conditional"
        assert result["issues"] == ["Review agent did not produce a clear verdict"]
        # summary should contain truncated agent output
        assert "okay" in result["summary"]

    def test_gate_review_agent_error(self, tmp_path):
        """When agent.run raises an exception, returns conditional with error message."""
        shell = make_shell(tmp_path)
        store = make_store_with_tasks([])

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.run = AsyncMock(side_effect=RuntimeError("connection timeout"))
            result = run(shell._run_gate_review(store, "spec"))

        assert result["status"] == "conditional"
        assert any("connection timeout" in issue for issue in result["issues"])
        assert "connection timeout" in result["summary"]

    def test_gate_review_uses_readonly_tools(self, tmp_path):
        """Gate reviewer must only receive read-only tools (no write_file, edit_file, bash)."""
        shell = make_shell(tmp_path)
        store = make_store_with_tasks([])

        mock_result = AgentResult(output="GATE: PASS", turns=1)
        captured_tools = []

        def capture_agent(*args, **kwargs):
            captured_tools.extend(kwargs.get("tools", []))
            inst = MagicMock()
            inst.run = AsyncMock(return_value=mock_result)
            return inst

        with patch("forge_agent.ForgeAgent", side_effect=capture_agent):
            run(shell._run_gate_review(store, "spec"))

        write_names = {t["name"] for t in captured_tools if t["name"] in
                       {"write_file", "edit_file", "bash", "search_replace_all"}}
        assert write_names == set(), f"Write tools must not be passed to gate reviewer: {write_names}"

    def test_gate_review_file_list_from_artifacts(self, tmp_path):
        """Gate reviewer prompt should include artifact file paths from completed tasks."""
        shell = make_shell(tmp_path)
        task = Task(
            id="t1", subject="Build server", description="", status="completed",
            artifacts={"app.py": "content", "routes.py": "content"},
        )
        store = make_store_with_tasks([task])

        mock_result = AgentResult(output="GATE: PASS", turns=1)
        captured_prompts = []

        def capture_agent(*args, **kwargs):
            inst = MagicMock()
            async def run_capture(prompt, system="", context=None):
                captured_prompts.append(prompt)
                return mock_result
            inst.run = run_capture
            return inst

        with patch("forge_agent.ForgeAgent", side_effect=capture_agent):
            run(shell._run_gate_review(store, "spec"))

        assert captured_prompts, "Agent.run should have been called"
        prompt_text = captured_prompts[0]
        assert "app.py" in prompt_text or "routes.py" in prompt_text


# ── Tests for --no-review flag in _cmd_build ──────────────────────────────────

class TestNoReviewFlag:
    def test_no_review_flag_skips_gate_review(self, tmp_path):
        """Passing '--no-review' in arg causes _run_gate_review to not be called."""
        from forge_cli import ForgeShell
        from forge_tasks import TaskStore
        from config import ForgeProject as FP

        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path
        shell.model = "bedrock/us.amazon.nova-2-lite-v1:0"
        shell.state = {}

        # Populate tasks.json with one pending task via TaskStore
        project = FP(root=tmp_path)
        store = TaskStore(project.tasks_file)
        store.create(subject="Setup", description="do setup")

        gate_review_called = []

        async def fake_gate_review(s, spec):
            gate_review_called.append(True)
            return {"status": "pass", "issues": [], "summary": "ok"}

        shell._run_gate_review = fake_gate_review
        shell._sync_task_state = MagicMock()
        shell._list_project_files = MagicMock(return_value=[])
        shell._gather_project_files = MagicMock(return_value={})

        async def run_build():
            await shell._cmd_build("--no-review")

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = MockAgent.return_value
            instance.run = AsyncMock(return_value=AgentResult(output="done", turns=1, artifacts={}))
            asyncio.run(run_build())

        assert gate_review_called == [], "Gate review must NOT be called when --no-review is passed"
