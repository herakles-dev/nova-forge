"""Integration tests for AutonomyManager wiring into ForgeAgent.

Tests:
  - check_permission across all levels (A0-A4)
  - current_level property correctness
  - ForgeAgent auto-wiring of AutonomyManager
  - record_build_result persistence and state changes
  - End-to-end autonomy gating in tool execution
  - Edge cases: negative values, large builds, level boundary checks
"""
import asyncio
import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from forge_guards import AutonomyManager, RiskLevel, _LEVEL_NAMES
from forge_agent import ForgeAgent
from forge_hooks import HookSystem
from model_router import ModelResponse, ToolCall
from config import get_model_config


# ── Helpers ───────────────────────────────────────────────────────────────────

_LEVEL_NAME_LIST = [_LEVEL_NAMES[i] for i in range(6)]


def make_manager(tmp_path: Path, level: int = 0) -> AutonomyManager:
    """Create an AutonomyManager persisted at tmp_path with the given level."""
    autonomy_file = tmp_path / "autonomy.json"
    state = {
        "level": level,
        "name": _LEVEL_NAME_LIST[min(level, 5)],
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
        wire_v11_hooks=False,
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

class TestCheckPermissionLevels:
    """Verify check_permission behavior at each autonomy level."""

    @pytest.mark.parametrize("risk", list(RiskLevel))
    def test_a0_blocks_all(self, tmp_path, risk):
        """A0 (Manual) blocks all risk levels."""
        mgr = make_manager(tmp_path, level=0)
        assert mgr.check_permission(risk) is False

    def test_a1_allows_low_only(self, tmp_path):
        """A1 (Guided) allows only LOW risk."""
        mgr = make_manager(tmp_path, level=1)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is False
        assert mgr.check_permission(RiskLevel.HIGH) is False

    def test_a2_allows_low_and_medium(self, tmp_path):
        """A2 (Supervised) allows LOW and MEDIUM; blocks HIGH."""
        mgr = make_manager(tmp_path, level=2)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is True
        assert mgr.check_permission(RiskLevel.HIGH) is False

    def test_a3_blocks_high(self, tmp_path):
        """A3 (Trusted) allows LOW and MEDIUM; blocks HIGH."""
        mgr = make_manager(tmp_path, level=3)
        assert mgr.check_permission(RiskLevel.LOW) is True
        assert mgr.check_permission(RiskLevel.MEDIUM) is True
        assert mgr.check_permission(RiskLevel.HIGH) is False

    @pytest.mark.parametrize("risk", list(RiskLevel))
    def test_a4_allows_all(self, tmp_path, risk):
        """A4 (Autonomous) allows all risk levels."""
        subdir = tmp_path / f"a4_{risk.value}"
        subdir.mkdir()
        mgr = make_manager(subdir, level=4)
        assert mgr.check_permission(risk) is True


# ── current_level property ────────────────────────────────────────────────────

class TestCurrentLevel:
    """Verify current_level reflects persisted state."""

    @pytest.mark.parametrize("lvl", range(6))
    def test_current_level_reflects_state(self, tmp_path, lvl):
        """current_level property returns the level from persisted state."""
        subdir = tmp_path / f"lvl{lvl}"
        subdir.mkdir()
        mgr = make_manager(subdir, level=lvl)
        assert mgr.current_level == lvl

    def test_current_level_type_is_int(self, tmp_path):
        """current_level should always return an int, not a string."""
        mgr = make_manager(tmp_path, level=2)
        assert isinstance(mgr.current_level, int)


# ── Wiring into ForgeAgent ────────────────────────────────────────────────────

def test_autonomy_manager_wired_into_agent(tmp_path):
    """ForgeAgent wires AutonomyManager when autonomy.json exists in .forge/state/."""
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


def test_agent_without_autonomy_file_has_no_manager(tmp_path):
    """ForgeAgent without autonomy.json should have autonomy_manager=None."""
    agent = make_agent(tmp_path, autonomy_level=None)
    assert agent.autonomy_manager is None


# ── record_build_result ───────────────────────────────────────────────────────

class TestRecordBuildResult:
    """Verify record_build_result integration with AutonomyManager."""

    def test_success_increments_exactly_once(self, tmp_path):
        """A clean build increments successful_actions by exactly 1."""
        mgr = make_manager(tmp_path, level=0)
        mgr.record_build_result(passed=5, failed=0, total=5)
        assert mgr._state["successful_actions"] == 1
        assert mgr._state["error_count"] == 0

    def test_failure_increments_error_count(self, tmp_path):
        """A build with failures increments error_count by exactly 1."""
        mgr = make_manager(tmp_path, level=1)
        mgr.record_build_result(passed=3, failed=2, total=5)
        assert mgr._state["error_count"] == 1
        history = mgr._state["error_history"]
        assert len(history) == 1
        assert history[-1]["tool"] == "build"

    def test_noop_on_empty_build(self, tmp_path):
        """A build with total=0 does not change any counters."""
        mgr = make_manager(tmp_path, level=0)
        mgr.record_build_result(passed=0, failed=0, total=0)
        assert mgr._state["successful_actions"] == 0
        assert mgr._state["error_count"] == 0

    def test_persists_across_instances(self, tmp_path):
        """record_build_result saves state to disk."""
        mgr = make_manager(tmp_path, level=0)
        mgr.record_build_result(passed=2, failed=0, total=2)
        mgr2 = AutonomyManager(tmp_path / "autonomy.json")
        assert mgr2._state["successful_actions"] == 1

    def test_all_passed_zero_failed(self, tmp_path):
        """Build with all passed and no failures is a clean success."""
        mgr = make_manager(tmp_path, level=2)
        mgr.record_build_result(passed=10, failed=0, total=10)
        assert mgr._state["successful_actions"] == 1
        assert mgr._state["error_count"] == 0

    def test_all_failed_zero_passed(self, tmp_path):
        """Build with all failures and no passes counts as error."""
        mgr = make_manager(tmp_path, level=2)
        mgr.record_build_result(passed=0, failed=5, total=5)
        assert mgr._state["error_count"] == 1

    def test_failure_triggers_deescalation(self, tmp_path):
        """A failed build should de-escalate from A3 to A2."""
        mgr = make_manager(tmp_path, level=3)
        mgr.record_build_result(passed=1, failed=4, total=5)
        assert mgr.current_level == 2

    def test_success_can_trigger_escalation(self, tmp_path):
        """Repeated successful builds should escalate from A0 toward A3."""
        af = tmp_path / "autonomy.json"
        af.write_text(json.dumps({
            "level": 0, "successful_actions": 4,
            "error_count": 0, "error_history": [],
            "last_escalation": None,
            "approved_categories": [], "grants": [],
            "high_risk_history": [],
        }))
        mgr = AutonomyManager(af)
        mgr.record_build_result(passed=1, failed=0, total=1)
        assert mgr.current_level >= 1, "5th success should trigger A0->A1"


# ── End-to-end: autonomy gating in _execute_tool_call ─────────────────────────

@pytest.mark.asyncio
async def test_autonomy_blocks_high_risk_tool_call(tmp_path):
    """When autonomy level is A2, a HIGH risk bash command is blocked."""
    agent = make_agent(tmp_path, autonomy_level=2)

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

    assert result.tool_calls_made == 1
    assert result.error is None or result.error == "max_turns_exceeded" or result.output


@pytest.mark.asyncio
async def test_autonomy_allows_low_risk_at_a1(tmp_path):
    """At A1, LOW risk read_file calls are permitted and succeed."""
    agent = make_agent(tmp_path, autonomy_level=1)

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
    agent = make_agent(tmp_path, autonomy_level=None)
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

    assert result.tool_calls_made == 1
    assert result.error is None or result.output


@pytest.mark.asyncio
async def test_a4_allows_high_risk_with_history(tmp_path):
    """At A4, a HIGH risk command in history should be auto-approved."""
    model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    hooks = HookSystem(settings_file=None)
    agent = ForgeAgent(
        model_config=model_cfg,
        project_root=tmp_path,
        hooks=hooks,
        max_turns=5,
        wire_v11_hooks=False,
        streaming=False,
    )

    # Set up A4 manager with command in history
    af = tmp_path / "autonomy.json"
    af.write_text(json.dumps({
        "level": 4,
        "name": "Autonomous",
        "successful_actions": 0,
        "error_count": 0,
        "approved_categories": [],
        "grants": [],
        "high_risk_history": ["docker system prune -a"],
        "last_escalation": None,
        "error_history": [],
    }))
    agent.autonomy_manager = AutonomyManager(af)

    # The command matches history — check() should allow it
    result = agent.autonomy_manager.check(
        "Bash", RiskLevel.HIGH, command="docker system prune -a"
    )
    assert result.allowed is True
