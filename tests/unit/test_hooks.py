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


# ── Additional hook tests ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_post_tool_use_runs_hooks():
    """PostToolUse hook receives the tool result."""
    hs = HookSystem(settings_file=None)
    received = {}

    def capture_hook(tool_name, args, result):
        received["tool_name"] = tool_name
        received["result"] = result
        return HookResult(blocked=False)

    hs.register(HookEvent.POST_TOOL_USE, capture_hook)
    await hs.post_tool_use("read_file", {"path": "x.py"}, result="file content here")

    assert received["tool_name"] == "read_file"
    assert received["result"] == "file content here"


@pytest.mark.asyncio
async def test_post_tool_use_blocking():
    """PostToolUse hook can block (informational — returns blocked result)."""
    hs = HookSystem(settings_file=None)

    def blocking_post(tool_name, args, result):
        return HookResult(blocked=True, reason="audit violation")

    hs.register(HookEvent.POST_TOOL_USE, blocking_post)
    result = await hs.post_tool_use("bash", {"command": "rm x"}, result="ok")
    assert result.blocked is True
    assert "audit violation" in result.reason


@pytest.mark.asyncio
async def test_multiple_hooks_first_block_wins():
    """With multiple PreToolUse hooks, the first blocking one wins."""
    hs = HookSystem(settings_file=None)

    def hook_a(tool_name, args, result):
        return HookResult(blocked=True, reason="hook A blocked")

    def hook_b(tool_name, args, result):
        return HookResult(blocked=True, reason="hook B blocked")

    hs.register(HookEvent.PRE_TOOL_USE, hook_a)
    hs.register(HookEvent.PRE_TOOL_USE, hook_b)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is True
    assert "hook A" in result.reason


@pytest.mark.asyncio
async def test_hook_modified_args_not_propagated_from_nonblocking():
    """Non-blocking hook's modified_args are not propagated (by design).

    The HookSystem returns a fresh HookResult() when no hooks block.
    The ForgeAgent itself consults hook_result.modified_args only when
    the blocking hook returns it.
    """
    hs = HookSystem(settings_file=None)

    def modifying_hook(tool_name, args, result):
        return HookResult(blocked=False, modified_args={"command": "ls -la"})

    hs.register(HookEvent.PRE_TOOL_USE, modifying_hook)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False
    # Non-blocking results don't propagate modified_args in the current design
    assert result.modified_args is None


@pytest.mark.asyncio
async def test_on_stop_does_not_block():
    """Stop hooks run but do not block (no blocking semantics)."""
    hs = HookSystem(settings_file=None)
    stop_called = []

    def stop_hook(tool_name, args, result):
        stop_called.append(True)
        return HookResult(blocked=True, reason="ignored")

    hs.register(HookEvent.STOP, stop_hook)
    await hs.on_stop(project="test")
    assert len(stop_called) == 1


@pytest.mark.asyncio
async def test_invalid_settings_file_loads_no_hooks(tmp_path):
    """Invalid JSON in settings file loads no hooks (graceful degradation)."""
    settings = tmp_path / "settings.json"
    settings.write_text("this is not valid json {{{")

    hs = HookSystem(settings_file=settings)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False  # No hooks loaded => allow


@pytest.mark.asyncio
async def test_missing_settings_file_loads_no_hooks(tmp_path):
    """Non-existent settings file loads no hooks."""
    settings = tmp_path / "nonexistent.json"
    hs = HookSystem(settings_file=settings)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False


@pytest.mark.asyncio
async def test_shell_hook_exit_1_treated_as_allow(tmp_path):
    """Shell hook with exit 1 (not 0 or 2) is treated as allow with warning."""
    script = tmp_path / "warn_hook.sh"
    script.write_text("#!/bin/bash\nexit 1\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    settings = tmp_path / "settings.json"
    settings.write_text(
        f'{{"hooks": {{"PreToolUse": [{{"command": "{script}", "timeout": 2000}}]}}}}'
    )

    hs = HookSystem(settings_file=settings)
    result = await hs.pre_tool_use("Bash", {"command": "ls"})
    assert result.blocked is False  # Exit 1 = allow (with warning)


@pytest.mark.asyncio
async def test_hook_system_session_id_unique():
    """Each HookSystem instance gets a unique session ID."""
    hs1 = HookSystem(settings_file=None)
    hs2 = HookSystem(settings_file=None)
    assert hs1._session_id != hs2._session_id
    assert hs1._session_id.startswith("forge-")
    assert hs2._session_id.startswith("forge-")
