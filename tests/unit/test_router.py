"""Unit tests for ModelRouter routing (no live API calls)."""
import os
import pytest
from unittest.mock import patch, MagicMock

from model_router import ModelRouter, BedrockAdapter, OpenAIAdapter, AnthropicAdapter


# ── Tests ────────────────────────────────────────────────────────────────────

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
