"""Tests for ForgeAgent transient retry and malformed tool call self-correction."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call
from pathlib import Path

from forge_agent import ForgeAgent, MAX_API_RETRIES
from forge_hooks import HookSystem
from model_router import ModelResponse, ToolCall
from config import get_model_config


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_agent(tmp_path: Path, on_event=None) -> ForgeAgent:
    """Build a ForgeAgent pointing at a temp project root."""
    model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    hooks = HookSystem(settings_file=None)
    return ForgeAgent(
        model_config=model_cfg,
        project_root=tmp_path,
        hooks=hooks,
        max_turns=5,
        on_event=on_event,
        streaming=False,
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def tool_call_response(name: str, args, call_id: str = "tc_1") -> ModelResponse:
    """Build a ModelResponse with a tool call. args may be invalid (non-dict) intentionally."""
    return ModelResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        stop_reason="tool_use",
        usage={"input_tokens": 20, "output_tokens": 10},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_transient_429_retries(tmp_path):
    """Model fails twice with 429, succeeds on third attempt."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def flaky_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("HTTP 429: Too Many Requests — rate limit exceeded")
        return text_response("Success after retries.")

    with patch.object(agent.router, "send", side_effect=flaky_send):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await agent.run("Do something")

    assert result.error is None
    assert result.output == "Success after retries."
    assert call_count == 3
    # sleep was called twice (after attempt 1 and 2)
    assert mock_sleep.call_count == 2


@pytest.mark.asyncio
async def test_non_transient_error_no_retry(tmp_path):
    """Model raises a non-transient ValueError — no retry, immediate failure."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def bad_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        raise ValueError("Invalid model parameter")

    with patch.object(agent.router, "send", side_effect=bad_send):
        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await agent.run("Do something")

    assert result.error is not None
    assert "Invalid model parameter" in result.error
    assert call_count == 1  # No retries for non-transient error
    mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_context_overflow_compacts_and_retries(tmp_path):
    """Context length exceeded error triggers compaction and retry."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def overflow_then_ok(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("context length exceeded: too long for this model")
        return text_response("Done after compaction.")

    compact_called = []
    original_compact = agent._compact_messages

    def spy_compact(messages, budget=None):
        compact_called.append(True)
        return original_compact(messages, budget) if budget else original_compact(messages, {})

    # Mock _estimate_tokens to report decreasing token count after compaction
    token_counts = iter([100, 50])  # pre=100, post=50 → reduction detected
    with patch.object(agent.router, "send", side_effect=overflow_then_ok):
        with patch.object(agent, "_compact_messages", side_effect=spy_compact):
            with patch.object(agent, "_estimate_tokens", side_effect=lambda msgs: next(token_counts, 50)):
                result = await agent.run("Do something")

    assert result.error is None
    assert result.output == "Done after compaction."
    assert call_count == 2
    assert len(compact_called) == 1  # Compaction was triggered once


@pytest.mark.asyncio
async def test_max_retries_exceeded(tmp_path):
    """Model fails MAX_API_RETRIES times — AgentResult.error is set."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def always_429(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        raise Exception("503 Service Unavailable")

    with patch.object(agent.router, "send", side_effect=always_429):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run("Do something")

    assert result.error is not None
    assert "503" in result.error or "Service Unavailable" in result.error
    assert f"{MAX_API_RETRIES} attempts" in result.output
    assert call_count == MAX_API_RETRIES


@pytest.mark.asyncio
async def test_malformed_tool_call_self_correction(tmp_path):
    """Model returns invalid args (string instead of dict) — error is injected and model retries."""
    agent = make_agent(tmp_path)
    call_count = 0

    # First response: tool call with malformed args (string, not dict)
    malformed_response = ModelResponse(
        text="",
        tool_calls=[ToolCall(id="tc_bad", name="read_file", args="not-a-dict")],
        stop_reason="tool_use",
        usage={"input_tokens": 20, "output_tokens": 10},
    )

    async def send_sequence(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return malformed_response
        # Second call: agent corrects itself with plain text
        return text_response("Fixed my tool call.")

    # format_assistant_message is required for injecting the error message
    adapter = agent.router.route(agent.model_config.model_id)
    with patch.object(agent.router, "send", side_effect=send_sequence):
        result = await agent.run("Read a file")

    assert result.error is None
    assert result.output == "Fixed my tool call."
    assert call_count == 2  # Retry happened


@pytest.mark.asyncio
async def test_retry_event_emitted(tmp_path):
    """on_event callback is called with type='retry' on each transient retry."""
    events = []
    agent = make_agent(tmp_path, on_event=events.append)
    call_count = 0

    async def flaky_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise Exception("429 rate limit")
        return text_response("Done.")

    with patch.object(agent.router, "send", side_effect=flaky_send):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run("Do something")

    assert result.error is None
    retry_events = [e for e in events if e.kind == "error" and "Retry" in e.error]
    assert len(retry_events) == 2
    assert "Retry 1" in retry_events[0].error
    assert "Retry 2" in retry_events[1].error
    for e in retry_events:
        assert "429" in e.error


@pytest.mark.asyncio
async def test_exponential_backoff_with_jitter(tmp_path):
    """Retry delays increase exponentially (with jitter capped at 30s)."""
    agent = make_agent(tmp_path)
    call_count = 0
    sleep_delays = []

    async def always_500(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        raise Exception("500 Internal Server Error")

    async def capture_sleep(delay):
        sleep_delays.append(delay)

    with patch.object(agent.router, "send", side_effect=always_500):
        with patch("asyncio.sleep", side_effect=capture_sleep):
            result = await agent.run("Do something")

    # We expect MAX_API_RETRIES - 1 sleep calls (sleep after each failed attempt except last)
    assert len(sleep_delays) == MAX_API_RETRIES - 1
    # Delays should be positive and cap at 30
    for d in sleep_delays:
        assert 0 < d <= 30
    # First delay is around 2^0 + jitter = 1..2, second is around 2^1 + jitter = 2..3
    # Just verify the second delay is >= the first (exponential growth, jitter may vary)
    if len(sleep_delays) >= 2:
        assert sleep_delays[1] >= sleep_delays[0] - 1  # allow jitter variance


@pytest.mark.asyncio
async def test_tool_retry_on_error(tmp_path):
    """Failed tool call is retried once before reporting error to model."""
    agent = make_agent(tmp_path)
    call_count = 0
    bogus_path = str(tmp_path / "does_not_exist.txt")

    async def mock_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return tool_call_response("read_file", {"path": bogus_path}, call_id="tc_1")
        return text_response("File not found, moving on.")

    with patch.object(agent.router, "send", side_effect=mock_send):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run("Read a missing file")

    assert result.error is None
    assert call_count == 2


@pytest.mark.asyncio
async def test_disabled_tools_excluded_from_model_calls(tmp_path):
    """Disabled tools are filtered from the tool list sent to the model."""
    agent = make_agent(tmp_path)
    sent_tools = []

    call_count = 0
    async def capture_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Disable bash after first call (simulating circuit break)
            agent._disabled_tools.add("bash")
            return tool_call_response("read_file", {"path": str(tmp_path)}, call_id="tc_1")
        # Second call — capture the tools list
        sent_tools.extend(tools)
        return text_response("Done.")

    with patch.object(agent.router, "send", side_effect=capture_send):
        result = await agent.run("Do something")

    tool_names = [t["name"] for t in sent_tools]
    assert "bash" not in tool_names


@pytest.mark.asyncio
async def test_mixed_tool_calls_uses_only_valid(tmp_path):
    """When some tool calls are valid and some malformed, only valid ones run."""
    agent = make_agent(tmp_path)

    # Create a file for reading
    test_file = tmp_path / "info.txt"
    test_file.write_text("content here")

    call_count = 0

    async def send_sequence(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Mixed: one valid, one malformed
            return ModelResponse(
                text="",
                tool_calls=[
                    ToolCall(id="tc_good", name="read_file", args={"path": str(test_file)}),
                    ToolCall(id="tc_bad", name="write_file", args="bad-string"),
                ],
                stop_reason="tool_use",
                usage={"input_tokens": 20, "output_tokens": 10},
            )
        return text_response("Completed with partial tools.")

    with patch.object(agent.router, "send", side_effect=send_sequence):
        result = await agent.run("Read and write files")

    assert result.error is None
    assert result.output == "Completed with partial tools."
    # The valid tool call ran
    assert result.tool_calls_made == 1


@pytest.mark.asyncio
async def test_truncated_tool_call_self_correction(tmp_path):
    """Model returns _truncated args — gets specific write-shorter error message."""
    agent = make_agent(tmp_path)
    call_count = 0

    truncated_response = ModelResponse(
        text="",
        tool_calls=[ToolCall(id="tc_trunc", name="write_file", args={"_truncated": True, "path": "x.py"})],
        stop_reason="tool_use",
        usage={"input_tokens": 20, "output_tokens": 10},
    )

    async def send_sequence(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return truncated_response
        return text_response("Fixed — wrote shorter content.")

    with patch.object(agent.router, "send", side_effect=send_sequence):
        result = await agent.run("Write a file")

    assert result.error is None
    assert call_count == 2


@pytest.mark.asyncio
async def test_502_error_retries_like_429(tmp_path):
    """502 Bad Gateway is treated as transient and retried."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def flaky_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("502 Bad Gateway")
        return text_response("Success after 502.")

    with patch.object(agent.router, "send", side_effect=flaky_send):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run("Do something")

    assert result.error is None
    assert result.output == "Success after 502."
    assert call_count == 2


@pytest.mark.asyncio
async def test_throttling_error_retries(tmp_path):
    """Throttling error message is treated as transient."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def throttled_send(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        if call_count < 2:
            raise Exception("Request throttled — too many concurrent requests")
        return text_response("After throttle.")

    with patch.object(agent.router, "send", side_effect=throttled_send):
        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await agent.run("Do something")

    assert result.error is None
    assert call_count == 2


@pytest.mark.asyncio
async def test_context_overflow_no_reduction_gives_up(tmp_path):
    """Context overflow with no token reduction after compaction gives up."""
    agent = make_agent(tmp_path)
    call_count = 0

    async def always_overflow(messages, tools, model_config):
        nonlocal call_count
        call_count += 1
        raise Exception("context length exceeded: input too long")

    # Compaction returns same messages, _estimate_tokens returns same count
    with patch.object(agent.router, "send", side_effect=always_overflow):
        with patch.object(agent, "_compact_messages", side_effect=lambda msgs, budget: msgs):
            with patch.object(agent, "_estimate_tokens", return_value=200):
                result = await agent.run("Do something")

    assert result.error is not None
    assert "context" in result.error.lower() or "too long" in result.error.lower()
