"""Tests for parallel wave execution in ForgeShell._cmd_build.

Covers:
- parallel wave execution (asyncio.gather within a wave)
- semaphore concurrency limiting
- error isolation (one failed task does not block others)
- sequential wave ordering (wave 1 waits for wave 0)
- single-task wave (no parallel overhead message)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
import time
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config import init_forge_dir
from forge_agent import AgentResult
from forge_tasks import TaskStore, Task


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_shell(tmp_path: Path):
    """Build a minimal ForgeShell pointing at tmp_path."""
    from forge_cli import ForgeShell
    shell = ForgeShell.__new__(ForgeShell)
    shell.project_path = tmp_path
    shell.model = "nova-lite"
    shell.config = {}
    shell._sync_task_state = MagicMock()
    shell._list_project_files = MagicMock(return_value=[])
    shell._gather_project_files = MagicMock(return_value={})
    shell._gather_upstream_artifacts = MagicMock(return_value={})
    # Mock assistant for autonomy-aware prompt building
    shell.assistant = MagicMock()
    shell.assistant.read_autonomy_level = MagicMock(return_value=2)
    shell.assistant.skill_level = "intermediate"
    # Mock session_manager for build completion profile updates
    shell.session_manager = MagicMock()
    mock_profile = MagicMock()
    mock_profile.skill_level = "intermediate"
    mock_profile.to_dict = MagicMock(return_value={})
    shell.session_manager.load_profile = MagicMock(return_value=mock_profile)
    shell.session_manager.update_profile_after_build = MagicMock(return_value=mock_profile)
    return shell


def _make_store(tmp_path: Path, n_tasks: int = 3) -> TaskStore:
    """Initialise a TaskStore with n_tasks independent (no deps) tasks.

    Tasks are created as 'in_progress' since _cmd_build marks them before
    calling _run_single_task. Tests that call _run_single_task directly must
    also ensure tasks are already in_progress.
    """
    init_forge_dir(tmp_path)
    project_tasks_file = tmp_path / ".forge" / "state" / "tasks.json"
    store = TaskStore(project_tasks_file)
    for i in range(n_tasks):
        t = store.create(
            subject=f"Task {i}",
            description=f"Description for task {i}",
            metadata={"project": "test", "sprint": "S1", "risk": "low"},
        )
        # _cmd_build marks tasks in_progress before calling _run_single_task
        store.update(t.id, status="in_progress")
    return store


def _good_result() -> AgentResult:
    return AgentResult(output="done", artifacts={"file.py": "# code"}, tool_calls_made=2)


def _error_result() -> AgentResult:
    return AgentResult(output="", error="Model timeout", tool_calls_made=0)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestParallelWaveExecution:
    """Three independent tasks in the same wave should all run concurrently."""

    @pytest.mark.asyncio
    async def test_parallel_wave_execution(self, tmp_path):
        """Tasks in the same wave run via asyncio.gather, not sequentially."""
        start_times: list[float] = []

        async def fake_agent_run(prompt, system):
            start_times.append(time.monotonic())
            await asyncio.sleep(0.05)  # simulate I/O latency
            return _good_result()

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=3)
        all_tasks = store.list()

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_agent_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(3)
            coros = [
                shell._run_single_task(t, store, all_tasks, 0, None, semaphore)
                for t in all_tasks
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

        assert len(results) == 3
        for r in results:
            assert not isinstance(r, Exception), f"Unexpected exception: {r}"
            assert r[2] == "pass"

        # All three tasks should start nearly simultaneously (within 40 ms of each other)
        # i.e. parallel, not sequential (sequential would be ~100 ms apart)
        assert len(start_times) == 3
        spread = max(start_times) - min(start_times)
        assert spread < 0.04, (
            f"Tasks started too far apart ({spread:.3f}s) — may not be parallel"
        )

    @pytest.mark.asyncio
    async def test_single_task_wave_does_not_raise(self, tmp_path):
        """A wave with a single task runs without error and returns correct tuple."""
        async def fake_agent_run(prompt, system):
            return _good_result()

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=1)
        all_tasks = store.list()

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_agent_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(1)
            result = await shell._run_single_task(all_tasks[0], store, all_tasks, 0, None, semaphore)

        w_idx, name, status, dur, tc, fc, *_extra = result
        assert status == "pass"
        assert w_idx == 0


class TestSemaphoreLimitsConcurrency:
    """With semaphore=2 and 4 tasks, only 2 should run at a time."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, tmp_path):
        concurrency_peak = 0
        active = 0
        lock = asyncio.Lock()

        async def fake_run(prompt, system):
            nonlocal concurrency_peak, active
            async with lock:
                active += 1
                concurrency_peak = max(concurrency_peak, active)
            await asyncio.sleep(0.02)
            async with lock:
                active -= 1
            return _good_result()

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=4)
        all_tasks = store.list()

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(2)
            coros = [
                shell._run_single_task(t, store, all_tasks, 0, None, semaphore)
                for t in all_tasks
            ]
            await asyncio.gather(*coros, return_exceptions=True)

        assert concurrency_peak <= 2, (
            f"Expected peak concurrency <= 2, got {concurrency_peak}"
        )

    @pytest.mark.asyncio
    async def test_semaphore_value_matches_min_of_limit_and_tasks(self, tmp_path):
        """Semaphore value should be min(provider_limit, task_count)."""
        from forge_cli import PROVIDER_CONCURRENCY

        shell = _make_shell(tmp_path)
        # nova-lite → bedrock → limit 3; with 2 tasks → semaphore should be 2
        from config import get_provider
        provider = get_provider(shell.model)
        limit = PROVIDER_CONCURRENCY.get(provider, 4)

        n_tasks = 2  # fewer tasks than the provider limit
        semaphore = asyncio.Semaphore(min(limit, n_tasks))
        assert semaphore._value == n_tasks


class TestFailedTaskDoesntBlockWave:
    """One task raising an exception should not prevent others from completing."""

    @pytest.mark.asyncio
    async def test_failed_task_doesnt_block_wave(self, tmp_path):
        call_count = 0

        async def fake_run(prompt, system):
            nonlocal call_count
            call_count += 1
            # The second call raises
            if call_count == 2:
                raise RuntimeError("Simulated LLM failure")
            return _good_result()

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=3)
        all_tasks = store.list()

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(3)
            coros = [
                shell._run_single_task(t, store, all_tasks, 0, None, semaphore)
                for t in all_tasks
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

        # All 3 coroutines must resolve (no unhandled exception propagation)
        assert len(results) == 3
        statuses = [r[2] if not isinstance(r, Exception) else "exception" for r in results]
        # Two tasks passed, one failed (exception handled internally)
        assert statuses.count("pass") == 2
        assert statuses.count("fail") == 1
        assert "exception" not in statuses

    @pytest.mark.asyncio
    async def test_agent_result_error_field_marks_fail(self, tmp_path):
        """AgentResult.error being set should mark the task failed without exception."""
        async def fake_run(prompt, system):
            return _error_result()

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=1)
        all_tasks = store.list()

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(1)
            result = await shell._run_single_task(all_tasks[0], store, all_tasks, 0, None, semaphore)

        w_idx, name, status, dur, tc, fc, *_extra = result
        assert status == "fail"
        assert w_idx == 0


class TestSequentialWavesRespected:
    """Wave 1 must only start after wave 0 completes."""

    @pytest.mark.asyncio
    async def test_sequential_waves_respected(self, tmp_path):
        """The _cmd_build wave loop must not launch wave N+1 until wave N finishes."""
        completion_order: list[str] = []

        async def fake_run(prompt, system):
            await asyncio.sleep(0.01)
            # Record subject from prompt
            for line in prompt.splitlines():
                if "Wave0 Task" in line or "Wave1 Task" in line:
                    completion_order.append(line.strip())
                    break
            return _good_result()

        shell = _make_shell(tmp_path)

        # Create 2 tasks where Wave1 Task is blocked by Wave0 Task
        init_forge_dir(tmp_path)
        project_tasks_file = tmp_path / ".forge" / "state" / "tasks.json"
        store = TaskStore(project_tasks_file)
        t1 = store.create(
            subject="Wave0 Task",
            description="First wave task",
            metadata={"project": "test", "sprint": "S1", "risk": "low"},
        )
        store.create(
            subject="Wave1 Task",
            description="Second wave task",
            metadata={"project": "test", "sprint": "S1", "risk": "low"},
            blocked_by=[t1.id],
        )

        with patch("forge_agent.ForgeAgent") as MockAgent, \
             patch("forge_agent.BUILT_IN_TOOLS", []):
            instance = AsyncMock()
            instance.run = fake_run
            MockAgent.return_value = instance

            await shell._cmd_build("--no-review")

        # Re-read from disk to see the final persisted state
        store_final = TaskStore(project_tasks_file)
        tasks_after = store_final.list()
        statuses = {t.subject: t.status for t in tasks_after}
        assert statuses["Wave0 Task"] == "completed"
        assert statuses["Wave1 Task"] == "completed"

        # Wave0 Task must appear in completion_order before Wave1 Task
        assert len(completion_order) == 2
        wave0_pos = next(
            (i for i, s in enumerate(completion_order) if "Wave0" in s), None
        )
        wave1_pos = next(
            (i for i, s in enumerate(completion_order) if "Wave1" in s), None
        )
        assert wave0_pos is not None
        assert wave1_pos is not None
        assert wave0_pos < wave1_pos, (
            f"Wave 1 recorded before wave 0 — sequential ordering violated. "
            f"Order: {completion_order}"
        )


class TestReturnTuple:
    """_run_single_task must return an 8-tuple with correct types."""

    @pytest.mark.asyncio
    async def test_return_tuple_structure_on_success(self, tmp_path):
        async def fake_run(prompt, system):
            return _good_result()

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=1)

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(1)
            result = await shell._run_single_task(store.list()[0], store, store.list(), 2, None, semaphore)

        assert isinstance(result, tuple)
        assert len(result) == 8
        w_idx, name, status, dur, tc, fc, *_extra = result
        assert w_idx == 2
        assert isinstance(name, str)
        assert status in ("pass", "fail")
        assert isinstance(dur, float) and dur >= 0
        assert isinstance(tc, int) and tc >= 0
        assert isinstance(fc, int) and fc >= 0

    @pytest.mark.asyncio
    async def test_return_tuple_structure_on_agent_exception(self, tmp_path):
        """When agent.run raises, _run_single_task catches it and returns fail tuple."""
        async def fake_run(prompt, system):
            raise RuntimeError("boom")

        shell = _make_shell(tmp_path)
        store = _make_store(tmp_path, n_tasks=1)

        with patch("forge_agent.ForgeAgent") as MockAgent:
            instance = AsyncMock()
            instance.run = fake_run
            MockAgent.return_value = instance

            semaphore = asyncio.Semaphore(1)
            result = await shell._run_single_task(store.list()[0], store, store.list(), 0, None, semaphore)

        w_idx, name, status, dur, tc, fc, *_extra = result
        assert status == "fail"
        assert tc == 0
        assert fc == 0
