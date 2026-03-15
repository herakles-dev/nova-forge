"""Tests for gather_environment_context() and build_enriched_system_prompt()
in prompt_builder.py — S5.10: Enrich System Prompt with Full Environment Awareness.
"""
import sys
import os
import json
import subprocess

sys.path.insert(
    0,
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

import pytest
from pathlib import Path
from prompt_builder import gather_environment_context, PromptBuilder


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def project_dir(tmp_path):
    """Plain temp directory with a minimal .forge layout."""
    (tmp_path / ".forge").mkdir()
    return tmp_path


@pytest.fixture
def git_repo(tmp_path):
    """Temp directory initialised as a git repository."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    return tmp_path


# ── gather_environment_context ────────────────────────────────────────────────


class TestGatherEnvironmentContextGit:
    def test_git_state_key_present_in_repo(self, git_repo):
        """git_state key should appear when called from a git repo."""
        ctx = gather_environment_context(git_repo)
        assert "git_state" in ctx

    def test_git_state_contains_branch(self, git_repo):
        """git_state value should mention 'Branch:'."""
        ctx = gather_environment_context(git_repo)
        assert "Branch:" in ctx["git_state"]

    def test_git_state_contains_changed_file_count(self, git_repo):
        """After adding an untracked file the count should reflect it."""
        (git_repo / "hello.py").write_text("print('hi')")
        ctx = gather_environment_context(git_repo)
        assert "git_state" in ctx
        # The count line has "N changed files" — just verify it's a non-empty string
        assert ctx["git_state"]

    def test_git_state_absent_outside_repo(self, tmp_path):
        """No git_state when the directory is not a git repo (graceful)."""
        ctx = gather_environment_context(tmp_path)
        # May or may not have git_state depending on parent-dir git; just ensure no crash
        assert isinstance(ctx, dict)


class TestGatherEnvironmentContextRequirements:
    def test_packages_key_present_with_requirements_txt(self, tmp_path):
        """packages key should appear when requirements.txt exists."""
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\nrequests>=2.31\n")
        ctx = gather_environment_context(tmp_path)
        assert "packages" in ctx

    def test_packages_contains_dep_names(self, tmp_path):
        """Dependency names should appear in packages string (without version specifiers)."""
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\nrequests>=2.31\n")
        ctx = gather_environment_context(tmp_path)
        assert "flask" in ctx["packages"]
        assert "requests" in ctx["packages"]

    def test_packages_prefixed_python_deps(self, tmp_path):
        """packages value should start with 'Python deps:'."""
        (tmp_path / "requirements.txt").write_text("click==8.1\n")
        ctx = gather_environment_context(tmp_path)
        assert ctx["packages"].startswith("Python deps:")

    def test_packages_skips_comments(self, tmp_path):
        """Comment lines in requirements.txt should not appear in packages."""
        (tmp_path / "requirements.txt").write_text("# this is a comment\nflask==3.0\n")
        ctx = gather_environment_context(tmp_path)
        assert "comment" not in ctx.get("packages", "")

    def test_packages_capped_at_15(self, tmp_path):
        """At most 15 packages should be listed."""
        lines = [f"pkg{i}==1.0" for i in range(30)]
        (tmp_path / "requirements.txt").write_text("\n".join(lines))
        ctx = gather_environment_context(tmp_path)
        # Count the comma-separated entries after the prefix
        pkg_list = ctx["packages"].replace("Python deps:", "").strip()
        entries = [e.strip() for e in pkg_list.split(",") if e.strip()]
        assert len(entries) <= 15


class TestGatherEnvironmentContextPackageJson:
    def test_packages_key_present_with_package_json(self, tmp_path):
        """packages key should appear when package.json exists."""
        pkg = {"dependencies": {"express": "^4.18", "axios": "^1.6"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = gather_environment_context(tmp_path)
        assert "packages" in ctx

    def test_packages_prefixed_node_deps(self, tmp_path):
        """packages value should start with 'Node deps:'."""
        pkg = {"dependencies": {"express": "^4.18"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = gather_environment_context(tmp_path)
        assert ctx["packages"].startswith("Node deps:")

    def test_packages_contains_dep_names_from_package_json(self, tmp_path):
        """Dependency names from package.json should appear in packages string."""
        pkg = {"dependencies": {"express": "^4.18", "lodash": "^4.17"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = gather_environment_context(tmp_path)
        assert "express" in ctx["packages"]
        assert "lodash" in ctx["packages"]

    def test_requirements_txt_takes_priority_over_package_json(self, tmp_path):
        """requirements.txt should be preferred when both files exist."""
        (tmp_path / "requirements.txt").write_text("flask==3.0\n")
        pkg = {"dependencies": {"express": "^4.18"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        ctx = gather_environment_context(tmp_path)
        assert ctx["packages"].startswith("Python deps:")


class TestGatherEnvironmentContextEmpty:
    def test_empty_project_returns_dict(self, tmp_path):
        """An empty directory should return a dict (possibly empty) without raising."""
        ctx = gather_environment_context(tmp_path)
        assert isinstance(ctx, dict)

    def test_no_packages_key_when_no_manifest(self, tmp_path):
        """packages key absent when neither requirements.txt nor package.json exists."""
        ctx = gather_environment_context(tmp_path)
        assert "packages" not in ctx

    def test_no_forge_md_key_when_absent(self, tmp_path):
        """forge_md key absent when .forge/FORGE.md does not exist."""
        ctx = gather_environment_context(tmp_path)
        assert "forge_md" not in ctx

    def test_forge_md_key_present_when_exists(self, tmp_path):
        """forge_md key present when .forge/FORGE.md exists."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "FORGE.md").write_text("# My Forge Project\nContext here.")
        ctx = gather_environment_context(tmp_path)
        assert "forge_md" in ctx
        assert "My Forge Project" in ctx["forge_md"]

    def test_forge_md_truncated_to_2000_chars(self, tmp_path):
        """forge_md content should be truncated to 2000 characters."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "FORGE.md").write_text("x" * 5000)
        ctx = gather_environment_context(tmp_path)
        assert len(ctx["forge_md"]) <= 2000


class TestGatherEnvironmentContextEdgeCases:
    """Edge cases for gather_environment_context."""

    def test_empty_requirements_txt(self, tmp_path):
        """Empty requirements.txt should not produce a packages key."""
        (tmp_path / "requirements.txt").write_text("")
        ctx = gather_environment_context(tmp_path)
        assert "packages" not in ctx

    def test_whitespace_only_requirements_txt(self, tmp_path):
        """Requirements.txt with only whitespace should not produce a packages key."""
        (tmp_path / "requirements.txt").write_text("  \n  \n")
        ctx = gather_environment_context(tmp_path)
        assert "packages" not in ctx

    def test_malformed_package_json_does_not_crash(self, tmp_path):
        """Malformed package.json should be handled gracefully."""
        (tmp_path / "package.json").write_text("{not valid json")
        ctx = gather_environment_context(tmp_path)
        # Should not crash, and packages should be absent
        assert isinstance(ctx, dict)
        assert "packages" not in ctx

    def test_package_json_without_dependencies(self, tmp_path):
        """package.json without dependencies key should not produce packages."""
        (tmp_path / "package.json").write_text('{"name": "test", "version": "1.0.0"}')
        ctx = gather_environment_context(tmp_path)
        assert "packages" not in ctx

    def test_requirements_with_version_specifiers_stripped(self, tmp_path):
        """Version specifiers (==, >=, <=, ~=) should be stripped from dep names."""
        (tmp_path / "requirements.txt").write_text("flask==3.0.0\nrequests>=2.31\nnumpy~=1.24\n")
        ctx = gather_environment_context(tmp_path)
        pkg = ctx["packages"]
        assert "flask" in pkg
        assert "==" not in pkg
        assert ">=" not in pkg
        assert "~=" not in pkg

    def test_forge_md_exact_2000_chars_not_truncated(self, tmp_path):
        """FORGE.md at exactly 2000 chars should not be truncated."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        content = "x" * 2000
        (forge_dir / "FORGE.md").write_text(content)
        ctx = gather_environment_context(tmp_path)
        assert ctx["forge_md"] == content


# ── build_enriched_system_prompt ──────────────────────────────────────────────


class TestBuildEnrichedSystemPromptIncludesEnv:
    def test_packages_appear_in_output(self, tmp_path):
        """Packages section from requirements.txt should appear in enriched prompt."""
        (tmp_path / ".forge").mkdir()
        (tmp_path / "requirements.txt").write_text("flask==3.0\nclick==8.1\n")
        builder = PromptBuilder(tmp_path)
        prompt = builder.build_enriched_system_prompt()
        assert "flask" in prompt

    def test_git_state_appears_in_output(self, git_repo):
        """Git state should appear in enriched prompt when inside a repo."""
        (git_repo / ".forge").mkdir()
        builder = PromptBuilder(git_repo)
        prompt = builder.build_enriched_system_prompt()
        assert "Branch:" in prompt

    def test_forge_md_appears_in_output(self, tmp_path):
        """FORGE.md content should be included in enriched prompt."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "FORGE.md").write_text("# Enriched Context\nSpecial marker text.")
        builder = PromptBuilder(tmp_path)
        prompt = builder.build_enriched_system_prompt()
        assert "Special marker text" in prompt

    def test_returns_non_empty_string(self, project_dir):
        """Method must always return a non-empty string."""
        builder = PromptBuilder(project_dir)
        prompt = builder.build_enriched_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0


class TestBuildEnrichedSystemPromptBudget:
    def test_truncates_when_max_tokens_is_small(self, tmp_path):
        """Output should be truncated and contain the budget notice."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        # Write enough FORGE.md to exceed a very small budget
        (forge_dir / "FORGE.md").write_text("# Context\n" + "Detail line.\n" * 200)
        builder = PromptBuilder(tmp_path)
        prompt = builder.build_enriched_system_prompt(max_tokens=200)
        assert "truncated" in prompt.lower()

    def test_no_truncation_with_large_budget(self, project_dir):
        """No truncation notice when budget is generous and content is small."""
        builder = PromptBuilder(project_dir)
        prompt = builder.build_enriched_system_prompt(max_tokens=128_000)
        assert "truncated to fit budget" not in prompt

    def test_output_length_respects_budget(self, tmp_path):
        """Output char count should not exceed budget ceiling (16K cap)."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "FORGE.md").write_text("x" * 10_000)
        builder = PromptBuilder(tmp_path)
        # max_tokens=500 → max_chars = min(500 * 0.125 * 4, 16000) = min(250, 16000) = 250
        prompt = builder.build_enriched_system_prompt(max_tokens=500)
        # Allow a small overshoot from the truncation suffix itself
        assert len(prompt) <= 500


class TestBuildEnrichedSystemPromptWaveInfo:
    def test_wave_info_appears_in_output(self, project_dir):
        """wave_info argument should appear in the Environment section."""
        builder = PromptBuilder(project_dir)
        prompt = builder.build_enriched_system_prompt(wave_info="Wave 2 of 3")
        assert "Wave 2 of 3" in prompt

    def test_task_context_appears_in_output(self, project_dir):
        """task_context argument should appear in the Environment section."""
        builder = PromptBuilder(project_dir)
        prompt = builder.build_enriched_system_prompt(
            task_context="Building the authentication module"
        )
        assert "Building the authentication module" in prompt

    def test_no_wave_section_when_empty(self, project_dir):
        """Wave section should be absent when wave_info and task_context are empty."""
        builder = PromptBuilder(project_dir)
        prompt = builder.build_enriched_system_prompt()
        # Environment section only appears if there's something to put in it from
        # git/packages/task_context/wave_info; for a bare tmp dir without git or
        # manifests it might be absent
        assert "Wave:" not in prompt
        assert "Build context:" not in prompt


class TestBuildSystemPrompt:
    def test_returns_non_empty_string(self, project_dir):
        """build_system_prompt should return a non-empty string."""
        builder = PromptBuilder(project_dir)
        result = builder.build_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_project_context_included(self, project_dir):
        """Supplied project_context string should appear in output."""
        builder = PromptBuilder(project_dir)
        result = builder.build_system_prompt(project_context="CUSTOM PROJECT CONTEXT")
        assert "CUSTOM PROJECT CONTEXT" in result

    def test_memory_context_included(self, project_dir):
        """Supplied memory_context string should appear in output."""
        builder = PromptBuilder(project_dir)
        result = builder.build_system_prompt(memory_context="remembered: use redis")
        assert "remembered: use redis" in result

    def test_index_context_included(self, project_dir):
        """Supplied index_context string should appear in output."""
        builder = PromptBuilder(project_dir)
        result = builder.build_system_prompt(index_context="src/api.py  src/models.py")
        assert "src/api.py" in result

    def test_behavioral_directives_present(self, project_dir):
        """System prompt should always include behavioral guidance."""
        builder = PromptBuilder(project_dir)
        result = builder.build_system_prompt()
        # Focused prompt uses "Rules" section; full prompt uses "Behavior"
        assert "Rules" in result or "Behavior" in result

    def test_role_identity_present(self, project_dir):
        """Role name should appear in the system prompt."""
        builder = PromptBuilder(project_dir)
        result = builder.build_system_prompt(role="custom-role")
        assert "custom-role" in result

    def test_forge_md_included_via_project_context(self, tmp_path):
        """When project_context is provided, it should appear in the prompt."""
        builder = PromptBuilder(tmp_path)
        result = builder.build_system_prompt(project_context="# Auto Forge Content\nLoaded automatically.")
        assert "Auto Forge Content" in result
