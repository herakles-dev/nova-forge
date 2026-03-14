"""Nova Forge Build Communications — shared context bus for multi-agent builds.

Thread-safe in-memory store for file claims, announcements, and agent status.
All agents in a build share one BuildContext instance, enabling coordination
without explicit message passing.

Usage:
    ctx = BuildContext(project_root)
    ctx.claim_file("app.py", "forge-backend-1")    # Returns True
    ctx.claim_file("app.py", "forge-frontend-2")   # Returns False (conflict)
    ctx.announce("forge-backend-1", "endpoint_ready", "POST /api/users created")
    ctx.to_context("forge-frontend-2", budget_chars=3000)  # Snapshot for prompt
"""

from __future__ import annotations

import asyncio
import signal
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


class BuildCancellation:
    """Cooperative cancellation signal for multi-agent builds.

    During builds, replaces SIGINT handler so Ctrl-C sets an asyncio.Event
    instead of raising KeyboardInterrupt. Agents check this event at safe
    boundaries (between turns, between tool calls) and exit cleanly.
    """

    def __init__(self):
        self.pause_requested = asyncio.Event()
        self._original_handler = None
        self._installed = False

    def install(self):
        """Replace SIGINT handler with pause signal. Call at build start."""
        self._original_handler = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, self._handle_sigint)
        self._installed = True

    def uninstall(self):
        """Restore original SIGINT handler. Call in finally block."""
        if self._installed and self._original_handler is not None:
            signal.signal(signal.SIGINT, self._original_handler)
            self._installed = False

    def _handle_sigint(self, signum, frame):
        self.pause_requested.set()

    def is_paused(self) -> bool:
        return self.pause_requested.is_set()

    def reset(self):
        self.pause_requested.clear()


@dataclass
class FileClaim:
    """A file ownership claim by an agent."""
    path: str
    agent_id: str
    claimed_at: float
    status: str = "claimed"  # "claimed" | "writing" | "done"


@dataclass
class AgentAnnouncement:
    """Append-only log entry for inter-agent coordination."""
    agent_id: str
    kind: str       # "file_created" | "endpoint_ready" | "export_ready" | "blocked"
    detail: str     # e.g. "POST /api/users created"
    timestamp: float


class BuildContext:
    """Shared context for all agents in a build. Thread-safe via threading.Lock."""

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self._lock = threading.Lock()
        self._claims: dict[str, FileClaim] = {}
        self._announcements: list[AgentAnnouncement] = []
        self._agent_status: dict[str, str] = {}
        self._conflicts: int = 0

    # ── File claiming ─────────────────────────────────────────────────────

    def claim_file(self, path: str, agent_id: str) -> bool:
        """Claim exclusive write access to a file.

        Returns True if claimed successfully, False if another agent owns it.
        Re-claiming by the same agent succeeds (idempotent).
        """
        with self._lock:
            existing = self._claims.get(path)
            if existing is not None and existing.agent_id != agent_id:
                self._conflicts += 1
                return False
            self._claims[path] = FileClaim(
                path=path,
                agent_id=agent_id,
                claimed_at=time.time(),
                status="claimed",
            )
            return True

    def release_file(self, path: str, agent_id: str) -> None:
        """Release a file claim. Only the owning agent can release."""
        with self._lock:
            existing = self._claims.get(path)
            if existing is not None and existing.agent_id == agent_id:
                del self._claims[path]

    def is_claimed(self, path: str) -> FileClaim | None:
        """Check if a file is claimed. Returns the FileClaim or None."""
        with self._lock:
            return self._claims.get(path)

    def get_claims_by_agent(self, agent_id: str) -> list[FileClaim]:
        """Get all files claimed by a specific agent."""
        with self._lock:
            return [c for c in self._claims.values() if c.agent_id == agent_id]

    def update_claim_status(self, path: str, agent_id: str, status: str) -> None:
        """Update the status of a claim (e.g. claimed -> writing -> done)."""
        with self._lock:
            existing = self._claims.get(path)
            if existing is not None and existing.agent_id == agent_id:
                existing.status = status

    # ── Announcements ─────────────────────────────────────────────────────

    def announce(self, agent_id: str, kind: str, detail: str) -> None:
        """Post an announcement to the shared log."""
        with self._lock:
            self._announcements.append(AgentAnnouncement(
                agent_id=agent_id,
                kind=kind,
                detail=detail,
                timestamp=time.time(),
            ))

    def get_announcements(
        self, since: float = 0, exclude_agent: str = ""
    ) -> list[AgentAnnouncement]:
        """Get announcements, optionally filtered by time and excluding self."""
        with self._lock:
            result = []
            for a in self._announcements:
                if a.timestamp >= since and a.agent_id != exclude_agent:
                    result.append(a)
            return result

    # ── Agent status ──────────────────────────────────────────────────────

    def set_status(self, agent_id: str, status: str) -> None:
        """Update an agent's current status."""
        with self._lock:
            self._agent_status[agent_id] = status

    def get_status(self) -> dict[str, str]:
        """Get all agent statuses."""
        with self._lock:
            return dict(self._agent_status)

    # ── Context snapshot ──────────────────────────────────────────────────

    def to_context(self, agent_id: str, budget_chars: int = 3000) -> str:
        """Render shared build state for prompt injection, respecting budget.

        Shows other agents' claims, announcements, and status — excluding
        the requesting agent's own entries to avoid circular context.
        """
        with self._lock:
            parts: list[str] = []
            used = 0

            # File claims by other agents
            other_claims = [
                c for c in self._claims.values() if c.agent_id != agent_id
            ]
            if other_claims:
                lines = ["## Build Coordination — File Claims"]
                for c in other_claims[:20]:
                    lines.append(f"- {c.path} ({c.status}) — {c.agent_id}")
                section = "\n".join(lines)
                if used + len(section) < budget_chars:
                    parts.append(section)
                    used += len(section)

            # Announcements from other agents
            other_ann = [
                a for a in self._announcements if a.agent_id != agent_id
            ]
            if other_ann:
                lines = ["## Build Coordination — Announcements"]
                for a in other_ann[-15:]:  # Most recent 15
                    lines.append(f"- [{a.kind}] {a.detail} (by {a.agent_id})")
                section = "\n".join(lines)
                if used + len(section) < budget_chars:
                    parts.append(section)
                    used += len(section)

            # Agent statuses
            other_status = {
                k: v for k, v in self._agent_status.items() if k != agent_id
            }
            if other_status:
                lines = ["## Build Coordination — Agent Status"]
                for aid, status in other_status.items():
                    lines.append(f"- {aid}: {status}")
                section = "\n".join(lines)
                if used + len(section) < budget_chars:
                    parts.append(section)
                    used += len(section)

            if not parts:
                return ""
            return "\n\n".join(parts)

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """Build summary statistics."""
        with self._lock:
            return {
                "claims": len(self._claims),
                "conflicts": self._conflicts,
                "announcements": len(self._announcements),
            }
