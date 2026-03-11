"""Tests for S5.18 — Token Efficiency + Smart Truncation + Prompt Budgeting."""
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from config import get_prompt_budget, get_model_config
from model_router import estimate_tokens
from forge_agent import ForgeAgent
from forge_hooks import HookSystem
from model_router import ModelResponse, ToolCall


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_agent(tmp_path: Path, model_str: str = "bedrock/us.amazon.nova-2-lite-v1:0") -> ForgeAgent:
    model_cfg = get_model_config(model_str)
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


# ── Budget zone tests ─────────────────────────────────────────────────────────

def test_get_prompt_budget_32k():
    """32K budget: zones must sum to context_window, compaction at 0.60."""
    budget = get_prompt_budget(32_000)
    zone_sum = (
        budget["system_prompt"]
        + budget["project_index"]
        + budget["chat_history"]
        + budget["memory"]
        + budget["working_space"]
    )
    assert zone_sum == 32_000, f"Zones sum to {zone_sum}, expected 32000"
    assert budget["compaction_threshold"] == 0.60


def test_get_prompt_budget_128k():
    """200K budget (fits 128K models): zones correct, compaction at 0.75."""
    budget = get_prompt_budget(200_000)
    zone_sum = (
        budget["system_prompt"]
        + budget["project_index"]
        + budget["chat_history"]
        + budget["memory"]
        + budget["working_space"]
    )
    assert zone_sum == 128_000, f"Zones sum to {zone_sum}, expected 128000"
    assert budget["compaction_threshold"] == 0.75


def test_get_prompt_budget_1m():
    """1M budget: zones correct, compaction at 0.80."""
    budget = get_prompt_budget(1_000_000)
    zone_sum = (
        budget["system_prompt"]
        + budget["project_index"]
        + budget["chat_history"]
        + budget["memory"]
        + budget["working_space"]
    )
    assert zone_sum == 300_000, f"Zones sum to {zone_sum}, expected 300000"
    assert budget["compaction_threshold"] == 0.80


def test_get_prompt_budget_boundary_32k():
    """Exactly 32K context uses the 32K budget."""
    budget = get_prompt_budget(32_000)
    assert budget["compaction_threshold"] == 0.60


def test_get_prompt_budget_boundary_200k():
    """200K context uses the 200K budget."""
    budget = get_prompt_budget(200_000)
    assert budget["compaction_threshold"] == 0.75


def test_get_prompt_budget_boundary_above_200k():
    """201K context uses the 1M budget."""
    budget = get_prompt_budget(201_000)
    assert budget["compaction_threshold"] == 0.80


# ── estimate_tokens helper ───────────────────────────────────────────────────

def test_estimate_tokens_basic():
    """4 chars == 1 token."""
    assert estimate_tokens("abcd") == 1


def test_estimate_tokens_empty():
    """Empty string returns minimum of 1."""
    assert estimate_tokens("") == 1


def test_estimate_tokens_longer():
    text = "a" * 400
    assert estimate_tokens(text) == 100


def test_estimate_tokens_unicode():
    """Unicode chars counted by length (bytes not counted)."""
    text = "hello world"
    result = estimate_tokens(text)
    assert result >= 1


# ── read_file large file auto-truncation ─────────────────────────────────────

@pytest.mark.asyncio
async def test_read_file_large_auto_truncation(tmp_path):
    """File > 300 lines with no offset/limit gets head+tail with omission notice."""
    agent = make_agent(tmp_path)

    # Create a 400-line file
    large_file = tmp_path / "large.py"
    lines = [f"line_{i}" for i in range(400)]
    large_file.write_text("\n".join(lines))

    result = await agent._tool_read_file({"path": str(large_file)})

    # Should show omission notice
    assert "lines omitted" in result
    assert "offset/limit" in result
    # Should include head (line_0) and tail (line_399)
    assert "line_0" in result
    assert "line_399" in result
    # Should NOT include middle lines
    assert "line_200" not in result


@pytest.mark.asyncio
async def test_read_file_small_no_truncation(tmp_path):
    """File <= 300 lines with no offset/limit shows all content."""
    agent = make_agent(tmp_path)

    small_file = tmp_path / "small.py"
    lines = [f"line_{i}" for i in range(100)]
    small_file.write_text("\n".join(lines))

    result = await agent._tool_read_file({"path": str(small_file)})

    assert "lines omitted" not in result
    assert "line_50" in result


@pytest.mark.asyncio
async def test_read_file_with_offset_skips_auto_truncation(tmp_path):
    """File > 300 lines but with explicit offset reads normally (no auto-truncation)."""
    agent = make_agent(tmp_path)

    large_file = tmp_path / "large2.py"
    lines = [f"line_{i}" for i in range(400)]
    large_file.write_text("\n".join(lines))

    # Explicit offset: should not trigger head+tail mode
    result = await agent._tool_read_file({"path": str(large_file), "offset": 200, "limit": 10})

    assert "lines omitted" not in result
    assert "line_199" in result  # offset 200 → 0-based 199


@pytest.mark.asyncio
async def test_read_file_exactly_300_lines_no_truncation(tmp_path):
    """File at exactly 300 lines does not trigger head+tail (boundary: > 300)."""
    agent = make_agent(tmp_path)

    file_300 = tmp_path / "exact300.py"
    lines = [f"line_{i}" for i in range(300)]
    file_300.write_text("\n".join(lines))

    result = await agent._tool_read_file({"path": str(file_300)})
    assert "lines omitted" not in result


# ── bash output adaptive truncation ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_bash_output_adaptive_truncation_32k(tmp_path):
    """32K model truncates bash output at 8K chars."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")
    assert agent.model_config.context_window == 32_000

    # Generate output well beyond 8K
    big_output = "x" * 20_000

    with patch("asyncio.create_subprocess_shell") as mock_create:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(big_output.encode(), b""))
        mock_create.return_value = mock_proc

        result = await agent._tool_bash({"command": "echo test"})

    assert len(result) <= 8_100  # 8K + small truncation message overhead
    assert "truncated" in result


@pytest.mark.asyncio
async def test_bash_output_adaptive_no_truncation_under_limit(tmp_path):
    """Output under 8K for 32K model is returned untruncated."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")

    small_output = "hello world\n"

    with patch("asyncio.create_subprocess_shell") as mock_create:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(small_output.encode(), b""))
        mock_create.return_value = mock_proc

        result = await agent._tool_bash({"command": "echo hello world"})

    assert "truncated" not in result
    assert "hello world" in result


# ── grep compact mode for 32K models ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_grep_compact_mode_32k(tmp_path):
    """32K model shows max 30 grep matches, not 50."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")
    assert agent.model_config.context_window == 32_000

    # Simulate 60 match lines
    fake_lines = [f"{tmp_path}/file_{i}.py:1:match_{i}" for i in range(60)]
    fake_output = "\n".join(fake_lines) + "\n"

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(fake_output.encode(), b""))
        mock_exec.return_value = mock_proc

        result = await agent._tool_grep({"pattern": "match"})

    # Summary line should mention 30 as the show limit
    assert "showing first 30" in result
    # Should not show all 60 lines
    assert "match_59" not in result


@pytest.mark.asyncio
async def test_grep_summary_first_format_10_plus_matches(tmp_path):
    """10+ grep matches use summary-first format with file:lineno index."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")

    # 15 matches across 3 files
    fake_lines = []
    for i in range(5):
        fake_lines.append(f"{tmp_path}/a.py:{i+1}:match")
    for i in range(5):
        fake_lines.append(f"{tmp_path}/b.py:{i+1}:match")
    for i in range(5):
        fake_lines.append(f"{tmp_path}/c.py:{i+1}:match")
    fake_output = "\n".join(fake_lines) + "\n"

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(fake_output.encode(), b""))
        mock_exec.return_value = mock_proc

        result = await agent._tool_grep({"pattern": "match"})

    # Summary-first format
    assert "15 matches in 3 files" in result
    assert "showing first" in result


@pytest.mark.asyncio
async def test_grep_few_matches_no_summary(tmp_path):
    """Fewer than 10 matches use simple format without summary-first header."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")

    fake_lines = [f"{tmp_path}/a.py:{i+1}:match" for i in range(5)]
    fake_output = "\n".join(fake_lines) + "\n"

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(fake_output.encode(), b""))
        mock_exec.return_value = mock_proc

        result = await agent._tool_grep({"pattern": "match"})

    # Should use simple "Found N matches" format
    assert "Found 5 matches" in result
    assert "showing first" not in result


# ── compaction threshold ──────────────────────────────────────────────────────

def test_compaction_threshold_32k():
    """32K model compaction threshold is 0.60, not 0.80."""
    budget = get_prompt_budget(32_000)
    assert budget["compaction_threshold"] == 0.60
    # Verify 80% would be higher (old value)
    assert 0.60 < 0.80


def test_compaction_threshold_200k():
    """200K model compaction threshold is 0.75."""
    budget = get_prompt_budget(200_000)
    assert budget["compaction_threshold"] == 0.75


def test_compact_messages_32k_keeps_3_pairs(tmp_path):
    """32K model compaction keeps last 3 tool-result pairs (6+1 tail messages)."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")

    # Build a message history: 1 system + 20 messages
    messages = [{"role": "user", "content": "Initial prompt"}]
    for i in range(10):
        messages.append({"role": "assistant", "content": f"Response {i}"})
        messages.append({"role": "user", "content": f"Tool result {i}"})

    result = agent._compact_messages(messages)

    # Should be shorter than input
    assert len(result) < len(messages)
    # Should contain compaction notice
    all_content = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str) else ""
        for m in result
    )
    assert "compacted" in all_content.lower()


def test_compact_messages_short_list_unchanged(tmp_path):
    """Message list of 7 or fewer is returned unchanged."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")

    messages = [{"role": "user", "content": f"msg {i}"} for i in range(5)]
    result = agent._compact_messages(messages)

    assert result == messages


def test_compact_messages_drops_read_file_content(tmp_path):
    """Compaction drops read_file tool content from middle section."""
    agent = make_agent(tmp_path, "bedrock/us.amazon.nova-2-lite-v1:0")

    # Build messages with a read_file tool use in the middle
    messages = [{"role": "user", "content": "Initial prompt"}]
    # Add enough messages to trigger compaction (> 7)
    messages.append({
        "role": "assistant",
        "content": [
            {"toolUse": {"name": "read_file", "toolUseId": "tc_1", "input": {"path": "big.py"}}},
        ]
    })
    messages.append({
        "role": "user",
        "content": [
            {"toolResult": {"toolUseId": "tc_1", "content": [{"text": "very long file content " * 100}]}}
        ]
    })
    # Add tail messages so compaction fires
    for i in range(8):
        messages.append({"role": "assistant", "content": f"Response {i}"})
        messages.append({"role": "user", "content": f"Result {i}"})

    result = agent._compact_messages(messages)

    # The compacted summary should contain "read — dropped" not the full content
    all_content = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str) else ""
        for m in result
    )
    assert "read — dropped" in all_content
    assert "very long file content" not in all_content
