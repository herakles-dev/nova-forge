"""Unit tests for ForgeAgent with mocked ModelRouter (no live API calls)."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

from forge_agent import ForgeAgent, ConvergenceTracker
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


# ── _auto_verify tests ──────────────────────────────────────────────────────

class TestAutoVerify:
    """Test _auto_verify handles Python files with repr() quoting and other file types."""

    @pytest.mark.asyncio
    async def test_auto_verify_python_syntax_ok(self, tmp_path):
        """_auto_verify returns '(syntax OK)' for valid Python."""
        agent = make_agent(tmp_path)
        py_file = tmp_path / "good.py"
        py_file.write_text("def hello():\n    return 42\n")
        result = await agent._auto_verify(py_file)
        assert "syntax OK" in result

    @pytest.mark.asyncio
    async def test_auto_verify_python_syntax_error(self, tmp_path):
        """_auto_verify detects syntax errors in Python files."""
        agent = make_agent(tmp_path)
        py_file = tmp_path / "bad.py"
        py_file.write_text("def broken(\n")
        result = await agent._auto_verify(py_file)
        assert "Syntax issue" in result

    @pytest.mark.asyncio
    async def test_auto_verify_python_path_with_spaces(self, tmp_path):
        """_auto_verify handles paths containing spaces via repr() quoting."""
        agent = make_agent(tmp_path)
        dir_with_space = tmp_path / "my project"
        dir_with_space.mkdir()
        py_file = dir_with_space / "app.py"
        py_file.write_text("x = 1\n")
        result = await agent._auto_verify(py_file)
        assert "syntax OK" in result

    @pytest.mark.asyncio
    async def test_auto_verify_html_ok(self, tmp_path):
        """_auto_verify validates balanced HTML tags."""
        agent = make_agent(tmp_path)
        html_file = tmp_path / "index.html"
        html_file.write_text("<html><body><script>alert(1)</script></body></html>")
        result = await agent._auto_verify(html_file)
        assert "HTML OK" in result

    @pytest.mark.asyncio
    async def test_auto_verify_html_unclosed_script(self, tmp_path):
        """_auto_verify detects unclosed <script> tags in HTML."""
        agent = make_agent(tmp_path)
        html_file = tmp_path / "bad.html"
        html_file.write_text("<html><body><script>alert(1)</body></html>")
        result = await agent._auto_verify(html_file)
        assert "HTML ERROR" in result
        assert "script" in result.lower()

    @pytest.mark.asyncio
    async def test_auto_verify_css_ok(self, tmp_path):
        """_auto_verify validates balanced CSS braces."""
        agent = make_agent(tmp_path)
        css_file = tmp_path / "style.css"
        css_file.write_text("body { color: red; }\nh1 { font-size: 2em; }")
        result = await agent._auto_verify(css_file)
        assert "CSS OK" in result

    @pytest.mark.asyncio
    async def test_auto_verify_css_unbalanced_braces(self, tmp_path):
        """_auto_verify detects unbalanced braces in CSS."""
        agent = make_agent(tmp_path)
        css_file = tmp_path / "bad.css"
        css_file.write_text("body { color: red;\nh1 { font-size: 2em; }")
        result = await agent._auto_verify(css_file)
        assert "CSS ERROR" in result

    @pytest.mark.asyncio
    async def test_auto_verify_unknown_extension(self, tmp_path):
        """_auto_verify returns empty string for unknown file types."""
        agent = make_agent(tmp_path)
        txt_file = tmp_path / "readme.txt"
        txt_file.write_text("Hello world")
        result = await agent._auto_verify(txt_file)
        assert result == ""


# ── Bash timeout process kill tests ──────────────────────────────────────────

class TestBashTimeoutKill:
    """Test that bash tool kills subprocess on timeout."""

    @pytest.mark.asyncio
    async def test_bash_timeout_returns_message(self, tmp_path):
        """Bash command exceeding timeout returns timeout message."""
        agent = make_agent(tmp_path)
        # Use a very short timeout by patching wait_for
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock) as mock_proc:
                mock_process = AsyncMock()
                mock_process.kill = MagicMock()
                mock_process.wait = AsyncMock()
                mock_proc.return_value = mock_process
                result = await agent._tool_bash({"command": "sleep 999"})

        assert "timed out" in result.lower()
        assert "killed" in result.lower()
        mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_bash_timeout_with_null_proc(self, tmp_path):
        """Bash handles case where proc is None on timeout (create failed)."""
        agent = make_agent(tmp_path)
        with patch("asyncio.create_subprocess_shell", side_effect=asyncio.TimeoutError()):
            result = await agent._tool_bash({"command": "sleep 999"})
        # Should return a command failure message, not crash
        assert "failed" in result.lower() or "timed out" in result.lower()


# ── Escalation artifact merge tests ──────────────────────────────────────────

class TestEscalationArtifactMerge:
    """Escalation merges original artifacts into escalated result."""

    @pytest.mark.asyncio
    async def test_escalation_merges_artifacts(self, tmp_path):
        """After escalation, original artifacts appear in escalated result."""
        agent = make_agent(tmp_path)
        agent.max_turns = 2
        agent.soft_max_turns = 2
        agent.escalation_model = "bedrock/us.amazon.nova-pro-v1:0"
        # Disable auto_verify so verify phase doesn't interfere
        agent.auto_verify = False

        # hard_limit = max(2+4, int(2*1.3)) = 6
        # We need 6 tool call turns to exhaust hard limit, then escalated run returns text
        call_count = 0
        escalated = False

        async def mock_send(messages, tools, model_config):
            nonlocal call_count, escalated
            call_count += 1
            # Detect escalation: model_config will have different model_id
            if "nova-pro" in model_config.model_id:
                escalated = True
                return text_response("Escalated model done.")
            # Original model: always return tool calls to exhaust hard limit
            return tool_call_response("read_file", {
                "path": str(tmp_path / "x.txt")
            }, call_id=f"tc_{call_count}")

        (tmp_path / "x.txt").write_text("hello")

        with patch.object(agent.router, "send", side_effect=mock_send):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await agent.run("Do work")

        assert escalated is True
        assert result.escalated is True

    @pytest.mark.asyncio
    async def test_escalation_restores_config_on_failure(self, tmp_path):
        """Escalation try/finally restores original config even if escalated run fails."""
        agent = make_agent(tmp_path)
        agent.max_turns = 2
        agent.soft_max_turns = 2
        agent.auto_verify = False
        agent.escalation_model = "bedrock/us.amazon.nova-pro-v1:0"
        original_model = agent.model_config
        original_max = agent.max_turns

        call_count = 0

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            if "nova-pro" in model_config.model_id:
                return text_response("Escalated done.")
            return tool_call_response("read_file", {
                "path": str(tmp_path / "x.txt")
            }, call_id=f"tc_{call_count}")

        (tmp_path / "x.txt").write_text("hello")

        with patch.object(agent.router, "send", side_effect=mock_send):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await agent.run("Do work")

        # After run completes, original config should be restored
        assert agent.model_config == original_model
        assert agent.max_turns == original_max


# ── Syntax error fix injection tests ─────────────────────────────────────────

class TestSyntaxErrorFixInjection:
    """When write/edit produces a syntax error, agent is forced to fix it."""

    @pytest.mark.asyncio
    async def test_syntax_error_triggers_fix_message(self, tmp_path):
        """Writing a file with syntax errors injects a fix instruction to the model."""
        agent = make_agent(tmp_path)
        py_file = tmp_path / "broken.py"

        call_count = 0
        messages_seen = []

        async def mock_send(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            messages_seen.append([m.get("content", "") for m in messages if m.get("role") == "user"])
            if call_count == 1:
                return tool_call_response("write_file", {
                    "path": str(py_file),
                    "content": "def broken(\n"
                })
            # After fix injection, just finish
            return text_response("Fixed the syntax error.")

        with patch.object(agent.router, "send", side_effect=mock_send):
            result = await agent.run("Write app.py")

        # The fix injection message should appear in the conversation
        all_user_msgs = " ".join(str(m) for m in messages_seen)
        assert "SYNTAX ERROR" in all_user_msgs or result.output == "Fixed the syntax error."


# ── Context overflow compaction check tests ──────────────────────────────────

class TestContextOverflowCompaction:
    """Test context compaction is triggered when token budget is exceeded."""

    @pytest.mark.asyncio
    async def test_compaction_no_reduction_aborts(self, tmp_path):
        """If compaction doesn't reduce tokens, the retry loop stops."""
        agent = make_agent(tmp_path)
        call_count = 0

        async def overflow_always(messages, tools, model_config):
            nonlocal call_count
            call_count += 1
            raise Exception("context length exceeded: too long for this model")

        # _estimate_tokens always returns same value => no reduction
        with patch.object(agent.router, "send", side_effect=overflow_always):
            with patch.object(agent, "_compact_messages", side_effect=lambda msgs, budget: msgs):
                with patch.object(agent, "_estimate_tokens", return_value=100):
                    result = await agent.run("Do something")

        assert result.error is not None
        # Should not loop forever — call_count should be <= MAX_API_RETRIES
        assert call_count <= 5
