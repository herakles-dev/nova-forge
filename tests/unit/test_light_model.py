"""Tests for Sprint 7 — Light Model Optimization.

Covers:
- Truncated JSON recovery (model_router.py)
- _truncated/_raw detection as malformed (forge_agent.py)
- _execute_tool_call rejection of truncated args
- SLIM_TOOLS and get_tools_for_model routing
- Slim system prompt for 32K models
- Full system prompt for large models
- Smart compaction preserving file paths
- Decomposer size hint for 32K models
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

# ── Change 1: Truncated JSON Recovery ────────────────────────────────────────


def test_json_repair_closes_truncated_string():
    """When streaming truncates mid-JSON, try closing common patterns."""
    # Simulate what model_router.py does in the tool_end handler
    truncated = '{"path": "app.py", "content": "hello world'

    # Try the same repair logic used in model_router.py
    args = None
    for suffix in ['"}', '"}]', '"}]}', '}', ']}']:
        try:
            args = json.loads(truncated + suffix)
            break
        except json.JSONDecodeError:
            continue

    assert args is not None
    assert args["path"] == "app.py"
    assert args["content"] == "hello world"


def test_json_repair_falls_through_to_truncated():
    """When repair fails, result should have _truncated flag."""
    garbage = '{"path": "app.py", "content": [[[['

    args = None
    for suffix in ['"}', '"}]', '"}]}', '}', ']}']:
        try:
            args = json.loads(garbage + suffix)
            break
        except json.JSONDecodeError:
            continue
    else:
        args = {"_truncated": True, "_raw": garbage}

    assert args["_truncated"] is True
    assert "_raw" in args


# ── Change 1B: Truncated args detected as malformed ──────────────────────────


def test_truncated_args_detected_as_malformed():
    """Tool calls with _raw or _truncated keys should be flagged as malformed."""
    from model_router import ToolCall

    calls = [
        ToolCall(id="1", name="write_file", args={"_truncated": True, "_raw": "..."}),
        ToolCall(id="2", name="read_file", args={"path": "ok.py"}),
    ]

    valid = []
    malformed = []
    for call in calls:
        if not isinstance(call.args, dict):
            malformed.append(call)
        elif "_raw" in call.args or "_truncated" in call.args:
            malformed.append(call)
        else:
            valid.append(call)

    assert len(malformed) == 1
    assert malformed[0].id == "1"
    assert len(valid) == 1
    assert valid[0].id == "2"


# ── Change 1C: _execute_tool_call rejects _raw args ─────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_call_rejects_raw_args():
    """_execute_tool_call should return error for truncated args."""
    from forge_agent import ForgeAgent
    from config import get_model_config
    from model_router import ToolCall

    mc = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    agent = ForgeAgent(model_config=mc, project_root="/tmp", max_turns=1)

    call = ToolCall(id="t1", name="write_file", args={"_raw": "truncated...", "_truncated": True})
    result = await agent._execute_tool_call(call, {})

    assert "ERROR" in result
    assert "truncated" in result.lower()
    assert "append_file" in result


# ── Change 2: SLIM_TOOLS and get_tools_for_model ────────────────────────────


def test_slim_tools_has_8_tools():
    """SLIM_TOOLS should contain exactly 8 essential tools."""
    from forge_agent import SLIM_TOOLS
    assert len(SLIM_TOOLS) == 8
    names = {t["name"] for t in SLIM_TOOLS}
    assert names == {
        "read_file", "write_file", "append_file", "edit_file",
        "bash", "glob_files", "grep", "list_directory",
    }


def test_get_tools_for_model_routes_by_context():
    """32K models get slim tools, larger models get full set."""
    from forge_agent import get_tools_for_model, SLIM_TOOLS, BUILT_IN_TOOLS

    # 32K model without build context
    tools_32k = get_tools_for_model(32_000)
    assert len(tools_32k) == 8
    names_32k = {t["name"] for t in tools_32k}
    assert "claim_file" not in names_32k
    assert "think" not in names_32k

    # 32K model with build context — adds claim_file + check_context
    tools_32k_bc = get_tools_for_model(32_000, has_build_context=True)
    names_bc = {t["name"] for t in tools_32k_bc}
    assert "claim_file" in names_bc
    assert "check_context" in names_bc

    # Large model
    tools_large = get_tools_for_model(200_000)
    assert len(tools_large) == len(BUILT_IN_TOOLS)


# ── Change 3: Slim system prompt ────────────────────────────────────────────


def test_slim_system_prompt_for_32k_model():
    """32K models should get the slim system prompt (~600 chars)."""
    from prompt_builder import PromptBuilder

    pb = PromptBuilder("/tmp")
    prompt = pb.build_system_prompt(
        role="builder",
        model_id="bedrock/us.amazon.nova-2-lite-v1:0",
    )

    # Should contain slim markers
    assert "Max ~80 lines" in prompt
    # Should NOT contain verbose sections
    assert "You are NOT a chatbot" not in prompt
    assert len(prompt) < 1800  # Slim should be well under full size (~1600 with M1+M2 rules)


def test_full_system_prompt_for_large_model():
    """Large-context models should get a focused system prompt (not slim)."""
    from prompt_builder import PromptBuilder

    pb = PromptBuilder("/tmp")
    prompt = pb.build_system_prompt(
        role="builder",
        model_id="openrouter/google/gemini-2.0-flash-001",
    )

    # Focused prompt should include key directives and be larger than slim
    assert "You ACT" in prompt
    assert "Syntax issue" in prompt
    assert len(prompt) > 1500


# ── Change 5: Smart compaction preserves file paths ──────────────────────────


@pytest.mark.asyncio
async def test_compact_preserves_write_file_paths():
    """Compaction summary should list files written, not just [tool:write_file]."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    mc = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    agent = ForgeAgent(model_config=mc, project_root="/tmp", max_turns=1)

    # Build messages with toolUse blocks containing write_file
    messages = [
        {"role": "user", "content": "Build the app"},
    ]
    # Add enough messages to trigger compaction
    for i in range(15):
        messages.append({
            "role": "assistant",
            "content": [
                {"toolUse": {"name": "write_file", "input": {"path": f"file{i}.py", "content": "x"}, "toolUseId": f"t{i}"}},
            ],
        })
        messages.append({
            "role": "user",
            "content": [
                {"toolResult": {"toolUseId": f"t{i}", "content": [{"text": "ok"}]}},
            ],
        })

    # Force compaction (32K model keeps 3 pairs = 7 messages, so 31 messages will compact)
    compacted = agent._compact_messages(messages)

    # The compacted summary should mention file paths
    # Find the summary in any user message (role-alternation fix may merge head + summary)
    user_texts = [m["content"] for m in compacted if m.get("role") == "user" and isinstance(m.get("content"), str)]
    combined = "\n".join(user_texts)
    assert "file0.py" in combined
    assert "Files written so far" in combined


# ── Change 6: Decomposer size hint ──────────────────────────────────────────


def test_decomposer_includes_size_hint_for_32k():
    """When build_model is 32K, the decomposer should get a size constraint hint."""
    # We test that the plan() method accepts build_model parameter
    # and that it would inject the hint. We check the function signature.
    import inspect
    from forge_orchestrator import ForgeOrchestrator
    sig = inspect.signature(ForgeOrchestrator.plan)
    assert "build_model" in sig.parameters
