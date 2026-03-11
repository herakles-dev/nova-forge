"""Nova Forge Assistant Layer — smart session assistant with adaptive UX.

Detects user skill level, recommends optimal config (autonomy, formation, model),
provides contextual hints, and adapts verbosity to skill level.

Usage:
    assistant = ForgeAssistant(shell)
    skill = assistant.detect_skill_level()
    hint = assistant.contextual_hint("after_plan")
    console.print(assistant.welcome_message())
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    pass  # ForgeShell imported lazily to avoid circular imports

# ── Autonomy level metadata ───────────────────────────────────────────────────
# Import canonical level names from forge_guards (single source of truth)
from forge_guards import _LEVEL_NAMES  # {0: "Manual", ..., 5: "Unattended"}

_LEVEL_DESCRIPTIONS = {
    0: (
        "Manual — I ask before EVERY action, including reading files.\n"
        "  Best for: audits, sensitive projects, seeing exactly what I do.\n"
        "  Tradeoff: very slow, lots of interruptions."
    ),
    1: (
        "Guided — I read files freely, but ask before writing anything.\n"
        "  Best for: beginners, learning the ropes, reviewing output.\n"
        "  Tradeoff: requires approval for each file write."
    ),
    2: (
        "Supervised — I read and write files freely, but ask before\n"
        "  running commands, database changes, or risky operations.\n"
        "  Best for: most users — good balance of speed and safety.\n"
        "  This is the recommended default."
    ),
    3: (
        "Trusted — I handle nearly everything independently. I only\n"
        "  stop for operations that could permanently delete data.\n"
        "  Best for: experienced devs who trust the output.\n"
        "  Tradeoff: less oversight."
    ),
    4: (
        "Autonomous — Full autopilot. I do everything without asking,\n"
        "  including destructive operations.\n"
        "  Best for: expert users, throwaway environments.\n"
        "  Warning: no guardrails."
    ),
    5: (
        "Unattended — Like Autonomous, but optimized for CI/CD and\n"
        "  background execution. Full audit logging, no interactive prompts.\n"
        "  Best for: automated pipelines, overnight batch builds.\n"
        "  Warning: no guardrails, enhanced logging."
    ),
}

_LEVEL_CAPABILITIES = {
    0: {
        "can": [],
        "asks": ["read files", "write files", "run commands", "all operations"],
    },
    1: {
        "can": ["read files freely"],
        "asks": ["write files", "run commands", "destructive operations"],
    },
    2: {
        "can": ["read files freely", "write files freely", "run safe commands"],
        "asks": ["destructive commands", "system-level operations"],
    },
    3: {
        "can": ["read files freely", "write files freely", "run most commands"],
        "asks": ["permanent data deletion"],
    },
    4: {
        "can": ["everything — no interruptions"],
        "asks": [],
    },
    5: {
        "can": ["everything — no interruptions", "enhanced audit logging"],
        "asks": [],
    },
}

# ── Formation descriptions (human-friendly) ──────────────────────────────────

# Formation names must match keys in formations.FORMATIONS exactly:
# single-file, lightweight-feature, feature-impl, new-project,
# bug-investigation, security-review, perf-optimization, code-review
_FORMATION_DESCRIPTIONS = {
    "single-file": (
        "One agent works on a single file. Best for small, focused edits "
        "like config changes or fixing a specific function."
    ),
    "lightweight-feature": (
        "One implementer + one tester working together. Good for small "
        "frontend-only or backend-only features (4-8 tasks)."
    ),
    "feature-impl": (
        "Backend and frontend agents work in parallel, then an integrator "
        "connects them. The standard formation for most projects."
    ),
    "new-project": (
        "Architect designs the structure first, then backend/frontend build "
        "in parallel, followed by integration and testing. Best for new apps."
    ),
    "bug-investigation": (
        "Three investigator agents hunt the bug from different angles: "
        "code trace, logs/metrics, and minimal reproduction."
    ),
    "security-review": (
        "Threat modeler + scanner + fixer pipeline. Good for reviewing "
        "auth flows, API security, and dependency vulnerabilities."
    ),
    "perf-optimization": (
        "Performance profiling + optimization pipeline. Identifies bottlenecks, "
        "proposes fixes, and benchmarks the improvements."
    ),
    "code-review": (
        "Three reviewers check security, performance, and test coverage "
        "in parallel, each focused on their specialty."
    ),
}

# ── Contextual hints ──────────────────────────────────────────────────────────

_HINTS: dict[str, str] = {
    "after_plan": (
        "Tip: /tasks shows the full breakdown. "
        "You can edit spec.md before building to adjust the plan."
    ),
    "after_build_pass": (
        "Nice! Use /preview for a shareable Cloudflare URL, "
        "or just describe what to add next."
    ),
    "after_build_fail": (
        "Try /build to retry failed tasks. "
        "Or describe the problem — I'll help fix it."
    ),
    "first_build": (
        "Tip: /status shows overall progress. /tasks shows each task. "
        "/build retries anything that failed."
    ),
    "no_credentials": (
        "You need API keys to use Nova. Run /login to set them up, "
        "or source ~/.secrets/hercules.env if you already have them."
    ),
    "model_choice": (
        "Nova models are fast and free-tier friendly. "
        "Gemini Flash is great for longer projects. "
        "Switch anytime with /model <name>."
    ),
    "formation_intro": (
        "Formations are pre-built agent team patterns. "
        "Nova picks the right one automatically, "
        "but you can override with /formation."
    ),
    "autonomy_intro": (
        "Autonomy levels control how often I ask for approval. "
        "Type /autonomy to see your current level and change it."
    ),
    "after_preview": (
        "Your project is live! Share the URL with anyone. "
        "Type /preview stop to shut it down."
    ),
    "returning_expert": (
        "Back again. /status to see where you left off."
    ),
}


# ── ForgeAssistant ────────────────────────────────────────────────────────────

class ForgeAssistant:
    """Smart session assistant — detects skill level, recommends config,
    provides contextual hints, and adapts verbosity to skill level.
    """

    def __init__(self, shell: "object") -> None:
        self.shell = shell
        self.skill_level: str = "beginner"  # beginner | intermediate | expert
        self.tips_shown: set[str] = set()
        self.interaction_count: int = 0
        self._skill_detected: bool = False

    # ── Skill detection ───────────────────────────────────────────────────────

    def detect_skill_level(self) -> str:
        """Detect user skill level from context signals (sync — reads files only)."""
        shell = self.shell
        signals = 0

        # Signal 1: builds completed
        builds = getattr(shell, "state", {}).get("builds_completed", 0)
        if builds >= 5:
            signals += 2
        elif builds >= 2:
            signals += 1

        # Signal 2: has env/credentials files (knows what they are)
        env_paths = [
            Path.home() / ".secrets" / "hercules.env",
            Path.home() / ".forge" / "credentials.env",
            Path.home() / ".env",
        ]
        if any(p.exists() for p in env_paths):
            signals += 1

        # Signal 3: project complexity — has a spec.md with many lines
        project_path = getattr(shell, "project_path", None)
        if project_path:
            spec = Path(project_path) / "spec.md"
            if spec.exists():
                try:
                    lines = len(spec.read_text(encoding="utf-8", errors="replace").splitlines())
                    if lines > 100:
                        signals += 1
                    if lines > 200:
                        signals += 1
                except OSError:
                    pass

        # Signal 4: multiple recent projects (power user)
        recent = getattr(shell, "state", {}).get("recent_projects", [])
        if len(recent) >= 5:
            signals += 2
        elif len(recent) >= 2:
            signals += 1

        # Signal 5: non-default model is saved (knows about model selection)
        config = getattr(shell, "config", {})
        if config.get("default_model") and config["default_model"] not in ("nova-lite", ""):
            signals += 1

        # Classify
        if signals >= 5:
            level = "expert"
        elif signals >= 2:
            level = "intermediate"
        else:
            level = "beginner"

        self.skill_level = level
        self._skill_detected = True
        return level

    def set_skill_level(self, level: str) -> None:
        """Explicitly set skill level (e.g., from user answer)."""
        if level in ("beginner", "intermediate", "expert"):
            self.skill_level = level
            self._skill_detected = True

    # ── Recommendations ───────────────────────────────────────────────────────

    def get_autonomy_recommendation(self) -> tuple[int, str]:
        """Recommend autonomy level based on skill + project risk.

        Returns (level: int, reason: str).
        """
        if self.skill_level == "expert":
            return (3, "You have experience — A3 (Trusted) lets you move fast with minimal interruptions.")
        elif self.skill_level == "intermediate":
            return (2, "A2 (Supervised) is the sweet spot — you can write files freely, "
                        "but I'll ask before risky commands.")
        else:
            return (1, "A1 (Guided) means I'll show you each file before writing it. "
                        "Good for learning what I'm doing.")

    def get_formation_recommendation(self, goal: str) -> tuple[str, str]:
        """Recommend formation based on project description keywords.

        Returns (formation_name: str, reason: str).
        Formation names match keys in formations.FORMATIONS exactly.
        """
        lower = goal.lower()

        # Security — check before debug to avoid "fix a security bug" matching debug
        if any(kw in lower for kw in ["security", "audit", "vulnerability", "penetration"]):
            return ("security-review", "Security-review formation: threat model + scanner + fixer pipeline.")

        # Debug / bug hunt — "fix" only matches with debug-adjacent words
        if any(kw in lower for kw in ["debug", "bug", "trace", "investigate"]):
            return ("bug-investigation", "Bug-investigation formation sends three investigators after the bug.")
        if "fix" in lower and any(kw in lower for kw in ["error", "crash", "broken", "failing"]):
            return ("bug-investigation", "Bug-investigation formation: three angles on the problem.")

        # Performance
        if any(kw in lower for kw in ["performance", "optimize", "slow", "bottleneck", "profil"]):
            return ("perf-optimization", "Perf-optimization formation: profile, fix, benchmark.")

        # Simple script or CLI — relaxed word count gate
        if any(kw in lower for kw in ["script", "cli tool", "small utility", "simple tool"]):
            return ("single-file", "Single-file formation: one agent, no coordination overhead.")

        # Large full-stack project — requires BOTH backend AND frontend keywords
        has_backend = any(kw in lower for kw in ["backend", "server", "flask", "fastapi", "django", "express"])
        has_frontend = any(kw in lower for kw in ["ui", "frontend", "dashboard", "website", "react", "html", "vue", "svelte"])
        if has_backend and has_frontend:
            return ("new-project", "New-project formation: architect first, then parallel backend/frontend teams.")

        # Explicit full-stack description
        if "full-stack" in lower or "full stack" in lower:
            return ("new-project", "New-project formation: architect first, then parallel backend/frontend teams.")

        # Backend API or service (single-layer)
        if any(kw in lower for kw in ["api", "rest", "service", "endpoint", "microservice", "backend", "database"]):
            return ("feature-impl", "Feature-impl formation: parallel backend/frontend then integration.")

        # Default
        return ("feature-impl", "Feature-impl is the standard formation for most projects.")

    def get_model_recommendation(self) -> tuple[str, str]:
        """Recommend model based on available credentials and project size.

        Returns (model_alias: str, reason: str).
        """
        # Use forge_cli's provider detection if available, else check env directly
        available: list[str] = []
        try:
            from config import MODEL_ALIASES, get_provider
            for alias, full_id in MODEL_ALIASES.items():
                provider = get_provider(full_id)
                # Map provider to required env vars
                env_map = {
                    "bedrock": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
                    "openai": ["OPENROUTER_API_KEY"],
                    "anthropic": ["ANTHROPIC_API_KEY"],
                }
                env_vars = env_map.get(provider, [])
                if all(os.environ.get(v) for v in env_vars):
                    available.append(alias)
        except ImportError:
            pass

        if not available:
            return ("nova-lite", "Nova Lite is the default. Run /login to configure a provider.")

        # Prefer nova-lite for beginners (familiar), gemini-flash for experts (speed)
        if self.skill_level == "expert" and "gemini-flash" in available:
            return ("gemini-flash", "Gemini Flash: 1M context, very fast, good for large projects.")
        if "nova-lite" in available:
            return ("nova-lite", "Nova Lite: the recommended starting point, backed by Amazon Bedrock.")
        if "gemini-flash" in available:
            return ("gemini-flash", "Gemini Flash is available and works well for most projects.")
        return (available[0], f"{available[0]} is available and ready to use.")

    # ── Contextual hints ──────────────────────────────────────────────────────

    def contextual_hint(self, context: str) -> Optional[str]:
        """Return a contextual hint string, or None if already shown or rate-limited.

        context values: after_plan, after_build_pass, after_build_fail,
                        first_build, no_credentials, model_choice,
                        formation_intro, autonomy_intro, after_preview,
                        returning_expert
        """
        if context in self.tips_shown:
            return None
        hint = _HINTS.get(context)
        if hint is None:
            return None

        # Beginners get more hints; experts only see actionable ones
        if self.skill_level == "expert":
            if context not in ("after_build_fail", "returning_expert", "after_preview"):
                return None

        self.tips_shown.add(context)
        return hint

    # ── Autonomy explanations ─────────────────────────────────────────────────

    def explain_autonomy(self, level: int) -> str:
        """Return a human-friendly explanation of an autonomy level."""
        if level not in _LEVEL_DESCRIPTIONS:
            return f"Unknown level {level}"
        name = _LEVEL_NAMES.get(level, str(level))
        return f"A{level} ({name}): {_LEVEL_DESCRIPTIONS[level]}"

    def explain_all_autonomy_levels(self) -> str:
        """Return a multi-line explanation of all autonomy levels (A0-A5)."""
        lines = []
        for lvl in sorted(_LEVEL_NAMES.keys()):
            name = _LEVEL_NAMES[lvl]
            desc = _LEVEL_DESCRIPTIONS.get(lvl, "").split("\n")[0]
            lines.append(f"  A{lvl} {name:12s} — {desc}")
        return "\n".join(lines)

    def format_autonomy_bar(self, level: int) -> str:
        """Return a visual autonomy level bar string (Rich-formatted).

        Example: 'Autonomy: [██░░░] A2 Supervised'
        """
        total = 5  # A0 through A5
        filled = min(level, total)
        empty = total - filled
        bar = "█" * filled + "░" * empty
        name = _LEVEL_NAMES.get(level, str(level))
        return f"[cyan]{bar}[/] A{level} [bold]{name}[/]"

    def get_autonomy_capabilities(self, level: int) -> tuple[list[str], list[str]]:
        """Return (can_do, asks_about) lists for the given level."""
        caps = _LEVEL_CAPABILITIES.get(level, {"can": [], "asks": []})
        return caps["can"], caps["asks"]

    # ── Formation explanations ────────────────────────────────────────────────

    def explain_formation(self, name: str) -> str:
        """Return a human-friendly explanation of a formation."""
        return _FORMATION_DESCRIPTIONS.get(
            name,
            f"Formation '{name}' — see /formation {name} for details."
        )

    # ── Welcome messages ──────────────────────────────────────────────────────

    def welcome_message(self) -> str:
        """Return a skill-level-appropriate welcome message (Rich markup)."""
        if self.skill_level == "expert":
            return (
                "[bold bright_white]Welcome back.[/]  [muted]Type to build, /help for commands.[/]"
            )
        elif self.skill_level == "intermediate":
            return (
                "[bold bright_white]Welcome back to Nova Forge![/]\n\n"
                "  Describe what you want to build — Nova plans and builds it.\n"
                "  [hint]New: /autonomy — control how much Nova asks for approval[/]\n"
                "  [hint]New: /guide — smart setup wizard[/]"
            )
        else:
            return (
                "[bold bright_white]Welcome to Nova Forge![/]\n\n"
                "  Nova Forge is an AI build system. You describe what you want,\n"
                "  and Nova — an AI agent — writes the code, start to finish.\n\n"
                "  [step]Getting started:[/]\n"
                "    [accent]1.[/]  Just type what you want to build\n"
                "    [accent]2.[/]  Nova will plan tasks and ask if you're ready\n"
                "    [accent]3.[/]  Nova builds everything, wave by wave\n"
                "    [accent]4.[/]  You get a working, runnable project\n\n"
                "  [hint]Tip: Start with something small, like \"a task list app\".[/]"
            )

    # ── Post-event guidance ───────────────────────────────────────────────────

    def post_plan_guidance(self, task_count: int, wave_count: int) -> str:
        """What to tell the user after planning completes."""
        if self.skill_level == "beginner":
            return (
                f"Nova created {task_count} tasks across {wave_count} wave(s).\n"
                f"  A 'wave' is a batch of tasks Nova runs in parallel.\n"
                f"  Type [accent]/build[/] to start — or [accent]/tasks[/] to review the plan first."
            )
        elif self.skill_level == "intermediate":
            return (
                f"{task_count} tasks, {wave_count} wave(s). "
                f"[accent]/tasks[/] to review, [accent]/build[/] to go."
            )
        else:
            return f"{task_count} tasks / {wave_count} waves — [accent]/build[/] to go"

    def post_build_guidance(self, passed: int, failed: int, total: int) -> str:
        """What to tell the user after build completes."""
        if failed == 0:
            if self.skill_level == "beginner":
                return (
                    f"Build complete! All {total} tasks passed.\n"
                    f"  [accent]/preview[/] — get a live URL to share\n"
                    f"  Tell me to add features or fix anything\n"
                    f"  [accent]cd {getattr(self.shell, 'project_path', '.')}[/] to explore your files"
                )
            else:
                return f"{total}/{total} passed. [accent]/preview[/] or describe what to add next."
        elif passed > 0:
            if self.skill_level == "beginner":
                return (
                    f"{passed} tasks passed, {failed} failed.\n"
                    f"  The core functionality likely works.\n"
                    f"  Type [accent]/build[/] to retry the failed parts,\n"
                    f"  or describe what went wrong and I'll fix it."
                )
            else:
                return f"{passed} pass / {failed} fail — [accent]/build[/] to retry."
        else:
            return "Build had issues. Type [accent]/tasks[/] to see what went wrong."

    # ── Autonomy state reading ────────────────────────────────────────────────

    def _get_autonomy_file(self) -> Path | None:
        """Get the correct autonomy state file path via ForgeProject."""
        project_path = getattr(self.shell, "project_path", None)
        if project_path is None:
            return None
        try:
            from config import ForgeProject
            fp = ForgeProject(Path(project_path))
            return fp.autonomy_file  # .forge/state/autonomy.json
        except Exception:
            # Fallback to known canonical path
            return Path(project_path) / ".forge" / "state" / "autonomy.json"

    def read_autonomy_level(self) -> int:
        """Read current autonomy level from the state file.

        Returns the level integer (0-5), defaults to 2 if not found.
        """
        state_file = self._get_autonomy_file()
        if state_file is None or not state_file.exists():
            return 2
        try:
            data = json.loads(state_file.read_text())
            return int(data.get("level", 2))
        except (json.JSONDecodeError, OSError, ValueError):
            return 2

    def set_autonomy_level(self, level: int, reason: str = "user request") -> bool:
        """Set autonomy level via AutonomyManager's public API.

        Level is clamped to [0, 5]. Returns True on success.
        """
        state_file = self._get_autonomy_file()
        if state_file is None:
            return False
        try:
            from forge_guards import AutonomyManager
            state_file.parent.mkdir(parents=True, exist_ok=True)
            mgr = AutonomyManager(state_file)
            mgr.set_level(level, reason=reason)
            return True
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Failed to set autonomy level: %s", e)
            return False
