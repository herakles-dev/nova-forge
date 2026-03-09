"""Tests for forge_audit.py — JSONL audit trail query and analysis."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from forge_audit import AuditQuery, AuditEntry, AuditStats


@pytest.fixture
def project(tmp_path):
    """Create a minimal .forge/audit/ structure."""
    audit_dir = tmp_path / ".forge" / "audit"
    audit_dir.mkdir(parents=True)
    return tmp_path


def _write_entries(project: Path, entries: list[dict], filename: str = "audit.jsonl") -> None:
    """Write JSONL entries to the audit file."""
    path = project / ".forge" / "audit" / filename
    lines = [json.dumps(e) for e in entries]
    path.write_text("\n".join(lines) + "\n")


# ── AuditEntry ────────────────────────────────────────────────────────────────

class TestAuditEntry:
    def test_from_dict_full(self):
        data = {
            "timestamp": "2026-03-09T10:00:00+00:00",
            "project": "myapp",
            "tool": "Write",
            "file": "src/main.py",
            "risk": "low",
            "outcome": "success",
            "autonomy_level": 2,
            "agent_id": "forge-impl-abc123",
            "task_id": "42",
            "diff_summary": "1 file changed, 10 insertions(+)",
        }
        e = AuditEntry.from_dict(data)
        assert e.timestamp == "2026-03-09T10:00:00+00:00"
        assert e.project == "myapp"
        assert e.tool == "Write"
        assert e.file == "src/main.py"
        assert e.risk == "low"
        assert e.outcome == "success"
        assert e.autonomy_level == 2
        assert e.agent_id == "forge-impl-abc123"
        assert e.diff_summary == "1 file changed, 10 insertions(+)"

    def test_from_dict_minimal(self):
        e = AuditEntry.from_dict({"timestamp": "2026-01-01T00:00:00Z"})
        assert e.timestamp == "2026-01-01T00:00:00Z"
        assert e.project == ""
        assert e.tool == ""
        assert e.autonomy_level == 0

    def test_from_dict_empty(self):
        e = AuditEntry.from_dict({})
        assert e.timestamp == ""


# ── AuditQuery — empty / missing ──────────────────────────────────────────────

class TestAuditQueryEmpty:
    def test_query_no_audit_file(self, project):
        # Remove the audit.jsonl (it doesn't exist yet)
        aq = AuditQuery(project)
        assert aq.query() == []

    def test_stats_empty(self, project):
        aq = AuditQuery(project)
        stats = aq.stats()
        assert stats.total_entries == 0
        assert stats.success_count == 0

    def test_recent_empty(self, project):
        aq = AuditQuery(project)
        assert aq.recent() == []

    def test_agent_usage_no_file(self, project):
        aq = AuditQuery(project)
        assert aq.agent_usage() == []

    def test_session_log_no_file(self, project):
        aq = AuditQuery(project)
        assert aq.session_log() == []


# ── AuditQuery — query with filters ──────────────────────────────────────────

class TestAuditQueryFilters:
    @pytest.fixture
    def populated(self, project):
        now = datetime.now(timezone.utc)
        entries = [
            {
                "timestamp": (now - timedelta(hours=2)).isoformat(),
                "tool": "Write",
                "risk": "low",
                "outcome": "success",
                "agent_id": "agent-1",
            },
            {
                "timestamp": (now - timedelta(minutes=30)).isoformat(),
                "tool": "Bash",
                "risk": "high",
                "outcome": "error",
                "agent_id": "agent-2",
            },
            {
                "timestamp": (now - timedelta(minutes=5)).isoformat(),
                "tool": "Write",
                "risk": "low",
                "outcome": "success",
                "agent_id": "agent-1",
            },
        ]
        _write_entries(project, entries)
        return project

    def test_query_all(self, populated):
        aq = AuditQuery(populated)
        results = aq.query()
        assert len(results) == 3

    def test_query_by_tool(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(tool="Write")
        assert len(results) == 2
        assert all(e.tool == "Write" for e in results)

    def test_query_by_tool_case_insensitive(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(tool="write")
        assert len(results) == 2

    def test_query_by_risk(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(risk="high")
        assert len(results) == 1
        assert results[0].tool == "Bash"

    def test_query_by_outcome(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(outcome="error")
        assert len(results) == 1

    def test_query_since_1h(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(since="1h")
        assert len(results) == 2  # Last two entries are within 1h

    def test_query_since_10m(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(since="10m")
        assert len(results) == 1  # Only the 5-minute-old entry

    def test_query_limit(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(limit=1)
        assert len(results) == 1

    def test_query_combined_filters(self, populated):
        aq = AuditQuery(populated)
        results = aq.query(tool="Write", outcome="success")
        assert len(results) == 2


# ── AuditQuery — stats ───────────────────────────────────────────────────────

class TestAuditQueryStats:
    def test_stats_aggregation(self, project):
        entries = [
            {"timestamp": "2026-03-09T10:00:00Z", "tool": "Write", "risk": "low", "outcome": "success", "agent_id": "a1"},
            {"timestamp": "2026-03-09T10:01:00Z", "tool": "Write", "risk": "low", "outcome": "success", "agent_id": "a1"},
            {"timestamp": "2026-03-09T10:02:00Z", "tool": "Bash", "risk": "high", "outcome": "error", "agent_id": "a2"},
        ]
        _write_entries(project, entries)

        aq = AuditQuery(project)
        stats = aq.stats()
        assert stats.total_entries == 3
        assert stats.success_count == 2
        assert stats.error_count == 1
        assert stats.tools_used == {"Write": 2, "Bash": 1}
        assert stats.risk_distribution == {"low": 2, "high": 1}
        assert stats.agents_active == {"a1", "a2"}
        assert stats.time_range[0] == "2026-03-09T10:00:00Z"
        assert stats.time_range[1] == "2026-03-09T10:02:00Z"


# ── AuditQuery — recent ──────────────────────────────────────────────────────

class TestAuditQueryRecent:
    def test_recent_returns_last_n(self, project):
        entries = [
            {"timestamp": f"2026-03-09T10:0{i}:00Z", "tool": f"tool-{i}"}
            for i in range(5)
        ]
        _write_entries(project, entries)

        aq = AuditQuery(project)
        recent = aq.recent(n=2)
        assert len(recent) == 2
        assert recent[0].tool == "tool-3"
        assert recent[1].tool == "tool-4"


# ── AuditQuery — agent_usage / session_log ────────────────────────────────────

class TestAuditQuerySecondaryLogs:
    def test_agent_usage(self, project):
        entries = [
            {"agent": "backend-architect", "calls": 5},
            {"agent": "spec-tester", "calls": 3},
        ]
        _write_entries(project, entries, "agent-usage.jsonl")

        aq = AuditQuery(project)
        usage = aq.agent_usage()
        assert len(usage) == 2
        assert usage[0]["agent"] == "backend-architect"

    def test_agent_usage_limit(self, project):
        entries = [{"agent": f"agent-{i}"} for i in range(10)]
        _write_entries(project, entries, "agent-usage.jsonl")

        aq = AuditQuery(project)
        usage = aq.agent_usage(limit=3)
        assert len(usage) == 3

    def test_session_log(self, project):
        entries = [
            {"session_id": "s1", "ended_at": "2026-03-09T10:00:00Z"},
        ]
        _write_entries(project, entries, "session-log.jsonl")

        aq = AuditQuery(project)
        logs = aq.session_log()
        assert len(logs) == 1
        assert logs[0]["session_id"] == "s1"


# ── AuditQuery._parse_since ──────────────────────────────────────────────────

class TestParseSince:
    def test_minutes(self):
        cutoff = AuditQuery._parse_since("30m")
        now = datetime.now(timezone.utc)
        delta = now - cutoff
        assert 29 * 60 <= delta.total_seconds() <= 31 * 60

    def test_hours(self):
        cutoff = AuditQuery._parse_since("2h")
        now = datetime.now(timezone.utc)
        delta = now - cutoff
        assert 119 * 60 <= delta.total_seconds() <= 121 * 60

    def test_days(self):
        cutoff = AuditQuery._parse_since("1d")
        now = datetime.now(timezone.utc)
        delta = now - cutoff
        assert 23 * 3600 <= delta.total_seconds() <= 25 * 3600

    def test_unknown_unit_defaults_to_1h(self):
        cutoff = AuditQuery._parse_since("5x")
        now = datetime.now(timezone.utc)
        delta = now - cutoff
        assert 50 * 60 <= delta.total_seconds() <= 70 * 60


# ── AuditQuery._after ────────────────────────────────────────────────────────

class TestAfter:
    def test_after_true(self):
        cutoff = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        assert AuditQuery._after("2026-03-09T11:00:00+00:00", cutoff)

    def test_after_false(self):
        cutoff = datetime(2026, 3, 9, 12, 0, tzinfo=timezone.utc)
        assert not AuditQuery._after("2026-03-09T10:00:00+00:00", cutoff)

    def test_after_unparseable_returns_true(self):
        cutoff = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        assert AuditQuery._after("not-a-timestamp", cutoff)

    def test_after_naive_timestamp_treated_as_utc(self):
        cutoff = datetime(2026, 3, 9, 10, 0, tzinfo=timezone.utc)
        assert AuditQuery._after("2026-03-09T11:00:00", cutoff)
