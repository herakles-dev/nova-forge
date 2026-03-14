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
