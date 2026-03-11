"""Tests for the append_file tool in ForgeAgent."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from forge_agent import BUILT_IN_TOOLS, ForgeAgent
from forge_guards import PathSandbox, SandboxViolation
from forge_hooks import HookResult

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_agent(project_root: Path, build_context=None) -> ForgeAgent:
    """Create a minimal ForgeAgent for tool testing."""
    sandbox = PathSandbox(project_root=project_root)
    agent = ForgeAgent.__new__(ForgeAgent)
    agent.project_root = project_root
    agent.sandbox = sandbox
    agent.build_context = build_context
    agent.agent_id = "test-agent-1"
    agent.on_event = None
    agent._files_read = set()
    agent._claimed_files = set()
    # Mock hooks with async methods
    agent.hooks = MagicMock()
    agent.hooks.pre_tool_use = AsyncMock(return_value=HookResult(blocked=False))
    agent.hooks.post_tool_use = AsyncMock(return_value=None)
    return agent


def _run(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Tests ────────────────────────────────────────────────────────────────────

class TestAppendFileToolSchema:
    """Test that append_file is properly defined in BUILT_IN_TOOLS."""

    def test_append_file_in_built_in_tools(self):
        names = [t["name"] for t in BUILT_IN_TOOLS]
        assert "append_file" in names

    def test_append_file_schema_has_required_fields(self):
        tool = next(t for t in BUILT_IN_TOOLS if t["name"] == "append_file")
        params = tool["parameters"]
        assert params["required"] == ["path", "content"]
        assert "path" in params["properties"]
        assert "content" in params["properties"]


class TestAppendFileCreatesNew:
    """Test append to non-existent file creates it."""

    def test_append_creates_new_file(self, tmp_path):
        agent = _make_agent(tmp_path)
        target = tmp_path / "new_file.txt"
        assert not target.exists()

        artifacts = {}
        result = _run(
            agent._tool_append_file({"path": "new_file.txt", "content": "hello world"}, artifacts)
        )

        assert target.exists()
        assert target.read_text() == "hello world"
        assert "Appended" in result
        assert "+11 chars" in result


class TestAppendToExisting:
    """Test append to existing file concatenates content."""

    def test_append_to_existing(self, tmp_path):
        agent = _make_agent(tmp_path)
        target = tmp_path / "app.js"
        target.write_text("// header\n")

        artifacts = {}
        result = _run(
            agent._tool_append_file({"path": "app.js", "content": "function main() {}\n"}, artifacts)
        )

        content = target.read_text()
        assert content == "// header\nfunction main() {}\n"
        assert "Appended" in result


class TestAppendMultipleTimes:
    """Test write + 3x append produces correct concatenation."""

    def test_append_multiple_times(self, tmp_path):
        agent = _make_agent(tmp_path)
        target = tmp_path / "big.py"

        artifacts = {}
        # Initial write
        _run(agent._tool_write_file({"path": "big.py", "content": "# part 1\n"}, artifacts))
        # Three appends
        for i in range(2, 5):
            _run(agent._tool_append_file({"path": "big.py", "content": f"# part {i}\n"}, artifacts))

        content = target.read_text()
        assert "# part 1" in content
        assert "# part 2" in content
        assert "# part 3" in content
        assert "# part 4" in content


class TestAppendSandbox:
    """Test sandbox rejects writes outside allowed roots."""

    def test_append_sandbox_rejects_outside(self, tmp_path):
        agent = _make_agent(tmp_path)
        artifacts = {}

        with pytest.raises(SandboxViolation):
            _run(agent._tool_append_file({"path": "/etc/passwd", "content": "bad"}, artifacts))


class TestAppendBuildContext:
    """Test BuildContext claim/conflict behavior."""

    def test_append_build_context_claim(self, tmp_path):
        from forge_comms import BuildContext
        ctx = BuildContext(tmp_path)
        agent = _make_agent(tmp_path, build_context=ctx)

        artifacts = {}
        result = _run(
            agent._tool_append_file({"path": "owned.py", "content": "x = 1\n"}, artifacts)
        )

        assert "Appended" in result
        claim = ctx.is_claimed("owned.py")
        assert claim is not None
        assert claim.agent_id == "test-agent-1"

    def test_append_build_context_conflict(self, tmp_path):
        from forge_comms import BuildContext
        ctx = BuildContext(tmp_path)
        ctx.claim_file("taken.py", "other-agent")

        agent = _make_agent(tmp_path, build_context=ctx)
        artifacts = {}
        result = _run(
            agent._tool_append_file({"path": "taken.py", "content": "bad"}, artifacts)
        )

        assert "CONFLICT" in result
        assert "other-agent" in result


class TestAppendAutoVerify:
    """Test syntax check runs after append on .py files."""

    def test_append_auto_verify_python(self, tmp_path):
        agent = _make_agent(tmp_path)
        target = tmp_path / "valid.py"

        artifacts = {}
        result = _run(
            agent._tool_append_file({"path": "valid.py", "content": "x = 1\n"}, artifacts)
        )

        assert "Appended" in result
        assert target.exists()


class TestAppendArtifacts:
    """Test artifact tracking has action=append with sizes."""

    def test_append_artifacts_tracking(self, tmp_path):
        agent = _make_agent(tmp_path)
        target = tmp_path / "tracked.txt"
        target.write_text("existing\n")

        artifacts = {}
        _run(agent._tool_append_file({"path": "tracked.txt", "content": "new stuff\n"}, artifacts))

        key = str(tmp_path / "tracked.txt")
        assert key in artifacts
        assert artifacts[key]["action"] == "append"
        assert artifacts[key]["appended"] == len("new stuff\n")
        assert artifacts[key]["size"] > 0


class TestAppendUnescape:
    """Test Nova escaping is handled."""

    def test_append_unescape_content(self, tmp_path):
        agent = _make_agent(tmp_path)
        artifacts = {}
        _run(
            agent._tool_append_file({"path": "esc.py", "content": 'print(\\"hello\\")\n'}, artifacts)
        )
        content = (tmp_path / "esc.py").read_text()
        assert 'print("hello")' in content


class TestAppendInToolProfiles:
    """Test append_file is in full and coding tool profiles."""

    def test_append_in_tool_profiles(self):
        from formations import TOOL_PROFILES
        assert "append_file" in TOOL_PROFILES["full"]
        assert "append_file" in TOOL_PROFILES["coding"]
        assert "append_file" not in TOOL_PROFILES["testing"]
        assert "append_file" not in TOOL_PROFILES["readonly"]
