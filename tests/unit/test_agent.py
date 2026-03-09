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
