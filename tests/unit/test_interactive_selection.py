"""Tests for interactive selection upgrades — /model, /config, /resume, /formation, /autonomy.

Verifies that commands:
1. Open interactive selectors when called without arguments (non-TTY returns defaults)
2. Still handle direct args correctly (backward compat)
3. Handle cancellation (None return from selector)
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from pathlib import Path

import pytest


def _run(coro):
    """Run async coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_shell(tmp_path):
    """Build a minimal ForgeShell without a live CLI session."""
    from forge_cli import ForgeShell
    shell = ForgeShell.__new__(ForgeShell)
    shell.project_path = tmp_path
    shell.config = {"default_model": "nova-lite"}
    shell.state = {"recent_projects": []}
    shell.model = "bedrock/us.amazon.nova-2-lite-v1:0"
    shell.assistant = MagicMock()
    shell.assistant.skill_level = "intermediate"
    shell.assistant.read_autonomy_level.return_value = 2
    shell.assistant.set_autonomy_level.return_value = True
    shell.assistant.explain_autonomy.return_value = "Test explanation"
    shell._preview_mgr = None
    return shell


# ── /model ────────────────────────────────────────────────────────────────────


class TestModelInteractive:
    """Test /model interactive selection."""

    def test_model_with_arg_still_works(self, tmp_path):
        """Direct arg /model nova-lite still works (backward compat)."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli._check_provider", return_value=True), \
             patch("forge_cli._save_config"):
            _run(shell._cmd_model("nova-lite"))

        assert "nova-2-lite" in shell.model

    def test_model_unknown_arg_shows_error(self, tmp_path, capsys):
        """Unknown model arg shows error."""
        shell = _make_shell(tmp_path)
        _run(shell._cmd_model("nonexistent-model"))
        # Should not crash

    def test_model_no_arg_opens_selector(self, tmp_path):
        """No arg opens interactive selector (non-TTY returns default)."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="gemini-flash") as mock_select, \
             patch("forge_cli._check_all_providers", return_value={"bedrock": True, "openrouter": True, "anthropic": False}), \
             patch("forge_cli._check_provider", return_value=True), \
             patch("forge_cli._save_config"):
            _run(shell._cmd_model(""))

        mock_select.assert_called_once()
        assert "gemini" in shell.model

    def test_model_cancel_does_nothing(self, tmp_path):
        """Cancelling selector (None) keeps current model."""
        shell = _make_shell(tmp_path)
        original = shell.model

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value=None), \
             patch("forge_cli._check_all_providers", return_value={"bedrock": True}):
            _run(shell._cmd_model(""))

        assert shell.model == original

    def test_model_select_same_does_nothing(self, tmp_path):
        """Selecting the same model keeps it unchanged."""
        shell = _make_shell(tmp_path)
        original = shell.model

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="nova-lite"), \
             patch("forge_cli._check_all_providers", return_value={"bedrock": True}):
            _run(shell._cmd_model(""))

        assert shell.model == original


# ── /resume ───────────────────────────────────────────────────────────────────


class TestResumeInteractive:
    """Test /resume interactive selection."""

    def test_resume_no_projects(self, tmp_path):
        """No recent projects shows message."""
        shell = _make_shell(tmp_path)
        _run(shell._cmd_resume(""))
        # Should not crash

    def test_resume_with_arg_still_works(self, tmp_path):
        """Direct arg /resume 1 still works."""
        shell = _make_shell(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        (project_dir / ".forge").mkdir()
        shell.state["recent_projects"] = [{"name": "my-project", "path": str(project_dir)}]

        _run(shell._cmd_resume("1"))
        assert shell.project_path == project_dir

    def test_resume_by_name(self, tmp_path):
        """Direct arg /resume my-project matches by name."""
        shell = _make_shell(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        (project_dir / ".forge").mkdir()
        shell.state["recent_projects"] = [{"name": "my-project", "path": str(project_dir)}]

        _run(shell._cmd_resume("my-project"))
        assert shell.project_path == project_dir

    def test_resume_no_arg_opens_selector(self, tmp_path):
        """No arg opens interactive selector."""
        shell = _make_shell(tmp_path)
        project_dir = tmp_path / "test-app"
        project_dir.mkdir()
        (project_dir / ".forge").mkdir()
        shell.state["recent_projects"] = [
            {"name": "test-app", "path": str(project_dir)},
        ]

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="test-app"):
            _run(shell._cmd_resume(""))

        assert shell.project_path == project_dir

    def test_resume_cancel_does_nothing(self, tmp_path):
        """Cancelling selector keeps current project."""
        shell = _make_shell(tmp_path)
        original = shell.project_path
        shell.state["recent_projects"] = [
            {"name": "test-app", "path": str(tmp_path / "test-app")},
        ]
        (tmp_path / "test-app").mkdir()

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value=None):
            _run(shell._cmd_resume(""))

        assert shell.project_path == original


# ── /config ───────────────────────────────────────────────────────────────────


class TestConfigInteractive:
    """Test /config interactive selection."""

    def test_config_with_arg_still_works(self, tmp_path):
        """Direct arg /config max_turns 20 still works."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli._save_config"):
            _run(shell._cmd_config("max_turns 20"))

        assert shell.config["max_turns"] == 20

    def test_config_no_arg_opens_selector(self, tmp_path):
        """No arg opens interactive setting selector, then value input."""
        shell = _make_shell(tmp_path)

        # Simulate: select "max_turns", then enter "15"
        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="max_turns"), \
             patch("forge_cli.ask_text", new_callable=AsyncMock, return_value="15"), \
             patch("forge_cli._save_config"):
            _run(shell._cmd_config(""))

        assert shell.config["max_turns"] == 15

    def test_config_bool_toggle(self, tmp_path):
        """Bool settings use toggle selector (on/off)."""
        shell = _make_shell(tmp_path)
        shell.config["auto_build"] = False

        select_calls = []

        async def mock_select(msg, choices, **kw):
            select_calls.append(msg)
            if "Edit setting" in msg:
                return "auto_build"
            return "on"

        with patch("forge_cli.ask_select", side_effect=mock_select), \
             patch("forge_cli._save_config"):
            _run(shell._cmd_config(""))

        assert shell.config["auto_build"] is True

    def test_config_model_uses_model_chooser(self, tmp_path):
        """default_model setting opens model chooser."""
        shell = _make_shell(tmp_path)

        call_count = [0]

        async def mock_select(msg, choices, **kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return "default_model"
            return "gemini-flash"

        with patch("forge_cli.ask_select", side_effect=mock_select), \
             patch("forge_cli._check_all_providers", return_value={"bedrock": True, "openrouter": True}), \
             patch("forge_cli._check_provider", return_value=True), \
             patch("forge_cli._save_config"):
            _run(shell._cmd_config(""))

        assert "gemini" in shell.model

    def test_config_cancel_does_nothing(self, tmp_path):
        """Cancelling selector makes no changes."""
        shell = _make_shell(tmp_path)
        original_config = dict(shell.config)

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value=None):
            _run(shell._cmd_config(""))

        assert shell.config == original_config


# ── /formation ────────────────────────────────────────────────────────────────


class TestFormationInteractive:
    """Test /formation interactive selection."""

    def test_formation_with_arg_shows_details(self, tmp_path):
        """Direct arg /formation single-file shows details."""
        shell = _make_shell(tmp_path)
        # Should not crash
        _run(shell._cmd_formation("single-file"))

    def test_formation_unknown_arg(self, tmp_path):
        """Unknown formation shows error."""
        shell = _make_shell(tmp_path)
        _run(shell._cmd_formation("nonexistent"))

    def test_formation_no_arg_opens_selector(self, tmp_path):
        """No arg opens interactive selector."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="feature-impl"):
            _run(shell._cmd_formation(""))
        # Should show formation details without crashing

    def test_formation_cancel_does_nothing(self, tmp_path):
        """Cancelling selector does nothing."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value=None):
            _run(shell._cmd_formation(""))
        # Should not crash


# ── /autonomy ─────────────────────────────────────────────────────────────────


class TestAutonomyInteractive:
    """Test /autonomy interactive selection."""

    def test_autonomy_with_number_still_works(self, tmp_path):
        """Direct arg /autonomy 3 still works."""
        shell = _make_shell(tmp_path)
        _run(shell._cmd_autonomy("3"))
        shell.assistant.set_autonomy_level.assert_called_with(3)

    def test_autonomy_explain_still_works(self, tmp_path):
        """Arg '?' shows explanation."""
        shell = _make_shell(tmp_path)
        shell.assistant.explain_all_autonomy_levels.return_value = "All levels..."
        _run(shell._cmd_autonomy("?"))
        # Should not crash

    def test_autonomy_no_arg_shows_panel_then_selector(self, tmp_path):
        """No arg shows current state, then offers level selection."""
        shell = _make_shell(tmp_path)

        with patch("forge_display.display_autonomy_panel"), \
             patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="3"):
            _run(shell._cmd_autonomy(""))

        shell.assistant.set_autonomy_level.assert_called_with(3)

    def test_autonomy_cancel_keeps_level(self, tmp_path):
        """Cancelling selector keeps current level."""
        shell = _make_shell(tmp_path)

        with patch("forge_display.display_autonomy_panel"), \
             patch("forge_cli.ask_select", new_callable=AsyncMock, return_value=None):
            _run(shell._cmd_autonomy(""))

        shell.assistant.set_autonomy_level.assert_not_called()

    def test_autonomy_select_same_keeps_level(self, tmp_path):
        """Selecting same level (2) does nothing."""
        shell = _make_shell(tmp_path)

        with patch("forge_display.display_autonomy_panel"), \
             patch("forge_cli.ask_select", new_callable=AsyncMock, return_value="2"):
            _run(shell._cmd_autonomy(""))

        shell.assistant.set_autonomy_level.assert_not_called()

    def test_autonomy_invalid_number_does_not_crash(self, tmp_path):
        """Non-numeric string arg to /autonomy does not crash."""
        shell = _make_shell(tmp_path)
        shell.assistant.explain_all_autonomy_levels.return_value = "All levels..."
        # 'abc' is not a number and not '?' — should handle gracefully
        _run(shell._cmd_autonomy("abc"))
        # Should not crash; no set_autonomy_level call
        shell.assistant.set_autonomy_level.assert_not_called()


# ── /resume — edge cases ────────────────────────────────────────────────


class TestResumeEdgeCases:
    """Additional edge cases for /resume command."""

    def test_resume_out_of_range_index(self, tmp_path):
        """Out-of-range numeric index does not crash."""
        shell = _make_shell(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        (project_dir / ".forge").mkdir()
        shell.state["recent_projects"] = [{"name": "my-project", "path": str(project_dir)}]

        original = shell.project_path
        _run(shell._cmd_resume("99"))
        # Should not crash; project_path may or may not change depending on error handling
        # but at minimum it should not raise

    def test_resume_nonexistent_name(self, tmp_path):
        """Non-matching name arg does not crash."""
        shell = _make_shell(tmp_path)
        project_dir = tmp_path / "my-project"
        project_dir.mkdir()
        (project_dir / ".forge").mkdir()
        shell.state["recent_projects"] = [{"name": "my-project", "path": str(project_dir)}]

        _run(shell._cmd_resume("nonexistent-project"))
        # Should handle gracefully


# ── /model — edge cases ─────────────────────────────────────────────────


class TestModelEdgeCases:
    """Additional edge cases for /model command."""

    def test_model_valid_known_model(self, tmp_path):
        """Setting a valid known model like nova-pro works."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli._check_provider", return_value=True), \
             patch("forge_cli._save_config"):
            _run(shell._cmd_model("nova-pro"))

        assert "nova-2-pro" in shell.model or "nova-pro" in shell.model


# ── /config — edge cases ────────────────────────────────────────────────


class TestConfigEdgeCases:
    """Additional edge cases for /config command."""

    def test_config_show_current(self, tmp_path):
        """Config with no arg and cancel shows current config without changes."""
        shell = _make_shell(tmp_path)
        shell.config["max_turns"] = 30
        original_max = shell.config["max_turns"]

        with patch("forge_cli.ask_select", new_callable=AsyncMock, return_value=None):
            _run(shell._cmd_config(""))

        assert shell.config["max_turns"] == original_max

    def test_config_set_auto_build(self, tmp_path):
        """Setting auto_build via direct arg works."""
        shell = _make_shell(tmp_path)

        with patch("forge_cli._save_config"):
            _run(shell._cmd_config("auto_build true"))

        assert shell.config.get("auto_build") is True
