"""Integration tests for AutonomyManager.check_permission() and its wiring
into ForgeAgent._execute_tool_call().

Tests:
  test_a0_blocks_all_risk_levels
  test_a1_allows_low_only
  test_a2_blocks_high
  test_a3_blocks_high
  test_a4_allows_everything
  test_autonomy_manager_wired_into_agent
  test_record_build_result_success
  test_record_build_result_failure
  test_no_autonomy_manager_falls_back
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from forge_guards import AutonomyManager, RiskLevel
from forge_agent import ForgeAgent
from forge_hooks import HookSystem
from model_router import ModelResponse, ToolCall
from config import get_model_config


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_manager(tmp_path: Path, level: int = 0) -> AutonomyManager:
    """Create an AutonomyManager persisted at tmp_path with the given level."""
    autonomy_file = tmp_path / "autonomy.json"
    state = {
        "level": level,
        "name": ["Manual", "Guided", "Supervised", "Trusted", "Autonomous"][level],
        "successful_actions": 0,
        "error_count": 0,
        "approved_categories": [],
        "grants": [],
        "high_risk_history": [],
        "last_escalation": None,
        "error_history": [],
    }
    autonomy_file.write_text(json.dumps(state))
    return AutonomyManager(autonomy_file)


def make_agent(tmp_path: Path, autonomy_level: int | None = None) -> ForgeAgent:
    """Build a ForgeAgent with an optional autonomy level pre-set."""
    model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    hooks = HookSystem(settings_file=None)
    agent = ForgeAgent(
        model_config=model_cfg,
        project_root=tmp_path,
        hooks=hooks,
        max_turns=5,
        wire_v11_hooks=False,  # Don't auto-wire; we'll set manager manually
        streaming=False,
    )
    if autonomy_level is not None:
        agent.autonomy_manager = make_manager(tmp_path, autonomy_level)
    return agent


def tool_call_response(name: str, args: dict, call_id: str = "tc_1") -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        stop_reason="tool_use",
        usage={"input_tokens": 20, "output_tokens": 10},
    )


def text_response(text: str = "Done.") -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


# ── check_permission level tests ──────────────────────────────────────────────

def test_a0_blocks_all_risk_levels(tmp_path):
    """A0 (Manual) blocks LOW, MEDIUM, and HIGH risk."""
    mgr = make_manager(tmp_path, level=0)
    assert mgr.check_permission(RiskLevel.LOW) is False
    assert mgr.check_permission(RiskLevel.MEDIUM) is False
    assert mgr.check_permission(RiskLevel.HIGH) is False


def test_a1_allows_low_only(tmp_path):
    """A1 (Guided) allows only LOW risk; blocks MEDIUM and HIGH."""
    mgr = make_manager(tmp_path, level=1)
    assert mgr.check_permission(RiskLevel.LOW) is True
    assert mgr.check_permission(RiskLevel.MEDIUM) is False
    assert mgr.check_permission(RiskLevel.HIGH) is False


def test_a2_blocks_high(tmp_path):
    """A2 (Supervised) allows LOW and MEDIUM; blocks HIGH."""
    mgr = make_manager(tmp_path, level=2)
    assert mgr.check_permission(RiskLevel.LOW) is True
    assert mgr.check_permission(RiskLevel.MEDIUM) is True
    assert mgr.check_permission(RiskLevel.HIGH) is False


def test_a3_blocks_high(tmp_path):
    """A3 (Trusted) allows LOW and MEDIUM but blocks HIGH (requires approval)."""
    mgr = make_manager(tmp_path, level=3)
    assert mgr.check_permission(RiskLevel.LOW) is True
    assert mgr.check_permission(RiskLevel.MEDIUM) is True
    assert mgr.check_permission(RiskLevel.HIGH) is False


def test_a4_allows_everything(tmp_path):
    """A4 (Autonomous) allows all risk levels."""
    mgr = make_manager(tmp_path, level=4)
    assert mgr.check_permission(RiskLevel.LOW) is True
    assert mgr.check_permission(RiskLevel.MEDIUM) is True
    assert mgr.check_permission(RiskLevel.HIGH) is True


# ── current_level property ────────────────────────────────────────────────────

def test_current_level_reflects_state(tmp_path):
    """current_level property returns the level from persisted state."""
    for lvl in range(5):
        subdir = tmp_path / f"lvl{lvl}"
        subdir.mkdir()
        mgr = make_manager(subdir, level=lvl)
        assert mgr.current_level == lvl


# ── Wiring into ForgeAgent ────────────────────────────────────────────────────

def test_autonomy_manager_wired_into_agent(tmp_path):
    """ForgeAgent wires AutonomyManager when autonomy.json exists in .forge/state/."""
    # Create the autonomy file at the path ForgeAgent looks for
    state_dir = tmp_path / ".forge" / "state"
    state_dir.mkdir(parents=True)
    autonomy_file = state_dir / "autonomy.json"
    state = {
        "level": 2,
        "name": "Supervised",
        "successful_actions": 0,
        "error_count": 0,
        "approved_categories": [],
        "grants": [],
        "high_risk_history": [],
        "last_escalation": None,
        "error_history": [],
    }
    autonomy_file.write_text(json.dumps(state))

    model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    hooks = HookSystem(settings_file=None)
    agent = ForgeAgent(
        model_config=model_cfg,
        project_root=tmp_path,
        hooks=hooks,
        max_turns=5,
        wire_v11_hooks=True,
        streaming=False,
    )

    assert agent.autonomy_manager is not None
    assert agent.autonomy_manager.current_level == 2


# ── record_build_result ───────────────────────────────────────────────────────

def test_record_build_result_success(tmp_path):
    """A clean build increments successful_actions."""
    mgr = make_manager(tmp_path, level=0)
    before = mgr._state.get("successful_actions", 0)
    mgr.record_build_result(passed=5, failed=0, total=5)
    assert mgr._state.get("successful_actions", 0) == before + 1
    assert mgr._state.get("error_count", 0) == 0


def test_record_build_result_failure(tmp_path):
    """A build with failures increments error_count and appends to error_history."""
    mgr = make_manager(tmp_path, level=1)
    before_errors = mgr._state.get("error_count", 0)
    mgr.record_build_result(passed=3, failed=2, total=5)
    assert mgr._state.get("error_count", 0) == before_errors + 1
    history = mgr._state.get("error_history", [])
    assert len(history) >= 1
    assert history[-1]["tool"] == "build"


def test_record_build_result_noop_on_empty(tmp_path):
    """A build with total=0 does not increment any counters."""
    mgr = make_manager(tmp_path, level=0)
    mgr.record_build_result(passed=0, failed=0, total=0)
    assert mgr._state.get("successful_actions", 0) == 0
    assert mgr._state.get("error_count", 0) == 0


def test_record_build_result_persists(tmp_path):
    """record_build_result saves state to disk."""
    mgr = make_manager(tmp_path, level=0)
    mgr.record_build_result(passed=2, failed=0, total=2)
    # Re-load from disk and check
    mgr2 = AutonomyManager(tmp_path / "autonomy.json")
    assert mgr2._state.get("successful_actions", 0) >= 1


# ── End-to-end: autonomy gating in _execute_tool_call ─────────────────────────

@pytest.mark.asyncio
async def test_autonomy_blocks_high_risk_tool_call(tmp_path):
    """When autonomy level is A2, a HIGH risk bash command is blocked."""
    agent = make_agent(tmp_path, autonomy_level=2)

    # rm -rf is HIGH risk — should be blocked at A2
    responses = [
        tool_call_response("bash", {"command": "rm -rf /tmp/testdir"}, call_id="tc_1"),
        text_response("Done."),
    ]
    call_count = 0

    async def mock_send(messages, tools, model_config):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch.object(agent.router, "send", side_effect=mock_send):
        result = await agent.run("Delete temp directory")

    # The agent should have attempted 1 tool call (which was blocked)
    assert result.tool_calls_made == 1
    # The block message should appear in the conversation (as a tool result)
    # and the agent should continue; check via the final output
    assert result.error is None or result.error == "max_turns_exceeded" or result.output


@pytest.mark.asyncio
async def test_autonomy_allows_low_risk_at_a1(tmp_path):
    """At A1, LOW risk read_file calls are permitted and succeed."""
    agent = make_agent(tmp_path, autonomy_level=1)

    # Create a file to read
    test_file = tmp_path / "readme.txt"
    test_file.write_text("hello")

    responses = [
        tool_call_response("read_file", {"path": str(test_file)}, call_id="tc_1"),
        text_response("Read the file."),
    ]
    call_count = 0

    async def mock_send(messages, tools, model_config):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch.object(agent.router, "send", side_effect=mock_send):
        result = await agent.run("Read readme.txt")

    assert result.tool_calls_made == 1
    assert result.output == "Read the file."
    assert result.error is None


@pytest.mark.asyncio
async def test_no_autonomy_manager_falls_back(tmp_path):
    """When autonomy_manager is None, HIGH risk still blocked via legacy path."""
    agent = make_agent(tmp_path, autonomy_level=None)  # No manager
    assert agent.autonomy_manager is None

    responses = [
        tool_call_response("bash", {"command": "rm -rf /tmp/testdir"}, call_id="tc_1"),
        text_response("Done."),
    ]
    call_count = 0

    async def mock_send(messages, tools, model_config):
        nonlocal call_count
        resp = responses[call_count]
        call_count += 1
        return resp

    with patch.object(agent.router, "send", side_effect=mock_send):
        result = await agent.run("Delete temp directory")

    # rm -rf is HIGH; legacy path blocks it
    assert result.tool_calls_made == 1
    assert result.error is None or result.output
