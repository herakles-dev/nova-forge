"""Nova Forge Audit Trail — JSONL query and diff tracking.

Provides query interface for the audit log written by the track-autonomy
hook. Supports filtering by time, project, agent, tool, and risk level.

Usage::

    from forge_audit import AuditQuery

    aq = AuditQuery(project_root=Path("./myapp"))
    entries = aq.query(since="1h", tool="Write")
    stats = aq.stats()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

from config import FORGE_DIR_NAME

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class AuditEntry:
    """A single audit log entry."""
    timestamp: str
    project: str = ""
    tool: str = ""
    file: str = ""
    risk: str = ""
    outcome: str = ""
    autonomy_level: int = 0
    agent_id: str = ""
    task_id: str = ""
    diff_summary: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> AuditEntry:
        return cls(
            timestamp=data.get("timestamp", ""),
            project=data.get("project", ""),
            tool=data.get("tool", ""),
            file=data.get("file", ""),
            risk=data.get("risk", ""),
            outcome=data.get("outcome", ""),
            autonomy_level=data.get("autonomy_level", 0),
            agent_id=data.get("agent_id", ""),
            task_id=data.get("task_id", ""),
            diff_summary=data.get("diff_summary", ""),
        )


@dataclass
class AuditStats:
    """Aggregated audit statistics."""
    total_entries: int = 0
    success_count: int = 0
    error_count: int = 0
    tools_used: dict[str, int] = field(default_factory=dict)
    risk_distribution: dict[str, int] = field(default_factory=dict)
    agents_active: set[str] = field(default_factory=set)
    time_range: tuple[str, str] = ("", "")


# ── AuditQuery ───────────────────────────────────────────────────────────────

class AuditQuery:
    """Query and analyze the audit trail for a project."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.audit_file = self.project_root / FORGE_DIR_NAME / "audit" / "audit.jsonl"
        self.agent_usage_file = self.project_root / FORGE_DIR_NAME / "audit" / "agent-usage.jsonl"
        self.session_log_file = self.project_root / FORGE_DIR_NAME / "audit" / "session-log.jsonl"

    def query(
        self,
        since: str | None = None,
        tool: str | None = None,
        risk: str | None = None,
        outcome: str | None = None,
        limit: int = 100,
    ) -> list[AuditEntry]:
        """Query audit entries with optional filters.

        Args:
            since: Time filter (e.g. "1h", "30m", "2d")
            tool: Filter by tool name
            risk: Filter by risk level
            outcome: Filter by outcome (success/error)
            limit: Max results
        """
        entries = self._load_entries(self.audit_file)

        # Time filter
        if since:
            cutoff = self._parse_since(since)
            entries = [e for e in entries if self._after(e.timestamp, cutoff)]

        # Tool filter
        if tool:
            entries = [e for e in entries if e.tool.lower() == tool.lower()]

        # Risk filter
        if risk:
            entries = [e for e in entries if e.risk.lower() == risk.lower()]

        # Outcome filter
        if outcome:
            entries = [e for e in entries if e.outcome.lower() == outcome.lower()]

        return entries[-limit:]

    def stats(self) -> AuditStats:
        """Generate aggregated statistics from the audit trail."""
        entries = self._load_entries(self.audit_file)

        if not entries:
            return AuditStats()

        stats = AuditStats(total_entries=len(entries))

        for e in entries:
            if e.outcome == "success":
                stats.success_count += 1
            elif e.outcome == "error":
                stats.error_count += 1

            stats.tools_used[e.tool] = stats.tools_used.get(e.tool, 0) + 1
            stats.risk_distribution[e.risk] = stats.risk_distribution.get(e.risk, 0) + 1

            if e.agent_id:
                stats.agents_active.add(e.agent_id)

        if entries:
            stats.time_range = (entries[0].timestamp, entries[-1].timestamp)

        return stats

    def agent_usage(self, limit: int = 50) -> list[dict]:
        """Query agent usage log."""
        if not self.agent_usage_file.exists():
            return []

        entries = []
        try:
            for line in self.agent_usage_file.read_text().strip().split("\n"):
                if line.strip():
                    entries.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            pass

        return entries[-limit:]

    def session_log(self, limit: int = 20) -> list[dict]:
        """Query session end log."""
        if not self.session_log_file.exists():
            return []

        entries = []
        try:
            for line in self.session_log_file.read_text().strip().split("\n"):
                if line.strip():
                    entries.append(json.loads(line))
        except (json.JSONDecodeError, OSError):
            pass

        return entries[-limit:]

    def recent(self, n: int = 10) -> list[AuditEntry]:
        """Get the N most recent audit entries."""
        entries = self._load_entries(self.audit_file)
        return entries[-n:]

    # ── Internal ─────────────────────────────────────────────────────────────

    def _load_entries(self, path: Path) -> list[AuditEntry]:
        """Load all entries from a JSONL file."""
        if not path.exists():
            return []

        entries: list[AuditEntry] = []
        try:
            for line in path.read_text().strip().split("\n"):
                if line.strip():
                    entries.append(AuditEntry.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load audit entries from %s: %s", path, exc)

        return entries

    @staticmethod
    def _parse_since(since: str) -> datetime:
        """Parse a relative time string to a datetime cutoff."""
        now = datetime.now(timezone.utc)
        value = int("".join(c for c in since if c.isdigit()) or "1")
        unit = since[-1].lower() if since else "h"

        if unit == "m":
            return now - timedelta(minutes=value)
        elif unit == "h":
            return now - timedelta(hours=value)
        elif unit == "d":
            return now - timedelta(days=value)
        return now - timedelta(hours=1)

    @staticmethod
    def _after(timestamp: str, cutoff: datetime) -> bool:
        """Check if a timestamp is after the cutoff."""
        try:
            dt = datetime.fromisoformat(timestamp)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt >= cutoff
        except (ValueError, TypeError):
            return True  # Include entries with unparseable timestamps
