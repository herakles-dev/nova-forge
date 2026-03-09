"""Nova Forge Teams — multi-agent team spawning and coordination.

Manages team creation, agent spawning with formation roles, ownership
boundaries, wave-based execution ordering, and teammate lifecycle.

Usage::

    from forge_teams import TeamManager

    tm = TeamManager(project_root=Path("./myapp"))
    team = tm.create_team("feature-impl", agents={...})
    tm.spawn_wave(team, wave=0)
    tm.check_health(team)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from config import ForgeProject, get_model_config, resolve_model, FORGE_DIR_NAME
from forge_agent import ForgeAgent, AgentResult
from forge_session import SessionManager, FormationState
from formations import get_formation, Formation, Role

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class TeammateConfig:
    """Configuration for a single teammate in a team."""
    role: str
    agent_name: str  # From agent registry (e.g. "backend-architect")
    model: str = ""  # Model override (empty = use formation default)
    ownership: dict = field(default_factory=lambda: {"directories": [], "files": [], "patterns": []})
    tool_policy: str = "coding"

    @property
    def agent_id(self) -> str:
        return f"forge-{self.role}-{uuid.uuid4().hex[:8]}"


@dataclass
class Team:
    """Active team state."""
    name: str
    formation_name: str
    project: str
    teammates: dict[str, TeammateConfig] = field(default_factory=dict)
    created_at: str = ""
    wave_progress: dict[int, str] = field(default_factory=dict)  # wave -> "pending"|"active"|"done"

    @property
    def team_id(self) -> str:
        return f"team-{self.name}-{self.project}"


@dataclass
class SpawnResult:
    """Result of spawning a wave of agents."""
    wave: int
    agents_spawned: int = 0
    results: dict[str, AgentResult] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def all_succeeded(self) -> bool:
        return not self.errors and all(
            r.error is None for r in self.results.values()
        )


# ── TeamManager ──────────────────────────────────────────────────────────────

class TeamManager:
    """Manages multi-agent team lifecycle."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.sm = SessionManager(self.project_root)

    def create_team(
        self,
        formation_name: str,
        teammates: dict[str, TeammateConfig],
        model_override: str | None = None,
    ) -> Team:
        """Create a new team from a formation definition.

        Args:
            formation_name: Name of the formation (e.g. "feature-impl")
            teammates: Role name → TeammateConfig mapping
            model_override: Override model for all teammates
        """
        formation = get_formation(formation_name)

        # Validate roles exist in formation
        valid_roles = {r.name for r in formation.roles}
        for role_name in teammates:
            if role_name not in valid_roles:
                logger.warning(
                    "Role '%s' not found in formation '%s' — proceeding anyway",
                    role_name, formation_name,
                )

        # Apply model override
        if model_override:
            for tc in teammates.values():
                if not tc.model:
                    tc.model = model_override

        team = Team(
            name=formation_name,
            formation_name=formation_name,
            project=self.project_root.name,
            teammates=teammates,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        # Save formation registry
        formation_state = FormationState(
            name=formation_name,
            project=self.project_root.name,
            teammates={
                role: {
                    "agent": tc.agent_name,
                    "agent_id": tc.agent_id,
                    "ownership": tc.ownership,
                }
                for role, tc in teammates.items()
            },
            started_at=team.created_at,
        )
        self.sm.save_formation(formation_state)

        logger.info(
            "Team created: %s with %d teammates for %s",
            formation_name, len(teammates), self.project_root.name,
        )

        return team

    async def spawn_wave(
        self,
        team: Team,
        wave: int,
        tasks_per_role: dict[str, list[dict]] | None = None,
        max_concurrent: int = 4,
    ) -> SpawnResult:
        """Spawn all agents for a given wave.

        Args:
            team: The active team
            wave: Wave index (0, 1, 2, ...)
            tasks_per_role: Role → list of task dicts to assign
            max_concurrent: Max parallel agent launches
        """
        formation = get_formation(team.formation_name)
        result = SpawnResult(wave=wave)
        team.wave_progress[wave] = "active"

        if wave >= len(formation.wave_order):
            result.errors.append(f"Wave {wave} exceeds formation waves ({len(formation.wave_order)})")
            return result

        wave_roles = formation.wave_order[wave]
        roles_to_spawn = {
            name: tc for name, tc in team.teammates.items()
            if name in wave_roles
        }

        if not roles_to_spawn:
            logger.warning("Wave %d has no matching teammates to spawn", wave)
            team.wave_progress[wave] = "done"
            return result

        # Spawn agents in parallel with semaphore
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _spawn_one(role_name: str, tc: TeammateConfig) -> tuple[str, AgentResult]:
            async with semaphore:
                return role_name, await self._run_agent(
                    team, role_name, tc,
                    tasks=tasks_per_role.get(role_name, []) if tasks_per_role else [],
                )

        spawn_tasks = [
            _spawn_one(role_name, tc)
            for role_name, tc in roles_to_spawn.items()
        ]

        completed = await asyncio.gather(*spawn_tasks, return_exceptions=True)

        for item in completed:
            if isinstance(item, Exception):
                result.errors.append(str(item))
            else:
                role_name, agent_result = item
                result.results[role_name] = agent_result
                result.agents_spawned += 1
                if agent_result.error:
                    result.errors.append(f"{role_name}: {agent_result.error}")

        team.wave_progress[wave] = "done"
        return result

    async def _run_agent(
        self,
        team: Team,
        role_name: str,
        tc: TeammateConfig,
        tasks: list[dict],
    ) -> AgentResult:
        """Run a single agent for a team role."""
        # Resolve model
        formation = get_formation(team.formation_name)
        role_def = next((r for r in formation.roles if r.name == role_name), None)
        model_pref = tc.model or (role_def.model if role_def else "fast")
        model_str = resolve_model(model_pref) if model_pref in ("smart", "fast") else model_pref

        # Build prompt with task assignments and ownership
        task_block = ""
        if tasks:
            task_lines = [f"- {t.get('subject', t.get('description', 'Task'))}" for t in tasks]
            task_block = "\n## Your Tasks\n" + "\n".join(task_lines)

        ownership_block = ""
        if tc.ownership:
            dirs = tc.ownership.get("directories", [])
            patterns = tc.ownership.get("patterns", [])
            if dirs or patterns:
                ownership_block = "\n## File Ownership\n"
                for d in dirs:
                    ownership_block += f"- dir: {d}\n"
                for p in patterns:
                    ownership_block += f"- pattern: {p}\n"

        prompt = (
            f"## Role\n{role_name} — {tc.agent_name}\n"
            f"\n## Project\n{team.project}\n"
            f"{task_block}"
            f"{ownership_block}"
            f"\n## Constraints\n"
            f"- Only modify files within your ownership boundaries\n"
            f"- Tool policy: {tc.tool_policy}\n"
        )

        try:
            mc = get_model_config(model_str)
            agent = ForgeAgent(
                model_config=mc,
                project_root=self.project_root,
                agent_id=tc.agent_id,
                max_turns=15,
            )
            return await agent.run(prompt=prompt)
        except Exception as exc:
            logger.error("Agent %s failed: %s", role_name, exc)
            return AgentResult(error=str(exc))

    def check_health(self, team: Team) -> dict[str, Any]:
        """Check team health status."""
        formation = self.sm.load_formation()

        return {
            "team": team.name,
            "project": team.project,
            "teammates": len(team.teammates),
            "wave_progress": dict(team.wave_progress),
            "formation_saved": formation is not None,
            "created_at": team.created_at,
        }

    def disband(self, team: Team) -> None:
        """Clean up team state."""
        self.sm.clear_formation()
        logger.info("Team disbanded: %s", team.name)


# ── Convenience: create team from formation ──────────────────────────────────

def build_team_from_formation(
    project_root: Path,
    formation_name: str,
    agent_overrides: dict[str, str] | None = None,
) -> Team:
    """High-level helper: create a team from a formation name.

    Auto-assigns default agents per role unless overridden.

    Args:
        project_root: Project path
        formation_name: Formation name
        agent_overrides: Optional role → agent_name overrides
    """
    formation = get_formation(formation_name)
    overrides = agent_overrides or {}

    teammates: dict[str, TeammateConfig] = {}
    for role in formation.roles:
        agent_name = overrides.get(role.name, f"spec-{role.name.split('-')[0]}")
        teammates[role.name] = TeammateConfig(
            role=role.name,
            agent_name=agent_name,
            tool_policy=role.tool_policy,
            ownership={"directories": role.ownership.get("directories", []), "files": [], "patterns": role.ownership.get("patterns", [])},
        )

    tm = TeamManager(project_root)
    return tm.create_team(formation_name, teammates)
