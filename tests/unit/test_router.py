"""Unit tests for ModelRouter routing (no live API calls)."""
import os
import pytest
from unittest.mock import patch, MagicMock

from model_router import (
    ModelRouter,
    BedrockAdapter,
    OpenAIAdapter,
    AnthropicAdapter,
    ToolCall,
    ModelResponse,
    StreamDelta,
    estimate_tokens,
    _bedrock_tool_result,
    _openai_tool_result,
    _anthropic_tool_result,
)


# ── Routing tests ────────────────────────────────────────────────────────────

def test_route_bedrock():
    """'bedrock/nova-lite' routes to BedrockAdapter."""
    router = ModelRouter()
    with patch("model_router.BedrockAdapter.__init__", return_value=None):
        adapter = router.route("bedrock/us.amazon.nova-2-lite-v1:0")
    assert isinstance(adapter, BedrockAdapter)


def test_route_openai():
    """'openai/gpt-4o' routes to OpenAIAdapter."""
    router = ModelRouter()
    with patch("model_router.OpenAIAdapter.__init__", return_value=None):
        adapter = router.route("openai/gpt-4o")
    assert isinstance(adapter, OpenAIAdapter)


def test_route_anthropic():
    """'anthropic/claude-sonnet' routes to AnthropicAdapter."""
    router = ModelRouter()
    with patch("model_router.AnthropicAdapter.__init__", return_value=None):
        adapter = router.route("anthropic/claude-sonnet-4-6-20250514")
    assert isinstance(adapter, AnthropicAdapter)


def test_route_openrouter():
    """'openrouter/google/gemini' routes to OpenAIAdapter (OpenAI-compatible)."""
    router = ModelRouter()
    with patch("model_router.OpenAIAdapter.__init__", return_value=None):
        adapter = router.route("openrouter/google/gemini-2.0-flash-001")
    assert isinstance(adapter, OpenAIAdapter)


def test_route_ollama_to_openai():
    """'ollama/llama3' routes to OpenAIAdapter."""
    router = ModelRouter()
    with patch("model_router.OpenAIAdapter.__init__", return_value=None):
        adapter = router.route("ollama/llama3")
    assert isinstance(adapter, OpenAIAdapter)


def test_route_unknown_prefix_defaults_to_openai():
    """Unknown provider prefix defaults to OpenAIAdapter."""
    router = ModelRouter()
    with patch("model_router.OpenAIAdapter.__init__", return_value=None):
        adapter = router.route("custom/my-model")
    assert isinstance(adapter, OpenAIAdapter)


# ── Data structure tests ──────────────────────────────────────────────────────

def test_tool_call_dataclass():
    """ToolCall should hold id, name, and args."""
    tc = ToolCall(id="t1", name="write_file", args={"path": "a.py", "content": "x"})
    assert tc.id == "t1"
    assert tc.name == "write_file"
    assert tc.args["path"] == "a.py"


def test_model_response_dataclass():
    """ModelResponse should hold text, tool_calls, stop_reason, usage."""
    resp = ModelResponse(
        text="done",
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 50},
    )
    assert resp.text == "done"
    assert resp.tool_calls == []
    assert resp.usage["input_tokens"] == 100


def test_stream_delta_defaults():
    """StreamDelta should have sensible defaults for optional fields."""
    delta = StreamDelta(kind="text", text="hello")
    assert delta.tool_name == ""
    assert delta.tool_id == ""
    assert delta.tool_args_chunk == ""


# ── estimate_tokens ───────────────────────────────────────────────────────────

def test_estimate_tokens_basic():
    """estimate_tokens uses char/4 heuristic."""
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 100) == 25


def test_estimate_tokens_empty():
    """Empty string should return 1 (minimum)."""
    assert estimate_tokens("") == 1


def test_estimate_tokens_short():
    """Short strings should return at least 1."""
    assert estimate_tokens("ab") >= 1


# ── Tool result formatters ────────────────────────────────────────────────────

def test_bedrock_tool_result_format():
    """Bedrock tool result has toolResult with toolUseId and content."""
    result = _bedrock_tool_result("call-1", "file written")
    assert result["role"] == "user"
    content = result["content"]
    assert len(content) == 1
    tr = content[0]["toolResult"]
    assert tr["toolUseId"] == "call-1"
    assert tr["content"] == [{"text": "file written"}]


def test_openai_tool_result_format():
    """OpenAI tool result has role=tool with tool_call_id."""
    result = _openai_tool_result("call-2", "success")
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "call-2"
    assert result["content"] == "success"


def test_anthropic_tool_result_format():
    """Anthropic tool result has type=tool_result with tool_use_id."""
    result = _anthropic_tool_result("call-3", "ok")
    assert result["role"] == "user"
    content = result["content"]
    assert len(content) == 1
    assert content[0]["type"] == "tool_result"
    assert content[0]["tool_use_id"] == "call-3"
    assert content[0]["content"] == "ok"


def test_model_router_format_tool_result_dispatches():
    """ModelRouter.format_tool_result routes to correct provider formatter."""
    router = ModelRouter()
    bedrock = router.format_tool_result("bedrock", "id1", "res1")
    assert "toolResult" in bedrock["content"][0]

    openai = router.format_tool_result("openai", "id2", "res2")
    assert openai["role"] == "tool"

    anthropic = router.format_tool_result("anthropic", "id3", "res3")
    assert anthropic["content"][0]["type"] == "tool_result"


def test_extract_tool_calls():
    """extract_tool_calls should return the tool_calls from a ModelResponse."""
    tc1 = ToolCall(id="t1", name="read_file", args={"path": "a.py"})
    tc2 = ToolCall(id="t2", name="bash", args={"command": "ls"})
    resp = ModelResponse(text="", tool_calls=[tc1, tc2], stop_reason="tool_use", usage={"input_tokens": 0, "output_tokens": 0})
    router = ModelRouter()
    calls = router.extract_tool_calls(resp)
    assert len(calls) == 2
    assert calls[0].name == "read_file"
    assert calls[1].name == "bash"
