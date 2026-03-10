"""Nova Forge Memory & Chat Persistence — cross-session learning.

Handles:
1. Chat history persistence (per-project, 50-turn cap)
2. Project memory (FORGE_MEMORY.md — patterns, preferences, solutions)
3. Build result tracking for context enrichment

Usage:
    from forge_memory import ChatHistory, ProjectMemory

    history = ChatHistory(project_root)
    history.add_turn("build me an API", "Created spec.md with 5 tasks")
    history.save()

    memory = ProjectMemory(project_root)
    memory.add("Always use port 8080 for this project", category="preference")
    memory.to_context(budget_chars=4000)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ── Chat History ─────────────────────────────────────────────────────────────

MAX_DISK_TURNS = 50
MAX_MEMORY_TURNS_SMALL = 3   # For 32K context models
MAX_MEMORY_TURNS_MEDIUM = 10  # For 128K context models
MAX_MEMORY_TURNS_LARGE = 20   # For 200K+ context models


@dataclass
class ChatTurn:
    """A single conversation turn."""
    user: str
    assistant: str = ""
    timestamp: float = 0.0
    build_result: dict | None = None  # Optional: {status, files, errors}

    def to_dict(self) -> dict:
        d = {"user": self.user, "timestamp": self.timestamp or time.time()}
        if self.assistant:
            d["assistant"] = self.assistant
        if self.build_result:
            d["build_result"] = self.build_result
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ChatTurn":
        return cls(
            user=d.get("user", ""),
            assistant=d.get("assistant", ""),
            timestamp=d.get("timestamp", 0),
            build_result=d.get("build_result"),
        )


class ChatHistory:
    """Per-project chat history with disk persistence."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self._history_file = self.project_root / ".forge" / "chat_history.json"
        self._turns: list[ChatTurn] = []
        self._load()

    def _load(self) -> None:
        """Load from disk."""
        if self._history_file.exists():
            try:
                data = json.loads(self._history_file.read_text())
                self._turns = [ChatTurn.from_dict(t) for t in data.get("turns", [])]
            except Exception:
                self._turns = []

    def save(self) -> None:
        """Save to disk, capping at MAX_DISK_TURNS."""
        # Trim to cap
        if len(self._turns) > MAX_DISK_TURNS:
            self._turns = self._turns[-MAX_DISK_TURNS:]
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"turns": [t.to_dict() for t in self._turns]}
        self._history_file.write_text(json.dumps(data, indent=2))

    def add_turn(self, user: str, assistant: str = "", build_result: dict | None = None) -> None:
        """Add a conversation turn."""
        self._turns.append(ChatTurn(
            user=user,
            assistant=assistant,
            timestamp=time.time(),
            build_result=build_result,
        ))

    def clear(self) -> None:
        """Clear all history."""
        self._turns.clear()
        if self._history_file.exists():
            self._history_file.unlink()

    @property
    def turns(self) -> list[ChatTurn]:
        return self._turns

    def recent(self, context_window: int = 32_000) -> list[ChatTurn]:
        """Get recent turns scaled to model context window size."""
        if context_window <= 32_000:
            n = MAX_MEMORY_TURNS_SMALL
        elif context_window <= 128_000:
            n = MAX_MEMORY_TURNS_MEDIUM
        else:
            n = MAX_MEMORY_TURNS_LARGE
        return self._turns[-n:]

    def to_context(self, context_window: int = 32_000) -> str:
        """Render recent history for prompt injection."""
        recent = self.recent(context_window)
        if not recent:
            return ""
        lines = []
        for turn in recent:
            lines.append(f"User: {turn.user[:500]}")
            if turn.assistant:
                lines.append(f"Nova: {turn.assistant[:500]}")
            if turn.build_result:
                status = turn.build_result.get("status", "unknown")
                files = turn.build_result.get("files_created", [])
                if files:
                    lines.append(f"  [Build: {status}, files: {', '.join(files[:5])}]")
        return "## Recent Conversation\n" + "\n".join(lines)


# ── Project Memory ───────────────────────────────────────────────────────────

MAX_MEMORY_ENTRIES = 50

_CATEGORY_HEADERS = {
    "pattern": "Patterns",
    "preference": "Preferences",
    "solution": "Solutions",
    "convention": "Conventions",
}


@dataclass
class MemoryEntry:
    """A single memory note."""
    note: str
    category: str = "pattern"
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        return {
            "note": self.note,
            "category": self.category,
            "timestamp": self.timestamp or time.time(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEntry":
        return cls(
            note=d.get("note", ""),
            category=d.get("category", "pattern"),
            timestamp=d.get("timestamp", 0),
        )


class ProjectMemory:
    """Per-project memory that persists across sessions."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        self._memory_file = self.project_root / ".forge" / "FORGE_MEMORY.md"
        self._data_file = self.project_root / ".forge" / "memory.json"
        self._entries: list[MemoryEntry] = []
        self._load()

    def _load(self) -> None:
        """Load from JSON data file."""
        if self._data_file.exists():
            try:
                data = json.loads(self._data_file.read_text())
                self._entries = [MemoryEntry.from_dict(e) for e in data.get("entries", [])]
            except Exception:
                self._entries = []

    def save(self) -> None:
        """Save to both JSON (data) and Markdown (human-readable)."""
        # Trim to cap (drop oldest)
        if len(self._entries) > MAX_MEMORY_ENTRIES:
            self._entries = self._entries[-MAX_MEMORY_ENTRIES:]

        # JSON data file
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        data = {"entries": [e.to_dict() for e in self._entries]}
        self._data_file.write_text(json.dumps(data, indent=2))

        # Markdown file (human-readable + injected into prompt)
        self._write_markdown()

    def _write_markdown(self) -> None:
        """Write FORGE_MEMORY.md for human reading and prompt injection."""
        lines = ["# Project Memory\n"]
        lines.append(f"*{len(self._entries)} entries, auto-managed by Nova*\n")

        # Group by category
        by_cat: dict[str, list[MemoryEntry]] = {}
        for entry in self._entries:
            cat = entry.category
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(entry)

        for cat in ("pattern", "preference", "solution", "convention"):
            entries = by_cat.get(cat, [])
            if not entries:
                continue
            header = _CATEGORY_HEADERS.get(cat, cat.title())
            lines.append(f"\n## {header}\n")
            for entry in entries:
                lines.append(f"- {entry.note}")

        self._memory_file.write_text("\n".join(lines) + "\n")

    def add(self, note: str, category: str = "pattern") -> str:
        """Add a memory entry. Returns confirmation message."""
        # Check for duplicates (fuzzy: if first 50 chars match)
        prefix = note[:50].lower()
        for existing in self._entries:
            if existing.note[:50].lower() == prefix:
                # Update existing
                existing.note = note
                existing.timestamp = time.time()
                self.save()
                return f"Updated existing memory: {note[:60]}..."

        self._entries.append(MemoryEntry(
            note=note,
            category=category if category in _CATEGORY_HEADERS else "pattern",
            timestamp=time.time(),
        ))
        self.save()
        return f"Remembered: {note[:60]}..."

    def clear(self) -> None:
        """Clear all memory."""
        self._entries.clear()
        if self._data_file.exists():
            self._data_file.unlink()
        if self._memory_file.exists():
            self._memory_file.unlink()

    @property
    def entries(self) -> list[MemoryEntry]:
        return self._entries

    def to_context(self, budget_chars: int = 4000) -> str:
        """Render memory for prompt injection, respecting budget."""
        if not self._entries:
            return ""

        lines = ["## Project Memory"]
        total_chars = len(lines[0])

        for entry in self._entries:
            line = f"- [{entry.category}] {entry.note}"
            if total_chars + len(line) + 1 > budget_chars:
                lines.append(f"... and {len(self._entries) - len(lines) + 1} more memories")
                break
            lines.append(line)
            total_chars += len(line) + 1

        return "\n".join(lines)

    def add_build_result(self, model: str, task_count: int, passed: int, failed: int,
                         stack: str = "", duration_s: float = 0) -> None:
        """Auto-remember build outcome."""
        status = "all passed" if failed == 0 else f"{failed} failed"
        note = f"Build with {model}: {passed}/{task_count} tasks ({status})"
        if stack:
            note += f", stack: {stack}"
        if duration_s > 0:
            note += f", took {duration_s:.0f}s"
        self.add(note, category="pattern")


# ── CLI State ────────────────────────────────────────────────────────────────

_STATE_FILE = Path.home() / ".forge" / "cli_state.json"


def load_cli_state() -> dict:
    """Load global CLI state."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def save_cli_state(state: dict) -> None:
    """Save global CLI state."""
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _STATE_FILE.write_text(json.dumps(state, indent=2))
