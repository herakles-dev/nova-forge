"""Tests for forge_pipeline.py — WaveExecutor, ArtifactManager, GateReviewer."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config import init_forge_dir, get_model_config
from forge_agent import AgentResult
from forge_pipeline import (
    ArtifactManager, WaveExecutor, GateReviewer,
    WaveResult, GateResult, PipelineResult,
)
from forge_tasks import TaskStore, Task
from formations import get_formation, Formation, Role


# ── ArtifactManager ──────────────────────────────────────────────────────────

class TestArtifactManager:
    def test_create_workspace(self, tmp_path):
        am = ArtifactManager(tmp_path / "artifacts")
        ws = am.create_agent_workspace(0, "backend-impl")
        assert ws.exists()
        assert ws.name == "backend-impl"
        assert ws.parent.name == "wave-0"

    def test_store_and_read(self, tmp_path):
        am = ArtifactManager(tmp_path / "artifacts")
        ref = am.store(0, "impl", "spec.md", "# Specification\nHello")
        assert ref["ref_key"] == "wave-0/impl/spec.md"
        assert ref["size_bytes"] > 0

        content = am.read(ref)
        assert "Specification" in content

    def test_read_missing_artifact(self, tmp_path):
        am = ArtifactManager(tmp_path / "artifacts")
        result = am.read({"ref_key": "missing", "path": "/nonexistent/file"})
        assert "not found" in result

    def test_merge_wave_artifacts(self, tmp_path):
        am = ArtifactManager(tmp_path / "artifacts")
        agent_results = {
            "backend": AgentResult(
                output="done",
                artifacts={"summary": "Backend built 3 endpoints"},
            ),
            "frontend": AgentResult(
                output="done",
                artifacts={"summary": "Frontend built 2 pages"},
            ),
        }
        merged = am.merge_wave_artifacts(0, agent_results)
        assert "backend:summary" in merged
        assert "frontend:summary" in merged
        assert merged["_wave_index"] == 0

    def test_merge_handles_error_agents(self, tmp_path):
        am = ArtifactManager(tmp_path / "artifacts")
        agent_results = {
            "failing": AgentResult(
                output="",
                error="Model timeout",
                artifacts={"partial": "some data"},
            ),
        }
        merged = am.merge_wave_artifacts(0, agent_results)
        assert "failing:partial" in merged

    def test_save_index(self, tmp_path):
        am = ArtifactManager(tmp_path / "artifacts")
        am.store(0, "impl", "file.py", "print('hello')")
        am.save_index()
        index_path = tmp_path / "artifacts" / "index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text())
        assert "wave-0/impl/file.py" in data

    def test_inject_upstream_inline(self, tmp_path):
        """Artifacts <= 2KB are inlined into context."""
        am = ArtifactManager(tmp_path / "artifacts")
        project = init_forge_dir(tmp_path)
        store = TaskStore(project.tasks_file)

        # Create a completed dependency task with small artifact
        dep = store.create(subject="Dep task", description="")
        store.update(dep.id, status="in_progress")
        store.update(dep.id, status="completed", artifacts={"spec": "small content"})

        # Create dependent task
        task = store.create(subject="Main task", description="", blocked_by=[dep.id])

        context = am.inject_upstream(task, store)
        key = f"task-{dep.id}:spec"
        assert key in context
        assert context[key] == "small content"

    def test_inject_upstream_truncates_large(self, tmp_path):
        """Artifacts > 2KB get truncated preview."""
        am = ArtifactManager(tmp_path / "artifacts")
        project = init_forge_dir(tmp_path)
        store = TaskStore(project.tasks_file)

        dep = store.create(subject="Dep", description="")
        store.update(dep.id, status="in_progress")
        big_content = "x" * 5000
        store.update(dep.id, status="completed", artifacts={"big": big_content})

        task = store.create(subject="Main", description="", blocked_by=[dep.id])
        context = am.inject_upstream(task, store)
        key = f"task-{dep.id}:big"
        assert key in context
        assert "truncated" in context[key]
        assert len(context[key]) < 5000


# ── WaveExecutor ─────────────────────────────────────────────────────────────

class TestWaveExecutor:
    @pytest.fixture
    def setup(self, tmp_path):
        project = init_forge_dir(tmp_path)
        store = TaskStore(project.tasks_file)
        formation = get_formation("lightweight-feature")
        executor = WaveExecutor(
            project_root=tmp_path,
            formation=formation,
            store=store,
            max_concurrent=2,
        )
        return executor, store, tmp_path

    def test_semaphore_initialized(self, setup):
        executor, _, _ = setup
        assert executor.semaphore._value == 2

    def test_empty_tasks_returns_empty_pipeline(self, setup):
        executor, store, _ = setup
        # No tasks — compute_waves returns []
        result = asyncio.run(executor.execute_all_waves())
        assert result.waves_completed == 0
        assert result.total_waves == 0
        assert result.errors == []

    @patch("forge_pipeline.ForgeAgent")
    def test_single_wave_execution(self, mock_agent_cls, setup):
        """One wave with one task — agent runs and task gets completed."""
        executor, store, tmp_path = setup

        # Create a task
        t = store.create(
            subject="Build feature",
            description="Implement the feature",
            metadata={"agent": "implementer"},
        )

        # Mock the agent's run() method
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=AgentResult(
            output="Feature built successfully",
            artifacts={"main.py": {"action": "write", "size": 100}},
        ))
        mock_agent_cls.return_value = mock_agent

        result = asyncio.run(executor.execute_all_waves())

        assert result.waves_completed >= 1
        assert len(result.errors) == 0

        # Task should be marked completed
        updated = store.get(t.id)
        assert updated.status == "completed"

    @patch("forge_pipeline.ForgeAgent")
    def test_failed_agent_marks_task_failed(self, mock_agent_cls, setup):
        executor, store, _ = setup

        t = store.create(
            subject="Failing task",
            description="This will fail",
            metadata={"agent": "implementer"},
        )

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=AgentResult(
            output="",
            error="Model refused to respond",
        ))
        mock_agent_cls.return_value = mock_agent

        result = asyncio.run(executor.execute_all_waves())

        assert len(result.errors) >= 1
        assert "refused" in result.errors[0].lower()

        updated = store.get(t.id)
        assert updated.status == "failed"

    @patch("forge_pipeline.ForgeAgent")
    def test_failed_blocks_dependents(self, mock_agent_cls, setup):
        executor, store, _ = setup

        t1 = store.create(subject="Step 1", description="", metadata={"agent": "implementer"})
        t2 = store.create(subject="Step 2", description="", metadata={"agent": "tester"}, blocked_by=[t1.id])

        # t1 fails
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=AgentResult(output="", error="crash"))
        mock_agent_cls.return_value = mock_agent

        result = asyncio.run(executor.execute_all_waves())

        t2_updated = store.get(t2.id)
        assert t2_updated.status == "blocked"

    def test_cycle_detection_returns_error(self, tmp_path):
        """Cycle in tasks → pipeline returns error immediately."""
        project = init_forge_dir(tmp_path)
        store = TaskStore(project.tasks_file)
        formation = get_formation("single-file")

        t1 = store.create(subject="A", description="")
        t2 = store.create(subject="B", description="", blocked_by=[t1.id])
        # Manually create cycle
        store.update(t1.id, blocked_by=[t2.id])

        executor = WaveExecutor(
            project_root=tmp_path,
            formation=formation,
            store=store,
        )
        result = asyncio.run(executor.execute_all_waves())
        assert result.waves_completed == 0
        assert any("cycle" in e.lower() for e in result.errors)

    def test_tool_filtering(self, setup):
        executor, _, _ = setup
        # "testing" policy should exclude write_file and edit_file
        tools = executor._filter_tools("testing")
        tool_names = {t["name"] for t in tools}
        assert "write_file" not in tool_names
        assert "edit_file" not in tool_names
        assert "read_file" in tool_names
        assert "bash" in tool_names

    def test_find_role_by_agent_hint(self, setup):
        executor, store, _ = setup
        t = store.create(subject="Test", description="", metadata={"agent": "tester"})
        role = executor._find_role_for_task(t)
        assert role.name == "tester"

    def test_find_role_fallback(self, setup):
        executor, store, _ = setup
        t = store.create(subject="Test", description="", metadata={"agent": "nonexistent"})
        role = executor._find_role_for_task(t)
        # Should fall back to first role in wave_order
        assert role is not None


# ── GateReviewer ─────────────────────────────────────────────────────────────

class TestGateReviewer:
    def test_no_criteria_auto_pass(self):
        reviewer = GateReviewer(project_root=Path("/tmp"))
        result = asyncio.run(reviewer.review(
            wave_results=[],
            gate_criteria=[],
        ))
        assert result.status == "PASS"
        assert "auto-pass" in result.reasons[0].lower()

    def test_no_wave_results_conditional(self):
        reviewer = GateReviewer(project_root=Path("/tmp"))
        result = asyncio.run(reviewer.review(
            wave_results=[],
            gate_criteria=["All tests pass"],
        ))
        assert result.status == "CONDITIONAL"

    def test_parse_verdict_valid_json(self):
        reviewer = GateReviewer()
        result = reviewer._parse_verdict(json.dumps({
            "status": "PASS",
            "reasons": ["All criteria met"],
            "recommendations": [],
        }))
        assert result.status == "PASS"
        assert result.reasons == ["All criteria met"]

    def test_parse_verdict_from_code_fence(self):
        reviewer = GateReviewer()
        output = "Here's my analysis:\n```json\n" + json.dumps({
            "status": "FAIL",
            "reasons": ["Missing tests"],
            "recommendations": ["Add unit tests"],
        }) + "\n```\nThat's my verdict."
        result = reviewer._parse_verdict(output)
        assert result.status == "FAIL"
        assert "Missing tests" in result.reasons

    def test_parse_verdict_embedded_json(self):
        reviewer = GateReviewer()
        output = 'I found: {"status": "CONDITIONAL", "reasons": ["Partial"], "recommendations": []}'
        result = reviewer._parse_verdict(output)
        assert result.status == "CONDITIONAL"

    def test_parse_verdict_no_json_returns_conditional(self):
        reviewer = GateReviewer()
        result = reviewer._parse_verdict("No JSON here at all, just text")
        assert result.status == "CONDITIONAL"

    def test_parse_verdict_empty_output(self):
        reviewer = GateReviewer()
        result = reviewer._parse_verdict("")
        assert result.status == "CONDITIONAL"

    def test_parse_verdict_invalid_status_normalized(self):
        reviewer = GateReviewer()
        result = reviewer._parse_verdict(json.dumps({
            "status": "MAYBE",
            "reasons": ["Unsure"],
            "recommendations": [],
        }))
        assert result.status == "CONDITIONAL"

    @patch("forge_pipeline.ForgeAgent")
    def test_review_with_mocked_agent(self, mock_agent_cls):
        """Full review flow with mocked agent returning PASS verdict."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(return_value=AgentResult(
            output=json.dumps({
                "status": "PASS",
                "reasons": ["All endpoints tested", "Coverage at 85%"],
                "recommendations": ["Consider adding load tests"],
            }),
        ))
        mock_agent_cls.return_value = mock_agent

        reviewer = GateReviewer(model="bedrock/us.amazon.nova-2-lite-v1:0")
        wave_result = WaveResult(
            wave_index=0,
            agent_results={"impl": AgentResult(output="built stuff")},
            artifacts={"impl:main.py": {"action": "write", "size": 500}},
            errors=[],
            duration=10.0,
        )

        result = asyncio.run(reviewer.review(
            wave_results=[wave_result],
            gate_criteria=["All endpoints tested", "Coverage > 80%"],
        ))
        assert result.status == "PASS"
        assert len(result.reasons) == 2

    @patch("forge_pipeline.ForgeAgent")
    def test_review_agent_exception(self, mock_agent_cls):
        """Agent raising exception → CONDITIONAL result."""
        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=RuntimeError("Model unavailable"))
        mock_agent_cls.return_value = mock_agent

        reviewer = GateReviewer()
        wave_result = WaveResult(
            wave_index=0, agent_results={}, artifacts={}, errors=[], duration=1.0,
        )

        result = asyncio.run(reviewer.review(
            wave_results=[wave_result],
            gate_criteria=["Syntax check"],
        ))
        assert result.status == "CONDITIONAL"
        assert "exception" in result.reasons[0].lower()

    def test_build_artifacts_summary(self):
        reviewer = GateReviewer()
        wr = WaveResult(
            wave_index=0,
            agent_results={"impl": AgentResult(output="done")},
            artifacts={"impl:file.py": "code here"},
            errors=["one error"],
            duration=5.5,
        )
        summary = reviewer._build_artifacts_summary([wr])
        assert "Wave 0" in summary
        assert "5.5s" in summary
        assert "Errors: 1" in summary
