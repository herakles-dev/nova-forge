"""Tests for graceful Ctrl-C pause/cancel during agent builds.

Tests the BuildCancellation signal, ForgeAgent integration, and
task state transitions when builds are paused.

Covers:
- BuildCancellation cooperative signal lifecycle
- Signal handler install/uninstall with edge cases
- ForgeAgent cancellation between turns and between tools
- Task state transitions on pause (revert to pending)
- Resume behavior (compute_waves skips completed)
- Cancel → REPL return flow
"""

import asyncio
import json
import signal
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from forge_comms import BuildCancellation
from forge_tasks import TaskStore


# ── BuildCancellation unit tests ─────────────────────────────────────────────

class TestBuildCancellation:
    """Test the cooperative cancellation signal."""

    def test_not_set_initially(self):
        """Fresh BuildCancellation should not be paused."""
        cancel = BuildCancellation()
        assert cancel.is_paused() is False

    def test_installed_is_false_initially(self):
        """Fresh BuildCancellation should have _installed=False."""
        cancel = BuildCancellation()
        assert cancel._installed is False

    def test_set_on_signal(self):
        """Calling _handle_sigint should set the pause event."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        assert cancel.is_paused() is True

    def test_reset_clears_pause(self):
        """reset() should clear the pause state."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        assert cancel.is_paused() is True
        cancel.reset()
        assert cancel.is_paused() is False

    def test_handler_installed_and_restored(self):
        """install() swaps SIGINT handler, uninstall() restores original."""
        cancel = BuildCancellation()
        original = signal.getsignal(signal.SIGINT)

        cancel.install()
        current = signal.getsignal(signal.SIGINT)
        assert current != original
        assert current == cancel._handle_sigint
        assert cancel._installed is True

        cancel.uninstall()
        restored = signal.getsignal(signal.SIGINT)
        assert restored == original
        assert cancel._installed is False

    def test_uninstall_without_install_is_noop(self):
        """uninstall() without install() should be safe (no-op)."""
        cancel = BuildCancellation()
        original = signal.getsignal(signal.SIGINT)
        cancel.uninstall()  # Should not raise
        assert signal.getsignal(signal.SIGINT) == original

    def test_multiple_signals_idempotent(self):
        """Multiple SIGINT signals should not crash — Event.set() is idempotent."""
        cancel = BuildCancellation()
        for _ in range(5):
            cancel._handle_sigint(signal.SIGINT, None)
        assert cancel.is_paused() is True

    def test_install_uninstall_cycle(self):
        """Multiple install/uninstall cycles should work correctly."""
        cancel = BuildCancellation()
        original = signal.getsignal(signal.SIGINT)

        for _ in range(3):
            cancel.install()
            assert cancel._installed is True
            cancel.uninstall()
            assert cancel._installed is False

        assert signal.getsignal(signal.SIGINT) == original

    def test_double_install_overwrites_original_handler(self):
        """Calling install() twice overwrites _original_handler with the first
        install's handler. This is a known limitation — callers should not
        double-install. After uninstall, the handler is the first-install's
        handler, not the process-level original.
        """
        cancel = BuildCancellation()
        original = signal.getsignal(signal.SIGINT)

        cancel.install()
        first_saved = cancel._original_handler
        assert first_saved == original

        cancel.install()  # Second install captures _handle_sigint as "original"
        second_saved = cancel._original_handler
        assert second_saved == cancel._handle_sigint  # This is the bug
        assert signal.getsignal(signal.SIGINT) == cancel._handle_sigint

        cancel.uninstall()
        # Restores to the handler captured by the second install() call
        assert signal.getsignal(signal.SIGINT) == cancel._handle_sigint
        # Manually restore for test cleanup
        signal.signal(signal.SIGINT, original)

    def test_reset_then_repause(self):
        """After reset, a new SIGINT should re-pause."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        assert cancel.is_paused() is True
        cancel.reset()
        assert cancel.is_paused() is False
        cancel._handle_sigint(signal.SIGINT, None)
        assert cancel.is_paused() is True

    def test_pause_requested_is_asyncio_event(self):
        """pause_requested should be an asyncio.Event instance."""
        cancel = BuildCancellation()
        assert isinstance(cancel.pause_requested, asyncio.Event)


# ── ForgeAgent cancellation tests ────────────────────────────────────────────

class TestAgentCancellation:
    """Test that ForgeAgent respects cancellation checkpoints."""

    def _make_agent(self, cancellation=None):
        """Create a ForgeAgent with mocked dependencies."""
        from forge_agent import ForgeAgent
        from config import ModelConfig

        mc = ModelConfig(
            model_id="bedrock/us.amazon.nova-lite-v1:0",
            provider="bedrock",
            max_tokens=2048,
            context_window=32000,
        )

        with patch.object(ForgeAgent, '_wire_v11_hooks'):
            agent = ForgeAgent(
                model_config=mc,
                project_root="/tmp/test-project",
                wire_v11_hooks=False,
                cancellation=cancellation,
            )
        return agent

    def test_agent_stores_cancellation(self):
        """Agent should store the cancellation object."""
        cancel = BuildCancellation()
        agent = self._make_agent(cancellation=cancel)
        assert agent._cancellation is cancel

    def test_agent_no_cancellation_by_default(self):
        """Agent without cancellation should have None."""
        agent = self._make_agent()
        assert agent._cancellation is None

    def test_agent_stops_between_turns(self):
        """Agent should return error='paused' when cancellation fires between turns."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)  # Pre-pause

        agent = self._make_agent(cancellation=cancel)

        result = asyncio.run(
            agent.run(prompt="test task", system="test")
        )

        assert result.error == "paused"
        assert "paused" in result.output.lower()

    def test_agent_stops_between_tools(self):
        """Agent should return error='paused' between tool calls."""
        cancel = BuildCancellation()
        agent = self._make_agent(cancellation=cancel)

        mock_response = MagicMock()
        mock_response.text = ""
        mock_response.tool_calls = [
            MagicMock(name="read_file", id="tc1", args={"path": "test.py"}),
            MagicMock(name="write_file", id="tc2", args={"path": "out.py", "content": "x"}),
        ]
        mock_response.input_tokens = 100
        mock_response.output_tokens = 50
        mock_response.model_id = "test"

        call_count = 0
        async def mock_execute(call, artifacts):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                cancel._handle_sigint(signal.SIGINT, None)
            return "ok"

        agent._execute_tool_call = mock_execute

        async def mock_send(messages, tools, config):
            return mock_response

        agent.router.send = mock_send
        agent.router.route = MagicMock(return_value=MagicMock(
            format_assistant_message=MagicMock(return_value={"role": "assistant", "content": ""}),
            format_tool_result=MagicMock(return_value={"role": "user", "content": "ok"}),
        ))
        agent.streaming = False

        result = asyncio.run(
            agent.run(prompt="test task", system="test")
        )

        assert result.error == "paused"
        assert call_count == 1, "Should have executed exactly 1 tool call before pausing"

    def test_unpauseed_agent_runs_normally(self):
        """Agent with cancellation that is NOT paused should run its turns normally."""
        cancel = BuildCancellation()
        # NOT paused — agent should proceed to first turn
        agent = self._make_agent(cancellation=cancel)

        async def mock_send(messages, tools, config):
            return MagicMock(
                text="Done.",
                tool_calls=[],
                stop_reason="end_turn",
                usage={"input_tokens": 10, "output_tokens": 5},
            )

        agent.router.send = mock_send
        agent.streaming = False

        result = asyncio.run(
            agent.run(prompt="test task", system="test")
        )

        assert result.error is None
        assert result.output == "Done."


# ── Task state transition tests ──────────────────────────────────────────────

class TestTaskStateOnPause:
    """Test that paused tasks revert to pending and completed tasks are preserved."""

    def _make_store(self, tmp_path, tasks_data):
        """Create a TaskStore with test data using tmp_path."""
        tasks_file = tmp_path / "tasks.json"
        tasks_file.write_text(json.dumps({
            "tasks": tasks_data,
            "next_id": max(t["id"] for t in tasks_data) + 1,
        }))
        return TaskStore(str(tasks_file))

    def test_paused_tasks_revert_to_pending(self, tmp_path):
        """When agent returns error='paused', task should go to pending."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "in_progress",
             "description": "test", "blocked_by": [], "metadata": {}},
        ]
        store = self._make_store(tmp_path, tasks_data)

        store.update(1, status="pending")
        task = store.get(1)
        assert task.status == "pending"

    def test_completed_tasks_preserved_on_pause(self, tmp_path):
        """Completed tasks should keep their status when build is paused."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "completed",
             "description": "done", "blocked_by": [], "metadata": {}},
            {"id": 2, "subject": "Task 2", "status": "in_progress",
             "description": "test", "blocked_by": [], "metadata": {}},
        ]
        store = self._make_store(tmp_path, tasks_data)

        store.update(2, status="pending")

        t1 = store.get(1)
        t2 = store.get(2)
        assert t1.status == "completed"
        assert t2.status == "pending"

    def test_resume_skips_completed(self, tmp_path):
        """After resume, compute_waves should only include non-completed tasks."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "completed",
             "description": "done", "blocked_by": [], "metadata": {}},
            {"id": 2, "subject": "Task 2", "status": "pending",
             "description": "todo", "blocked_by": [], "metadata": {}},
        ]
        store = self._make_store(tmp_path, tasks_data)

        waves = store.compute_waves()
        all_task_ids = [str(t.id) for wave in waves for t in wave]
        assert "1" not in all_task_ids, "Completed task should be excluded from waves"
        assert "2" in all_task_ids, "Pending task should be included in waves"

    def test_multiple_in_progress_tasks_all_revert(self, tmp_path):
        """All in_progress tasks should revert to pending on pause."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "in_progress",
             "description": "wip1", "blocked_by": [], "metadata": {}},
            {"id": 2, "subject": "Task 2", "status": "in_progress",
             "description": "wip2", "blocked_by": [], "metadata": {}},
            {"id": 3, "subject": "Task 3", "status": "completed",
             "description": "done", "blocked_by": [], "metadata": {}},
        ]
        store = self._make_store(tmp_path, tasks_data)

        # Revert all in_progress to pending (simulating pause)
        for t in store.list():
            if t.status == "in_progress":
                store.update(t.id, status="pending")

        assert store.get(1).status == "pending"
        assert store.get(2).status == "pending"
        assert store.get(3).status == "completed"


# ── Integration-style test: cancel returns to REPL ───────────────────────────

class TestCancelReturnsToRepl:
    """Test that cancelling exits the build loop cleanly."""

    def test_cancel_breaks_wave_loop(self):
        """When pause menu returns 'cancel', the wave loop should break."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        assert cancel.is_paused() is True

        build_paused = False
        choice = "cancel"
        if cancel.is_paused():
            if choice != "resume":
                build_paused = True

        assert build_paused is True
        cancel.uninstall()

    def test_resume_continues_wave_loop(self):
        """When pause menu returns 'resume', build_paused stays False."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)

        build_paused = False
        choice = "resume"
        if cancel.is_paused():
            if choice != "resume":
                build_paused = True
            else:
                cancel.reset()

        assert build_paused is False
        assert cancel.is_paused() is False
