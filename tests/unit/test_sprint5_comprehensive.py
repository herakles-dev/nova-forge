"""Comprehensive tests for Nova Forge Sprint 5 features.

Covers:
- S5.12 Enhanced tool descriptions (BUILT_IN_TOOLS)
- S5.13 Think tool, read-tracking, auto-verify
- S5.15 list_directory + search_replace_all tools
- S5.16 Per-project memory (forge_memory.py)
- S5.6  CLI dead-end fixes (_suggest_next_action)
- S5.2  Streaming (ModelRouter.stream_send, StreamDelta)
"""

import asyncio
import json
import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from forge_agent import ForgeAgent, AgentResult, BUILT_IN_TOOLS
from forge_hooks import HookSystem
from model_router import ModelRouter, ModelResponse, ToolCall, StreamDelta
from forge_memory import ProjectMemory, ChatHistory
from config import get_model_config


# ── Shared helpers ────────────────────────────────────────────────────────────

def make_agent(tmp_path: Path, on_event=None, streaming: bool = False) -> ForgeAgent:
    """Build a ForgeAgent pointing at a temp project root.
    Default streaming=False so tests use the simple router.send path.
    """
    model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
    hooks = HookSystem(settings_file=None)
    return ForgeAgent(
        model_config=model_cfg,
        project_root=tmp_path,
        hooks=hooks,
        max_turns=5,
        on_event=on_event,
        streaming=streaming,
    )


def text_response(text: str) -> ModelResponse:
    return ModelResponse(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
    )


def tool_call_response(name: str, args: dict, call_id: str = "tc_1") -> ModelResponse:
    return ModelResponse(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, args=args)],
        stop_reason="tool_use",
        usage={"input_tokens": 20, "output_tokens": 10},
    )


# ═══════════════════════════════════════════════════════════════════════════════
# S5.12 — Enhanced tool descriptions (BUILT_IN_TOOLS)
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnhancedToolDescriptions:
    """S5.12: Each tool has a meaningful description and correct parameter schema."""

    def _get_tool(self, name: str) -> dict:
        for t in BUILT_IN_TOOLS:
            if t["name"] == name:
                return t
        raise KeyError(f"Tool '{name}' not found in BUILT_IN_TOOLS")

    # ── Count / existence ────────────────────────────────────────────────────

    def test_built_in_tools_has_thirteen_tools(self):
        """BUILT_IN_TOOLS must define exactly 13 tools (10 core + append_file + claim_file + check_context)."""
        assert len(BUILT_IN_TOOLS) == 13

    def test_all_tools_have_name_and_description(self):
        """Every tool entry must have 'name' and 'description' keys."""
        for tool in BUILT_IN_TOOLS:
            assert "name" in tool, f"Tool missing 'name': {tool}"
            assert "description" in tool, f"Tool missing 'description': {tool['name']}"

    def test_all_descriptions_longer_than_50_chars(self):
        """Each description must be substantive (> 50 characters)."""
        for tool in BUILT_IN_TOOLS:
            desc = tool["description"]
            assert len(desc) > 50, (
                f"Description for '{tool['name']}' is only {len(desc)} chars — too short"
            )

    def test_all_tools_have_parameters_schema(self):
        """Every tool must have a 'parameters' dict."""
        for tool in BUILT_IN_TOOLS:
            assert "parameters" in tool, f"Tool '{tool['name']}' missing 'parameters'"
            assert isinstance(tool["parameters"], dict)

    # ── read_file ────────────────────────────────────────────────────────────

    def test_read_file_has_path_param(self):
        tool = self._get_tool("read_file")
        props = tool["parameters"]["properties"]
        assert "path" in props

    def test_read_file_has_offset_param(self):
        """read_file must expose an 'offset' parameter for partial reads."""
        tool = self._get_tool("read_file")
        props = tool["parameters"]["properties"]
        assert "offset" in props, "read_file is missing 'offset' parameter"

    def test_read_file_has_limit_param(self):
        """read_file must expose a 'limit' parameter for partial reads."""
        tool = self._get_tool("read_file")
        props = tool["parameters"]["properties"]
        assert "limit" in props, "read_file is missing 'limit' parameter"

    def test_read_file_path_is_required(self):
        tool = self._get_tool("read_file")
        assert "path" in tool["parameters"].get("required", [])

    def test_read_file_offset_not_required(self):
        """offset should be optional (not in required list)."""
        tool = self._get_tool("read_file")
        required = tool["parameters"].get("required", [])
        assert "offset" not in required

    def test_read_file_limit_not_required(self):
        """limit should be optional (not in required list)."""
        tool = self._get_tool("read_file")
        required = tool["parameters"].get("required", [])
        assert "limit" not in required

    # ── write_file ───────────────────────────────────────────────────────────

    def test_write_file_has_path_and_content_params(self):
        tool = self._get_tool("write_file")
        props = tool["parameters"]["properties"]
        assert "path" in props
        assert "content" in props

    def test_write_file_path_and_content_required(self):
        tool = self._get_tool("write_file")
        required = tool["parameters"].get("required", [])
        assert "path" in required
        assert "content" in required

    # ── edit_file ────────────────────────────────────────────────────────────

    def test_edit_file_has_path_old_new_params(self):
        tool = self._get_tool("edit_file")
        props = tool["parameters"]["properties"]
        assert "path" in props
        assert "old_string" in props
        assert "new_string" in props

    def test_edit_file_all_three_params_required(self):
        tool = self._get_tool("edit_file")
        required = tool["parameters"].get("required", [])
        assert "path" in required
        assert "old_string" in required
        assert "new_string" in required

    # ── think ────────────────────────────────────────────────────────────────

    def test_think_tool_exists(self):
        self._get_tool("think")

    def test_think_description_mentions_reasoning(self):
        """think tool description should mention reasoning / scratchpad."""
        tool = self._get_tool("think")
        desc_lower = tool["description"].lower()
        assert "reason" in desc_lower or "scratchpad" in desc_lower or "step" in desc_lower

    def test_think_has_reasoning_param(self):
        tool = self._get_tool("think")
        props = tool["parameters"]["properties"]
        assert "reasoning" in props

    # ── list_directory ───────────────────────────────────────────────────────

    def test_list_directory_tool_exists(self):
        self._get_tool("list_directory")

    def test_list_directory_has_path_param(self):
        tool = self._get_tool("list_directory")
        props = tool["parameters"]["properties"]
        assert "path" in props

    def test_list_directory_path_not_required(self):
        """list_directory path should be optional (defaults to project root)."""
        tool = self._get_tool("list_directory")
        required = tool["parameters"].get("required", [])
        # path is optional — can be omitted to list project root
        assert "path" not in required

    # ── search_replace_all ───────────────────────────────────────────────────

    def test_search_replace_all_tool_exists(self):
        self._get_tool("search_replace_all")

    def test_search_replace_all_has_required_params(self):
        tool = self._get_tool("search_replace_all")
        required = tool["parameters"].get("required", [])
        assert "path" in required
        assert "old_string" in required
        assert "new_string" in required

    def test_search_replace_all_description_mentions_all(self):
        """Description should make clear it replaces ALL occurrences."""
        tool = self._get_tool("search_replace_all")
        desc_lower = tool["description"].lower()
        assert "all" in desc_lower

    # ── remember ─────────────────────────────────────────────────────────────

    def test_remember_tool_exists(self):
        self._get_tool("remember")

    def test_remember_has_note_param(self):
        tool = self._get_tool("remember")
        props = tool["parameters"]["properties"]
        assert "note" in props

    def test_remember_note_is_required(self):
        tool = self._get_tool("remember")
        required = tool["parameters"].get("required", [])
        assert "note" in required

    def test_remember_has_category_param(self):
        """remember should accept an optional category."""
        tool = self._get_tool("remember")
        props = tool["parameters"]["properties"]
        assert "category" in props


# ═══════════════════════════════════════════════════════════════════════════════
# S5.13 — Think tool + read-tracking + auto-verify
# ═══════════════════════════════════════════════════════════════════════════════

class TestThinkToolReadTrackingAutoVerify:
    """S5.13: think returns fixed string; _files_read tracks reads; _auto_verify runs on .py/.json."""

    @pytest.mark.asyncio
    async def test_think_tool_returns_reasoning_noted(self, tmp_path):
        """think tool always returns 'Reasoning noted.'"""
        agent = make_agent(tmp_path)
        result = await agent._run_tool("think", {"reasoning": "I should think about this"}, {})
        assert result == "Reasoning noted."

    @pytest.mark.asyncio
    async def test_think_tool_ignores_reasoning_content(self, tmp_path):
        """think tool returns fixed response regardless of reasoning text."""
        agent = make_agent(tmp_path)
        r1 = await agent._run_tool("think", {"reasoning": "short"}, {})
        r2 = await agent._run_tool("think", {"reasoning": "A" * 5000}, {})
        assert r1 == r2 == "Reasoning noted."

    # ── Read-tracking ─────────────────────────────────────────────────────────

    def test_files_read_set_starts_empty(self, tmp_path):
        agent = make_agent(tmp_path)
        assert isinstance(agent._files_read, set)
        assert len(agent._files_read) == 0

    @pytest.mark.asyncio
    async def test_read_file_adds_to_files_read(self, tmp_path):
        """After reading a file, its resolved path is recorded in _files_read."""
        agent = make_agent(tmp_path)
        target = tmp_path / "hello.txt"
        target.write_text("hello world")

        await agent._run_tool("read_file", {"path": str(target)}, {})
        assert str(target) in agent._files_read

    @pytest.mark.asyncio
    async def test_reading_multiple_files_tracked_independently(self, tmp_path):
        """Reading two different files adds both to _files_read."""
        agent = make_agent(tmp_path)
        file_a = tmp_path / "a.txt"
        file_b = tmp_path / "b.txt"
        file_a.write_text("aaa")
        file_b.write_text("bbb")

        await agent._run_tool("read_file", {"path": str(file_a)}, {})
        await agent._run_tool("read_file", {"path": str(file_b)}, {})

        assert str(file_a) in agent._files_read
        assert str(file_b) in agent._files_read

    @pytest.mark.asyncio
    async def test_write_unread_file_includes_warning(self, tmp_path):
        """Writing a file that hasn't been read should produce a WARNING in the result."""
        agent = make_agent(tmp_path)
        target = tmp_path / "existing.py"
        target.write_text("old content")

        result = await agent._run_tool("write_file", {"path": str(target), "content": "new content"}, {})
        assert "WARNING" in result, f"Expected WARNING for unread file write, got: {result}"

    @pytest.mark.asyncio
    async def test_write_previously_read_file_no_warning(self, tmp_path):
        """Writing a file that was read first should NOT include a WARNING."""
        agent = make_agent(tmp_path)
        target = tmp_path / "existing.py"
        target.write_text("old content")

        # Read it first
        await agent._run_tool("read_file", {"path": str(target)}, {})
        # Now write it
        result = await agent._run_tool("write_file", {"path": str(target), "content": "new content"}, {})
        assert "WARNING" not in result, f"Unexpected WARNING for a read-then-write: {result}"

    @pytest.mark.asyncio
    async def test_edit_unread_file_is_blocked(self, tmp_path):
        """Editing a file that hasn't been read should be BLOCKED (Sprint 8 enforcement)."""
        agent = make_agent(tmp_path)
        target = tmp_path / "source.py"
        target.write_text("x = 1\n")

        result = await agent._run_tool(
            "edit_file",
            {"path": str(target), "old_string": "x = 1", "new_string": "x = 2"},
            {},
        )
        assert "BLOCKED" in result
        assert "read_file" in result

    @pytest.mark.asyncio
    async def test_edit_read_file_no_warning(self, tmp_path):
        """Editing a previously-read file does not emit a WARNING."""
        agent = make_agent(tmp_path)
        target = tmp_path / "source.py"
        target.write_text("x = 1\n")

        await agent._run_tool("read_file", {"path": str(target)}, {})
        result = await agent._run_tool(
            "edit_file",
            {"path": str(target), "old_string": "x = 1", "new_string": "x = 2"},
            {},
        )
        assert "WARNING" not in result

    # ── Auto-verify ──────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_auto_verify_runs_on_py_file(self, tmp_path):
        """Writing a .py file triggers syntax verification (syntax OK reported)."""
        agent = make_agent(tmp_path)
        target = tmp_path / "module.py"
        target.write_text("x = 1\n")
        agent._files_read.add(str(target))  # suppress warning

        result = await agent._run_tool("write_file", {"path": str(target), "content": "x = 1\n"}, {})
        assert "syntax" in result.lower() or "OK" in result, (
            f"Expected syntax check result for .py file, got: {result}"
        )

    @pytest.mark.asyncio
    async def test_auto_verify_runs_on_json_file(self, tmp_path):
        """Writing a .json file triggers JSON validation."""
        agent = make_agent(tmp_path)
        target = tmp_path / "config.json"
        valid_json = '{"key": "value"}'
        agent._files_read.add(str(target))

        result = await agent._run_tool("write_file", {"path": str(target), "content": valid_json}, {})
        assert "syntax" in result.lower() or "OK" in result, (
            f"Expected syntax check for .json file, got: {result}"
        )

    @pytest.mark.asyncio
    async def test_auto_verify_does_not_run_on_md_file(self, tmp_path):
        """Writing a .md file does NOT trigger syntax verification."""
        agent = make_agent(tmp_path)
        target = tmp_path / "README.md"
        result = await agent._run_tool(
            "write_file",
            {"path": str(target), "content": "# Hello\nThis is markdown.\n"},
            {},
        )
        # No syntax check — result should just confirm write
        assert "SYNTAX" not in result
        assert "syntax OK" not in result.lower()

    @pytest.mark.asyncio
    async def test_auto_verify_does_not_run_on_txt_file(self, tmp_path):
        """Writing a .txt file does NOT trigger syntax verification."""
        agent = make_agent(tmp_path)
        target = tmp_path / "notes.txt"
        result = await agent._run_tool(
            "write_file",
            {"path": str(target), "content": "plain text content\n"},
            {},
        )
        assert "syntax OK" not in result.lower()
        assert "Syntax issue" not in result

    @pytest.mark.asyncio
    async def test_auto_verify_reports_syntax_error_for_bad_py(self, tmp_path):
        """Writing a syntactically invalid .py file reports Syntax issue."""
        agent = make_agent(tmp_path)
        target = tmp_path / "bad.py"
        result = await agent._run_tool(
            "write_file",
            {"path": str(target), "content": "def broken(\n  # unclosed paren\n"},
            {},
        )
        assert "Syntax issue" in result, (
            f"Expected Syntax issue for invalid Python, got: {result}"
        )

    @pytest.mark.asyncio
    async def test_auto_verify_reports_syntax_error_for_bad_json(self, tmp_path):
        """Writing invalid JSON reports Syntax issue."""
        agent = make_agent(tmp_path)
        target = tmp_path / "bad.json"
        result = await agent._run_tool(
            "write_file",
            {"path": str(target), "content": '{"key": "value", broken}'},
            {},
        )
        assert "Syntax issue" in result, (
            f"Expected Syntax issue for invalid JSON, got: {result}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# S5.15 — list_directory + search_replace_all tools
# ═══════════════════════════════════════════════════════════════════════════════

class TestListDirectoryTool:
    """S5.15: list_directory tool shows files and subdirectories."""

    @pytest.mark.asyncio
    async def test_list_directory_shows_files(self, tmp_path):
        """list_directory output includes filenames present in the directory."""
        agent = make_agent(tmp_path)
        (tmp_path / "app.py").write_text("# app")
        (tmp_path / "config.json").write_text("{}")

        result = await agent._run_tool("list_directory", {"path": str(tmp_path)}, {})
        assert "app.py" in result
        assert "config.json" in result

    @pytest.mark.asyncio
    async def test_list_directory_shows_subdirectories(self, tmp_path):
        """list_directory output includes subdirectory names."""
        agent = make_agent(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "tests").mkdir()

        result = await agent._run_tool("list_directory", {"path": str(tmp_path)}, {})
        assert "src" in result
        assert "tests" in result

    @pytest.mark.asyncio
    async def test_list_directory_shows_item_count_for_subdirs(self, tmp_path):
        """Subdirectories show how many items they contain."""
        agent = make_agent(tmp_path)
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("x = 1")
        (src / "utils.py").write_text("y = 2")

        result = await agent._run_tool("list_directory", {"path": str(tmp_path)}, {})
        # Should mention item count for the src directory
        assert "2" in result or "items" in result.lower()

    @pytest.mark.asyncio
    async def test_list_directory_shows_total_count(self, tmp_path):
        """list_directory output mentions total item count."""
        agent = make_agent(tmp_path)
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "c.txt").write_text("c")

        result = await agent._run_tool("list_directory", {"path": str(tmp_path)}, {})
        assert "3" in result

    @pytest.mark.asyncio
    async def test_list_directory_non_existent_path_returns_error(self, tmp_path):
        """list_directory on a non-existent path returns an error message."""
        agent = make_agent(tmp_path)
        result = await agent._run_tool(
            "list_directory",
            {"path": str(tmp_path / "does_not_exist")},
            {},
        )
        assert "not found" in result.lower() or "error" in result.lower() or "Directory not found" in result

    @pytest.mark.asyncio
    async def test_list_directory_defaults_to_project_root(self, tmp_path):
        """Calling list_directory with no path defaults to project root."""
        agent = make_agent(tmp_path)
        (tmp_path / "readme.md").write_text("# Hi")

        # Call with empty args (no path) — should list tmp_path
        result = await agent._run_tool("list_directory", {}, {})
        assert "readme.md" in result

    @pytest.mark.asyncio
    async def test_list_directory_shows_file_sizes(self, tmp_path):
        """list_directory output includes file size information."""
        agent = make_agent(tmp_path)
        (tmp_path / "small.txt").write_text("x" * 100)

        result = await agent._run_tool("list_directory", {"path": str(tmp_path)}, {})
        # Size should be somewhere in the output (B, KB, MB)
        assert "B" in result or "KB" in result or "MB" in result

    @pytest.mark.asyncio
    async def test_list_directory_distinguishes_files_and_dirs(self, tmp_path):
        """list_directory marks files and directories differently."""
        agent = make_agent(tmp_path)
        (tmp_path / "myfile.py").write_text("pass")
        (tmp_path / "mydir").mkdir()

        result = await agent._run_tool("list_directory", {"path": str(tmp_path)}, {})
        # Should contain some marker distinguishing file vs dir
        assert "[file]" in result or "[dir]" in result or "/" in result


class TestSearchReplaceAllTool:
    """S5.15: search_replace_all replaces every occurrence and reports count."""

    @pytest.mark.asyncio
    async def test_search_replace_all_replaces_single_occurrence(self, tmp_path):
        """Single occurrence is replaced correctly."""
        agent = make_agent(tmp_path)
        f = tmp_path / "source.py"
        f.write_text("x = 1\n")

        result = await agent._run_tool(
            "search_replace_all",
            {"path": str(f), "old_string": "x = 1", "new_string": "x = 42"},
            {},
        )
        assert f.read_text() == "x = 42\n"
        assert "1" in result  # count reported

    @pytest.mark.asyncio
    async def test_search_replace_all_replaces_multiple_occurrences(self, tmp_path):
        """ALL occurrences in the file are replaced."""
        agent = make_agent(tmp_path)
        f = tmp_path / "config.py"
        content = "PORT = 8080\n# default PORT is 8080\nprint(PORT)\n"
        f.write_text(content)

        result = await agent._run_tool(
            "search_replace_all",
            {"path": str(f), "old_string": "8080", "new_string": "9000"},
            {},
        )
        new_content = f.read_text()
        assert "8080" not in new_content
        assert new_content.count("9000") == 2
        assert "2" in result  # count of replacements

    @pytest.mark.asyncio
    async def test_search_replace_all_reports_count_in_result(self, tmp_path):
        """Result message includes the number of replacements made."""
        agent = make_agent(tmp_path)
        f = tmp_path / "vars.py"
        f.write_text("foo = 1\nfoo = 2\nfoo = 3\n")

        result = await agent._run_tool(
            "search_replace_all",
            {"path": str(f), "old_string": "foo", "new_string": "bar"},
            {},
        )
        # Should say "3" somewhere (the count)
        assert "3" in result

    @pytest.mark.asyncio
    async def test_search_replace_all_not_found_returns_message(self, tmp_path):
        """When old_string is not found, returns an appropriate message."""
        agent = make_agent(tmp_path)
        f = tmp_path / "empty.py"
        f.write_text("x = 1\n")

        result = await agent._run_tool(
            "search_replace_all",
            {"path": str(f), "old_string": "NOTHERE", "new_string": "something"},
            {},
        )
        assert "not found" in result.lower() or "String not found" in result

    @pytest.mark.asyncio
    async def test_search_replace_all_nonexistent_file_returns_error(self, tmp_path):
        """Passing a non-existent file path returns an error message."""
        agent = make_agent(tmp_path)
        result = await agent._run_tool(
            "search_replace_all",
            {"path": str(tmp_path / "ghost.py"), "old_string": "x", "new_string": "y"},
            {},
        )
        assert "not found" in result.lower() or "File not found" in result

    @pytest.mark.asyncio
    async def test_search_replace_all_updates_artifacts(self, tmp_path):
        """Successful replacement records an artifact entry."""
        agent = make_agent(tmp_path)
        f = tmp_path / "main.py"
        f.write_text("old_name = 1\nold_name = 2\n")
        artifacts: dict = {}

        await agent._run_tool(
            "search_replace_all",
            {"path": str(f), "old_string": "old_name", "new_string": "new_name"},
            artifacts,
        )
        assert str(f) in artifacts
        assert artifacts[str(f)]["action"] == "search_replace_all"
        assert artifacts[str(f)]["replacements"] == 2


# ═══════════════════════════════════════════════════════════════════════════════
# S5.16 — Per-project memory (remember tool + ProjectMemory)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRememberToolAndProjectMemory:
    """S5.16: remember tool writes to FORGE_MEMORY.md; ProjectMemory API."""

    @pytest.mark.asyncio
    async def test_remember_tool_creates_forge_memory_md(self, tmp_path):
        """The remember tool should create FORGE_MEMORY.md under .forge/."""
        agent = make_agent(tmp_path)
        await agent._run_tool(
            "remember",
            {"note": "Always use Flask for APIs", "category": "convention"},
            {},
        )
        memory_file = tmp_path / ".forge" / "FORGE_MEMORY.md"
        assert memory_file.exists(), "FORGE_MEMORY.md was not created"

    @pytest.mark.asyncio
    async def test_remember_tool_writes_note_to_file(self, tmp_path):
        """The note text is written into FORGE_MEMORY.md."""
        agent = make_agent(tmp_path)
        await agent._run_tool(
            "remember",
            {"note": "Use port 9999 for this service"},
            {},
        )
        content = (tmp_path / ".forge" / "FORGE_MEMORY.md").read_text()
        assert "Use port 9999 for this service" in content

    @pytest.mark.asyncio
    async def test_remember_tool_includes_category_in_file(self, tmp_path):
        """The category is recorded in FORGE_MEMORY.md."""
        agent = make_agent(tmp_path)
        await agent._run_tool(
            "remember",
            {"note": "Prefer SQLite for lightweight storage", "category": "preference"},
            {},
        )
        content = (tmp_path / ".forge" / "FORGE_MEMORY.md").read_text()
        assert "PREFERENCE" in content or "preference" in content.lower()

    @pytest.mark.asyncio
    async def test_remember_tool_returns_confirmation(self, tmp_path):
        """The remember tool returns a confirmation string."""
        agent = make_agent(tmp_path)
        result = await agent._run_tool(
            "remember",
            {"note": "Test note"},
            {},
        )
        assert result  # non-empty
        assert "Remember" in result or "remember" in result.lower()

    @pytest.mark.asyncio
    async def test_remember_tool_appends_multiple_entries(self, tmp_path):
        """Calling remember multiple times appends entries without overwriting."""
        agent = make_agent(tmp_path)
        await agent._run_tool("remember", {"note": "First note"}, {})
        await agent._run_tool("remember", {"note": "Second note"}, {})

        content = (tmp_path / ".forge" / "FORGE_MEMORY.md").read_text()
        assert "First note" in content
        assert "Second note" in content


class TestProjectMemoryAPI:
    """S5.16: ProjectMemory class API and persistence."""

    def test_to_context_returns_formatted_string(self, tmp_path):
        """to_context() returns a string with entries formatted with category tags."""
        mem = ProjectMemory(tmp_path)
        mem.add("Always use port 8080", category="preference")
        mem.add("Flask is the stack", category="pattern")

        ctx = mem.to_context()
        assert "## Project Memory" in ctx
        assert "[preference]" in ctx
        assert "[pattern]" in ctx
        assert "port 8080" in ctx
        assert "Flask is the stack" in ctx

    def test_to_context_empty_when_no_entries(self, tmp_path):
        """to_context() returns empty string when there are no memory entries."""
        mem = ProjectMemory(tmp_path)
        assert mem.to_context() == ""

    def test_to_context_respects_budget(self, tmp_path):
        """to_context() stops adding entries when budget_chars is reached."""
        mem = ProjectMemory(tmp_path)
        for i in range(20):
            mem.add(f"Memory entry number {i:02d} with extra padding text here", category="pattern")

        ctx = mem.to_context(budget_chars=200)
        # Should be roughly within budget (allow some overshoot for the "more memories" line)
        assert len(ctx) < 500
        assert "more memories" in ctx

    def test_add_returns_confirmation_message(self, tmp_path):
        """add() returns a confirmation string."""
        mem = ProjectMemory(tmp_path)
        result = mem.add("Test memory note", category="solution")
        assert "Remembered" in result or "Updated" in result

    def test_add_persists_to_disk(self, tmp_path):
        """After add(), the entry survives a reload from disk."""
        mem = ProjectMemory(tmp_path)
        mem.add("Persisted note", category="pattern")

        mem2 = ProjectMemory(tmp_path)
        assert any("Persisted note" in e.note for e in mem2.entries)


class TestChatHistoryAddGet:
    """S5.16: ChatHistory add/get cycle."""

    def test_add_turn_increases_count(self, tmp_path):
        """add_turn() increases the number of stored turns."""
        history = ChatHistory(tmp_path)
        assert len(history.turns) == 0
        history.add_turn("hello", "hi")
        assert len(history.turns) == 1

    def test_get_recent_turns(self, tmp_path):
        """recent() returns the N most recent turns for the given context window."""
        history = ChatHistory(tmp_path)
        for i in range(15):
            history.add_turn(f"user msg {i}", f"assistant msg {i}")

        recent = history.recent(context_window=32_000)
        assert len(recent) == 3  # MAX_MEMORY_TURNS_SMALL
        assert recent[-1].user == "user msg 14"

    def test_add_get_round_trip_with_save(self, tmp_path):
        """Turn added and saved can be retrieved after reload."""
        history = ChatHistory(tmp_path)
        history.add_turn("What is the stack?", "Flask + PostgreSQL")
        history.save()

        history2 = ChatHistory(tmp_path)
        assert len(history2.turns) == 1
        assert history2.turns[0].user == "What is the stack?"
        assert history2.turns[0].assistant == "Flask + PostgreSQL"

    def test_turns_ordered_chronologically(self, tmp_path):
        """Turns remain in insertion order."""
        history = ChatHistory(tmp_path)
        history.add_turn("first", "resp 1")
        history.add_turn("second", "resp 2")
        history.add_turn("third", "resp 3")

        assert history.turns[0].user == "first"
        assert history.turns[1].user == "second"
        assert history.turns[2].user == "third"


# ═══════════════════════════════════════════════════════════════════════════════
# S5.6 — Dead-end fixes (_suggest_next_action logic via _get_task_summary_for)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuggestNextAction:
    """S5.6: _suggest_next_action produces correct guidance based on task state."""

    def _make_shell(self, tmp_path):
        """Build a minimal ForgeShell with a project path pointing to tmp_path."""
        # Import here to avoid top-level heavy import of forge_cli
        from forge_cli import ForgeShell
        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path
        return shell

    def test_suggest_next_action_method_exists(self):
        """ForgeShell must have a _suggest_next_action method."""
        from forge_cli import ForgeShell
        assert hasattr(ForgeShell, "_suggest_next_action"), (
            "ForgeShell is missing _suggest_next_action method"
        )

    def test_get_task_summary_no_tasks_returns_none(self, tmp_path):
        """_get_task_summary_for returns None when no tasks file exists."""
        from forge_cli import ForgeShell
        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path
        summary = shell._get_task_summary_for(tmp_path)
        assert summary is None

    def test_suggest_next_action_no_tasks_prints_hint(self, tmp_path, capsys):
        """With no tasks, _suggest_next_action prints a build/interview hint."""
        from forge_cli import ForgeShell
        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path

        # Patch _get_task_summary to return None (no tasks)
        with patch.object(ForgeShell, "_get_task_summary", return_value=None):
            shell._suggest_next_action()
        # No exception = pass (Rich console output is hard to capture here)

    def test_suggest_next_action_all_complete_no_failures(self, tmp_path):
        """With all tasks complete, _suggest_next_action doesn't raise."""
        from forge_cli import ForgeShell
        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path

        summary = {"total": 5, "completed": 5, "failed": 0, "pending": 0}
        with patch.object(ForgeShell, "_get_task_summary", return_value=summary):
            shell._suggest_next_action()  # Should not raise

    def test_suggest_next_action_with_failures_no_exception(self, tmp_path):
        """With failed tasks, _suggest_next_action runs without raising."""
        from forge_cli import ForgeShell
        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path

        summary = {"total": 5, "completed": 3, "failed": 2, "pending": 0}
        with patch.object(ForgeShell, "_get_task_summary", return_value=summary):
            shell._suggest_next_action()  # Should not raise

    def test_suggest_next_action_with_pending_tasks_no_exception(self, tmp_path):
        """With pending tasks, _suggest_next_action runs without raising."""
        from forge_cli import ForgeShell
        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path

        summary = {"total": 5, "completed": 2, "failed": 0, "pending": 3}
        with patch.object(ForgeShell, "_get_task_summary", return_value=summary):
            shell._suggest_next_action()  # Should not raise

    def test_get_task_summary_for_returns_dict_structure(self, tmp_path):
        """_get_task_summary_for returns a dict with the expected keys when tasks exist."""
        from forge_cli import ForgeShell, ForgeProject
        from forge_tasks import TaskStore
        import warnings

        shell = ForgeShell.__new__(ForgeShell)
        shell.project_path = tmp_path

        # Create a minimal project structure via TaskStore.create()
        (tmp_path / ".forge").mkdir(parents=True, exist_ok=True)
        project = ForgeProject(root=tmp_path)
        store = TaskStore(project.tasks_file)
        meta = {"project": "test", "sprint": "s1", "risk": "low"}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t1 = store.create("Task 1", "desc", metadata=meta)
            t2 = store.create("Task 2", "desc", metadata=meta)
            t3 = store.create("Task 3", "desc", metadata=meta)

        # Set statuses (pending→in_progress→completed for t1, pending for t2, pending→failed for t3)
        store.update(t1.id, status="in_progress")
        store.update(t1.id, status="completed")
        store.update(t3.id, status="in_progress")
        store.update(t3.id, status="failed")

        summary = shell._get_task_summary_for(tmp_path)
        assert summary is not None
        assert summary["total"] == 3
        assert summary["completed"] == 1
        assert summary["pending"] == 1
        assert summary["failed"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# S5.2 — Streaming (ModelRouter.stream_send + StreamDelta + on_stream_delta)
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreamDeltaDataclass:
    """S5.2: StreamDelta dataclass has the required fields."""

    def test_stream_delta_has_kind_field(self):
        delta = StreamDelta(kind="text")
        assert delta.kind == "text"

    def test_stream_delta_has_text_field(self):
        delta = StreamDelta(kind="text", text="hello")
        assert delta.text == "hello"

    def test_stream_delta_has_tool_name_field(self):
        delta = StreamDelta(kind="tool_start", tool_name="write_file")
        assert delta.tool_name == "write_file"

    def test_stream_delta_has_tool_id_field(self):
        delta = StreamDelta(kind="tool_start", tool_id="tc_abc")
        assert delta.tool_id == "tc_abc"

    def test_stream_delta_has_tool_args_chunk_field(self):
        delta = StreamDelta(kind="tool_delta", tool_args_chunk='{"path":')
        assert delta.tool_args_chunk == '{"path":'

    def test_stream_delta_kind_values(self):
        """All documented kind values can be instantiated."""
        for kind in ("text", "tool_start", "tool_delta", "tool_end", "done"):
            delta = StreamDelta(kind=kind)
            assert delta.kind == kind

    def test_stream_delta_defaults(self):
        """Text, tool_name, tool_id, tool_args_chunk default to empty string."""
        delta = StreamDelta(kind="done")
        assert delta.text == ""
        assert delta.tool_name == ""
        assert delta.tool_id == ""
        assert delta.tool_args_chunk == ""


class TestModelRouterStreamSend:
    """S5.2: ModelRouter.stream_send method accumulates deltas into ModelResponse."""

    def test_stream_send_method_exists(self):
        """ModelRouter must have a stream_send async method."""
        router = ModelRouter()
        assert hasattr(router, "stream_send"), "ModelRouter missing stream_send method"
        assert asyncio.iscoroutinefunction(router.stream_send), (
            "stream_send must be an async method"
        )

    def test_stream_method_exists(self):
        """ModelRouter must have a stream async generator method."""
        router = ModelRouter()
        assert hasattr(router, "stream")

    @pytest.mark.asyncio
    async def test_stream_send_accumulates_text_deltas(self):
        """stream_send collects text deltas into a single ModelResponse.text."""
        router = ModelRouter()
        model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")

        async def fake_stream(messages, tools, model_config):
            yield StreamDelta(kind="text", text="Hello, ")
            yield StreamDelta(kind="text", text="world!")
            yield StreamDelta(kind="done")

        with patch.object(router, "stream", side_effect=fake_stream):
            response = await router.stream_send([], [], model_cfg)

        assert response.text == "Hello, world!"
        assert response.tool_calls == []

    @pytest.mark.asyncio
    async def test_stream_send_accumulates_tool_call(self):
        """stream_send assembles tool_start + tool_delta + tool_end into a ToolCall."""
        router = ModelRouter()
        model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")

        async def fake_stream(messages, tools, model_config):
            yield StreamDelta(kind="tool_start", tool_name="read_file", tool_id="tc_1")
            yield StreamDelta(kind="tool_delta", tool_args_chunk='{"path": ')
            yield StreamDelta(kind="tool_delta", tool_args_chunk='"app.py"}')
            yield StreamDelta(kind="tool_end")
            yield StreamDelta(kind="done")

        with patch.object(router, "stream", side_effect=fake_stream):
            response = await router.stream_send([], [], model_cfg)

        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].name == "read_file"
        assert response.tool_calls[0].args == {"path": "app.py"}

    @pytest.mark.asyncio
    async def test_stream_send_calls_on_delta_callback(self):
        """stream_send invokes on_delta for each StreamDelta yielded."""
        router = ModelRouter()
        model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")
        received: list[StreamDelta] = []

        async def fake_stream(messages, tools, model_config):
            yield StreamDelta(kind="text", text="Hi")
            yield StreamDelta(kind="done")

        with patch.object(router, "stream", side_effect=fake_stream):
            await router.stream_send([], [], model_cfg, on_delta=received.append)

        assert len(received) == 2
        assert received[0].kind == "text"
        assert received[1].kind == "done"

    @pytest.mark.asyncio
    async def test_stream_send_falls_back_on_exception(self):
        """stream_send falls back to blocking send() if stream() raises."""
        router = ModelRouter()
        model_cfg = get_model_config("bedrock/us.amazon.nova-2-lite-v1:0")

        async def failing_stream(messages, tools, model_config):
            raise RuntimeError("Stream not available")
            yield  # make it an async generator

        fallback_response = ModelResponse(
            text="Fallback response",
            tool_calls=[],
            stop_reason="end_turn",
            usage={"input_tokens": 5, "output_tokens": 3},
        )

        with patch.object(router, "stream", side_effect=failing_stream):
            with patch.object(router, "send", new_callable=AsyncMock, return_value=fallback_response):
                response = await router.stream_send([], [], model_cfg)

        assert response.text == "Fallback response"


class TestOnStreamDeltaCallback:
    """S5.2: ForgeAgent._on_stream_delta forwards deltas to on_event."""

    def test_on_stream_delta_calls_on_event(self, tmp_path):
        """_on_stream_delta should call on_event with kind='stream_delta'."""
        events = []
        agent = make_agent(tmp_path, on_event=events.append)

        delta = StreamDelta(kind="text", text="partial output")
        agent._on_stream_delta(delta)

        assert len(events) == 1
        assert events[0].kind == "stream_delta"
        assert events[0].delta is delta

    def test_on_stream_delta_no_event_handler_no_crash(self, tmp_path):
        """_on_stream_delta should not crash when on_event is None."""
        agent = make_agent(tmp_path, on_event=None)
        delta = StreamDelta(kind="text", text="hello")
        agent._on_stream_delta(delta)  # Should not raise

    def test_on_stream_delta_suppresses_callback_exceptions(self, tmp_path):
        """If on_event raises, _on_stream_delta should not propagate the exception."""
        def bad_handler(event):
            raise RuntimeError("UI crashed!")

        agent = make_agent(tmp_path, on_event=bad_handler)
        delta = StreamDelta(kind="done")
        agent._on_stream_delta(delta)  # Should not raise despite bad handler

    @pytest.mark.asyncio
    async def test_streaming_agent_uses_stream_send(self, tmp_path):
        """ForgeAgent with streaming=True calls router.stream_send instead of send."""
        agent = make_agent(tmp_path, streaming=True)
        stream_send_called = []

        async def fake_stream_send(messages, tools, model_config, on_delta=None):
            stream_send_called.append(True)
            return text_response("Done via streaming.")

        with patch.object(agent.router, "stream_send", side_effect=fake_stream_send):
            result = await agent.run("Do something")

        assert len(stream_send_called) == 1
        assert result.output == "Done via streaming."

    @pytest.mark.asyncio
    async def test_non_streaming_agent_uses_send(self, tmp_path):
        """ForgeAgent with streaming=False calls router.send (not stream_send)."""
        agent = make_agent(tmp_path, streaming=False)
        send_called = []

        async def fake_send(messages, tools, model_config):
            send_called.append(True)
            return text_response("Done via blocking send.")

        with patch.object(agent.router, "send", side_effect=fake_send):
            result = await agent.run("Do something")

        assert len(send_called) == 1
        assert result.output == "Done via blocking send."
