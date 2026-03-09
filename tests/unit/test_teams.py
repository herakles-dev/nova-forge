"""Tests for forge_teams.py — multi-agent team spawning and coordination."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from forge_teams import (
    TeamManager,
    TeammateConfig,
    Team,
    SpawnResult,
    build_team_from_formation,
)
from forge_session import SessionManager


@pytest.fixture
def project(tmp_path):
    """Create a minimal .forge/ project."""
    forge = tmp_path / ".forge"
    forge.mkdir()
    (forge / "state").mkdir()
    (forge / "settings.json").write_text("{}")
    return tmp_path


# ── TeammateConfig ────────────────────────────────────────────────────────────

class TestTeammateConfig:
    def test_defaults(self):
        tc = TeammateConfig(role="impl", agent_name="backend-architect")
        assert tc.model == ""
        assert tc.tool_policy == "coding"
        assert tc.ownership == {"directories": [], "files": [], "patterns": []}

    def test_agent_id_format(self):
        tc = TeammateConfig(role="tester", agent_name="spec-tester")
        aid = tc.agent_id
        assert aid.startswith("forge-tester-")
        assert len(aid) == len("forge-tester-") + 8

    def test_agent_id_unique(self):
        tc = TeammateConfig(role="impl", agent_name="spec-impl")
        ids = {tc.agent_id for _ in range(10)}
        assert len(ids) == 10  # Each call generates new UUID


# ── Team ──────────────────────────────────────────────────────────────────────

class TestTeam:
    def test_team_id(self):
        t = Team(name="feature-impl", formation_name="feature-impl", project="myapp")
        assert t.team_id == "team-feature-impl-myapp"

    def test_wave_progress_starts_empty(self):
        t = Team(name="test", formation_name="test", project="p")
        assert t.wave_progress == {}


# ── SpawnResult ───────────────────────────────────────────────────────────────

class TestSpawnResult:
    def test_all_succeeded_empty(self):
        r = SpawnResult(wave=0)
        assert r.all_succeeded  # No errors, no results

    def test_all_succeeded_with_errors(self):
        r = SpawnResult(wave=0, errors=["something failed"])
        assert not r.all_succeeded

    def test_all_succeeded_with_agent_error(self):
        from forge_agent import AgentResult
        r = SpawnResult(
            wave=0,
            results={"impl": AgentResult(error="crashed")},
        )
        assert not r.all_succeeded


# ── TeamManager ───────────────────────────────────────────────────────────────

class TestTeamManager:
    def test_create_team(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(
                role="implementer",
                agent_name="spec-implementer",
            ),
        }
        team = tm.create_team("single-file", teammates)
        assert team.name == "single-file"
        assert team.formation_name == "single-file"
        assert team.project == project.name
        assert len(team.teammates) == 1
        assert team.created_at != ""

    def test_create_team_saves_formation(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(
                role="implementer",
                agent_name="spec-implementer",
            ),
        }
        tm.create_team("single-file", teammates)
        # Formation should be persisted
        formation = tm.sm.load_formation()
        assert formation is not None
        assert formation.name == "single-file"

    def test_create_team_model_override(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(
                role="implementer",
                agent_name="spec-impl",
            ),
            "tester": TeammateConfig(
                role="tester",
                agent_name="spec-tester",
                model="already-set",  # Should not be overridden
            ),
        }
        team = tm.create_team("lightweight-feature", teammates, model_override="nova-lite")
        assert team.teammates["implementer"].model == "nova-lite"
        assert team.teammates["tester"].model == "already-set"  # Not overridden

    def test_create_team_invalid_role_warns(self, project, caplog):
        import logging
        tm = TeamManager(project)
        teammates = {
            "nonexistent-role": TeammateConfig(
                role="nonexistent-role",
                agent_name="spec-impl",
            ),
        }
        with caplog.at_level(logging.WARNING):
            team = tm.create_team("single-file", teammates)
        assert "not found in formation" in caplog.text

    def test_check_health(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        team = tm.create_team("single-file", teammates)
        health = tm.check_health(team)
        assert health["team"] == "single-file"
        assert health["teammates"] == 1
        assert health["formation_saved"] is True

    def test_disband(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        team = tm.create_team("single-file", teammates)
        tm.disband(team)
        assert tm.sm.load_formation() is None

    def test_spawn_wave_exceeds_waves(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        team = tm.create_team("single-file", teammates)

        result = asyncio.run(tm.spawn_wave(team, wave=99))
        assert not result.all_succeeded
        assert any("exceeds" in e for e in result.errors)

    def test_spawn_wave_no_matching_roles(self, project):
        tm = TeamManager(project)
        # Create team with role that doesn't match wave 0 of single-file
        teammates = {
            "random-role": TeammateConfig(role="random-role", agent_name="spec-impl"),
        }
        team = tm.create_team("single-file", teammates)

        result = asyncio.run(tm.spawn_wave(team, wave=0))
        assert result.agents_spawned == 0
        assert team.wave_progress[0] == "done"


# ── build_team_from_formation ─────────────────────────────────────────────────

class TestBuildTeamFromFormation:
    def test_creates_team_from_formation(self, project):
        team = build_team_from_formation(project, "single-file")
        assert team.name == "single-file"
        assert "implementer" in team.teammates
        assert team.teammates["implementer"].tool_policy == "coding"

    def test_respects_agent_overrides(self, project):
        team = build_team_from_formation(
            project, "single-file",
            agent_overrides={"implementer": "backend-architect"},
        )
        assert team.teammates["implementer"].agent_name == "backend-architect"

    def test_feature_impl_has_multiple_roles(self, project):
        team = build_team_from_formation(project, "feature-impl")
        assert len(team.teammates) >= 3  # At least backend-impl, frontend-impl, tester

    def test_ownership_populated(self, project):
        team = build_team_from_formation(project, "feature-impl")
        for role_name, tc in team.teammates.items():
            assert "directories" in tc.ownership
            assert "patterns" in tc.ownership
