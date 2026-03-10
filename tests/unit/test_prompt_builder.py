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


# ── V11 system prompt tests ───────────────────────────────────────────────────

class TestBuildSystemPrompt:
    """Tests for PromptBuilder.build_system_prompt() — V11-grade system prompts."""

    def test_build_system_prompt_includes_identity(self, builder):
        prompt = builder.build_system_prompt(role="builder")
        # Identity section should open with Nova's self-description
        assert "Nova" in prompt
        assert "autonomous" in prompt.lower()
        # Must be action-oriented, not chatbot framing
        assert "ACT" in prompt or "act" in prompt.lower()

    def test_build_system_prompt_includes_tool_rules(self, builder):
        prompt = builder.build_system_prompt(role="builder")
        assert "Tool Usage Rules" in prompt
        # Core tool rules must be present
        assert "read a file before editing" in prompt
        assert "glob_files" in prompt
        assert "edit_file" in prompt

    def test_build_system_prompt_includes_behavior(self, builder):
        prompt = builder.build_system_prompt(role="builder")
        assert "Behavior" in prompt
        assert "documentation" in prompt.lower()
        assert "stubs" in prompt.lower() or "placeholder" in prompt.lower()

    def test_build_system_prompt_includes_error_handling(self, builder):
        prompt = builder.build_system_prompt(role="builder")
        assert "Error Handling" in prompt
        assert "max turns" in prompt.lower()
        assert "blockers" in prompt.lower() or "blocker" in prompt.lower()

    def test_build_system_prompt_role_profiles(self, builder):
        # builder role
        builder_prompt = builder.build_system_prompt(role="builder")
        assert "Builder" in builder_prompt
        assert "working code" in builder_prompt.lower() or "iterate" in builder_prompt.lower()

        # reviewer role — must NOT modify files
        reviewer_prompt = builder.build_system_prompt(role="reviewer")
        assert "Reviewer" in reviewer_prompt
        assert "NOT modify" in reviewer_prompt or "Do NOT modify" in reviewer_prompt

        # planner role
        planner_prompt = builder.build_system_prompt(role="planner")
        assert "Planner" in planner_prompt
        assert "spec" in planner_prompt.lower()

        # tester role
        tester_prompt = builder.build_system_prompt(role="tester")
        assert "Tester" in tester_prompt
        assert "pytest" in tester_prompt.lower() or "test" in tester_prompt.lower()

        # implementer role
        impl_prompt = builder.build_system_prompt(role="implementer")
        assert "Implementer" in impl_prompt
        assert "pattern" in impl_prompt.lower()

    def test_build_system_prompt_with_context(self, builder):
        project_ctx = "This project builds a REST API using FastAPI and PostgreSQL."
        prompt = builder.build_system_prompt(
            role="builder",
            project_context=project_ctx,
        )
        assert "Project Context" in prompt
        assert "FastAPI" in prompt
        assert "PostgreSQL" in prompt

    def test_build_system_prompt_with_memory_context(self, builder):
        memory = "Previously discovered: use bcrypt for password hashing."
        prompt = builder.build_system_prompt(
            role="builder",
            memory_context=memory,
        )
        assert "Project Memory" in prompt
        assert "bcrypt" in prompt

    def test_build_system_prompt_with_index_context(self, builder):
        index = "src/\n  api.py\n  models.py\ntests/\n  test_api.py"
        prompt = builder.build_system_prompt(
            role="builder",
            index_context=index,
        )
        assert "Project Structure" in prompt
        assert "api.py" in prompt

    def test_build_system_prompt_budget_truncation(self, builder):
        # Provide a very long project context — should be truncated
        long_context = "A" * 20_000
        prompt = builder.build_system_prompt(
            role="builder",
            project_context=long_context,
            max_context_chars=500,
        )
        assert "Project Context" in prompt
        # Truncation note must appear
        assert "truncated" in prompt.lower()
        # The raw 20K block must NOT appear verbatim
        assert long_context not in prompt

    def test_build_system_prompt_default_builder_role(self, builder):
        # Calling without role arg should default to "builder"
        prompt_default = builder.build_system_prompt()
        prompt_explicit = builder.build_system_prompt(role="builder")
        assert prompt_default == prompt_explicit

    def test_build_system_prompt_minimum_length_builder(self, builder):
        # The builder role prompt must be substantial — at least 80 lines
        prompt = builder.build_system_prompt(role="builder")
        line_count = len(prompt.splitlines())
        assert line_count >= 80, (
            f"Builder system prompt has only {line_count} lines; expected >= 80"
        )

    def test_build_system_prompt_unknown_role_fallback(self, builder):
        # Unknown role should not crash and should include the role name
        prompt = builder.build_system_prompt(role="custom-specialist")
        assert "custom-specialist" in prompt
        # Core sections still present
        assert "Tool Usage Rules" in prompt
        assert "Behavior" in prompt

    def test_build_system_prompt_no_context_blocks_by_default(self, builder):
        # Without optional context args, no stray "Project Context" header appears
        prompt = builder.build_system_prompt(role="builder")
        assert "Project Context" not in prompt
        assert "Project Memory" not in prompt
        assert "Project Structure" not in prompt
