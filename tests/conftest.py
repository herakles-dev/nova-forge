"""Shared pytest fixtures for Nova Forge test suite."""
import sys
import os

# Ensure nova-forge source is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path

from config import init_forge_dir, ForgeProject
from model_router import ModelResponse, ToolCall


@pytest.fixture
def tmp_project(tmp_path):
    """Create a tmp dir with .forge/ structure initialized."""
    project = init_forge_dir(tmp_path)
    return project


@pytest.fixture
def mock_model_response():
    """Factory fixture returning canned ModelResponse objects."""
    def _factory(
        text: str = "Done.",
        tool_calls: list = None,
        stop_reason: str = "end_turn",
        usage: dict = None,
    ) -> ModelResponse:
        return ModelResponse(
            text=text,
            tool_calls=tool_calls or [],
            stop_reason=stop_reason,
            usage=usage or {"input_tokens": 10, "output_tokens": 5},
        )
    return _factory


@pytest.fixture
def sample_task_metadata():
    """Dict with required project/sprint/risk fields."""
    return {
        "project": "nova-forge",
        "sprint": "sprint-1",
        "risk": "low",
    }
