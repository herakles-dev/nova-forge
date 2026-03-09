"""Unit tests for forge_hooks.HookSystem."""
import asyncio
import os
import stat
import pytest
from pathlib import Path

from forge_hooks import HookSystem, HookEvent, HookResult


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_hooks_allows():
    """Empty HookSystem allows all tool calls."""
    hs = HookSystem(settings_file=None)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False


@pytest.mark.asyncio
async def test_python_hook_blocks():
    """A registered Python hook that returns blocked=True blocks the call."""
    hs = HookSystem(settings_file=None)

    def blocking_hook(tool_name, args, result):
        return HookResult(blocked=True, reason="test block")

    hs.register(HookEvent.PRE_TOOL_USE, blocking_hook)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is True
    assert "test block" in result.reason


@pytest.mark.asyncio
async def test_python_hook_allows():
    """A registered Python hook that returns blocked=False allows the call."""
    hs = HookSystem(settings_file=None)

    def allowing_hook(tool_name, args, result):
        return HookResult(blocked=False)

    hs.register(HookEvent.PRE_TOOL_USE, allowing_hook)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False


@pytest.mark.asyncio
async def test_shell_hook_exit_0(tmp_path):
    """Shell hook with exit 0 allows the tool call."""
    script = tmp_path / "allow_hook.sh"
    script.write_text("#!/bin/bash\nexit 0\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    settings = tmp_path / "settings.json"
    settings.write_text(
        f'{{"hooks": {{"PreToolUse": [{{"command": "{script}", "timeout": 2000}}]}}}}'
    )

    hs = HookSystem(settings_file=settings)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False


@pytest.mark.asyncio
async def test_shell_hook_exit_2(tmp_path):
    """Shell hook with exit 2 blocks the tool call."""
    script = tmp_path / "block_hook.sh"
    script.write_text("#!/bin/bash\necho 'blocked by policy' >&2\nexit 2\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    settings = tmp_path / "settings.json"
    settings.write_text(
        f'{{"hooks": {{"PreToolUse": [{{"command": "{script}", "timeout": 2000}}]}}}}'
    )

    hs = HookSystem(settings_file=settings)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is True
    assert "blocked by policy" in result.reason


@pytest.mark.asyncio
async def test_hook_timeout(tmp_path):
    """Shell hook that sleeps beyond the timeout is killed and treated as allow."""
    script = tmp_path / "slow_hook.sh"
    script.write_text("#!/bin/bash\nsleep 10\nexit 2\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    settings = tmp_path / "settings.json"
    settings.write_text(
        f'{{"hooks": {{"PreToolUse": [{{"command": "{script}", "timeout": 1000}}]}}}}'
    )

    hs = HookSystem(settings_file=settings)
    # Timeout is 1 second; sleep 10 should time out and be treated as allow
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False
