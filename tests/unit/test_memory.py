"""Tests for forge_memory.py — Chat History + Project Memory."""

import json
import time
import pytest
from pathlib import Path

from forge_memory import ChatHistory, ChatTurn, ProjectMemory, MemoryEntry


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def project_dir(tmp_path):
    """Create a project with .forge directory."""
    (tmp_path / ".forge").mkdir()
    return tmp_path


# ── Chat History Tests ───────────────────────────────────────────────────────

class TestChatHistory:
    def test_add_turn(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("build me an API", "Created spec.md")
        assert len(history.turns) == 1
        assert history.turns[0].user == "build me an API"
        assert history.turns[0].assistant == "Created spec.md"

    def test_save_and_load(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("hello", "hi there")
        history.add_turn("build it", "Done!")
        history.save()

        # Reload
        history2 = ChatHistory(project_dir)
        assert len(history2.turns) == 2
        assert history2.turns[0].user == "hello"
        assert history2.turns[0].assistant == "hi there"
        assert history2.turns[1].user == "build it"
        assert history2.turns[1].assistant == "Done!"

    def test_caps_at_50_turns(self, project_dir):
        history = ChatHistory(project_dir)
        for i in range(60):
            history.add_turn(f"message {i}", f"response {i}")
        history.save()

        history2 = ChatHistory(project_dir)
        assert len(history2.turns) == 50
        # Should keep the most recent
        assert history2.turns[-1].user == "message 59"
        assert history2.turns[0].user == "message 10"

    def test_clear(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("hello", "hi")
        history.save()
        history.clear()
        assert len(history.turns) == 0
        # File should be deleted
        assert not (project_dir / ".forge" / "chat_history.json").exists()

    def test_clear_when_no_file(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("test", "resp")
        history.clear()
        assert len(history.turns) == 0

    def test_recent_scales_with_context(self, project_dir):
        history = ChatHistory(project_dir)
        for i in range(25):
            history.add_turn(f"msg {i}", f"resp {i}")

        # Small context (32K) -> 3 turns
        recent_small = history.recent(context_window=32_000)
        assert len(recent_small) == 3
        assert recent_small[-1].user == "msg 24"

        # Medium context (128K) -> 10 turns
        recent_medium = history.recent(context_window=128_000)
        assert len(recent_medium) == 10

        # Large context (200K+) -> 20 turns
        recent_large = history.recent(context_window=200_000)
        assert len(recent_large) == 20

    def test_recent_with_fewer_turns_than_limit(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("only", "one")
        recent = history.recent(context_window=200_000)
        assert len(recent) == 1

    def test_recent_empty_history(self, project_dir):
        history = ChatHistory(project_dir)
        recent = history.recent(context_window=32_000)
        assert recent == []

    def test_to_context_format(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("build an API", "Created 5 tasks")
        ctx = history.to_context(context_window=32_000)
        assert "User: build an API" in ctx
        assert "Nova: Created 5 tasks" in ctx
        assert "## Recent Conversation" in ctx

    def test_to_context_empty(self, project_dir):
        history = ChatHistory(project_dir)
        assert history.to_context() == ""

    def test_build_result_in_turn(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("build it", "Done!", build_result={
            "status": "pass", "files_created": ["app.py", "models.py"]
        })
        ctx = history.to_context()
        assert "Build: pass" in ctx
        assert "app.py" in ctx

    def test_build_result_persists_through_save(self, project_dir):
        history = ChatHistory(project_dir)
        history.add_turn("build it", "Done!", build_result={
            "status": "pass", "files_created": ["app.py"]
        })
        history.save()

        history2 = ChatHistory(project_dir)
        assert history2.turns[0].build_result is not None
        assert history2.turns[0].build_result["status"] == "pass"

    def test_handles_corrupt_file(self, project_dir):
        hfile = project_dir / ".forge" / "chat_history.json"
        hfile.write_text("not valid json {{{")
        history = ChatHistory(project_dir)
        assert len(history.turns) == 0  # Graceful recovery

    def test_handles_missing_forge_dir(self, tmp_path):
        history = ChatHistory(tmp_path)
        history.add_turn("test", "response")
        history.save()
        assert (tmp_path / ".forge" / "chat_history.json").exists()

    def test_to_context_truncates_long_messages(self, project_dir):
        history = ChatHistory(project_dir)
        long_msg = "x" * 1000
        history.add_turn(long_msg, "short")
        ctx = history.to_context()
        # User messages truncated to 500 chars
        assert len(ctx) < 1000

    def test_turn_has_timestamp(self, project_dir):
        before = time.time()
        history = ChatHistory(project_dir)
        history.add_turn("test", "resp")
        after = time.time()
        ts = history.turns[0].timestamp
        assert before <= ts <= after


# ── Project Memory Tests ─────────────────────────────────────────────────────

class TestProjectMemory:
    def test_add_entry(self, project_dir):
        mem = ProjectMemory(project_dir)
        result = mem.add("Always use port 8080", category="preference")
        assert "Remembered" in result
        assert len(mem.entries) == 1
        assert mem.entries[0].note == "Always use port 8080"
        assert mem.entries[0].category == "preference"

    def test_save_and_load(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("Flask stack", category="pattern")
        mem.add("Use pytest", category="convention")
        mem.save()

        mem2 = ProjectMemory(project_dir)
        assert len(mem2.entries) == 2
        assert mem2.entries[0].note == "Flask stack"
        assert mem2.entries[1].note == "Use pytest"

    def test_writes_markdown(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("Flask stack", category="pattern")
        mem.add("Port 8080", category="preference")
        mem.save()

        md_file = project_dir / ".forge" / "FORGE_MEMORY.md"
        assert md_file.exists()
        content = md_file.read_text()
        assert "## Patterns" in content
        assert "Flask stack" in content
        assert "## Preferences" in content
        assert "Port 8080" in content

    def test_markdown_groups_by_category(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("pattern 1", category="pattern")
        mem.add("solution 1", category="solution")
        mem.add("convention 1", category="convention")
        mem.save()

        md_file = project_dir / ".forge" / "FORGE_MEMORY.md"
        content = md_file.read_text()
        assert "## Patterns" in content
        assert "## Solutions" in content
        assert "## Conventions" in content

    def test_deduplication(self, project_dir):
        mem = ProjectMemory(project_dir)
        # Both strings share the same first 50 characters
        base = "Always use port 8080 for the backend server in this"
        mem.add(base + " project", category="preference")
        result = mem.add(base + " application and all services", category="preference")
        # Second add updates the first (same 50-char prefix)
        assert len(mem.entries) == 1
        assert "application and all services" in mem.entries[0].note
        assert "Updated" in result

    def test_no_dedup_for_different_prefixes(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("First unique note about architecture", category="pattern")
        mem.add("Second unique note about deployment", category="pattern")
        assert len(mem.entries) == 2

    def test_caps_at_50_entries(self, project_dir):
        mem = ProjectMemory(project_dir)
        for i in range(55):
            mem.add(f"Memory entry number {i:03d} with unique prefix text", category="pattern")
        mem.save()

        mem2 = ProjectMemory(project_dir)
        assert len(mem2.entries) == 50

    def test_clear(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("test", category="pattern")
        mem.save()
        mem.clear()
        assert len(mem.entries) == 0
        assert not (project_dir / ".forge" / "FORGE_MEMORY.md").exists()
        assert not (project_dir / ".forge" / "memory.json").exists()

    def test_to_context_format(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("Flask stack", category="pattern")
        mem.add("Port 8080", category="preference")
        ctx = mem.to_context()
        assert "## Project Memory" in ctx
        assert "[pattern] Flask stack" in ctx
        assert "[preference] Port 8080" in ctx

    def test_to_context_respects_budget(self, project_dir):
        mem = ProjectMemory(project_dir)
        for i in range(30):
            mem.add(f"This is a fairly long memory entry number {i} with details", category="pattern")
        ctx = mem.to_context(budget_chars=500)
        assert len(ctx) <= 600  # Some header overshoot OK
        assert "more memories" in ctx

    def test_to_context_large_budget_includes_all(self, project_dir):
        mem = ProjectMemory(project_dir)
        for i in range(5):
            mem.add(f"Short note {i}", category="pattern")
        ctx = mem.to_context(budget_chars=10000)
        assert "more memories" not in ctx
        for i in range(5):
            assert f"Short note {i}" in ctx

    def test_to_context_empty(self, project_dir):
        mem = ProjectMemory(project_dir)
        assert mem.to_context() == ""

    def test_add_build_result(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add_build_result(
            model="nova-lite", task_count=5, passed=4, failed=1,
            stack="Flask", duration_s=45.2
        )
        assert len(mem.entries) == 1
        note = mem.entries[0].note
        assert "nova-lite" in note
        assert "4/5" in note
        assert "45s" in note
        assert "Flask" in note
        assert "1 failed" in note

    def test_add_build_result_all_passed(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add_build_result(model="nova-pro", task_count=3, passed=3, failed=0)
        note = mem.entries[0].note
        assert "all passed" in note
        assert "nova-pro" in note

    def test_invalid_category_defaults(self, project_dir):
        mem = ProjectMemory(project_dir)
        mem.add("test", category="invalid_category")
        assert mem.entries[0].category == "pattern"  # Falls back to pattern

    def test_valid_categories_accepted(self, tmp_path):
        for cat in ("pattern", "preference", "solution", "convention"):
            proj = tmp_path / cat
            (proj / ".forge").mkdir(parents=True)
            mem = ProjectMemory(proj)
            mem.add(f"test {cat} unique", category=cat)
            assert mem.entries[0].category == cat

    def test_handles_corrupt_data_file(self, project_dir):
        data_file = project_dir / ".forge" / "memory.json"
        data_file.write_text("CORRUPT!!!")
        mem = ProjectMemory(project_dir)
        assert len(mem.entries) == 0  # Graceful recovery


# ── ChatTurn Tests ───────────────────────────────────────────────────────────

class TestChatTurn:
    def test_to_dict(self):
        turn = ChatTurn(user="hello", assistant="hi", timestamp=1234567890.0)
        d = turn.to_dict()
        assert d["user"] == "hello"
        assert d["assistant"] == "hi"
        assert d["timestamp"] == 1234567890.0

    def test_to_dict_without_assistant(self):
        turn = ChatTurn(user="hello", timestamp=1234567890.0)
        d = turn.to_dict()
        assert "assistant" not in d  # Omitted when empty

    def test_to_dict_with_build_result(self):
        turn = ChatTurn(
            user="build", assistant="done",
            build_result={"status": "pass", "files_created": ["app.py"]},
        )
        d = turn.to_dict()
        assert d["build_result"]["status"] == "pass"

    def test_from_dict(self):
        d = {"user": "hello", "assistant": "hi", "timestamp": 1234567890.0}
        turn = ChatTurn.from_dict(d)
        assert turn.user == "hello"
        assert turn.assistant == "hi"
        assert turn.timestamp == 1234567890.0

    def test_from_dict_minimal(self):
        d = {"user": "hello"}
        turn = ChatTurn.from_dict(d)
        assert turn.user == "hello"
        assert turn.assistant == ""
        assert turn.timestamp == 0
        assert turn.build_result is None

    def test_from_dict_empty(self):
        turn = ChatTurn.from_dict({})
        assert turn.user == ""
        assert turn.assistant == ""


# ── MemoryEntry Tests ────────────────────────────────────────────────────────

class TestMemoryEntry:
    def test_to_dict(self):
        entry = MemoryEntry(note="test", category="pattern", timestamp=123.0)
        d = entry.to_dict()
        assert d["note"] == "test"
        assert d["category"] == "pattern"
        assert d["timestamp"] == 123.0

    def test_to_dict_auto_timestamp(self):
        entry = MemoryEntry(note="test", category="pattern")
        d = entry.to_dict()
        assert d["timestamp"] > 0  # Auto-filled

    def test_from_dict(self):
        d = {"note": "test", "category": "solution", "timestamp": 123.0}
        entry = MemoryEntry.from_dict(d)
        assert entry.note == "test"
        assert entry.category == "solution"
        assert entry.timestamp == 123.0

    def test_from_dict_defaults(self):
        entry = MemoryEntry.from_dict({})
        assert entry.note == ""
        assert entry.category == "pattern"
        assert entry.timestamp == 0
