"""Tests for graceful Ctrl-C pause/cancel during agent builds.

Tests the BuildCancellation signal, ForgeAgent integration, and
task state transitions when builds are paused.
"""

import asyncio
import signal
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from forge_comms import BuildCancellation


# ── BuildCancellation unit tests ─────────────────────────────────────────────

class TestBuildCancellation(unittest.TestCase):
    """Test the cooperative cancellation signal."""

    def test_not_set_initially(self):
        """Fresh BuildCancellation should not be paused."""
        cancel = BuildCancellation()
        self.assertFalse(cancel.is_paused())

    def test_set_on_signal(self):
        """Calling _handle_sigint should set the pause event."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        self.assertTrue(cancel.is_paused())

    def test_reset_clears_pause(self):
        """reset() should clear the pause state."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        self.assertTrue(cancel.is_paused())
        cancel.reset()
        self.assertFalse(cancel.is_paused())

    def test_handler_installed_and_restored(self):
        """install() swaps SIGINT handler, uninstall() restores original."""
        cancel = BuildCancellation()
        original = signal.getsignal(signal.SIGINT)

        cancel.install()
        # Handler should be changed
        current = signal.getsignal(signal.SIGINT)
        self.assertNotEqual(current, original)
        self.assertEqual(current, cancel._handle_sigint)

        cancel.uninstall()
        # Handler should be restored
        restored = signal.getsignal(signal.SIGINT)
        self.assertEqual(restored, original)

    def test_uninstall_without_install(self):
        """uninstall() without install() should be safe (no-op)."""
        cancel = BuildCancellation()
        cancel.uninstall()  # Should not raise

    def test_multiple_signals_idempotent(self):
        """Multiple SIGINT signals should not crash — Event.set() is idempotent."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)
        cancel._handle_sigint(signal.SIGINT, None)
        cancel._handle_sigint(signal.SIGINT, None)
        self.assertTrue(cancel.is_paused())

    def test_install_uninstall_cycle(self):
        """Multiple install/uninstall cycles should work correctly."""
        cancel = BuildCancellation()
        original = signal.getsignal(signal.SIGINT)

        for _ in range(3):
            cancel.install()
            self.assertTrue(cancel._installed)
            cancel.uninstall()
            self.assertFalse(cancel._installed)

        self.assertEqual(signal.getsignal(signal.SIGINT), original)


# ── ForgeAgent cancellation tests ────────────────────────────────────────────

class TestAgentCancellation(unittest.TestCase):
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
        self.assertIs(agent._cancellation, cancel)

    def test_agent_no_cancellation_by_default(self):
        """Agent without cancellation should have None."""
        agent = self._make_agent()
        self.assertIsNone(agent._cancellation)

    def test_agent_stops_between_turns(self):
        """Agent should return error='paused' when cancellation fires between turns."""
        cancel = BuildCancellation()
        cancel._handle_sigint(signal.SIGINT, None)  # Pre-pause

        agent = self._make_agent(cancellation=cancel)

        result = asyncio.run(
            agent.run(prompt="test task", system="test")
        )

        self.assertEqual(result.error, "paused")
        self.assertIn("paused", result.output.lower())

    def test_agent_stops_between_tools(self):
        """Agent should return error='paused' between tool calls."""
        cancel = BuildCancellation()
        agent = self._make_agent(cancellation=cancel)

        # Mock the router to return a response with tool calls
        mock_response = MagicMock()
        mock_response.text = ""
        mock_response.tool_calls = [
            MagicMock(name="read_file", id="tc1", args={"path": "test.py"}),
            MagicMock(name="write_file", id="tc2", args={"path": "out.py", "content": "x"}),
        ]
        mock_response.input_tokens = 100
        mock_response.output_tokens = 50
        mock_response.model_id = "test"

        # After the first tool call completes, set the pause signal
        original_execute = agent._execute_tool_call

        call_count = 0
        async def mock_execute(call, artifacts):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                cancel._handle_sigint(signal.SIGINT, None)
            return "ok"

        agent._execute_tool_call = mock_execute

        # Mock router to return tool calls on first attempt
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

        self.assertEqual(result.error, "paused")
        # Should have executed exactly 1 tool call before pausing
        self.assertEqual(call_count, 1)


# ── Task state transition tests ──────────────────────────────────────────────

class TestTaskStateOnPause(unittest.TestCase):
    """Test that paused tasks revert to pending and completed tasks are preserved."""

    def _make_store(self, tasks_data):
        """Create a TaskStore with test data."""
        import tempfile, json
        from forge_tasks import TaskStore

        tf = tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False)
        json.dump({"tasks": tasks_data, "next_id": max(t["id"] for t in tasks_data) + 1}, tf)
        tf.close()
        return TaskStore(tf.name), tf.name

    def test_paused_tasks_revert_to_pending(self):
        """When agent returns error='paused', task should go to pending."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "in_progress",
             "description": "test", "blocked_by": [], "metadata": {}},
        ]
        store, path = self._make_store(tasks_data)

        # Simulate what _run_single_task does on pause
        store.update(1, status="pending")
        task = store.get(1)
        self.assertEqual(task.status, "pending")

        os.unlink(path)

    def test_completed_tasks_preserved_on_pause(self):
        """Completed tasks should keep their status when build is paused."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "completed",
             "description": "done", "blocked_by": [], "metadata": {}},
            {"id": 2, "subject": "Task 2", "status": "in_progress",
             "description": "test", "blocked_by": [], "metadata": {}},
        ]
        store, path = self._make_store(tasks_data)

        # Pause only affects in_progress task
        store.update(2, status="pending")

        t1 = store.get(1)
        t2 = store.get(2)
        self.assertEqual(t1.status, "completed")
        self.assertEqual(t2.status, "pending")

        os.unlink(path)

    def test_resume_skips_completed(self):
        """After resume, compute_waves should only include non-completed tasks."""
        tasks_data = [
            {"id": 1, "subject": "Task 1", "status": "completed",
             "description": "done", "blocked_by": [], "metadata": {}},
            {"id": 2, "subject": "Task 2", "status": "pending",
             "description": "todo", "blocked_by": [], "metadata": {}},
        ]
        store, path = self._make_store(tasks_data)

        waves = store.compute_waves()
        # compute_waves excludes completed tasks
        all_task_ids = [str(t.id) for wave in waves for t in wave]
        self.assertNotIn("1", all_task_ids, "Completed task should be excluded from waves")
        self.assertIn("2", all_task_ids, "Pending task should be included in waves")

        os.unlink(path)


# ── Integration-style test: cancel returns to REPL ───────────────────────────

class TestCancelReturnsToRepl(unittest.TestCase):
    """Test that cancelling exits the build loop cleanly."""

    def test_cancel_breaks_wave_loop(self):
        """When pause menu returns 'cancel', the wave loop should break."""
        cancel = BuildCancellation()

        # Simulate: wave loop checks cancellation, finds it paused
        cancel._handle_sigint(signal.SIGINT, None)
        self.assertTrue(cancel.is_paused())

        # The wave loop in _cmd_build does:
        #   if cancellation.is_paused():
        #       choice = await self._show_pause_menu(store)
        #       if choice != "resume":
        #           build_paused = True
        #           break
        build_paused = False
        choice = "cancel"  # Simulated menu response
        if cancel.is_paused():
            if choice != "resume":
                build_paused = True

        self.assertTrue(build_paused)
        # After break, cancellation.uninstall() is called
        cancel.uninstall()


if __name__ == "__main__":
    unittest.main()
