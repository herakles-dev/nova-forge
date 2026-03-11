"""Nova Forge Session Manager — session lifecycle, state persistence, handoff.

Manages the session directory (.forge/), task state snapshots, formation
registries, autonomy state, user profiles, and handoff context generation.
Provides the persistence layer that survives across agent invocations and
context clears.

V9 additions: UserProfile dataclass for skill-level-aware defaults, profile
persistence, and experience-based skill progression.

Usage::

    from forge_session import SessionManager

    sm = SessionManager(project_root=Path("./myapp"))
    sm.init()                          # Create .forge/ structure
    sm.save_autonomy(level=2, ...)     # Persist autonomy state
    sm.save_formation_registry(...)    # Persist formation
    profile = sm.load_profile()        # Load user profile
    context = sm.handoff()             # Generate continuation context
    report = sm.status()               # Get progress report
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock

from config import ForgeProject, init_forge_dir, FORGE_DIR_NAME

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class SessionStatus:
    """Snapshot of session progress."""
    project_name: str
    total_tasks: int = 0
    completed: int = 0
    in_progress: int = 0
    pending: int = 0
    failed: int = 0
    blocked: int = 0
    autonomy_level: int = 0
    formation: str = ""
    last_updated: str = ""
    session_writes: int = 0

    @property
    def percent(self) -> float:
        if self.total_tasks == 0:
            return 0.0
        return (self.completed / self.total_tasks) * 100


@dataclass
class AutonomyState:
    """Persistent autonomy trust state."""
    level: int = 2
    successful: int = 0
    errors: int = 0
    rollbacks: int = 0
    approved_categories: list[str] = field(default_factory=list)
    grants: list[dict] = field(default_factory=list)
    recent_errors: list[dict] = field(default_factory=list)
    last_escalation: str = ""
    last_deescalation: str = ""

    def to_dict(self) -> dict:
        return {
            "level": self.level,
            "successful": self.successful,
            "errors": self.errors,
            "rollbacks": self.rollbacks,
            "approved_categories": self.approved_categories,
            "grants": self.grants,
            "recent_errors": self.recent_errors,
            "last_escalation": self.last_escalation,
            "last_deescalation": self.last_deescalation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> AutonomyState:
        return cls(
            level=data.get("level", 0),
            successful=data.get("successful", 0),
            errors=data.get("errors", 0),
            rollbacks=data.get("rollbacks", 0),
            approved_categories=data.get("approved_categories", []),
            grants=data.get("grants", []),
            recent_errors=data.get("recent_errors", []),
            last_escalation=data.get("last_escalation", ""),
            last_deescalation=data.get("last_deescalation", ""),
        )


@dataclass
class FormationState:
    """Active formation tracking."""
    name: str
    project: str
    teammates: dict[str, dict] = field(default_factory=dict)
    tool_policies: dict = field(default_factory=dict)
    started_at: str = ""

    def to_dict(self) -> dict:
        return {
            "formation": self.name,
            "project": self.project,
            "teammates": self.teammates,
            "tool_policies": self.tool_policies,
            "started_at": self.started_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FormationState:
        return cls(
            name=data.get("formation", ""),
            project=data.get("project", ""),
            teammates=data.get("teammates", {}),
            tool_policies=data.get("tool_policies", {}),
            started_at=data.get("started_at", ""),
        )


@dataclass
class UserProfile:
    """Persistent user preferences that affect session behavior.

    Tracks skill level, preferred settings, and build experience.
    Stored in .forge/profile.json (project-level) or ~/.forge/profile.json (global).
    """
    skill_level: str = "beginner"        # beginner, intermediate, expert
    preferred_autonomy: int = 2          # Default A2 (Supervised)
    preferred_model: str = "nova-lite"
    preferred_formation: str = "auto"
    builds_completed: int = 0
    builds_failed: int = 0
    verbosity: str = "normal"            # minimal, normal, verbose
    show_explanations: bool = True       # Show beginner-friendly explanations

    def to_dict(self) -> dict:
        return {
            "skill_level": self.skill_level,
            "preferred_autonomy": self.preferred_autonomy,
            "preferred_model": self.preferred_model,
            "preferred_formation": self.preferred_formation,
            "builds_completed": self.builds_completed,
            "builds_failed": self.builds_failed,
            "verbosity": self.verbosity,
            "show_explanations": self.show_explanations,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        return cls(
            skill_level=d.get("skill_level", "beginner"),
            preferred_autonomy=d.get("preferred_autonomy", 2),
            preferred_model=d.get("preferred_model", "nova-lite"),
            preferred_formation=d.get("preferred_formation", "auto"),
            builds_completed=d.get("builds_completed", 0),
            builds_failed=d.get("builds_failed", 0),
            verbosity=d.get("verbosity", "normal"),
            show_explanations=d.get("show_explanations", True),
        )


# ── SessionManager ──────────────────────────────────────────────────────────

class SessionManager:
    """Manages session lifecycle and persistent state for a Forge project.

    State files live in ``.forge/state/``:
    - ``task-state.json`` — aggregate task counts
    - ``autonomy.json`` — trust level + history
    - ``formation-registry.json`` — active formation
    - ``session-meta.json`` — session metadata
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project = ForgeProject(root=project_root)

    # ── Initialization ───────────────────────────────────────────────────────

    def init(self) -> ForgeProject:
        """Create .forge/ directory structure if it doesn't exist."""
        return init_forge_dir(self.project.root)

    def is_initialized(self) -> bool:
        """Check if .forge/ directory exists."""
        return self.project.forge_dir.is_dir()

    # ── Task State ───────────────────────────────────────────────────────────

    def load_task_state(self) -> dict:
        """Load task state from .forge/state/task-state.json."""
        state_file = self.project.state_dir / "task-state.json"
        if not state_file.exists():
            return {
                "project": self.project.name,
                "total": 0, "completed": 0, "pending": 0,
                "in_progress": 0, "failed": 0, "blocked": 0,
                "last_updated": "",
            }

        lock = FileLock(str(state_file) + ".lock", timeout=2)
        try:
            with lock:
                return json.loads(state_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load task state: %s", exc)
            return {"project": self.project.name, "total": 0, "completed": 0,
                    "pending": 0, "in_progress": 0, "failed": 0, "blocked": 0}

    def save_task_state(self, state: dict) -> None:
        """Save task state with file locking."""
        self.project.state_dir.mkdir(parents=True, exist_ok=True)
        state_file = self.project.state_dir / "task-state.json"
        state["last_updated"] = datetime.now(timezone.utc).isoformat()

        lock = FileLock(str(state_file) + ".lock", timeout=2)
        with lock:
            state_file.write_text(json.dumps(state, indent=2) + "\n")

    # ── Autonomy State ───────────────────────────────────────────────────────

    def load_autonomy(self) -> AutonomyState:
        """Load autonomy trust state."""
        if not self.project.autonomy_file.exists():
            return AutonomyState()

        try:
            data = json.loads(self.project.autonomy_file.read_text())
            return AutonomyState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return AutonomyState()

    def save_autonomy(self, state: AutonomyState) -> None:
        """Persist autonomy state."""
        self.project.state_dir.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(self.project.autonomy_file) + ".lock", timeout=2)
        with lock:
            self.project.autonomy_file.write_text(
                json.dumps(state.to_dict(), indent=2) + "\n"
            )

    # ── User Profile ────────────────────────────────────────────────────────

    def save_profile(self, profile: UserProfile) -> None:
        """Save user profile to .forge/profile.json."""
        self.project.state_dir.mkdir(parents=True, exist_ok=True)
        profile_file = self.project.state_dir / "profile.json"
        lock = FileLock(str(profile_file) + ".lock", timeout=2)
        with lock:
            profile_file.write_text(
                json.dumps(profile.to_dict(), indent=2) + "\n"
            )

    def load_profile(self) -> UserProfile:
        """Load user profile from .forge/profile.json.

        Falls back to global profile (~/.forge/profile.json) if project-level
        profile does not exist, then to defaults.
        """
        # Project-level profile
        profile_file = self.project.state_dir / "profile.json"
        if profile_file.exists():
            try:
                data = json.loads(profile_file.read_text())
                return UserProfile.from_dict(data)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load project profile: %s", exc)

        # Global profile fallback
        try:
            from config import load_global_profile
            global_data = load_global_profile()
            if global_data:
                return UserProfile.from_dict(global_data)
        except Exception:
            pass

        return UserProfile()

    def update_profile_after_build(
        self,
        profile: UserProfile,
        passed: int,
        failed: int,
    ) -> UserProfile:
        """Update profile after a build — track experience, potentially upgrade skill level.

        Progression rules:
        - After 3 successful builds (0 failures): beginner -> intermediate
        - After 10 successful builds (0 failures): intermediate -> expert
        - Failures do not reset progress but do not count toward thresholds.

        Returns the updated profile (also saves it).
        """
        if passed > 0 and failed == 0:
            profile.builds_completed += 1
        elif failed > 0:
            profile.builds_failed += 1

        # Skill progression
        old_skill = profile.skill_level
        if profile.skill_level == "beginner" and profile.builds_completed >= 3:
            profile.skill_level = "intermediate"
            profile.preferred_autonomy = max(profile.preferred_autonomy, 2)
            logger.info("Skill level upgraded: beginner -> intermediate (after %d builds)", profile.builds_completed)
        elif profile.skill_level == "intermediate" and profile.builds_completed >= 10:
            profile.skill_level = "expert"
            profile.preferred_autonomy = max(profile.preferred_autonomy, 3)
            logger.info("Skill level upgraded: intermediate -> expert (after %d builds)", profile.builds_completed)

        self.save_profile(profile)
        return profile

    # ── Formation Registry ───────────────────────────────────────────────────

    def load_formation(self) -> Optional[FormationState]:
        """Load active formation registry."""
        reg_file = self.project.state_dir / "formation-registry.json"
        if not reg_file.exists():
            return None

        try:
            data = json.loads(reg_file.read_text())
            return FormationState.from_dict(data)
        except (json.JSONDecodeError, OSError):
            return None

    def save_formation(self, state: FormationState) -> None:
        """Persist formation registry."""
        self.project.state_dir.mkdir(parents=True, exist_ok=True)
        reg_file = self.project.state_dir / "formation-registry.json"

        lock = FileLock(str(reg_file) + ".lock", timeout=2)
        with lock:
            reg_file.write_text(json.dumps(state.to_dict(), indent=2) + "\n")

    def clear_formation(self) -> None:
        """Remove formation registry (session ended)."""
        reg_file = self.project.state_dir / "formation-registry.json"
        if reg_file.exists():
            reg_file.unlink()

    # ── Session Metadata ─────────────────────────────────────────────────────

    def load_session_meta(self) -> dict:
        """Load session metadata."""
        meta_file = self.project.state_dir / "session-meta.json"
        if not meta_file.exists():
            return {}
        try:
            return json.loads(meta_file.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def save_session_meta(self, meta: dict) -> None:
        """Save session metadata."""
        self.project.state_dir.mkdir(parents=True, exist_ok=True)
        meta_file = self.project.state_dir / "session-meta.json"
        meta["last_updated"] = datetime.now(timezone.utc).isoformat()
        meta_file.write_text(json.dumps(meta, indent=2) + "\n")

    # ── Status Report ────────────────────────────────────────────────────────

    def status(self) -> SessionStatus:
        """Generate a status report for the session."""
        task_state = self.load_task_state()
        autonomy = self.load_autonomy()
        formation = self.load_formation()

        return SessionStatus(
            project_name=self.project.name,
            total_tasks=task_state.get("total", 0),
            completed=task_state.get("completed", 0),
            in_progress=task_state.get("in_progress", 0),
            pending=task_state.get("pending", 0),
            failed=task_state.get("failed", 0),
            blocked=task_state.get("blocked", 0),
            autonomy_level=autonomy.level,
            formation=formation.name if formation else "",
            last_updated=task_state.get("last_updated", ""),
        )

    # ── Handoff Context ──────────────────────────────────────────────────────

    def handoff(self) -> str:
        """Generate continuation context for session handoff.

        This produces a markdown block that captures enough state for a new
        session to resume where this one left off.
        """
        status = self.status()
        autonomy = self.load_autonomy()
        formation = self.load_formation()

        lines = [
            f"# Forge Session Handoff: {self.project.name}",
            f"",
            f"## Progress",
            f"- Tasks: {status.completed}/{status.total_tasks} ({status.percent:.0f}%)",
            f"- In Progress: {status.in_progress}",
            f"- Pending: {status.pending}",
            f"- Failed: {status.failed}",
            f"- Blocked: {status.blocked}",
            f"",
            f"## Autonomy",
            f"- Level: A{autonomy.level}",
            f"- Successful: {autonomy.successful}",
            f"- Errors: {autonomy.errors}",
            f"- Categories: {', '.join(autonomy.approved_categories) or 'none'}",
        ]

        if formation:
            lines.extend([
                f"",
                f"## Formation",
                f"- Name: {formation.name}",
                f"- Teammates: {len(formation.teammates)}",
            ])
            for role, info in formation.teammates.items():
                agent = info.get("agent", "?")
                agent_id = info.get("agent_id", "?")
                lines.append(f"  - {role}: {agent} ({agent_id})")

        # Load recent audit entries
        audit_file = self.project.audit_dir / "audit.jsonl"
        if audit_file.exists():
            try:
                all_lines = audit_file.read_text().strip().split("\n")
                recent = all_lines[-5:]
                lines.extend(["", "## Recent Activity"])
                for line in recent:
                    entry = json.loads(line)
                    lines.append(
                        f"- {entry.get('timestamp', '?')[:19]} | "
                        f"{entry.get('tool', '?')} | {entry.get('outcome', '?')}"
                    )
            except (json.JSONDecodeError, OSError):
                pass

        # Include user profile if available
        profile = self.load_profile()
        if profile.skill_level != "beginner" or profile.builds_completed > 0:
            lines.extend([
                "",
                "## User Profile",
                f"- Skill Level: {profile.skill_level}",
                f"- Builds Completed: {profile.builds_completed}",
                f"- Preferred Autonomy: A{profile.preferred_autonomy}",
                f"- Verbosity: {profile.verbosity}",
            ])

        lines.extend([
            "",
            "## Next Steps",
            "Continue from where the previous session left off.",
            f"Project root: {self.project.root}",
            f"Last updated: {status.last_updated or 'never'}",
        ])

        return "\n".join(lines)

    # ── Artifacts ────────────────────────────────────────────────────────────

    def store_artifact(self, task_id: str, name: str, content: str) -> Path:
        """Store an artifact for a task."""
        artifact_dir = self.project.artifacts_dir / task_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = artifact_dir / name
        artifact_path.write_text(content)
        return artifact_path

    def load_artifact(self, task_id: str, name: str) -> Optional[str]:
        """Load a task artifact."""
        artifact_path = self.project.artifacts_dir / task_id / name
        if not artifact_path.exists():
            return None
        return artifact_path.read_text()

    def list_artifacts(self, task_id: str) -> list[str]:
        """List artifact names for a task."""
        artifact_dir = self.project.artifacts_dir / task_id
        if not artifact_dir.exists():
            return []
        return sorted(f.name for f in artifact_dir.iterdir() if f.is_file())

    # ── Compliance Check ─────────────────────────────────────────────────────

    def check_compliance(self) -> list[tuple[str, bool, str]]:
        """Run basic V11 compliance gates.

        Returns list of (gate_name, passed, detail).
        """
        gates: list[tuple[str, bool, str]] = []

        # Gate 1: .forge/ directory exists
        gates.append((
            "forge_dir",
            self.project.forge_dir.is_dir(),
            str(self.project.forge_dir),
        ))

        # Gate 2: state/ directory exists
        gates.append((
            "state_dir",
            self.project.state_dir.is_dir(),
            str(self.project.state_dir),
        ))

        # Gate 3: audit/ directory exists
        gates.append((
            "audit_dir",
            self.project.audit_dir.is_dir(),
            str(self.project.audit_dir),
        ))

        # Gate 4: settings.json exists
        gates.append((
            "settings_file",
            self.project.settings_file.exists(),
            str(self.project.settings_file),
        ))

        # Gate 5: autonomy state initialized
        gates.append((
            "autonomy_state",
            self.project.autonomy_file.exists(),
            str(self.project.autonomy_file),
        ))

        # Gate 6: task state accessible
        task_state_file = self.project.state_dir / "task-state.json"
        gates.append((
            "task_state",
            task_state_file.exists(),
            str(task_state_file),
        ))

        # Gate 7: FORGE.md exists
        gates.append((
            "forge_md",
            self.project.forge_md.exists(),
            str(self.project.forge_md),
        ))

        # Gate 8: schemas accessible
        schemas_dir = Path(__file__).parent / "schemas"
        gates.append((
            "schemas_accessible",
            schemas_dir.is_dir() and any(schemas_dir.glob("*.json")),
            str(schemas_dir),
        ))

        # Gate 9: agents accessible
        agents_dir = Path(__file__).parent / "agents"
        gates.append((
            "agents_accessible",
            agents_dir.is_dir() and any(agents_dir.glob("*.yml")),
            str(agents_dir),
        ))

        # Gate 10: no legacy artifacts
        has_legacy = (
            (self.project.root / "spec.yml").exists()
            or (self.project.root / "state.md").exists()
        )
        gates.append((
            "no_legacy",
            not has_legacy,
            "spec.yml or state.md found" if has_legacy else "clean",
        ))

        return gates

    def is_compliant(self) -> bool:
        """Quick compliance check — all gates pass."""
        return all(passed for _, passed, _ in self.check_compliance())

    # ── Auto-fix ─────────────────────────────────────────────────────────────

    def auto_fix(self) -> list[str]:
        """Fix auto-fixable compliance gates. Returns list of fixes applied."""
        fixes: list[str] = []

        # Fix gate 1-3: create directories
        if not self.project.forge_dir.is_dir():
            init_forge_dir(self.project.root)
            fixes.append("Created .forge/ directory structure")

        # Fix gate 5: initialize autonomy
        if not self.project.autonomy_file.exists():
            self.save_autonomy(AutonomyState(level=2))
            fixes.append("Initialized autonomy state at A2 (Supervised)")

        # Fix gate 4: create minimal settings.json
        if not self.project.settings_file.exists():
            settings = {
                "hooks": {},
                "forge_version": "1.0",
                "created": datetime.now(timezone.utc).isoformat(),
            }
            self.project.settings_file.write_text(json.dumps(settings, indent=2) + "\n")
            fixes.append("Created settings.json")

        # Fix gate 7: create minimal FORGE.md
        if not self.project.forge_md.exists():
            self.project.forge_md.write_text(
                f"# {self.project.name}\n\n"
                f"Managed by Nova Forge.\n"
            )
            fixes.append("Created FORGE.md")

        return fixes
