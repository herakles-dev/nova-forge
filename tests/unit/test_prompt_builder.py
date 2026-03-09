"""Tests for prompt_builder.py — 7-section prompt construction."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest
from pathlib import Path
from prompt_builder import PromptBuilder


@pytest.fixture
def builder(tmp_path):
    """PromptBuilder with a temp project root."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return PromptBuilder(tmp_path)


class TestBuild:
    def test_returns_system_and_user_tuple(self, builder):
        system, user = builder.build(
            role="implementer",
            task={"subject": "Build API", "description": "Create REST endpoints"},
        )
        assert isinstance(system, str)
        assert isinstance(user, str)
        assert len(system) > 0
        assert len(user) > 0

    def test_system_prompt_contains_role(self, builder):
        system, _ = builder.build(
            role="backend-impl",
            task={"subject": "DB schema", "description": "Create tables"},
        )
        assert "backend-impl" in system

    def test_user_prompt_contains_task_subject(self, builder):
        _, user = builder.build(
            role="tester",
            task={"subject": "Run unit tests", "description": "Execute pytest suite"},
        )
        assert "Run unit tests" in user

    def test_user_prompt_contains_sprint_and_risk(self, builder):
        _, user = builder.build(
            role="impl",
            task={
                "subject": "Feature X",
                "description": "...",
                "metadata": {"sprint": "sprint-02", "risk": "high"},
            },
        )
        assert "sprint-02" in user
        assert "high" in user

    def test_context_section_included(self, builder):
        _, user = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
            context={"upstream-spec": "Here is the architecture doc content..."},
        )
        assert "upstream-spec" in user
        assert "architecture doc" in user

    def test_tool_policy_section(self, builder):
        _, user = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
            tool_policy={
                "available": ["read_file", "write_file"],
                "restricted": ["bash"],
            },
        )
        assert "read_file" in user
        assert "bash" in user.lower()

    def test_constraints_section(self, builder):
        _, user = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
            formation={
                "ownership": ["src/routes/", "src/models/"],
                "forbidden_paths": ["/etc/passwd"],
                "tool_policy": {"restricted": [], "available": []},
            },
        )
        assert "src/routes/" in user
        assert "/etc/passwd" in user

    def test_output_section_present(self, builder):
        _, user = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
        )
        assert "Expected Output" in user


class TestTokenBudget:
    def test_large_context_gets_truncated(self, builder):
        huge_context = {"big": "x" * 200_000}
        _, user = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
            context=huge_context,
            max_tokens=1000,  # Very tight budget
        )
        # Should be truncated, not full 200K chars
        assert len(user) < 200_000
        assert "truncated" in user.lower()

    def test_small_context_not_truncated(self, builder):
        small_context = {"note": "hello world"}
        _, user = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
            context=small_context,
            max_tokens=32_000,
        )
        assert "hello world" in user
        assert "truncated" not in user.lower()


class TestForgeMd:
    def test_forge_md_included_in_system(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        forge_md = forge_dir / "FORGE.md"
        forge_md.write_text("# My Project\nThis is the forge context.")

        builder = PromptBuilder(tmp_path)
        system, _ = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
        )
        assert "My Project" in system

    def test_no_forge_md_still_works(self, builder):
        system, _ = builder.build(
            role="impl",
            task={"subject": "X", "description": "Y"},
        )
        # Should not crash, just have role section
        assert "impl" in system


class TestAgentDefinition:
    def test_load_agent_with_frontmatter(self, tmp_path):
        forge_dir = tmp_path / ".forge"
        agents_dir = forge_dir / "agents"
        agents_dir.mkdir(parents=True)

        agent_file = agents_dir / "my-agent.md"
        agent_file.write_text(
            "---\nname: My Custom Agent\ndescription: Does cool stuff\n---\n"
            "# Agent Body\nDetailed instructions here."
        )

        builder = PromptBuilder(tmp_path)
        fm, body = builder.load_agent_definition("my-agent")
        assert fm["name"] == "My Custom Agent"
        assert "Detailed instructions" in body

    def test_missing_agent_returns_empty(self, builder):
        fm, body = builder.load_agent_definition("nonexistent")
        assert fm == {}
        assert body == ""
