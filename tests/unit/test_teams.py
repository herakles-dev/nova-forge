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

    def test_custom_values(self):
        tc = TeammateConfig(
            role="tester", agent_name="spec-tester",
            model="nova-lite", tool_policy="testing",
            ownership={"directories": ["tests/"], "files": [], "patterns": ["*.test.py"]},
        )
        assert tc.model == "nova-lite"
        assert tc.tool_policy == "testing"
        assert tc.ownership["directories"] == ["tests/"]

    def test_agent_id_format(self):
        tc = TeammateConfig(role="tester", agent_name="spec-tester")
        aid = tc.agent_id
        assert aid.startswith("forge-tester-")
        assert len(aid) == len("forge-tester-") + 8

    def test_agent_id_unique(self):
        tc = TeammateConfig(role="impl", agent_name="spec-impl")
        ids = {tc.agent_id for _ in range(10)}
        assert len(ids) == 10  # Each call generates new UUID

    def test_agent_id_contains_role(self):
        tc = TeammateConfig(role="backend-impl", agent_name="arch")
        assert "backend-impl" in tc.agent_id


# ── Team ──────────────────────────────────────────────────────────────────────

class TestTeam:
    def test_team_id(self):
        t = Team(name="feature-impl", formation_name="feature-impl", project="myapp")
        assert t.team_id == "team-feature-impl-myapp"

    def test_wave_progress_starts_empty(self):
        t = Team(name="test", formation_name="test", project="p")
        assert t.wave_progress == {}

    def test_teammates_default_empty(self):
        t = Team(name="test", formation_name="test", project="p")
        assert t.teammates == {}

    def test_created_at_default_empty(self):
        t = Team(name="test", formation_name="test", project="p")
        assert t.created_at == ""

    def test_team_with_teammates(self):
        tc = TeammateConfig(role="impl", agent_name="spec-impl")
        t = Team(
            name="my-team", formation_name="feature-impl",
            project="myapp", teammates={"impl": tc},
        )
        assert len(t.teammates) == 1
        assert t.teammates["impl"].agent_name == "spec-impl"


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

    def test_all_succeeded_with_successful_results(self):
        from forge_agent import AgentResult
        r = SpawnResult(
            wave=0,
            results={"impl": AgentResult(error=None)},
        )
        assert r.all_succeeded

    def test_wave_index_stored(self):
        r = SpawnResult(wave=3)
        assert r.wave == 3

    def test_agents_spawned_default_zero(self):
        r = SpawnResult(wave=0)
        assert r.agents_spawned == 0


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
        assert "implementer" in formation.teammates

    def test_create_team_formation_has_agent_info(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(
                role="implementer",
                agent_name="spec-implementer",
            ),
        }
        tm.create_team("single-file", teammates)
        formation = tm.sm.load_formation()
        impl_info = formation.teammates["implementer"]
        assert impl_info["agent"] == "spec-implementer"
        assert "agent_id" in impl_info
        assert "ownership" in impl_info

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

    def test_create_team_model_override_skips_empty_only(self, project):
        tm = TeamManager(project)
        teammates = {
            "a": TeammateConfig(role="implementer", agent_name="spec-impl", model=""),
            "b": TeammateConfig(role="implementer", agent_name="spec-impl", model="custom-model"),
        }
        team = tm.create_team("single-file", teammates, model_override="nova-pro")
        assert team.teammates["a"].model == "nova-pro"
        assert team.teammates["b"].model == "custom-model"

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
        # Team is still created despite warning
        assert team is not None
        assert len(team.teammates) == 1

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
        assert health["project"] == project.name
        assert "wave_progress" in health
        assert "created_at" in health

    def test_disband(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        team = tm.create_team("single-file", teammates)
        tm.disband(team)
        assert tm.sm.load_formation() is None

    def test_disband_idempotent(self, project):
        tm = TeamManager(project)
        teammates = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        team = tm.create_team("single-file", teammates)
        tm.disband(team)
        tm.disband(team)  # Should not raise
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

    def test_create_multiple_teams(self, project):
        tm = TeamManager(project)
        teammates1 = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        teammates2 = {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        }
        team1 = tm.create_team("single-file", teammates1)
        team2 = tm.create_team("single-file", teammates2)
        # Both created; second overwrites formation state
        assert team1 is not None
        assert team2 is not None


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
            assert "files" in tc.ownership

    def test_team_id_includes_project(self, project):
        team = build_team_from_formation(project, "single-file")
        assert project.name in team.team_id

    def test_no_agent_overrides_uses_defaults(self, project):
        team = build_team_from_formation(project, "single-file")
        # Default agent naming convention: spec-{role_first_part}
        impl = team.teammates["implementer"]
        assert impl.agent_name != ""

    def test_formation_saved_to_session(self, project):
        build_team_from_formation(project, "single-file")
        sm = SessionManager(project)
        formation = sm.load_formation()
        assert formation is not None
        assert formation.name == "single-file"


# ── Task list isolation ──────────────────────────────────────────────────────

class TestTaskListIsolation:
    """Verify that teams with different teammates maintain isolation."""

    def test_separate_teams_have_separate_teammates(self, project):
        tm = TeamManager(project)
        team1_mates = {
            "implementer": TeammateConfig(role="implementer", agent_name="agent-a"),
        }
        team2_mates = {
            "implementer": TeammateConfig(role="implementer", agent_name="agent-b"),
        }
        team1 = tm.create_team("single-file", team1_mates)
        team2 = tm.create_team("single-file", team2_mates)
        assert team1.teammates["implementer"].agent_name == "agent-a"
        assert team2.teammates["implementer"].agent_name == "agent-b"

    def test_team_wave_progress_isolated(self, project):
        tm = TeamManager(project)
        tc = {"implementer": TeammateConfig(role="implementer", agent_name="spec-impl")}
        team1 = tm.create_team("single-file", tc)
        team2 = tm.create_team("single-file", {
            "implementer": TeammateConfig(role="implementer", agent_name="spec-impl"),
        })
        team1.wave_progress[0] = "done"
        assert team2.wave_progress.get(0) is None

    def test_disband_one_team_does_not_affect_other_object(self, project):
        tm = TeamManager(project)
        tc1 = {"implementer": TeammateConfig(role="implementer", agent_name="spec-impl")}
        tc2 = {"implementer": TeammateConfig(role="implementer", agent_name="spec-impl")}
        team1 = tm.create_team("single-file", tc1)
        team2 = tm.create_team("single-file", tc2)
        tm.disband(team1)
        # team2 object still has its teammates in memory
        assert len(team2.teammates) == 1
