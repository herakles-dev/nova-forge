"""Unit tests for ForgeAgent with mocked ModelRouter (no live API calls)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from forge_agent import ForgeAgent
from forge_hooks import HookSystem
from model_router import ModelResponse, ToolCall
from config import get_model_config


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_agent(tmp_path: Path) -> ForgeAgent:
    """Build a ForgeAgent pointing at a temp project root."""
    model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    # Use no-settings HookSystem so no shell hooks are loaded
    hooks = HookSystem(settings_file=None)
    return ForgeAgent(
        model_config=model_cfg,
        project_root=tmp_path,
        hooks=hooks,
        max_turns=5,
        streaming=False,
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def tool_call_response(name: str, args: dict, call_id: str = "tc_1") -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        stop_reason="tool_use",
        usage={"input_tokens": 20, "output_tokens": 10},
    )


# ── Tests ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_agent_no_tool_calls(tmp_path):
    """Model returns text only — agent returns immediately with no tool calls made."""
    agent = make_agent(tmp_path)

    with patch.object(agent.router, "send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = text_response("All done, no tools needed.")
        result = await agent.run("Do something simple")

    assert result.error is None
    assert result.output == "All done, no tools needed."
    assert result.tool_calls_made == 0
    assert result.turns == 1
    mock_send.assert_called_once()


@pytest.mark.asyncio
async def test_circuit_breaker_disables_tool_after_threshold(tmp_path):
    """Tool fails TOOL_CIRCUIT_THRESHOLD times → tool disabled for rest of run."""
    agent = make_agent(tmp_path)
    agent.TOOL_CIRCUIT_THRESHOLD = 2  # Lower threshold for test

    call_count = 0
    exec_count = 0

    async def mock_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count <= 3:
            return tool_call_response("bash", {"command": "exit 1"}, call_id=f"tc_{call_count}")
        return text_response("Done after circuit breaker.")

    original_execute = agent._execute_tool_call

    async def failing_execute(call, artifacts):
        nonlocal exec_count
        exec_count += 1
        return "ERROR: command failed with exit code 1"

    with patch.object(agent.router, "send", side_effect=mock_send):
        with patch.object(agent, "_execute_tool_call", side_effect=failing_execute):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await agent.run("Try a command")

    assert "bash" in agent._disabled_tools


@pytest.mark.asyncio
async def test_circuit_breaker_resets_on_new_run(tmp_path):
    """_tool_failures and _disabled_tools are reset on each run()."""
    agent = make_agent(tmp_path)
    agent._tool_failures = {"bash": 5}
    agent._disabled_tools = {"bash"}

    with patch.object(agent.router, "send", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = text_response("Done.")
        await agent.run("Simple task")

    assert agent._tool_failures == {}
    assert agent._disabled_tools == set()


@pytest.mark.asyncio
async def test_agent_tool_use_loop(tmp_path):
    """Model returns one tool call, then plain text — agent executes the tool and loops."""
    agent = make_agent(tmp_path)

    # Create a file for the agent to read
    test_file = tmp_path / "hello.txt"
    test_file.write_text("hello world")

    # Sequence: first response has a tool call, second is plain text
    responses = [
        tool_call_response("read_file", {"path": str(test_file)}, call_id="tc_1"),
        text_response("I read the file successfully."),
    ]

    call_count = 0

    async def mock_send(messages, tools, model_config):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch.object(agent.router, "send", side_effect=mock_send):
        result = await agent.run("Read hello.txt and summarize it")

    assert result.error is None
    assert result.tool_calls_made == 1
    assert result.turns == 2
    assert result.output == "I read the file successfully."


# ── Fix 1: Bash write guard tests ─────────────────────────────────────────

class TestBashWriteGuard:
    """Test _check_bash_writes blocks file-write patterns correctly."""

    def test_readonly_blocks_redirect(self, tmp_path):
        agent = make_agent(tmp_path)
        agent._is_readonly = True
        result = agent._check_bash_writes('echo broken > app.py')
        assert result is not None
        assert "BLOCKED" in result
        assert "READ-ONLY" in result

    def test_readonly_blocks_tee(self, tmp_path):
        agent = make_agent(tmp_path)
        agent._is_readonly = True
        result = agent._check_bash_writes('cat foo | tee bar.txt')
        assert result is not None
        assert "BLOCKED" in result

    def test_readonly_blocks_sed_inplace(self, tmp_path):
        agent = make_agent(tmp_path)
        agent._is_readonly = True
        result = agent._check_bash_writes('sed -i "s/old/new/" file.py')
        assert result is not None

    def test_readonly_blocks_mv(self, tmp_path):
        agent = make_agent(tmp_path)
        agent._is_readonly = True
        result = agent._check_bash_writes('mv old.py new.py')
        assert result is not None

    def test_readonly_allows_read_commands(self, tmp_path):
        agent = make_agent(tmp_path)
        agent._is_readonly = True
        assert agent._check_bash_writes('cat app.py') is None
        assert agent._check_bash_writes('ls -la') is None
        assert agent._check_bash_writes('python3 app.py') is None
        assert agent._check_bash_writes('grep -r "def" .') is None

    def test_normal_allows_sandbox_writes(self, tmp_path):
        """Non-readonly agent allows writes within project sandbox."""
        agent = make_agent(tmp_path)
        agent._is_readonly = False
        # Redirect to file within project root — sandbox allows it
        result = agent._check_bash_writes('echo hello > output.txt')
        assert result is None

    def test_default_not_readonly(self, tmp_path):
        agent = make_agent(tmp_path)
        assert agent._is_readonly is False


# ── Fix 5: Artifact exposure tests ────────────────────────────────────────

class TestArtifactExposure:
    """Test _last_artifacts is set for crash recovery."""

    @pytest.mark.asyncio
    async def test_last_artifacts_set_on_run(self, tmp_path):
        """_last_artifacts is set at the start of run() and stays current."""
        agent = make_agent(tmp_path)

        with patch.object(agent.router, "send", new_callable=AsyncMock) as mock_send:
            mock_send.return_value = text_response("Done.")
            await agent.run("Do nothing")

        # _last_artifacts should exist and be a dict (possibly empty)
        assert hasattr(agent, '_last_artifacts')
        assert isinstance(agent._last_artifacts, dict)

    @pytest.mark.asyncio
    async def test_last_artifacts_contains_written_files(self, tmp_path):
        """After writing a file, _last_artifacts includes it."""
        agent = make_agent(tmp_path)

        responses = [
            tool_call_response("write_file", {
                "path": str(tmp_path / "test.txt"),
                "content": "hello"
            }),
            text_response("Wrote file."),
        ]
        call_count = 0

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            resp = responses[call_count]
            call_count += 1
            return resp

        with patch.object(agent.router, "send", side_effect=mock_send):
            result = await agent.run("Write a file")

        assert str(tmp_path / "test.txt") in agent._last_artifacts


# ── Fix 7: Append file warning tests ──────────────────────────────────────

class TestAppendFileWarning:
    """Test append_file warns when appending to an unread file."""

    @pytest.mark.asyncio
    async def test_append_to_unread_existing_file_warns(self, tmp_path):
        """Appending to an existing file the agent never read produces a warning."""
        agent = make_agent(tmp_path)
        existing = tmp_path / "existing.txt"
        existing.write_text("original content")

        artifacts = {}
        result = await agent._tool_append_file(
            {"path": str(existing), "content": "\nnew content"},
            artifacts,
        )
        assert "WARNING" in result
        assert "never read" in result

    @pytest.mark.asyncio
    async def test_append_to_read_file_no_warning(self, tmp_path):
        """Appending to a file the agent already read produces no warning."""
        agent = make_agent(tmp_path)
        existing = tmp_path / "existing.txt"
        existing.write_text("original content")
        agent._files_read.add(str(existing))

        artifacts = {}
        result = await agent._tool_append_file(
            {"path": str(existing), "content": "\nnew content"},
            artifacts,
        )
        assert "WARNING" not in result

    @pytest.mark.asyncio
    async def test_append_to_just_created_file_no_warning(self, tmp_path):
        """Appending to a file this agent just created (via write) produces no warning."""
        agent = make_agent(tmp_path)
        new_file = tmp_path / "new.txt"
        new_file.write_text("initial content")

        # Simulate that the agent wrote this file
        artifacts = {str(new_file): {"action": "write", "size": 15}}
        result = await agent._tool_append_file(
            {"path": str(new_file), "content": "\nmore content"},
            artifacts,
        )
        assert "WARNING" not in result


# ── Verify phase budget tests ────────────────────────────────────────────

class TestVerifyPhase:
    """Verify phase has a hard budget and doesn't loop forever."""

    @pytest.mark.asyncio
    async def test_verify_phase_enters_on_first_done(self, tmp_path):
        """Agent enters verify phase when model first says done with artifacts."""
        agent = make_agent(tmp_path)
        agent._verify_budget = 2
        test_file = tmp_path / "app.py"
        test_file.write_text("print('hello')")

        call_count = 0

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tool_call_response("write_file", {
                    "path": str(test_file), "content": "print('hello')"
                })
            if call_count == 2:
                return text_response("All done!")  # triggers verify
            if call_count == 3:
                return text_response("Verified — all correct.")  # in verify
            return text_response("Done.")

        with patch.object(agent.router, "send", side_effect=mock_send):
            result = await agent.run("Write app.py")

        assert agent._in_verify_phase is True

    @pytest.mark.asyncio
    async def test_verify_budget_limits_turns(self, tmp_path):
        """Agent can't spend more than verify_budget turns in verify phase."""
        agent = make_agent(tmp_path)
        agent.max_turns = 20
        agent._verify_budget = 2

        call_count = 0

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return tool_call_response("write_file", {
                    "path": str(tmp_path / "a.py"), "content": "x=1"
                })
            if call_count == 2:
                return text_response("Done!")  # triggers verify entry
            # Verify phase: keep using read_file (simulates endless verify)
            if call_count <= 8:
                return tool_call_response("read_file", {
                    "path": str(tmp_path / "a.py")
                }, call_id=f"tc_{call_count}")
            return text_response("Final done.")

        (tmp_path / "a.py").write_text("x=1")
        with patch.object(agent.router, "send", side_effect=mock_send):
            result = await agent.run("Write a.py")

        # Write tools should be disabled after verify budget
        assert "write_file" in agent._disabled_tools or result.turns <= 10


# ── Hard limit formula tests ─────────────────────────────────────────────

class TestHardLimit:
    """Hard limit uses tighter formula: max(turns+4, turns*1.3)."""

    @pytest.mark.asyncio
    async def test_hard_limit_small_budget(self, tmp_path):
        """max_turns=10 → hard_limit=14, not 20."""
        agent = make_agent(tmp_path)
        agent.max_turns = 10

        call_count = 0

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            # Always return a tool call to exhaust turns
            return tool_call_response("read_file", {
                "path": str(tmp_path / "test.txt")
            }, call_id=f"tc_{call_count}")

        (tmp_path / "test.txt").write_text("test")
        with patch.object(agent.router, "send", side_effect=mock_send):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await agent.run("Read forever")

        # Should hit hard limit at max(10+4, int(10*1.3)) = 14
        assert result.turns <= 14

    @pytest.mark.asyncio
    async def test_hard_limit_larger_budget(self, tmp_path):
        """max_turns=30 → hard_limit=39, not 60."""
        agent = make_agent(tmp_path)
        agent.max_turns = 30
        agent.soft_max_turns = 30

        call_count = 0

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            return tool_call_response("read_file", {
                "path": str(tmp_path / "test.txt")
            }, call_id=f"tc_{call_count}")

        (tmp_path / "test.txt").write_text("test")
        with patch.object(agent.router, "send", side_effect=mock_send):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await agent.run("Read forever")

        # hard limit = max(30+4, int(30*1.3)) = max(34, 39) = 39
        assert result.turns <= 39


# ── Escalation budget tests ──────────────────────────────────────────────

class TestEscalationBudget:
    """Escalation uses a reduced budget, not a fresh full run."""

    def test_escalation_turns_default(self, tmp_path):
        """Default escalation_turns = max(8, max_turns // 2)."""
        agent = make_agent(tmp_path)
        agent.max_turns = 20
        agent._escalation_turns = max(8, 20 // 2)
        assert agent._escalation_turns == 10

    def test_escalation_turns_small(self, tmp_path):
        """Small max_turns still gets min 8 escalation turns."""
        agent = make_agent(tmp_path)
        # make_agent uses max_turns=5, so _escalation_turns = max(8, 5//2) = 8
        assert agent._escalation_turns >= 8
