#!/usr/bin/env python3
"""Nova Forge Interactive CLI — your AI build assistant.

Launch:  python forge_cli.py
    or:  forge chat

Describe what you want. Nova builds it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style as PTStyle

from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich import box
from rich.columns import Columns
from rich.rule import Rule

from forge_theme import (
    console, gradient_text, status_bar, file_tree, wave_header,
    SPINNERS, BRAND,
)

from forge_prompt import (
    ask_select, ask_confirm, ask_text, ask_checkbox,
    ask_text_optional, build_model_choices, Separator,
)
from questionary import Choice
from forge_assistant import ForgeAssistant

# ── Setup ────────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    MODEL_ALIASES, DEFAULT_MODELS, resolve_model, get_model_config, get_provider,
    ForgeProject, init_forge_dir, compute_turn_budget,
)

logger = logging.getLogger("forge.cli")

# ── Theme (from forge_theme.py) ──────────────────────────────────────────────

PT_STYLE = PTStyle.from_dict({
    "prompt": "#c084fc bold",
    "": "#e4e4f0",
})

VERSION = "0.4.0"

# ── Concurrency limits per provider ──────────────────────────────────────────

PROVIDER_CONCURRENCY: dict[str, int] = {
    "bedrock": 3,
    "openai": 6,    # OpenRouter
    "anthropic": 4,
}

# ── Persistent state & config ────────────────────────────────────────────────

STATE_DIR = Path.home() / ".forge"
STATE_FILE = STATE_DIR / "cli_state.json"
CONFIG_FILE = STATE_DIR / "config.json"
HISTORY_FILE = Path.home() / ".forge_history"

# Default config
DEFAULT_CONFIG: dict[str, Any] = {
    "default_model": "nova-lite",
    "model_preset": "nova",     # "nova" = AWS-only, "mixed" = best-per-task, "premium" = Nova Pro
    "project_dir": str(Path.home() / "projects"),
    "max_turns": 50,
    "temperature": 0.3,
    "auto_build": True,         # Auto-confirm builds in guided flow
    "show_tips": True,
    "theme": "default",
}

def _load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text())
            config.update(saved)
        except (json.JSONDecodeError, OSError):
            pass
    return config

def _save_config(config: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Only save non-default values
    to_save = {k: v for k, v in config.items() if k in DEFAULT_CONFIG}
    CONFIG_FILE.write_text(json.dumps(to_save, indent=2) + "\n")

def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"recent_projects": [], "first_run": True, "builds_completed": 0}

def _save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")

def _add_recent_project(state: dict, path: str, name: str) -> None:
    projects = state.get("recent_projects", [])
    projects = [p for p in projects if p["path"] != path]
    projects.insert(0, {"path": path, "name": name, "last_used": time.strftime("%Y-%m-%d")})
    state["recent_projects"] = projects[:10]


# ── Credential detection ─────────────────────────────────────────────────────

PROVIDER_CREDS = {
    "bedrock": {
        "env_vars": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "display": "Amazon Bedrock (Nova models)",
        "setup_hint": (
            "Set AWS credentials:\n"
            "  export AWS_ACCESS_KEY_ID=your-key\n"
            "  export AWS_SECRET_ACCESS_KEY=your-secret\n"
            "  export AWS_DEFAULT_REGION=us-east-1\n\n"
            "  Or: aws configure"
        ),
        "models": ["nova-lite", "nova-pro", "nova-premier"],
    },
    "openrouter": {
        "env_vars": ["OPENROUTER_API_KEY"],
        "display": "OpenRouter (Gemini, Claude, etc.)",
        "setup_hint": (
            "Set your OpenRouter API key:\n"
            "  export OPENROUTER_API_KEY=your-key\n\n"
            "  Get a key at: https://openrouter.ai/keys"
        ),
        "models": ["gemini-flash", "gemini-pro"],
    },
    "anthropic": {
        "env_vars": ["ANTHROPIC_API_KEY"],
        "display": "Anthropic (Claude models)",
        "setup_hint": (
            "Set your Anthropic API key:\n"
            "  export ANTHROPIC_API_KEY=your-key\n\n"
            "  Get a key at: https://console.anthropic.com/"
        ),
        "models": ["claude-sonnet", "claude-haiku"],
    },
}

def _check_provider(provider: str) -> bool:
    """Check if a provider's credentials are available in the environment."""
    info = PROVIDER_CREDS.get(provider, {})
    return all(os.environ.get(var) for var in info.get("env_vars", []))

def _check_all_providers() -> dict[str, bool]:
    """Return {provider: is_configured} for all providers."""
    return {name: _check_provider(name) for name in PROVIDER_CREDS}

def _provider_for_model(alias: str) -> str:
    """Get which provider a model alias requires."""
    for prov, info in PROVIDER_CREDS.items():
        if alias in info["models"]:
            return prov
    return "bedrock"

def _available_models() -> list[str]:
    """Return model aliases that have working credentials."""
    providers = _check_all_providers()
    available = []
    for alias in MODEL_ALIASES:
        prov = _provider_for_model(alias)
        if providers.get(prov, False):
            available.append(alias)
    return available

def _try_load_env_file(path: str) -> bool:
    """Load a shell env file (KEY=VALUE format) into os.environ."""
    p = Path(path).expanduser()
    if not p.exists():
        return False
    try:
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Handle export KEY=VALUE and KEY=VALUE
            if line.startswith("export "):
                line = line[7:]
            if "=" in line:
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip().strip('"').strip("'")
                if key and val:
                    os.environ[key] = val
        return True
    except OSError:
        return False

# ── ASCII Art & Branding ─────────────────────────────────────────────────────

LOGO = r"""[#c084fc]
    _   __                  ______
   / | / /___ _   ______ _ / ____/___  _________ ____
  /  |/ / __ \ | / / __ `// /_  / __ \/ ___/ __ `/ _ \
 / /|  / /_/ / |/ / /_/ // __/ / /_/ / /  / /_/ /  __/
/_/ |_/\____/|___/\__,_//_/    \____/_/   \__, /\___/
                                         /____/[/]"""

_TAGLINE_TEXT = "AI Build Orchestrator"

WELCOME_FIRST_RUN = """
[bold bright_white]Welcome to Nova Forge![/]

Nova Forge is an open-source AI build system powered by [brand]Amazon Nova[/].
Tell it what you want to build, and it writes the code — start to finish.

[step]How it works:[/]
  [accent]1.[/]  You describe what you want        [muted]"a REST API for bookmarks"[/]
  [accent]2.[/]  Nova plans the project             [muted]spec, tasks, architecture[/]
  [accent]3.[/]  Nova builds it, wave by wave       [muted]real code, not stubs[/]
  [accent]4.[/]  You get a working project           [muted]ready to run[/]

[hint]Just describe your idea — Nova handles the rest.[/]
"""

WELCOME_RETURNING = """[bold bright_white]Welcome back![/]  [muted]Nova Forge v{version}[/]"""

IDEAS = [
    "a REST API for managing bookmarks",
    "a weather dashboard with Flask",
    "a task tracker with SQLite",
    "a URL shortener service",
    "a blog engine with markdown support",
    "a chat server with WebSockets",
    "a file organizer CLI tool",
    "a habit tracker with streaks",
]

TIPS = [
    "You can say [accent]\"build me X\"[/] and Nova will plan + build automatically.",
    "Type [accent]/status[/] anytime to see your project's progress.",
    "After a build, check the generated files — they're real, runnable code.",
    "Use [accent]/tasks[/] to see the full task breakdown with dependencies.",
    "Nova works best with clear, specific descriptions of what you want.",
    "You can change the AI model with [accent]/models[/] — Nova, Gemini, Claude all work.",
    "Type [accent]/help[/] to see all available commands.",
    "Nova builds wave-by-wave — later tasks can depend on earlier ones.",
]

HELP_TEXT = """
[bold bright_white]Getting Started[/]
  [accent]/guide[/]              Smart setup wizard — recommends formation, autonomy, model
  [accent]/interview[/]          5-step guided project setup (advanced)
  [accent]/autonomy[/]           View or change how much Nova asks for approval
  [accent]/autonomy ?[/]         Explain all 5 autonomy levels
  [accent]/autonomy[/] [muted]0-5[/]       Set level  [muted](0=Manual · 2=Supervised · 4=Autonomous · 5=Unattended)[/]

[bold bright_white]Build[/]
  [accent]/plan[/] [muted]<goal>[/]        Plan a project from a description
  [accent]/build[/]              Execute the plan — Nova writes all the code
  [accent]/preview[/]            Launch Cloudflare Tunnel for live preview
  [accent]/deploy[/]             Ship to production with Docker + nginx
  [accent]/status[/]             Progress bar and project overview
  [accent]/tasks[/]              See all tasks with status and dependencies

[bold bright_white]Configuration[/]
  [accent]/model[/]               Switch AI model  [muted](interactive selector, or /model nova-lite)[/]
  [accent]/models[/]             Show all available models + credential status
  [accent]/config[/]             Edit settings  [muted](interactive, or /config key value)[/]
  [accent]/login[/]              Set up API credentials for a provider

[bold bright_white]Project[/]
  [accent]/resume[/]             Resume a recent project  [muted](interactive, or /resume 1)[/]
  [accent]/new[/] [muted]<name>[/]          Start a fresh project directory
  [accent]/cd[/] [muted]<path>[/]           Switch project directory
  [accent]/pwd[/]                Show current project location
  [accent]/formation[/]          View agent formations  [muted](interactive selector)[/]
  [accent]/audit[/]              View the build audit log
  [accent]/builds[/]             Build history with proof-of-work detail
  [accent]/health[/]             System health dashboard
  [accent]/competition[/]        Hackathon submission readiness

[bold bright_white]General[/]
  [accent]/clear[/]              Clear the screen
  [accent]/help[/]               This screen
  [accent]/quit[/]               Exit

[bold bright_white]Quick Start[/]                              [muted]Or just type what you want to build[/]
  [muted]>[/] Build me a REST API for managing recipes
  [muted]>[/] Create a CLI tool that converts CSV to JSON
  [muted]>[/] I need a todo app with a SQLite backend
"""


# ── Interactive Shell ────────────────────────────────────────────────────────

class ForgeShell:
    """Interactive CLI shell for Nova Forge — guided, eager, friendly."""

    def __init__(self, project_path: str | Path = ".", default_model: str | None = None):
        self.config = _load_config()
        self.project_path = Path(project_path).resolve()
        self.state = _load_state()
        self.session_builds = 0
        self._chat_history: Any = None    # Lazy-loaded ChatHistory
        self._preview_mgr: Any = None     # PreviewManager instance
        self.assistant = ForgeAssistant(self)  # Smart assistant layer

        # Apply model preset (must happen before model resolution so formations are patched)
        preset_name = self.config.get("model_preset", "nova")
        try:
            from forge_models import apply_preset
            apply_preset(preset_name)
        except (KeyError, ImportError):
            preset_name = ""

        # Resolve model: CLI flag > saved config > preset default > hardcoded default
        if default_model:
            self.model = resolve_model(default_model)
        elif self.config.get("default_model"):
            self.model = resolve_model(self.config["default_model"])
        else:
            self.model = DEFAULT_MODELS["planning"]

        self._ensure_project()

    def _ensure_project(self) -> None:
        forge_dir = self.project_path / ".forge"
        if not forge_dir.exists():
            init_forge_dir(self.project_path)

    @property
    def chat_history(self):
        if self._chat_history is None:
            from forge_memory import ChatHistory
            self._chat_history = ChatHistory(self.project_path)
        return self._chat_history

    # ── Main entry ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main REPL — welcome, onboard, build."""
        console.clear()
        console.print(LOGO)
        subtitle = gradient_text(f"  {_TAGLINE_TEXT}")
        console.print(subtitle)
        console.print(f"  [dim]v{VERSION}[/]")
        console.print()

        # Try auto-loading credentials from known locations
        self._auto_load_credentials()

        is_first = self.state.get("first_run", True)

        if is_first:
            # Check credentials before onboarding
            if not self._check_credentials_status(quiet=True):
                await self._setup_wizard()
            await self._onboard_first_run()
        else:
            # Show credential status if nothing is configured
            if not self._check_credentials_status(quiet=True):
                self._show_credential_warning()
            await self._onboard_returning()

        # Main loop
        session = PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
            style=PT_STYLE,
        )

        while True:
            try:
                # Show autonomy level in prompt (e.g. "nova [A2] > ")
                autonomy_lvl = self.assistant.read_autonomy_level()
                prompt_str = HTML(f"<prompt>nova [A{autonomy_lvl}] &gt; </prompt>")
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: session.prompt(prompt_str),
                )
            except (EOFError, KeyboardInterrupt):
                self._goodbye()
                break

            user_input = user_input.strip()
            if not user_input:
                continue

            if user_input.startswith("/"):
                should_quit = await self._handle_slash(user_input)
                if should_quit:
                    break
            else:
                await self._handle_natural(user_input)

            console.print()

    # ── First-run onboarding ─────────────────────────────────────────────

    async def _onboard_first_run(self) -> None:
        # Ask skill level before welcoming, so we can adapt verbosity
        skill_choice = await ask_select(
            "How would you describe your experience level?",
            [
                Choice(
                    title="Beginner",
                    value="beginner",
                    description="New to coding or new to Nova Forge — extra guidance and explanations",
                ),
                Choice(
                    title="Intermediate",
                    value="intermediate",
                    description="Comfortable with code and CLIs — balanced guidance",
                ),
                Choice(
                    title="Expert",
                    value="expert",
                    description="Experienced developer — minimal explanations, just build",
                ),
            ],
            default="beginner",
            use_shortcuts=True,
        )
        if skill_choice:
            self.assistant.set_skill_level(skill_choice)
        else:
            self.assistant.detect_skill_level()

        # Show skill-adaptive welcome
        console.print()
        console.print(self.assistant.welcome_message())
        console.print()

        # Recommend autonomy level based on skill
        rec_level, rec_reason = self.assistant.get_autonomy_recommendation()
        console.print(f"  [step]Recommended autonomy:[/] A{rec_level} ({rec_reason})")
        console.print(f"  [hint]You can change this anytime with /autonomy[/]")
        console.print()

        # Apply recommended autonomy
        self.assistant.set_autonomy_level(rec_level)

        # Mark first run complete
        self.state["first_run"] = False
        _save_state(self.state)

        # Immediately ask what to build
        console.print(Rule("[bold bright_white]Let's build something[/]", style="bright_magenta"))
        console.print()

        idea = random.choice(IDEAS)
        console.print(f"  [hint]Try something like: \"{idea}\"[/]")
        console.print()

        goal = await ask_text("What do you want to build?")
        if goal is None:
            self._goodbye()
            return

        goal = goal.strip()
        if goal:
            await self._guided_build(goal)

    # ── Returning user onboarding ────────────────────────────────────────

    async def _onboard_returning(self) -> None:
        builds = self.state.get("builds_completed", 0)

        # Detect skill level from history
        self.assistant.detect_skill_level()

        console.print(WELCOME_RETURNING.format(version=VERSION))
        if builds > 0:
            console.print(f"  [muted]{builds} project{'s' if builds != 1 else ''} built so far[/]")

        # Show autonomy level
        autonomy_lvl = self.assistant.read_autonomy_level()
        autonomy_bar = self.assistant.format_autonomy_bar(autonomy_lvl)
        console.print(f"  [muted]Autonomy:[/] {autonomy_bar}  [dim](/autonomy to change)[/]")

        from forge_models import get_active_preset, MODEL_PRESETS
        active = get_active_preset()
        if active:
            desc = MODEL_PRESETS[active]["description"]
            console.print(f"  [nova]Preset:[/] {active} [muted]— {desc}[/]")

        # Show contextual hint for returning expert
        if self.assistant.skill_level == "expert":
            hint = self.assistant.contextual_hint("returning_expert")
            if hint:
                console.print(f"  [hint]{hint}[/]")

        console.print()

        # Auto-resume: find the most recent project with pending/failed work
        resumed = self._try_auto_resume()
        if resumed:
            return

        # Second pass: if no pending/failed, auto-switch to most recent completed project
        recent = self.state.get("recent_projects", [])
        for proj in recent:
            p = Path(proj["path"])
            if not p.exists():
                continue
            summary = self._get_task_summary_for(p)
            if summary and summary["total"] > 0 and summary["completed"] == summary["total"]:
                self.project_path = p
                self._ensure_project()
                console.print(Panel(
                    f"[bold]{proj['name']}[/]\n"
                    f"  [success]{summary['total']}/{summary['total']} tasks complete[/]\n"
                    f"  [muted]{p}[/]\n\n"
                    f"  [hint]/preview[/] — share a live URL\n"
                    f"  [hint]/deploy[/] — ship to production\n"
                    f"  Tell me to add features, or describe a new project!",
                    border_style="green",
                    title="[bold green] Project Ready [/]",
                    padding=(1, 2),
                ))
                console.print()
                return

        # Show recent projects with status
        if recent:
            console.print("  [step]Recent projects:[/]")
            for i, proj in enumerate(recent[:5], 1):
                p = Path(proj["path"])
                if not p.exists():
                    console.print(f"    [accent]{i}.[/] [muted]{proj['name']}  (deleted)[/]")
                    continue
                summary = self._get_task_summary_for(p)
                if summary and summary["total"] > 0:
                    done = summary["completed"]
                    total = summary["total"]
                    failed = summary["failed"]
                    if done == total:
                        tag = "[success]complete[/]"
                    elif failed > 0:
                        tag = f"[yellow]{done}/{total} done, {failed} failed[/]"
                    else:
                        tag = f"[cyan]{done}/{total} done[/]"
                else:
                    tag = "[muted]empty[/]"
                console.print(f"    [accent]{i}.[/] {proj['name']:30s} {tag}")
                console.print(f"       [muted]{proj['path']}[/]")
            console.print()
            console.print(f"  [hint]Type [accent]/resume[/] or [accent]/resume 1[/] to continue a project[/]")
            console.print()

        if self.config.get("show_tips", True):
            console.print(f"  [hint]Tip: {random.choice(TIPS)}[/]")
            console.print()

    def _try_auto_resume(self) -> bool:
        """Auto-switch to the most recent project that has pending/failed work."""
        recent = self.state.get("recent_projects", [])
        for proj in recent:
            p = Path(proj["path"])
            if not p.exists():
                continue
            summary = self._get_task_summary_for(p)
            if summary and (summary["pending"] > 0 or summary["failed"] > 0 or summary.get("in_progress", 0) > 0):
                # Found a project with work to do — switch to it
                self.project_path = p
                self._ensure_project()
                done = summary["completed"]
                total = summary["total"]
                pending = summary["pending"]
                failed = summary["failed"]

                remaining = pending + failed + summary.get("in_progress", 0)
                console.print(Panel(
                    f"[bold]{proj['name']}[/]\n"
                    f"  {done}/{total} tasks done, {remaining} remaining\n"
                    f"  [muted]{p}[/]\n\n"
                    f"  Type [accent]/build[/] to continue, or describe something new.",
                    border_style="bright_magenta",
                    title="[bold bright_magenta] Resuming [/]",
                    padding=(1, 2),
                ))
                console.print()
                return True
        return False

    # ── Guided build flow (the magic) ────────────────────────────────────

    async def _guided_build(self, goal: str, scope_context: str | None = None) -> None:
        """The full guided pipeline: goal → interview → plan → confirm → build → celebrate."""

        # Step 1: Derive project name
        name = self._derive_project_name(goal)
        console.print()
        console.print(f"  [step]Project:[/] [bold]{name}[/]")
        console.print(f"  [step]Goal:[/]    {goal}")
        console.print()

        # Check credentials before doing anything
        active_prov = _provider_for_model(
            next((a for a, fid in MODEL_ALIASES.items() if fid == self.model), "nova-lite")
        )
        if not _check_provider(active_prov):
            info = PROVIDER_CREDS.get(active_prov, {})
            console.print(f"  [warning]Need {info.get('display', active_prov)} credentials first.[/]")
            console.print(f"  [hint]Run /login to set up, or /model to switch models.[/]")
            return

        # Create project directory
        base_dir = Path(self.config.get("project_dir", str(Path.home() / "projects")))
        project_dir = base_dir / name
        if project_dir.exists():
            # Add suffix if exists
            for i in range(2, 100):
                candidate = base_dir / f"{name}-{i}"
                if not candidate.exists():
                    project_dir = candidate
                    break

        project_dir.mkdir(parents=True, exist_ok=True)
        self.project_path = project_dir
        self._ensure_project()

        console.print(f"  [success]Created[/] {project_dir}")
        console.print()

        # Step 2: Smart planning — Nova proposes, user confirms
        if scope_context is None:
            scope_context = await self._smart_planning(goal)
            if scope_context is None:
                console.print("  [muted]Planning cancelled.[/]")
                return

        # Step 3: Plan
        console.print(Rule(f"[bold {BRAND['accent']}] Planning [/]", style=BRAND["accent"]))
        console.print()
        await self._cmd_plan(goal, scope_context=scope_context)

        # Step 3: Confirm build
        if not self._has_tasks():
            console.print("  [warning]Planning didn't produce tasks. Try a more specific description.[/]")
            return

        # Post-plan hint from assistant
        from forge_display import display_assistant_hint
        summary_after_plan = self._get_task_summary()
        if summary_after_plan:
            task_count = summary_after_plan["total"]
            try:
                waves = []
                from forge_tasks import TaskStore
                from config import ForgeProject as _FP
                _store = TaskStore(_FP(root=self.project_path).tasks_file)
                waves = _store.compute_waves()
            except Exception:
                waves = []
            guidance = self.assistant.post_plan_guidance(task_count, len(waves))
            console.print(f"  [hint]{guidance}[/]")

        console.print()
        console.print(Rule(f"[bold {BRAND['cyan']}] Build [/]", style=BRAND["cyan"]))
        console.print()

        if not await ask_confirm("Ready to build?"):
            console.print("  [muted]No problem. You can edit the plan and run /build when ready.[/]")
            return

        # Step 4: Build!
        await self._cmd_build("")

        # Step 5: Celebrate + next steps
        self._celebrate()

        # Track in state
        self.state["builds_completed"] = self.state.get("builds_completed", 0) + 1
        _add_recent_project(self.state, str(self.project_path), name)
        _save_state(self.state)

    # ── Celebration ──────────────────────────────────────────────────────

    def _celebrate(self) -> None:
        """Show build results and next steps."""
        tasks_summary = self._get_task_summary()
        if not tasks_summary:
            return

        total = tasks_summary["total"]
        done = tasks_summary["completed"]
        failed = tasks_summary["failed"]

        console.print()

        if failed == 0 and done == total:
            # Show file tree of the built project
            all_files = self._list_project_files()
            tree_str = ""
            if all_files:
                tree = file_tree(all_files[:15], str(self.project_path))
                from io import StringIO
                from rich.console import Console as _C
                buf = StringIO()
                _C(file=buf, force_terminal=True).print(tree)
                tree_str = "\n" + buf.getvalue()

            console.print(Panel(
                f"[bold {BRAND['green']}]\u2713 Build complete![/]\n\n"
                f"  {status_bar(done, total, 20)}\n"
                f"{tree_str}\n"
                f"  [step]Quick actions:[/]\n"
                f"    [{BRAND['accent2']}]/preview[/]  \u2192  Share a live URL\n"
                f"    [{BRAND['cyan']}]/deploy[/]   \u2192  Ship to production\n"
                f"    [{BRAND['accent']}]/tasks[/]    \u2192  Review task details",
                border_style=BRAND["green"],
                title=f"[bold {BRAND['green']}] Done! [/]",
                padding=(1, 2),
            ))
        elif done > 0:
            console.print(Panel(
                f"[bold {BRAND['orange']}]Build partially complete[/]\n\n"
                f"  {status_bar(done, total, 20)}\n"
                f"  [success]{done}[/] passed  [error]{failed}[/] failed  "
                f"out of {total} tasks\n\n"
                f"  Type [{BRAND['cyan']}]/build[/] to retry failed tasks,\n"
                f"  or tell me what to fix.",
                border_style=BRAND["orange"],
                title=f"[bold {BRAND['orange']}] Almost there [/]",
                padding=(1, 2),
            ))
        else:
            console.print(f"  [error]Build had issues.[/] Type [accent]/tasks[/] to see what went wrong.")
            console.print(f"  [hint]You can describe the problem and I'll help fix it.[/]")

    # ── Goodbye ──────────────────────────────────────────────────────────

    def _goodbye(self) -> None:
        _save_state(self.state)
        builds = self.state.get("builds_completed", 0)
        console.print()
        if builds > 0:
            console.print(f"  [{BRAND['accent2']}]\u2728[/] [muted]{builds} project{'s' if builds != 1 else ''} built. See you next time.[/]")
        else:
            console.print(f"  [{BRAND['accent2']}]\u2728[/] [muted]Come back when you're ready to build.[/]")
        console.print()

    # ── Credential management ────────────────────────────────────────────

    def _auto_load_credentials(self) -> None:
        """Try loading credentials from common locations."""
        env_paths = [
            "~/.secrets/hercules.env",
            "~/.forge/credentials.env",
            "~/.env",
            ".env",
        ]
        for path in env_paths:
            if _try_load_env_file(path):
                logger.debug("Loaded credentials from %s", path)

    def _check_credentials_status(self, quiet: bool = False) -> bool:
        """Check and optionally display credential status. Returns True if any provider works."""
        providers = _check_all_providers()
        any_configured = any(providers.values())

        if not quiet:
            console.print()
            console.print("  [step]Provider Status[/]")
            for name, configured in providers.items():
                info = PROVIDER_CREDS[name]
                icon = "[success]ready[/]" if configured else "[muted]not configured[/]"
                models = ", ".join(info["models"])
                console.print(f"    {info['display']:40s} {icon}")
                if configured:
                    console.print(f"      [muted]Models: {models}[/]")
            console.print()

            if any_configured:
                avail = _available_models()
                console.print(f"  [success]{len(avail)} models available:[/] {', '.join(avail)}")
            else:
                console.print(f"  [warning]No providers configured.[/] Run [accent]/login[/] to set up.")
            console.print()

        return any_configured

    def _show_credential_warning(self) -> None:
        """Show a gentle warning about missing credentials."""
        providers = _check_all_providers()
        active_prov = _provider_for_model(
            next((a for a, fid in MODEL_ALIASES.items() if fid == self.model), "nova-lite")
        )
        if not providers.get(active_prov, False):
            console.print(Panel(
                f"[warning]Your active model ({_short_model(self.model)}) needs credentials.[/]\n\n"
                f"  Run [accent]/login[/] to set up, or [accent]/model[/] to switch.\n"
                f"  [muted]You can also: source ~/.secrets/hercules.env[/]",
                border_style="yellow",
                padding=(0, 2),
            ))
            console.print()

    async def _setup_wizard(self) -> None:
        """Interactive credential setup wizard."""
        console.print(Rule("[step] Setup [/]", style="cyan"))
        console.print()
        console.print("  Nova Forge needs API credentials to talk to AI models.")
        console.print("  Let's get you set up. [muted](You can skip and do this later with /login)[/]")
        console.print()

        # Check if we have a known env file
        env_path = Path("~/.secrets/hercules.env").expanduser()
        if env_path.exists():
            console.print(f"  [success]Found:[/] {env_path}")
            _try_load_env_file(str(env_path))
            if _check_all_providers().get("bedrock"):
                console.print(f"  [success]AWS credentials loaded — Bedrock is ready![/]")
                console.print()
                return

        # Show what's needed
        providers = _check_all_providers()
        for name, configured in providers.items():
            if configured:
                info = PROVIDER_CREDS[name]
                console.print(f"  [success]{info['display']}[/] — ready")

        unconfigured = [n for n, c in providers.items() if not c]
        if not unconfigured:
            console.print("  [success]All providers ready![/]")
            console.print()
            return

        console.print()
        console.print("  [step]To get started, set up at least one provider:[/]")
        console.print()

        provider_choices = [
            Choice(
                title=PROVIDER_CREDS[name]["display"],
                value=name,
                description=f"Models: {', '.join(PROVIDER_CREDS[name]['models'])}",
            )
            for name in unconfigured
        ]
        provider = await ask_select("Set up a provider", provider_choices, use_shortcuts=True)
        if provider is None:
            console.print("  [muted]Skipped. You can run /login anytime.[/]")
            console.print()
            return

        await self._login_provider(provider)

    async def _login_provider(self, provider: str) -> None:
        """Guide user through setting up a specific provider."""
        info = PROVIDER_CREDS[provider]
        console.print()
        console.print(f"  [step]Setting up {info['display']}[/]")
        console.print()

        creds: dict[str, str] = {}

        for var in info["env_vars"]:
            current = os.environ.get(var, "")
            hint = f"current: ...{current[-8:]}" if current else None
            val = await ask_text(f"  {var}", default=current, instruction=hint)
            if val is None:
                console.print("  [muted]Cancelled.[/]")
                return

            val = val.strip()
            if val:
                creds[var] = val
            elif current:
                creds[var] = current
            else:
                console.print(f"  [warning]Skipped {var}[/]")

        if not creds:
            return

        # Apply to environment
        for k, v in creds.items():
            os.environ[k] = v

        # Save to credentials file
        creds_file = STATE_DIR / "credentials.env"
        STATE_DIR.mkdir(parents=True, exist_ok=True)

        # Append new vars (don't overwrite existing)
        existing = {}
        if creds_file.exists():
            for line in creds_file.read_text().splitlines():
                if "=" in line and not line.startswith("#"):
                    k, _, v = line.partition("=")
                    existing[k.strip()] = v.strip()

        existing.update(creds)
        lines = [f"{k}={v}" for k, v in existing.items()]
        creds_file.write_text("\n".join(lines) + "\n")
        creds_file.chmod(0o600)

        # Verify
        if _check_provider(provider):
            console.print()
            console.print(f"  [success]{info['display']} is ready![/]")
            models = ", ".join(info["models"])
            console.print(f"  [muted]Available models: {models}[/]")

            # Auto-switch to first available model for this provider if current isn't working
            active_prov = _provider_for_model(
                next((a for a, fid in MODEL_ALIASES.items() if fid == self.model), "")
            )
            if not _check_provider(active_prov):
                new_model = info["models"][0]
                self.model = resolve_model(new_model)
                self.config["default_model"] = new_model
                _save_config(self.config)
                console.print(f"  [info]Switched to:[/] {new_model}")
        else:
            console.print(f"  [warning]Credentials saved but verification failed.[/]")

        console.print()

    # ── Slash command router ─────────────────────────────────────────────

    async def _handle_slash(self, raw: str) -> bool:
        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        match cmd:
            case "/quit" | "/exit" | "/q":
                self._goodbye()
                return True
            case "/help" | "/h" | "/?":
                console.print(HELP_TEXT)
                self._suggest_next_action()
            case "/clear" | "/cls":
                console.clear()
                console.print(LOGO)
                console.print(f"  {_TAGLINE_TEXT}")
            case "/pwd":
                console.print(f"  [info]Project:[/] {self.project_path}")
            case "/cd":
                self._cmd_cd(arg)
            case "/resume":
                await self._cmd_resume(arg)
            case "/new":
                await self._cmd_new(arg)
            case "/plan":
                if not arg:
                    console.print()
                    console.print("  [hint]What should Nova plan?[/]")
                    console.print("  [muted]Example: /plan Build a REST API for managing recipes[/]")
                else:
                    await self._cmd_plan(arg)
            case "/build":
                await self._cmd_build(arg)
            case "/status":
                self._cmd_status()
            case "/tasks":
                self._cmd_tasks()
            case "/model":
                await self._cmd_model(arg)
            case "/models":
                self._cmd_models()
            case "/config":
                await self._cmd_config(arg)
            case "/login":
                await self._cmd_login(arg)
            case "/formation":
                await self._cmd_formation(arg)
            case "/audit":
                self._cmd_audit()
            case "/builds":
                self._cmd_builds(arg)
            case "/preview":
                await self._cmd_preview(arg)
            case "/deploy":
                self._cmd_deploy(arg)
            case "/interview":
                await self._cmd_interview()
            case "/autonomy":
                await self._cmd_autonomy(arg)
            case "/guide":
                await self._cmd_guide(arg)
            case "/health":
                self._cmd_health()
            case "/competition":
                self._cmd_competition()
            case _:
                console.print(f"  [warning]Unknown command:[/] {cmd}")
                console.print(f"  [hint]Type /help for commands, or just describe what you want.[/]")

        return False

    # ── /cd ──────────────────────────────────────────────────────────────

    def _cmd_cd(self, path: str) -> None:
        if not path:
            console.print(f"  [info]Current project:[/] {self.project_path}")
            return
        new_path = Path(path).resolve()
        if not new_path.is_dir():
            console.print(f"  [error]Not a directory:[/] {new_path}")
            return
        self.project_path = new_path
        self._ensure_project()
        console.print(f"  [success]Switched to[/] {self.project_path}")

        # Show project state and guidance
        summary = self._get_task_summary()
        if summary:
            console.print(f"  [muted]{summary['completed']}/{summary['total']} tasks done[/]")
            self._suggest_next_action()

    # ── /resume ───────────────────────────────────────────────────────────

    async def _cmd_resume(self, arg: str) -> None:
        """Resume a recent project."""
        recent = self.state.get("recent_projects", [])
        if not recent:
            console.print("  [muted]No recent projects. Start one with /new or describe what to build.[/]")
            return

        # If a number or name was given, use it directly
        if arg:
            target = None
            if arg.isdigit():
                idx = int(arg) - 1
                if 0 <= idx < len(recent):
                    target = recent[idx]
            else:
                # Match by name
                for proj in recent:
                    if proj["name"] == arg or arg in proj["name"]:
                        target = proj
                        break

            if not target:
                console.print(f"  [error]Project not found:[/] {arg}")
                console.print(f"  [hint]Use /resume to see the list[/]")
                return
        else:
            # No argument — interactive project selection
            choices: list[Choice | Separator] = []
            for proj in recent[:10]:
                p = Path(proj["path"])
                if not p.exists():
                    choices.append(Choice(
                        title=proj["name"],
                        value=proj["name"],
                        disabled="directory deleted",
                    ))
                    continue

                summary = self._get_task_summary_for(p)
                if summary and summary["total"] > 0:
                    done = summary["completed"]
                    total = summary["total"]
                    if done == total:
                        status = "complete"
                    else:
                        remaining = total - done
                        status = f"{done}/{total} done, {remaining} to go"
                else:
                    status = "no tasks"

                is_active = p == self.project_path
                title = f"{proj['name']:30s}  {status}"
                if is_active:
                    title += "  (current)"

                choices.append(Choice(
                    title=title,
                    value=proj["name"],
                    description=str(p),
                ))

            if not choices:
                console.print("  [muted]No recent projects.[/]")
                return

            selected = await ask_select(
                "Resume project",
                choices,
                use_search_filter=len(choices) > 5,
            )
            if selected is None:
                return

            target = None
            for proj in recent:
                if proj["name"] == selected:
                    target = proj
                    break
            if not target:
                return

        p = Path(target["path"])
        if not p.exists():
            console.print(f"  [error]Directory deleted:[/] {target['path']}")
            return

        self.project_path = p
        self._ensure_project()
        console.print(f"  [success]Resumed[/] [bold]{target['name']}[/]")
        console.print(f"  [muted]{p}[/]")

        summary = self._get_task_summary()
        if summary:
            done = summary["completed"]
            total = summary["total"]
            failed = summary["failed"]
            pending = summary["pending"]
            in_prog = summary.get("in_progress", 0)
            remaining = pending + failed + in_prog
            console.print(f"  {done}/{total} tasks done", end="")
            if remaining > 0:
                console.print(f", {remaining} remaining")
                console.print()
                console.print(f"  [hint]Type [accent]/build[/] to continue[/]")
            else:
                console.print(" [success](all complete)[/]")
                self._suggest_next_action()

    # ── /model ────────────────────────────────────────────────────────────

    async def _cmd_model(self, arg: str) -> None:
        """Switch the active model."""
        if not arg:
            # Interactive model selection with arrow keys
            current_alias = _short_model(self.model)
            console.print(f"  [step]Active model:[/] [accent]{current_alias}[/]  [muted]({self.model})[/]")
            console.print()

            providers = _check_all_providers()
            choices = build_model_choices(
                available_providers=providers,
                current_model=current_alias,
            )

            selected = await ask_select(
                "Switch model",
                choices,
                default=current_alias,
                use_search_filter=True,
            )
            if selected is None or selected == current_alias:
                return
            arg = selected

        # Switch model
        if arg not in MODEL_ALIASES:
            console.print(f"  [error]Unknown model:[/] {arg}")
            console.print(f"  [hint]Available: {', '.join(MODEL_ALIASES.keys())}[/]")
            return

        # Check credentials
        prov = _provider_for_model(arg)
        if not _check_provider(prov):
            info = PROVIDER_CREDS[prov]
            console.print(f"  [warning]{arg} needs {info['display']} credentials.[/]")
            console.print(f"  [hint]Run /login to set up, or set: {', '.join(info['env_vars'])}[/]")
            return

        self.model = resolve_model(arg)
        self.config["default_model"] = arg
        _save_config(self.config)
        console.print(f"  [success]Switched to[/] [accent]{arg}[/]  [muted]({self.model})[/]")

    # ── /config ──────────────────────────────────────────────────────────

    async def _cmd_config(self, arg: str) -> None:
        """View or modify configuration."""
        if not arg:
            # Interactive config editor
            config_descriptions = {
                "default_model": ("Default AI model", DEFAULT_CONFIG["default_model"]),
                "model_preset": ("Model preset (nova/mixed/premium)", DEFAULT_CONFIG["model_preset"]),
                "project_dir": ("New project directory", DEFAULT_CONFIG["project_dir"]),
                "max_turns": ("Max agent turns per task", DEFAULT_CONFIG["max_turns"]),
                "temperature": ("Model temperature", DEFAULT_CONFIG["temperature"]),
                "auto_build": ("Auto-confirm builds", DEFAULT_CONFIG["auto_build"]),
                "show_tips": ("Show tips on startup", DEFAULT_CONFIG["show_tips"]),
            }

            choices: list[Choice | Separator] = []
            for key, (desc, default) in config_descriptions.items():
                current = self.config.get(key, default)
                if isinstance(current, bool):
                    val_str = "on" if current else "off"
                else:
                    val_str = str(current)
                choices.append(Choice(
                    title=f"{key:20s}  = {val_str}",
                    value=key,
                    description=desc,
                ))

            selected_key = await ask_select(
                "Edit setting",
                choices,
                use_search_filter=True,
            )
            if selected_key is None:
                return

            # Now prompt for the new value based on the setting type
            current_val = self.config.get(selected_key, DEFAULT_CONFIG[selected_key])

            if selected_key == "default_model":
                providers = _check_all_providers()
                model_choices = build_model_choices(
                    available_providers=providers,
                    current_model=_short_model(self.model),
                )
                new_val = await ask_select(
                    f"Set {selected_key}",
                    model_choices,
                    default=_short_model(self.model),
                    use_search_filter=True,
                )
                if new_val is None:
                    return
                arg = f"{selected_key} {new_val}"
            elif selected_key == "model_preset":
                from forge_models import MODEL_PRESETS
                preset_choices = [
                    Choice(title=name, value=name, description=p["description"])
                    for name, p in MODEL_PRESETS.items()
                ]
                new_val = await ask_select(
                    f"Set {selected_key}",
                    preset_choices,
                    default=str(current_val) if current_val else None,
                )
                if new_val is None:
                    return
                arg = f"{selected_key} {new_val}"
            elif isinstance(current_val, bool):
                toggle_choices = [
                    Choice(title="on", value="on", description="Enable this setting"),
                    Choice(title="off", value="off", description="Disable this setting"),
                ]
                new_val = await ask_select(
                    f"Set {selected_key}",
                    toggle_choices,
                    default="on" if current_val else "off",
                )
                if new_val is None:
                    return
                arg = f"{selected_key} {new_val}"
            else:
                new_val = await ask_text(
                    f"Set {selected_key}",
                    default=str(current_val),
                    instruction=f"(current: {current_val})",
                )
                if new_val is None:
                    return
                arg = f"{selected_key} {new_val}"

        # Parse "key value"
        parts = arg.split(None, 1)
        key = parts[0]
        val = parts[1] if len(parts) > 1 else None

        if key not in DEFAULT_CONFIG:
            console.print(f"  [error]Unknown setting:[/] {key}")
            console.print(f"  [hint]Available: {', '.join(DEFAULT_CONFIG.keys())}[/]")
            return

        if val is None:
            # Show single value
            current = self.config.get(key, DEFAULT_CONFIG[key])
            console.print(f"  [info]{key}:[/] {current}")
            return

        # Parse value by type
        default_val = DEFAULT_CONFIG[key]
        if isinstance(default_val, bool):
            parsed = val.lower() in ("true", "on", "yes", "1")
        elif isinstance(default_val, int):
            try:
                parsed = int(val)
            except ValueError:
                console.print(f"  [error]{key} must be a number[/]")
                return
        elif isinstance(default_val, float):
            try:
                parsed = float(val)
            except ValueError:
                console.print(f"  [error]{key} must be a number[/]")
                return
        else:
            parsed = val

        # Validate specific keys
        if key == "model_preset":
            from forge_models import MODEL_PRESETS, apply_preset
            if val not in MODEL_PRESETS:
                console.print(f"  [error]Unknown preset:[/] {val}")
                for name, p in MODEL_PRESETS.items():
                    console.print(f"    [accent]{name:10s}[/] {p['description']}")
                return
            desc = apply_preset(val)
            parsed = val
            # Also update default_model to match the preset
            self.model = resolve_model(MODEL_PRESETS[val]["default_model"])
            self.config["default_model"] = MODEL_PRESETS[val]["default_model"]
            console.print(f"  [success]Preset applied:[/] {desc}")

        if key == "default_model":
            if val not in MODEL_ALIASES:
                console.print(f"  [error]Unknown model:[/] {val}")
                console.print(f"  [hint]Available: {', '.join(MODEL_ALIASES.keys())}[/]")
                return
            self.model = resolve_model(val)

        if key == "temperature" and not (0.0 <= parsed <= 1.0):
            console.print(f"  [error]Temperature must be between 0.0 and 1.0[/]")
            return

        if key == "max_turns" and parsed < 1:
            console.print(f"  [error]Max turns must be at least 1[/]")
            return

        self.config[key] = parsed
        _save_config(self.config)
        console.print(f"  [success]Set[/] {key} = {parsed}")

    # ── /login ───────────────────────────────────────────────────────────

    async def _cmd_login(self, arg: str) -> None:
        """Set up API credentials for a provider."""
        providers = _check_all_providers()

        if arg:
            # Direct provider login
            if arg in PROVIDER_CREDS:
                await self._login_provider(arg)
                return
            # Try matching by model alias
            prov = _provider_for_model(arg)
            if prov:
                await self._login_provider(prov)
                return
            console.print(f"  [error]Unknown provider:[/] {arg}")
            console.print(f"  [hint]Available: {', '.join(PROVIDER_CREDS.keys())}[/]")
            return

        # Show status and let user choose
        console.print()
        console.print("  [step]API Providers[/]")
        console.print()

        login_choices = [
            Choice(
                title=f"{PROVIDER_CREDS[name]['display']}  {'[ready]' if configured else '[not set up]'}",
                value=name,
                description=f"Models: {', '.join(PROVIDER_CREDS[name]['models'])}",
            )
            for name, configured in providers.items()
        ]
        chosen = await ask_select("Set up which provider?", login_choices, use_shortcuts=True)
        if chosen is None:
            console.print("  [muted]Cancelled.[/]")
            return

        await self._login_provider(chosen)

    # ── /new ─────────────────────────────────────────────────────────────

    async def _cmd_new(self, name: str) -> None:
        if not name:
            console.print("  [hint]Give your project a name:[/]")
            console.print("  [muted]Example: /new my-cool-api[/]")
            return

        base_dir = Path(self.config.get("project_dir", str(Path.home() / "projects")))
        project_dir = base_dir / name
        project_dir.mkdir(parents=True, exist_ok=True)

        from forge_compliance import ComplianceChecker
        cc = ComplianceChecker(project_dir)
        cc.fix()

        self.project_path = project_dir
        _add_recent_project(self.state, str(project_dir), name)
        _save_state(self.state)

        console.print(f"  [success]Created[/] {project_dir}")
        console.print()
        console.print(f"  [hint]Now tell me what to build, or use /plan <goal>[/]")

    # ── /plan ────────────────────────────────────────────────────────────

    async def _cmd_plan(self, goal: str, scope_context: str | None = None) -> None:
        if not goal:
            return

        console.print(f"  [nova]Nova[/] is analyzing your idea...")
        console.print()

        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("Generating project spec and tasks...", total=None)

            from forge_orchestrator import ForgeOrchestrator
            orch = ForgeOrchestrator(self.project_path, model=self.model)
            result = await orch.plan(goal, model=self.model, extra_context=scope_context)

        if result.error and not result.spec_path:
            console.print(f"  [error]Planning failed:[/] {result.error}")
            console.print(f"  [hint]Check that AWS credentials are loaded (source ~/.secrets/hercules.env)[/]")
            return


        # Show spec summary
        if result.spec_path and result.spec_path.exists():
            spec_text = result.spec_path.read_text()
            lines = spec_text.strip().split("\n")
            # Extract title from spec
            title = ""
            for line in lines:
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            if title:
                console.print(f"  [success]Spec:[/] {title}  [muted]({len(lines)} lines)[/]")
            else:
                console.print(f"  [success]Spec:[/] spec.md  [muted]({len(lines)} lines)[/]")

        # Show tasks
        if result.tasks_path and result.tasks_path.exists():
            try:
                tasks_data = json.loads(result.tasks_path.read_text())
                console.print(f"  [success]Plan:[/] {len(tasks_data)} tasks")
                console.print()
                self._render_task_table(tasks_data)

                # Check for file ownership conflicts
                file_owners: dict[str, list[str]] = {}
                for t in tasks_data:
                    for f in t.get("files", []):
                        file_owners.setdefault(f, []).append(t.get("subject", "?"))
                conflicts = {f: owners for f, owners in file_owners.items() if len(owners) > 1}
                if conflicts:
                    console.print()
                    console.print("  [warning]File ownership conflicts:[/]")
                    for f, owners in conflicts.items():
                        console.print(f"    [warning]{f}[/] ← {', '.join(owners)}")
                    console.print("  [hint]Consider merging overlapping tasks before /build[/]")
            except json.JSONDecodeError:
                console.print(f"  [success]Plan:[/] {result.task_count} tasks")
        elif result.task_count > 0:
            console.print(f"  [success]Plan:[/] {result.task_count} tasks ready")

    # ── Single-task executor (used by _cmd_build for parallel waves) ─────

    async def _run_single_task(
        self,
        task: Any,
        store: Any,
        all_tasks: list,
        wave_idx: int,
        formation: Any,
        semaphore: asyncio.Semaphore,
        build_ctx: Any = None,
        cancellation: Any = None,
        display: Any = None,
    ) -> tuple[int, str, str, float, int, int, float, str]:
        """Execute a single task inside a semaphore-limited slot.

        Returns (wave_idx, subject, status, duration_secs, tool_calls, files_count, cost, model_id).
        """
        from forge_agent import ForgeAgent, BUILT_IN_TOOLS

        async with semaphore:
            # Resolve per-task model and tool set from formation role
            role = None
            if formation is not None:
                try:
                    role = self._assign_formation_role(task, formation)
                except Exception:
                    role = None

            if role is not None:
                # User's /model choice overrides formation defaults
                task_model = self.model or resolve_model(role.model)
                # Use max output tokens based on model capability
                from config import get_context_window
                ctx = get_context_window(task_model)
                build_max_tokens = 5120 if ctx >= 200_000 else 4096
                task_mc = get_model_config(task_model, max_tokens=build_max_tokens)
                role_name = role.name
                try:
                    from formations import TOOL_PROFILES
                    from forge_agent import get_tools_for_model
                    base_tools = get_tools_for_model(ctx, has_build_context=build_ctx is not None)
                    allowed_names = TOOL_PROFILES.get(role.tool_policy, set())
                    task_tools = (
                        [t for t in base_tools if t["name"] in allowed_names]
                        if allowed_names
                        else base_tools
                    )
                    if not task_tools:
                        task_tools = base_tools
                except Exception:
                    task_tools = BUILT_IN_TOOLS
            else:
                from config import get_context_window
                ctx = get_context_window(self.model)
                build_max_tokens = 5120 if ctx >= 200_000 else 4096
                task_mc = get_model_config(self.model, max_tokens=build_max_tokens)
                role_name = "implementer"
                from forge_agent import get_tools_for_model
                task_tools = get_tools_for_model(ctx, has_build_context=build_ctx is not None)

            from forge_models import get_escalation_model
            escalation = get_escalation_model(task_mc.model_id)

            # Pre-claim files for this task so parallel agents can't steal ownership
            task_files_meta = (task.metadata or {}).get("files", [])
            if task_files_meta and build_ctx is not None:
                agent_id = f"forge-{role_name}-{task.id}"
                for tf in task_files_meta:
                    build_ctx.claim_file(tf, agent_id)

            # Create per-task event callback that routes to the correct trace
            # When tasks run in parallel, each agent needs its own closure
            # to set _active_task_id before delegating to display.on_event
            if display:
                def _make_event_cb(tid):
                    def _cb(event):
                        display._active_task_id = tid
                        display.on_event(event)
                    return _cb
                task_event_cb = _make_event_cb(task.id)
            else:
                task_event_cb = None

            # Compute adaptive turn budget from task metadata
            _task_meta = task.metadata or {}
            _budget = compute_turn_budget(
                _task_meta,
                max_turns_ceiling=self.config.get("max_turns", 30),
            )

            agent = ForgeAgent(
                model_config=task_mc,
                project_root=self.project_path,
                tools=task_tools,
                max_turns=_budget["soft_limit"],
                soft_max_turns=_budget["soft_limit"],
                agent_id=f"forge-{role_name}-{task.id}",
                escalation_model=escalation,
                build_context=build_ctx,
                cancellation=cancellation,
                on_event=task_event_cb,
            )
            agent._verify_budget = _budget["verify_budget"]
            agent._escalation_turns = _budget["escalation_turns"]

            spec_text = ""
            spec_path = self.project_path / "spec.md"
            if spec_path.exists():
                spec_text = spec_path.read_text()[:4000]

            # Gather upstream artifacts for context
            upstream_context = self._gather_upstream_artifacts(task, store, all_tasks)
            context_sections = []
            if upstream_context:
                for section_content in upstream_context.values():
                    context_sections.append(section_content)

            context_hint = ""
            if context_sections:
                context_hint = "\n\n" + "\n\n".join(context_sections)

            # Also include existing files not from artifacts
            existing = self._gather_project_files()
            if existing:
                file_list = ", ".join(list(existing.keys())[:15])
                context_hint += f"\n\nExisting files in project: {file_list}"

            # Full project file manifest — every agent sees the FULL planned layout
            all_planned_files: dict[str, str] = {}
            for other_task in all_tasks:
                other_files = (other_task.metadata or {}).get("files", [])
                for f in other_files:
                    all_planned_files[f] = other_task.subject

            # File ownership boundaries from planning metadata
            ownership_hint = ""
            task_files = (task.metadata or {}).get("files", [])

            # Read-only enforcement: tasks with no assigned files lose write tools
            is_readonly_task = not task_files
            if is_readonly_task:
                READ_ONLY_TOOLS = {"read_file", "bash", "grep", "glob_files", "list_directory", "think"}
                agent.tools = [t for t in agent.tools if t["name"] in READ_ONLY_TOOLS]
                agent._is_readonly = True
                ownership_hint = (
                    "\n\n## CRITICAL: READ-ONLY TASK\n"
                    "You have NO assigned files. Do NOT write/edit any files.\n"
                    "REPORT bugs — do NOT fix them. Use read_file and bash to investigate."
                )
            elif task_files:
                ownership_hint = (
                    f"\n\n## CRITICAL: File Ownership Boundaries\n"
                    f"You may ONLY create/modify these files: {', '.join(task_files)}\n"
                    f"NEVER write to files not in this list — they are owned by other agents "
                    f"and your writes WILL BE REJECTED. Focus exclusively on your assigned files."
                )

            # Show the full file layout so agents know where ALL files live
            if all_planned_files:
                other_files_map = {f: t for f, t in all_planned_files.items() if f not in task_files}
                if other_files_map:
                    ownership_hint += (
                        f"\n\n## Full Project File Layout\n"
                        f"These files are being created by other agents in parallel. "
                        f"When your code references other files, use THESE EXACT PATHS:\n"
                    )
                    for fpath, tname in sorted(other_files_map.items()):
                        ownership_hint += f"- `{fpath}` (from: {tname})\n"
                    ownership_hint += (
                        f"\nIMPORTANT: If you serve files via Flask/FastAPI/Express, "
                        f"match the exact paths above. For Flask:\n"
                        f"- Files in `templates/` → use `render_template()`\n"
                        f"- Files in `static/` → use `send_static_file()` or `url_for('static')`\n"
                        f"- Do NOT use `send_static_file()` for files in `templates/`"
                    )

            # Downstream awareness — tell agent who depends on their output
            downstream = [t for t in all_tasks if task.id in (t.blocked_by or [])]
            if downstream:
                ownership_hint += "\n\n## Downstream Consumers\n"
                ownership_hint += "These tasks depend on YOUR output — optimize for them:\n"
                for dt in downstream:
                    ownership_hint += f"- {dt.subject}: {(dt.description or '')[:80]}\n"

            # Module dependency context from project index
            try:
                from forge_index import get_or_create_index
                idx = get_or_create_index(self.project_path)
                dep_context = idx.to_dependency_context(
                    task_files if task_files else [],
                    budget_chars=1500
                )
                if dep_context:
                    context_hint += f"\n\n## Module Dependencies\n{dep_context}"
            except Exception:
                pass

            # Build mandatory read instruction for tasks with dependencies
            mandatory_reads = []
            for dep_id in (task.blocked_by or []):
                dep = store.get(dep_id)
                if dep and dep.artifacts:
                    for fpath in dep.artifacts.keys():
                        short = self._shorten_path(fpath)
                        if short.endswith(('.py', '.js', '.ts', '.jsx', '.tsx')):
                            mandatory_reads.append(short)
            mandatory_reads = list(dict.fromkeys(mandatory_reads))[:8]

            read_instruction = ""
            if mandatory_reads:
                read_instruction = (
                    f"\n\n## MANDATORY: Read Before Writing\n"
                    f"Your task depends on these upstream files. You MUST call read_file on each one "
                    f"BEFORE writing any code that imports from or interacts with them:\n"
                    + ", ".join(mandatory_reads) + "\n"
                    f"Do NOT assume what functions, classes, or APIs these files contain. "
                    f"Read them and use their ACTUAL interface."
                )

            # Extract spec constraints (negations like "NOT SQLAlchemy", "do NOT use X")
            spec_constraints = ""
            if spec_text:
                import re as _re
                constraints = []
                for m in _re.finditer(
                    r'(?:NOT|not|never|NEVER|do not|Do not|don\'t|Don\'t|avoid|AVOID)\s+(?:use\s+)?(\S+(?:\s+\S+)?)',
                    spec_text,
                ):
                    constraints.append(m.group(0).strip())
                # Also extract explicit tech choices from task description
                for m in _re.finditer(
                    r'(?:NOT|not|never|NEVER|do not|Do not)\s+(?:use\s+)?(\S+(?:\s+\S+)?)',
                    task.description or "",
                ):
                    constraints.append(m.group(0).strip())
                if constraints:
                    unique = list(dict.fromkeys(constraints))[:6]
                    spec_constraints = (
                        "\n\n## SPEC CONSTRAINTS — MUST FOLLOW\n"
                        + "\n".join(f"- {c}" for c in unique)
                    )

            expected_files = (task.metadata or {}).get("files", [])

            # Output limit coaching for small-context models
            chunk_hint = ""
            if ctx <= 32_000:
                chunk_hint = (
                    "\n\n## OUTPUT LIMIT\n"
                    "You have a ~4K token output limit (~80 lines of code per tool call).\n"
                    "NEVER write more than 80 lines in a single write_file call.\n"
                    "Strategy: write_file (first 80 lines) → append_file (next 80) → repeat.\n"
                )

            # Context budget: truncate context_hint if total prompt exceeds 35% of context window
            max_prompt_chars = int(ctx * 0.35 * 4)  # 35% of context, ~4 chars/token
            total = len(spec_text) + len(ownership_hint) + len(context_hint) + len(read_instruction) + len(spec_constraints) + 500
            if total > max_prompt_chars:
                budget = max(2000, max_prompt_chars - (total - len(context_hint)))
                logger.warning("Task %s: context truncated from %d to %d chars", task.subject, total, max_prompt_chars)
                context_hint = context_hint[:budget] + "\n... (truncated to fit context window)"

            files_hint = ", ".join(expected_files) if expected_files else "NONE — read-only task"
            prompt = (
                f"## Project Spec\n{spec_text}\n\n"
                f"## Your Task\n{task.subject}: {task.description}\n\n"
                f"## Your Files\n"
                f"You MUST create these files: {files_hint}\n"
                f"Only write YOUR files. Do NOT create files that belong to other tasks.\n\n"
                f"## Instructions\n"
                f"Implement this task COMPLETELY. Use write_file to create EVERY file listed above. "
                f"CRITICAL: Include ALL functions listed in the task description. "
                f"Write the first ~80 lines via write_file, then IMMEDIATELY call append_file for the rest. "
                f"Get ALL functions down in write_file + append_file calls before moving to the next file. "
                f"Do NOT write a partial file (e.g. only init_db) and edit it later. "
                f"Read existing files first with read_file if you need context. "
                f"Write complete, working code — not stubs or placeholders. "
                f"Do NOT describe file contents in text — use write_file/append_file tools with the full content."
                f"{chunk_hint}"
                f"{spec_constraints}"
                f"{read_instruction}"
                f"{ownership_hint}"
                f"{context_hint}"
            )

            # V11-grade system prompt
            from prompt_builder import PromptBuilder
            pb = PromptBuilder(self.project_path)
            system_prompt = pb.build_system_prompt(
                role="builder",
                project_context=spec_text[:2000] if spec_text else "",
                model_id=task_mc.model_id,
                autonomy_level=4,  # Automated builds always A4 (autonomous) — no human in loop
            )

            # Pass acceptance criteria to agent for self-test during self-correction
            acceptance = (task.metadata or {}).get("acceptance_criteria", [])
            if acceptance and isinstance(acceptance, list):
                agent._acceptance_criteria = acceptance

            wave_start = time.time()
            try:
                result = await agent.run(prompt=prompt, system=system_prompt)
                duration = time.time() - wave_start
                tc = result.tool_calls_made
                fc = len(result.artifacts) if result.artifacts else 0

                # Detect no-write completion: task expected to create files but wrote nothing
                if expected_files and fc == 0 and not result.error:
                    retry_prompt = (
                        f"You completed the task description but did NOT use the write_file tool to create any files.\n"
                        f"You MUST create the following files using the write_file tool: {', '.join(expected_files)}\n"
                        f"Do NOT describe what to write — actually call write_file with the full file content.\n\n"
                        f"Original task:\n{prompt}"
                    )
                    result = await agent.run(prompt=retry_prompt, system=system_prompt)
                    duration = time.time() - wave_start
                    tc += result.tool_calls_made
                    fc = len(result.artifacts) if result.artifacts else 0

                # Detect stub files: task wrote files but content is placeholder/skeleton
                if expected_files and not result.error:
                    stub_files = []
                    min_size = {"py": 100, "js": 200, "html": 200, "css": 100}
                    for fpath in expected_files:
                        full = self.project_path / fpath
                        if full.exists():
                            size = full.stat().st_size
                            ext = fpath.rsplit(".", 1)[-1] if "." in fpath else ""
                            threshold = min_size.get(ext, 100)
                            if size < threshold:
                                stub_files.append(f"{fpath} ({size} bytes)")
                    if stub_files:
                        retry_prompt = (
                            f"You wrote these files but they are STUBS or PLACEHOLDERS with almost no content:\n"
                            f"{', '.join(stub_files)}\n\n"
                            f"You MUST rewrite them with COMPLETE, FULLY FUNCTIONAL code. "
                            f"For large files, use write_file for the initial section then append_file for remaining sections. "
                            f"Do NOT write comments like 'implement here' or 'placeholder'. "
                            f"Write the ACTUAL working implementation.\n\n"
                            f"Original task:\n{prompt}"
                        )
                        result = await agent.run(prompt=retry_prompt, system=system_prompt)
                        duration = time.time() - wave_start
                        tc += result.tool_calls_made
                        fc = max(fc, len(result.artifacts) if result.artifacts else 0)

                from forge_models import estimate_cost
                task_cost = estimate_cost(task_mc.model_id, result.tokens_in, result.tokens_out)
                model_used = result.model_id or task_mc.model_id

                if result.error == "paused":
                    store.update(task.id, status="pending", artifacts=result.artifacts)
                    return (wave_idx, task.subject, "paused", duration, tc, fc, task_cost, model_used)
                elif result.error:
                    store.update(task.id, status="failed", artifacts=result.artifacts)
                    return (wave_idx, task.subject, "fail", duration, tc, fc, task_cost, model_used)
                else:
                    store.update(task.id, status="completed", artifacts=result.artifacts)
                    return (wave_idx, task.subject, "pass", duration, tc, fc, task_cost, model_used)

            except Exception as exc:
                duration = time.time() - wave_start
                _raw = getattr(agent, '_last_artifacts', None)
                partial = _raw if isinstance(_raw, dict) else {}
                logger.warning("Task %s crashed: %s", task.subject, exc, exc_info=True)
                store.update(task.id, status="failed", artifacts=partial)
                return (wave_idx, task.subject, "fail", duration, 0, 0, 0.0, "")

    def _assign_formation_role(self, task: Any, formation: Any) -> Any:
        """Map a task to the best-matching formation role by keyword heuristics."""
        role_by_name: dict[str, Any] = {r.name: r for r in formation.roles}

        # Priority 1: Explicit agent hint in task metadata
        agent_hint = (task.metadata or {}).get("agent", "")
        if agent_hint and agent_hint in role_by_name:
            return role_by_name[agent_hint]

        desc = (task.subject + " " + (task.description or "")).lower()
        role_keywords: dict[str, list[str]] = {
            "backend-impl":   ["backend", "api", "server", "route", "endpoint", "database", "model"],
            "frontend-impl":  ["frontend", "ui", "component", "page", "html", "css", "style"],
            "integrator":     ["integrate", "connect", "wire", "compose", "nginx", "docker-compose"],
            "architect":      ["architect", "design", "structure", "scaffold", "plan", "config"],
            "impl-1":         ["scaffold", "skeleton", "build", "create", "implement", "add", "write"],
            "impl-2":         ["database", "schema", "migration", "seed", "sql"],
            "implementer":    ["build", "create", "implement", "add", "write", "feature"],
            "tester":         ["test", "spec", "verify", "check", "validate", "coverage"],
            "optimizer":      ["optim", "perf", "speed", "profile", "benchmark", "cache"],
            "investigator-1": ["debug", "bug", "fix", "trace", "error"],
            "investigator-2": ["log", "metric", "temporal", "history", "timeline"],
            "investigator-3": ["isolat", "reproduc", "minimal", "repro"],
            "threat-modeler": ["threat", "model", "risk", "attack", "surface"],
            "scanner":        ["scan", "audit", "cve", "dependency", "vuln"],
            "fixer":          ["fix", "patch", "remediat", "harden"],
            "reviewer-1":     ["security", "auth", "injection", "secret"],
            "reviewer-2":     ["perf", "speed", "n+1", "memory", "complexity"],
            "reviewer-3":     ["coverage", "test", "edge", "case", "gap"],
        }

        best_role_name = None
        best_score = 0
        for r in formation.roles:
            keywords = role_keywords.get(r.name, [])
            score = sum(1 for kw in keywords if kw in desc)
            if score > best_score:
                best_score = score
                best_role_name = r.name

        if not best_role_name:
            for r in formation.roles:
                if any(x in r.name for x in ("impl", "build", "implement")):
                    best_role_name = r.name
                    break
        if not best_role_name:
            best_role_name = formation.roles[0].name

        return role_by_name[best_role_name]

    # ── Artifact handoff helpers ──────────────────────────────────────────

    def _shorten_path(self, path_str: str) -> str:
        """Convert absolute path to relative."""
        try:
            return str(Path(path_str).relative_to(self.project_path))
        except (ValueError, TypeError):
            return str(Path(path_str).name)

    def _shorten_paths(self, paths: list) -> list:
        return [self._shorten_path(p) for p in paths]

    def _extract_exports_from_files(self, file_paths: list) -> list:
        """Extract function/class signatures and API endpoints using AST (with regex fallback)."""
        import ast as _ast
        exports = []
        for rel_path in file_paths:
            full_path = self.project_path / rel_path
            if not full_path.exists() or full_path.suffix != '.py':
                continue
            try:
                content = full_path.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(content)
                for node in _ast.iter_child_nodes(tree):
                    if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        if not node.name.startswith('_'):
                            args = ", ".join(a.arg for a in node.args.args if a.arg != 'self')
                            exports.append(f"- `{rel_path}`: def {node.name}({args})")
                    elif isinstance(node, _ast.ClassDef) and not node.name.startswith('_'):
                        # Extract public methods
                        methods = [
                            n.name for n in _ast.iter_child_nodes(node)
                            if isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef))
                            and not n.name.startswith('_')
                        ]
                        base_str = ""
                        if node.bases:
                            base_str = "(" + ", ".join(
                                getattr(b, 'id', getattr(b, 'attr', '?'))
                                for b in node.bases
                            ) + ")"
                        method_str = f" [{', '.join(methods)}]" if methods else ""
                        exports.append(f"- `{rel_path}`: class {node.name}{base_str}{method_str}")
                # Also capture route decorators (not in AST node types directly)
                for line in content.split('\n'):
                    stripped = line.strip()
                    if '@app.route' in stripped or '@router.' in stripped:
                        exports.append(f"- `{rel_path}`: {stripped}")
            except SyntaxError:
                # Fallback to regex for files with syntax errors
                try:
                    for line in content.split('\n'):
                        stripped = line.strip()
                        if stripped.startswith('def ') and not stripped.startswith('def _'):
                            sig = stripped.split('):')[0] + ')'
                            exports.append(f"- `{rel_path}`: {sig}")
                        elif stripped.startswith('class ') and not stripped.startswith('class _'):
                            name = stripped.split('(')[0].split(':')[0].replace('class ', '')
                            exports.append(f"- `{rel_path}`: class {name}")
                except Exception:
                    continue
            except Exception:
                continue
        return exports[:30]

    def _extract_interface_summary(self, path: Path, max_chars: int = 500) -> str:
        """Extract compact interface summary using AST. Fallback to filename."""
        if not path.exists() or path.suffix != ".py":
            return str(path.name)
        try:
            import ast as _ast
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            parts = []
            for node in _ast.iter_child_nodes(tree):
                if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                    if not node.name.startswith("_"):
                        args = ", ".join(a.arg for a in node.args.args if a.arg != "self")
                        parts.append(f"{node.name}({args})")
                elif isinstance(node, _ast.ClassDef) and not node.name.startswith("_"):
                    parts.append(f"class {node.name}")
            return "; ".join(parts)[:max_chars] if parts else str(path.name)
        except Exception:
            return str(path.name)

    def _gather_upstream_artifacts(
        self,
        task: Any,
        store: Any,
        all_tasks: list,
    ) -> dict:
        """Gather structured artifact context from upstream tasks (including failed with artifacts)."""
        context: dict = {}

        # 1. Direct dependencies' artifacts (completed AND failed-with-artifacts)
        upstream_artifacts = []
        for dep_id in task.blocked_by:
            dep = store.get(dep_id)
            if dep and dep.artifacts:
                upstream_artifacts.append({
                    "task": dep.subject,
                    "status": dep.status,
                    "files": list(dep.artifacts.keys()),
                    "details": dep.artifacts,
                })

        if upstream_artifacts:
            lines = ["## Upstream Task Results"]
            for ua in upstream_artifacts:
                lines.append(f"\n### {ua['task']}")
                if ua.get("status") == "failed":
                    lines.append("**WARNING**: Upstream task failed — files may be incomplete. READ them before use.")
                lines.append(f"Files created: {', '.join(self._shorten_paths(ua['files']))}")
                for fpath, info in list(ua['details'].items())[:5]:
                    short = self._shorten_path(fpath)
                    action = info.get('action', 'unknown') if isinstance(info, dict) else 'written'
                    lines.append(f"  - {short} ({action})")
                    # Inline interface summary for .py files
                    full = self.project_path / short
                    if full.suffix == ".py" and full.exists():
                        iface = self._extract_interface_summary(full)
                        if iface != full.name:
                            lines.append(f"    Interface: {iface}")
            context["upstream_results"] = "\n".join(lines)

        # 2. All project files from completed/failed tasks (with artifacts)
        all_created_files = []
        for t in all_tasks:
            if t.status in ("completed", "failed") and t.artifacts:
                for fpath in t.artifacts.keys():
                    short = self._shorten_path(fpath)
                    all_created_files.append(short)

        if all_created_files:
            context["project_files"] = (
                "## Project Files (created by prior tasks)\n" +
                ", ".join(sorted(set(all_created_files)))
            )

        # 3. Auto-extract exports/endpoints
        exports = self._extract_exports_from_files(all_created_files)
        if exports:
            context["available_exports"] = (
                "## Available Imports & Endpoints\n" +
                "\n".join(exports)
            )

        return context

    # ── /build ───────────────────────────────────────────────────────────

    async def _cmd_build(self, arg: str) -> None:
        from forge_tasks import TaskStore
        from config import ForgeProject as FP

        project = FP(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()

        if not tasks:
            console.print("  [hint]No tasks yet. Describe what you want to build and I'll plan it first.[/]")
            return

        pending = [t for t in tasks if t.status == "pending"]
        failed_tasks = [t for t in tasks if t.status == "failed"]
        stale_active = [t for t in tasks if t.status == "in_progress"]
        retryable = pending + failed_tasks + stale_active

        if not retryable:
            console.print("  [success]All tasks already complete![/]")
            self._cmd_status()
            self._suggest_next_action()
            return

        # Reset failed and stale in_progress tasks to pending for retry
        for t in failed_tasks + stale_active:
            store.update(t.id, status="pending")

        # ── Formation selection ───────────────────────────────────────────
        formation = None
        try:
            from formations import select_formation

            n = len(tasks)
            scope = "small" if n <= 3 else ("medium" if n <= 8 else "large")

            complex_keywords = {"architecture", "design", "integrate", "migrate", "refactor", "security", "oauth"}
            all_text = " ".join(
                (t.subject + " " + (t.description or "")).lower() for t in tasks
            )
            if any(kw in all_text for kw in complex_keywords):
                complexity = "complex"
            elif n <= 3:
                complexity = "routine"
            else:
                complexity = "medium"

            formation = select_formation(complexity=complexity, scope=scope)
        except Exception:
            formation = None

        if formation is not None:
            console.print(
                f"  [accent]Formation:[/] {formation.name} "
                f"({len(formation.roles)} roles)"
            )
            for role in formation.roles:
                wave_idx_hint = next(
                    (i for i, wave in enumerate(formation.wave_order) if role.name in wave),
                    0,
                )
                display_model = self.model or role.model
                console.print(
                    f"    [muted]Wave {wave_idx_hint}:[/] {role.name} "
                    f"[muted]({_short_model(display_model)})[/]"
                )
            console.print()

        # ── Build context for multi-agent coordination ────────────────────
        from forge_comms import BuildContext, BuildCancellation
        build_ctx = BuildContext(self.project_path)

        # ── Cooperative cancellation (Ctrl-C -> pause menu) ──────────────
        cancellation = BuildCancellation()
        cancellation.install()

        # ── Execute waves ─────────────────────────────────────────────────
        console.print(f"  [nova]Nova[/] is building your project...")
        console.print()

        try:
            waves = store.compute_waves()
        except ValueError as exc:
            console.print(f"  [error]Dependency issue:[/] {exc}")
            cancellation.uninstall()
            return

        total_start = time.time()
        total_tool_calls = 0
        total_files = 0
        wave_results: list[tuple[int, str, str, float]] = []
        build_paused = False
        self._build_total_cost = 0.0  # Reset cost tracking for this build

        # ── Live progress display ────────────────────────────────────────
        from forge_display import BuildDisplay
        display = BuildDisplay(total_tasks=len(retryable))
        progress = display.create_progress()

        wave_idx = 0
        with progress:
          while wave_idx < len(waves):
            # Check for pause at wave boundary
            if cancellation.is_paused():
                progress.stop()
                choice = await self._show_pause_menu(store)
                if choice == "resume":
                    cancellation.reset()
                    waves = store.compute_waves()
                    wave_idx = 0  # restart; existing filter skips completed
                    progress.start()
                    continue
                else:  # cancel or None
                    build_paused = True
                    break

            wave_tasks = waves[wave_idx]
            runnable = [
                t for t in wave_tasks
                if store.get(t.id) and store.get(t.id).status not in ("completed", "blocked")
            ]
            if not runnable:
                for t in wave_tasks:
                    wave_results.append((wave_idx, t.subject, "skip", 0.0))
                wave_idx += 1
                continue

            # Determine concurrency limit based on provider
            provider = get_provider(self.model)
            max_concurrent = PROVIDER_CONCURRENCY.get(provider, 4)
            semaphore = asyncio.Semaphore(min(max_concurrent, len(runnable)))

            # Wave header — print through progress context
            console.print()
            console.print(f"  ", end="")
            console.print(wave_header(wave_idx, len(waves), len(runnable)))
            if len(runnable) > 1:
                console.print(
                    f"  [muted]({len(runnable)} tasks in parallel, max {semaphore._value} concurrent)[/]"
                )

            # Mark all runnable tasks as in_progress before launching
            for task in runnable:
                store.update(task.id, status="in_progress")

            # Notify display of each starting task
            for task in runnable:
                display.start_task(task.id, task.subject)

            # Launch all tasks in this wave concurrently (with live display)
            coros = [
                self._run_single_task(task, store, tasks, wave_idx, formation, semaphore, build_ctx, cancellation, display)
                for task in runnable
            ]
            results = await asyncio.gather(*coros, return_exceptions=True)

            # Process results
            total_cost = getattr(self, '_build_total_cost', 0.0)
            any_paused = False
            for i, result in enumerate(results):
                task = runnable[i]
                if isinstance(result, Exception):
                    store.update(task.id, status="failed")
                    wave_results.append((wave_idx, task.subject, "fail", 0.0))
                    display.end_task(task.id, passed=False, suppress_output=True)
                    console.print(
                        f"  [error]{task.subject}[/]  [muted](error: {result})[/]"
                    )
                else:
                    w_idx, name, status, dur, tc, fc, cost, model_used = result
                    wave_results.append((w_idx, name, status, dur))
                    total_tool_calls += tc
                    total_files += fc
                    total_cost += cost
                    short_m = _short_model(model_used) if model_used else ""
                    from forge_models import format_cost
                    cost_str = f"  {format_cost(cost)}" if cost > 0 else ""
                    model_str = f"  {short_m}" if short_m else ""
                    if status == "pass":
                        display.end_task(task.id, passed=True, suppress_output=True)
                        console.print(f"  [success]\u2713[/] {name}  [muted]{dur:.0f}s{model_str}{cost_str}[/]")
                    elif status == "fail":
                        display.end_task(task.id, passed=False, suppress_output=True)
                        console.print(f"  [error]\u2717[/] {name}  [muted]{dur:.0f}s{model_str}{cost_str}[/]")
                    elif status == "paused":
                        console.print(f"  [warning]\u23f8[/] {name}  [muted](paused)[/]")
                        any_paused = True
                    else:
                        console.print(f"  [muted]\u2022 {name}  (skipped)[/]")
            self._build_total_cost = total_cost

            # If any task was paused, check cancellation at wave boundary
            if any_paused and cancellation.is_paused():
                progress.stop()
                display.update_pause_visual()
                choice = await self._show_pause_menu(store)
                if choice == "resume":
                    cancellation.reset()
                    waves = store.compute_waves()
                    wave_idx = 0
                    progress.start()
                    continue
                else:
                    build_paused = True
                    break

            wave_idx += 1

        total_duration = time.time() - total_start
        self._sync_task_state()

        # Write artifact manifest
        artifact_manifest = {}
        for t in store.list():
            if t.artifacts:
                artifact_manifest[t.id] = {
                    "task": t.subject,
                    "status": t.status,
                    "files": list(t.artifacts.keys()),
                }
        if artifact_manifest:
            manifest_dir = self.project_path / "artifacts"
            manifest_dir.mkdir(exist_ok=True)
            (manifest_dir / "index.json").write_text(
                json.dumps(artifact_manifest, indent=2)
            )

        # ── Post-build pipeline: verify → integration-check → re-verify → gate ──
        if not build_paused:
            # Step 1: Early verification BEFORE integration-check can touch files
            pre_verify_result = None
            if "--no-verify" not in arg:
                pre_verify_result = await self._run_verification(store)

            # Step 2: Integration check (formation-based) — only if issues found
            from forge_verify import scan_file_references
            issues = scan_file_references(self.project_path)
            if issues:
                console.print()
                console.print(f"  [warning]Integration issues detected ({len(issues)}):[/]")
                for iss in issues[:5]:
                    console.print(f"    [muted]• {iss}[/]")
                console.print("  [info]Running integration-check formation (auditor → fixer → verifier)...[/]")

                # Gather all generated files for context
                all_gen_files = []
                for t in store.list():
                    if t.status == "completed" and t.metadata:
                        all_gen_files.extend(t.metadata.get("files", []))
                all_gen_files = list(dict.fromkeys(all_gen_files))

                # Extract affected files from issue descriptions (Fix 6: scope fixer)
                import re as _re_fix
                affected_files = set()
                for iss in issues:
                    # Extract filenames like "app.py:", "templates/index.html", etc.
                    for m in _re_fix.finditer(r'(\S+\.(?:py|js|html|css|json|yml|yaml))', iss):
                        candidate = m.group(1).rstrip(":")
                        if candidate in all_gen_files or any(candidate in f for f in all_gen_files):
                            affected_files.add(candidate)
                # If we couldn't extract specific files, fall back to matching gen files
                if not affected_files:
                    affected_files = set(all_gen_files)
                fixer_files = sorted(affected_files)

                # Create integration tasks
                issues_text = "\n".join(f"- {iss}" for iss in issues)
                file_tree_text = "\n".join(f"  {f}" for f in all_gen_files)

                auditor_task = store.create(
                    subject="Audit cross-file integration",
                    description=(
                        f"Read ALL project files and identify cross-file issues.\n\n"
                        f"Known issues from static scan:\n{issues_text}\n\n"
                        f"Project files:\n{file_tree_text}\n\n"
                        f"Read each file listed above. For each issue, explain:\n"
                        f"1. What file has the problem\n"
                        f"2. What the code does (e.g. send_static_file('index.html'))\n"
                        f"3. Where the referenced file actually is (e.g. templates/index.html)\n"
                        f"4. What the fix should be (e.g. change to render_template, add import)\n"
                    ),
                    metadata={"agent": "auditor", "files": [], "integration_fix": True},
                )
                fixer_task = store.create(
                    subject="Fix integration issues",
                    description=(
                        f"Fix the cross-file issues identified by the auditor.\n\n"
                        f"CRITICAL RULES:\n"
                        f"- render_template() is a STANDALONE function: from flask import render_template\n"
                        f"- It is NOT a method on app: NEVER write app.render_template()\n"
                        f"- Files in templates/ → use render_template()\n"
                        f"- Files in static/ → use send_static_file() or url_for('static')\n"
                        f"- Always READ the file BEFORE editing to see its actual content\n"
                        f"- Only fix the specific issues — do not rewrite entire files\n"
                    ),
                    metadata={"agent": "fixer", "files": fixer_files, "integration_fix": True},
                    blocked_by=[auditor_task.id],
                )
                verifier_task = store.create(
                    subject="Verify integration fixes",
                    description=(
                        f"Verify the app works after fixes:\n"
                        f"1. Read app.py to find the port number\n"
                        f"2. Run: python3 app.py & (start server in background)\n"
                        f"3. Wait 2 seconds, then run: curl -s -o /dev/null -w '%{{http_code}}' http://localhost:PORT/\n"
                        f"4. Confirm status is 200\n"
                        f"5. Run: kill %1 (stop server)\n"
                        f"If status is not 200, report what went wrong."
                    ),
                    metadata={"agent": "verifier", "files": [], "integration_fix": True},
                    blocked_by=[fixer_task.id],
                )

                # Run integration tasks using the integration-check formation
                from formations import get_formation
                fix_formation = get_formation("integration-check")
                fix_tasks = [auditor_task, fixer_task, verifier_task]
                fix_waves = store.compute_waves()

                # Filter to only integration fix tasks
                for fw_idx, fw_tasks in enumerate(fix_waves):
                    fix_runnable = [
                        t for t in fw_tasks
                        if (t.metadata or {}).get("integration_fix")
                        and store.get(t.id) and store.get(t.id).status not in ("completed", "blocked")
                    ]
                    if not fix_runnable:
                        continue

                    provider = get_provider(self.model)
                    max_concurrent = PROVIDER_CONCURRENCY.get(provider, 4)
                    fix_sem = asyncio.Semaphore(min(max_concurrent, len(fix_runnable)))

                    for ft in fix_runnable:
                        store.update(ft.id, status="in_progress")
                        console.print(f"    [muted]Running {ft.subject}...[/]")

                    fix_coros = [
                        self._run_single_task(
                            ft, store, fix_tasks, fw_idx, fix_formation,
                            fix_sem, build_ctx, cancellation, display
                        )
                        for ft in fix_runnable
                    ]
                    fix_results = await asyncio.gather(*fix_coros, return_exceptions=True)

                    for i, fr in enumerate(fix_results):
                        ft = fix_runnable[i]
                        if isinstance(fr, Exception):
                            store.update(ft.id, status="failed")
                            console.print(f"    [error]✗[/] {ft.subject}  [muted](error: {fr})[/]")
                        else:
                            _, name, status, dur, tc, fc, cost, model_used = fr
                            short_m = _short_model(model_used) if model_used else ""
                            if status == "pass":
                                console.print(f"    [success]✓[/] {name}  [muted]{dur:.0f}s  {short_m}[/]")
                            else:
                                console.print(f"    [error]✗[/] {name}  [muted]{dur:.0f}s  {short_m}[/]")

                # Re-check if issues are resolved
                remaining = scan_file_references(self.project_path)
                if remaining:
                    console.print(f"  [warning]Still {len(remaining)} integration issue(s) after fix attempt[/]")
                else:
                    console.print("  [success]Integration issues resolved![/]")

            # Step 3: Re-verify after integration fixes (if fixes were applied)
            verify_result = pre_verify_result
            if "--no-verify" not in arg and issues:
                verify_result = await self._run_verification(store)

            # Step 4: Gate review (skip with --no-review)
            if "--no-review" not in arg:
                spec_text = ""
                spec_path = self.project_path / "spec.md"
                if spec_path.exists():
                    spec_text = spec_path.read_text()[:4000]
                with Progress(SpinnerColumn("dots"), TextColumn("[muted]Gate review...[/]"), TimeElapsedColumn(), console=console, transient=True) as p:
                    p.add_task("gate", total=None)
                    gate_result = await self._run_gate_review(store, spec_text)
                gate_status = gate_result["status"]
                if gate_status == "pass":
                    console.print("  [success]Gate: PASS[/]")
                elif gate_status == "fail":
                    console.print(f"  [error]Gate: FAIL[/] — {gate_result['summary']}")
                else:
                    console.print(f"  [warning]Gate: CONDITIONAL[/] — {gate_result['summary']}")

            # Step 5: L4 Functional gate — repair if functional tests failed
            if "--no-verify" not in arg and verify_result:
                await self._functional_repair_gate(store, arg, verify_result)

        # Post-build integrity check: verify expected files exist on disk
        missing_files = []
        for task_entry in store.list():
            if task_entry.status == "completed":
                for fpath in (task_entry.metadata or {}).get("files", []):
                    full = self.project_path / fpath
                    if not full.exists():
                        missing_files.append(fpath)
        if missing_files:
            console.print()
            console.print(f"  [warning]Missing files ({len(missing_files)}):[/] {', '.join(missing_files[:8])}")
            console.print(f"  [hint]Tasks completed but these files were never written to disk.[/]")

        # Summary line
        passed = sum(1 for _, _, s, _ in wave_results if s == "pass")
        failed = sum(1 for _, _, s, _ in wave_results if s == "fail")
        paused_count = sum(1 for _, _, s, _ in wave_results if s == "paused")
        total_cost = getattr(self, '_build_total_cost', 0.0)
        from forge_models import format_cost
        console.print()

        # Structured build summary panel
        summary_parts = []
        result_parts = []
        if passed:
            result_parts.append(f"[success]{passed} passed[/]")
        if failed:
            result_parts.append(f"[error]{failed} failed[/]")
        if paused_count:
            result_parts.append(f"[warning]{paused_count} paused[/]")
        sep = " \u00b7 "
        summary_parts.append(f"  Result     {sep.join(result_parts)}")
        summary_parts.append(f"  Duration   [step]{total_duration:.1f}s[/]")
        summary_parts.append(f"  Tools      {total_tool_calls} calls")
        if total_cost > 0:
            summary_parts.append(f"  Cost       {format_cost(total_cost)}")

        # Communication stats
        if build_ctx:
            cs = build_ctx.stats()
            if cs["claims"] > 0 or cs["announcements"] > 0:
                summary_parts.append(
                    f"  Coord      {cs['claims']} claims, {cs['conflicts']} conflicts blocked"
                )

        border_color = BRAND["green"] if failed == 0 else BRAND["orange"]
        console.print(Panel(
            "\n".join(summary_parts),
            title=f"[bold {BRAND['accent2']}] Build Summary [/]",
            border_style=border_color,
            padding=(0, 1),
        ))

        if passed == 0 and failed > 0:
            console.print()
            console.print("  [error]All tasks failed.[/] Possible causes:")
            console.print("    • Model credentials expired — run /login")
            console.print("    • Model rate-limited — wait and retry, or /model to switch")
            console.print("    • Task descriptions too vague — try more specific goals")
            console.print("    • Context window exceeded — use a larger model (/model nova-pro)")
            console.print()
            console.print("  [hint]Run /builds for detailed failure logs, or /build to retry.[/]")

        # File tree for generated files
        all_files = self._list_project_files()
        if all_files:
            console.print(file_tree(all_files[:20], str(self.project_path)))

        # Skill progression tracking removed — was never wired up

        # Save build log (proof-of-work)
        if display and display.traces:
            spec_text = ""
            spec_path = self.project_path / "spec.md"
            if spec_path.exists():
                try:
                    spec_text = spec_path.read_text()[:200]
                except Exception:
                    pass
            log_path = display.save_build_log(
                self.project_path,
                model_id=self.model,
                goal=spec_text,
            )
            if log_path:
                console.print(f"  [muted]Build log: {log_path.relative_to(self.project_path)}[/]")

        # Auto-preview on successful build
        if not build_paused and passed > 0 and "--no-preview" not in arg:
            await self._auto_preview()

        cancellation.uninstall()

        if build_paused:
            console.print()
            console.print("  [hint]Build paused. Run /build again to resume from pending tasks.[/]")
        else:
            self._suggest_next_action()

    async def _show_pause_menu(self, store: Any) -> str | None:
        """Show interactive pause menu after Ctrl-C during build.

        Returns "resume", "cancel", or None (on Ctrl-C in menu = cancel).
        """
        completed = len([t for t in store.list() if t.status == "completed"])
        total = len(store.list())
        pending = len([t for t in store.list() if t.status in ("pending", "in_progress")])
        console.print()
        console.print(
            f"  [warning]Build paused.[/]  "
            f"{completed}/{total} tasks completed, {pending} remaining."
        )
        console.print()

        choice = await ask_select("What would you like to do?", [
            Choice("Resume build", value="resume", description="Continue from where you left off"),
            Choice("Cancel build", value="cancel", description="Stop and keep completed work"),
        ])
        return choice

    async def _run_gate_review(self, store: Any, spec_text: str) -> dict:
        """Run adversarial gate review on build artifacts.

        Returns: {"status": "pass"|"fail"|"conditional", "issues": [...], "summary": "..."}
        """
        from forge_agent import ForgeAgent, BUILT_IN_TOOLS

        # Read-only tools for review agent
        review_tools = [
            t for t in BUILT_IN_TOOLS
            if t["name"] in {"read_file", "glob_files", "grep", "list_directory", "think"}
        ]

        mc = get_model_config(self.model, max_tokens=4096)
        agent = ForgeAgent(
            model_config=mc,
            project_root=self.project_path,
            tools=review_tools,
            max_turns=10,
            agent_id="forge-gate-reviewer",
        )

        # Gather file list from completed tasks
        completed_tasks = store.list(status="completed")
        file_list = []
        for t in completed_tasks:
            if t.artifacts:
                file_list.extend(t.artifacts.keys())

        files_str = "\n".join(f"- {self._shorten_path(f)}" for f in file_list[:20])
        task_str = "\n".join(f"- [{t.id}] {t.subject}" for t in completed_tasks)

        prompt = (
            f"## Gate Review\n\n"
            f"Review the build output. The project should match the spec.\n\n"
            f"### Spec\n{spec_text[:2000]}\n\n"
            f"### Completed Tasks\n{task_str}\n\n"
            f"### Files Created\n{files_str}\n\n"
            f"### Your Instructions\n"
            f"1. Use glob_files and read_file to examine the created files\n"
            f"2. Check that: files exist, no syntax errors, imports resolve, "
            f"code is complete (not stubs)\n"
            f"3. End your response with exactly one of these on its own line:\n"
            f"   GATE: PASS\n"
            f"   GATE: FAIL - [reason]\n"
            f"   GATE: CONDITIONAL - [issues to fix]\n"
        )

        system = (
            "You are a code reviewer. Examine the files carefully. "
            "Be strict: placeholder code, empty functions, or missing imports = FAIL. "
            "Working code with minor style issues = PASS. "
            "Working code with some gaps = CONDITIONAL."
        )

        try:
            result = await agent.run(prompt=prompt, system=system)
            output = result.output or ""

            if "GATE: PASS" in output:
                return {"status": "pass", "issues": [], "summary": "All checks passed"}
            elif "GATE: FAIL" in output:
                reason = output.split("GATE: FAIL")[-1].strip().lstrip("- ").strip()
                return {"status": "fail", "issues": [reason], "summary": reason[:200]}
            elif "GATE: CONDITIONAL" in output:
                reason = output.split("GATE: CONDITIONAL")[-1].strip().lstrip("- ").strip()
                return {"status": "conditional", "issues": [reason], "summary": reason[:200]}
            else:
                return {
                    "status": "conditional",
                    "issues": ["Review agent did not produce a clear verdict"],
                    "summary": output[:200],
                }
        except Exception as exc:
            return {
                "status": "conditional",
                "issues": [f"Gate review error: {exc}"],
                "summary": str(exc)[:200],
            }

    async def _run_verification(self, store: Any) -> "VerifyResult | None":
        """Run runtime verification: start app, test with browser, report results.

        Returns the VerifyResult so callers (e.g. functional repair gate) can
        inspect L4 results without re-running the entire pipeline.
        """
        from forge_verify import BuildVerifier

        spec_text = ""
        spec_path = self.project_path / "spec.md"
        if spec_path.exists():
            try:
                spec_text = spec_path.read_text()[:4000]
            except Exception:
                pass

        verifier = BuildVerifier(self.project_path, spec_text=spec_text)

        try:
            with Progress(SpinnerColumn("dots"), TextColumn("[muted]Verifying build...[/]"), TimeElapsedColumn(), console=console, transient=True) as p:
                p.add_task("verify", total=None)
                completed = store.list(status="completed") if store else []
                vr = await verifier.verify(tasks=completed)

            # Agent-repair file reference mismatches before reporting
            failed_checks = [c for c in vr.checks if not c.passed and c.name in ("file_references", "root_route")]
            if failed_checks:
                console.print("  [info]Running repair agent for file reference issues...[/]")
                repaired = await self._agent_repair_preview(0)
                if repaired:
                    console.print("  [info]Repair agent applied fixes — re-verifying...[/]")
                    vr = await verifier.verify(tasks=completed)

            if vr.status == "pass":
                console.print(f"  [success]Verify: PASS[/] — {vr.summary}")
            elif vr.status == "fail":
                console.print(f"  [error]Verify: FAIL[/] — {vr.summary}")
            else:
                console.print(f"  [warning]Verify: PARTIAL[/] — {vr.summary}")

            # Show individual check results
            for check in vr.checks:
                icon = "[success]OK[/]" if check.passed else "[error]FAIL[/]"
                console.print(f"    {icon}  {check.name}: {check.detail[:80]}")
                if check.evidence_path:
                    console.print(f"         [muted]screenshot: {check.evidence_path}[/]")
            return vr
        except Exception as exc:
            console.print(f"  [warning]Verify: SKIP[/] — {exc}")
            return None

    async def _agent_repair_preview(self, port: int) -> bool:
        """Run a ForgeAgent to diagnose and fix why GET / fails.

        The agent reads the project files and error logs, identifies the issue,
        and fixes it. Returns True if repair was attempted.
        """
        from forge_agent import ForgeAgent
        from config import get_model_config

        # Build a file tree for context
        files = []
        for f in sorted(self.project_path.rglob("*")):
            if f.is_file() and not any(p in f.parts for p in (".forge", "__pycache__", "artifacts", ".git", "node_modules")):
                rel = f.relative_to(self.project_path)
                files.append(str(rel))

        file_tree_text = "\n".join(f"  {f}" for f in files[:30])

        # Read server error log for actual traceback
        error_log = ""
        err_log_path = self.project_path / ".forge" / "preview-stderr.log"
        if err_log_path.exists():
            try:
                log_text = err_log_path.read_text()
                # Get the last 2000 chars (most recent errors)
                error_log = log_text[-2000:] if len(log_text) > 2000 else log_text
            except Exception:
                pass

        # Also try hitting the URL with debug info
        error_response = ""
        if port > 0:
            import urllib.request
            import urllib.error
            try:
                req = urllib.request.Request(
                    f"http://localhost:{port}/",
                    headers={"User-Agent": "NovaForge/1.0"}
                )
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                try:
                    error_response = e.read().decode("utf-8", errors="replace")[:1500]
                except Exception:
                    pass
            except Exception:
                pass

        error_section = ""
        if error_log:
            error_section += f"\n\n## Server Error Log (stderr)\n```\n{error_log}\n```"
        if error_response:
            error_section += f"\n\n## HTTP Error Response Body\n```\n{error_response}\n```"

        prompt = (
            f"## Problem\n"
            f"This web app's GET / is broken. The server starts but returns an error.\n\n"
            f"## Project Files\n{file_tree_text}\n"
            f"{error_section}\n\n"
            f"## Your Task\n"
            f"1. Read app.py (or the main server file) to see how / is routed\n"
            f"2. Check for these common issues:\n"
            f"   - app.send_static_file('x.html') but file is in templates/ → use render_template('x.html') instead (it's a standalone function, NOT app.render_template)\n"
            f"   - render_template('x.html') but file is in static/ → copy file to templates/ directory\n"
            f"   - Missing import: render_template must be imported from flask (e.g. from flask import Flask, render_template)\n"
            f"   - No route for / → add @app.route('/') def index(): ...\n"
            f"   - Template syntax errors (Jinja2 {{ }} conflicts with JS) → use {{% raw %}} blocks\n"
            f"   - Missing os import when os.environ is used\n"
            f"3. Read the actual files involved to confirm the issue\n"
            f"4. Fix the code using edit_file or write_file\n\n"
            f"IMPORTANT: Actually fix the code. Do NOT just describe what to do — use your tools."
        )

        task_mc = get_model_config(self.model, max_tokens=4096)
        agent = ForgeAgent(
            model_config=task_mc,
            project_root=self.project_path,
            max_turns=8,
            agent_id="forge-preview-repair",
        )

        try:
            result = await agent.run(prompt=prompt, system="You are a code repair agent. Fix the broken web app route. Be concise — read, diagnose, fix.")
            return result.tool_calls_made > 0 and not result.error
        except Exception:
            return False

    async def _functional_repair_gate(self, store: Any, arg: str, initial_result: Any = None) -> None:
        """Dispatch repair agents for L4 functional test failures.

        Uses the VerifyResult from _run_verification (passed as initial_result)
        for the first check to avoid re-running the entire L1-L4 pipeline.
        Only re-runs verification after a repair agent makes changes.
        Up to 2 repair cycles.
        """
        from forge_verify import BuildVerifier

        spec_text = ""
        spec_path = self.project_path / "spec.md"
        if spec_path.exists():
            try:
                spec_text = spec_path.read_text()[:4000]
            except Exception:
                pass

        for repair_cycle in range(2):
            # First iteration: reuse the VerifyResult from _run_verification
            # Subsequent iterations: re-run verification after repair
            if repair_cycle == 0 and initial_result is not None:
                vr = initial_result
            else:
                verifier = BuildVerifier(self.project_path, spec_text=spec_text)
                try:
                    completed = store.list(status="completed") if store else []
                    vr = await verifier.verify(tasks=completed)
                except Exception:
                    return

            # Find L4 functional failures
            functional_fails = [
                c for c in vr.checks
                if not c.passed and c.name in (
                    "button_functionality", "form_roundtrip", "api_roundtrip",
                    "js_handlers", "functional_summary",
                )
            ]

            if not functional_fails:
                if repair_cycle > 0:
                    console.print("  [success]Functional gate: PASS[/] (after repair)")
                return

            # Report failures
            console.print()
            fail_details = [f"{c.name}: {c.detail}" for c in functional_fails]
            if repair_cycle == 0:
                console.print(f"  [warning]Functional gate: {len(functional_fails)} issue(s) detected[/]")
            for fd in fail_details[:5]:
                console.print(f"    [muted]• {fd[:100]}[/]")

            # Spawn repair agent
            console.print(f"  [info]Running functional repair agent (cycle {repair_cycle + 1}/2)...[/]")

            from forge_agent import ForgeAgent
            repair_mc = get_model_config(self.model, max_tokens=4096)

            # Build file tree for context
            all_gen_files = []
            for t in store.list():
                if t.status == "completed" and t.metadata:
                    all_gen_files.extend(t.metadata.get("files", []))
            all_gen_files = list(dict.fromkeys(all_gen_files))
            file_tree_text = "\n".join(f"  {f}" for f in all_gen_files[:20])

            failures_text = "\n".join(f"- {fd}" for fd in fail_details)
            repair_prompt = (
                f"## Functional Test Failures\n"
                f"The app loads but these features DON'T WORK:\n\n"
                f"{failures_text}\n\n"
                f"## Project Files\n{file_tree_text}\n\n"
                f"## Your Task\n"
                f"1. Read the files involved (start with the main HTML and JS files)\n"
                f"2. For each failure:\n"
                f"   - 'button_functionality': Find the button, check its event listener. "
                f"     Wire it to the correct API call with fetch() and update the DOM.\n"
                f"   - 'form_roundtrip': Check the form's submit handler. "
                f"     Ensure it POSTs data and refreshes the list.\n"
                f"   - 'api_roundtrip': Check the API route returns proper JSON.\n"
                f"   - 'js_handlers': Add addEventListener to unwired buttons.\n"
                f"3. Fix each issue using edit_file\n"
                f"4. After fixing, test with bash: start the server and curl the endpoints\n\n"
                f"CRITICAL: Actually FIX the code. Do not describe what to do — use your tools."
            )

            # Restrict repair agent tools: edit only, no full rewrites
            REPAIR_TOOLS = {"read_file", "edit_file", "bash", "grep", "glob_files", "list_directory", "think"}
            from forge_agent import BUILT_IN_TOOLS as _ALL_TOOLS
            repair_tools = [t for t in _ALL_TOOLS if t["name"] in REPAIR_TOOLS]

            repair_agent = ForgeAgent(
                model_config=repair_mc,
                project_root=self.project_path,
                tools=repair_tools,
                max_turns=12,
                agent_id=f"forge-functional-repair-{repair_cycle}",
            )

            try:
                repair_result = await repair_agent.run(
                    prompt=repair_prompt,
                    system=(
                        "You are a code repair agent specializing in fixing broken web app features. "
                        "Read the code, find why buttons/forms don't work, and fix them. "
                        "Common issues: missing event listeners, fetch() not calling the right URL, "
                        "DOM not updated after API response, missing function implementations. "
                        "Be concise — read, diagnose, fix."
                    ),
                )
                if repair_result.tool_calls_made > 0:
                    console.print(f"    [success]Repair agent made {repair_result.tool_calls_made} tool calls[/]")
                else:
                    console.print(f"    [warning]Repair agent made no changes[/]")
                    return  # No point re-verifying if nothing changed
            except Exception as e:
                console.print(f"    [error]Repair agent failed: {e}[/]")
                return

        # Final report after max repair cycles
        console.print("  [warning]Functional gate: some issues may remain after 2 repair cycles[/]")

    # ── /status ──────────────────────────────────────────────────────────

    def _cmd_status(self) -> None:
        summary = self._get_task_summary()
        if not summary:
            console.print("  [hint]No project in progress. Tell me what you want to build![/]")
            return

        total = summary["total"]
        done = summary["completed"]

        console.print()
        console.print(f"  [bold {BRAND['accent2']}]{self.project_path.name}[/]")
        console.print(f"  {status_bar(done, total)}")

        # Show autonomy level
        autonomy_lvl = self.assistant.read_autonomy_level()
        autonomy_bar = self.assistant.format_autonomy_bar(autonomy_lvl)
        console.print(f"  [muted]Autonomy:[/] {autonomy_bar}  [dim](/autonomy to change)[/]")
        console.print()

        parts = []
        if done:
            parts.append(f"[success]{done} done[/]")
        if summary["in_progress"]:
            parts.append(f"[cyan]{summary['in_progress']} active[/]")
        if summary["pending"]:
            parts.append(f"[muted]{summary['pending']} pending[/]")
        if summary["failed"]:
            parts.append(f"[error]{summary['failed']} failed[/]")
        console.print(f"  {' | '.join(parts)}")

        all_files = self._list_project_files()
        if all_files:
            total_size = sum(
                (self.project_path / f).stat().st_size
                for f in all_files
                if (self.project_path / f).exists()
            )
            console.print(f"  [muted]{len(all_files)} files ({total_size:,} bytes)[/]")

        self._suggest_next_action()

    # ── /health ──────────────────────────────────────────────────────────

    def _cmd_health(self) -> None:
        """Show system health dashboard — preview, model, project state."""
        import shutil as _shutil

        console.print()
        table = Table(title="System Health", box=box.ROUNDED, border_style="dim")
        table.add_column("Component", style="bold")
        table.add_column("Status")
        table.add_column("Detail", style="dim")

        # Preview status
        if self._preview_mgr:
            h = self._preview_mgr.health()
            if h["running"]:
                table.add_row("Preview", "[green]● Running[/]", f"{h.get('stack', '?')} on port {h.get('port', '?')}")
            else:
                table.add_row("Preview", "[red]● Down[/]", "Use /preview to start")
        else:
            table.add_row("Preview", "[dim]● Not started[/]", "Use /preview to start")

        # cloudflared binary
        cf = _shutil.which("cloudflared")
        table.add_row("cloudflared", "[green]● Found[/]" if cf else "[red]● Missing[/]",
                       cf or "Install cloudflared for preview tunnels")

        # Model availability
        model_id = self.model
        provider = get_provider(model_id)
        try:
            cfg = get_model_config(model_id)
            table.add_row("Model", "[green]● Ready[/]", f"{cfg.short_name} ({provider})")
        except Exception:
            table.add_row("Model", "[red]● Error[/]", f"{model_id} ({provider})")

        # Project state
        summary = self._get_task_summary()
        if summary:
            total = summary["total"]
            done = summary["completed"]
            failed = summary["failed"]
            detail = f"{done}/{total} done"
            if failed:
                detail += f", {failed} failed"
            table.add_row("Project", "[green]● Active[/]", detail)
        else:
            table.add_row("Project", "[dim]● No tasks[/]", str(self.project_path))

        # Disk space
        try:
            usage = _shutil.disk_usage(str(self.project_path))
            free_gb = usage.free / (1024 ** 3)
            color = "green" if free_gb > 5 else "yellow" if free_gb > 1 else "red"
            table.add_row("Disk", f"[{color}]● {free_gb:.1f} GB free[/]", str(self.project_path))
        except Exception:
            table.add_row("Disk", "[dim]● Unknown[/]", "")

        console.print(table)
        console.print()

    # ── /tasks ───────────────────────────────────────────────────────────

    def _cmd_tasks(self) -> None:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()

        if not tasks:
            console.print("  [hint]No tasks yet. Describe what you want to build![/]")
            return

        # Compute wave assignments for display
        try:
            waves = store.compute_waves()
            task_wave_map: dict[int, int] = {}
            for wi, wave in enumerate(waves):
                for wt in wave:
                    task_wave_map[wt.id] = wi
        except Exception:
            task_wave_map = {}

        table = Table(
            box=box.ROUNDED, show_header=True,
            header_style=f"bold {BRAND['cyan']}", padding=(0, 1),
            title=f"[bold {BRAND['accent2']}]{self.project_path.name}[/]",
            border_style=BRAND["accent"],
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Wave", width=5)
        table.add_column("Task", min_width=30)
        table.add_column("Status", width=10)
        table.add_column("Risk", width=6)

        status_icons = {
            "completed":   f"[{BRAND['green']}]\u2713 done[/]",
            "in_progress": f"[{BRAND['cyan']}]\u26a1 active[/]",
            "pending":     f"[dim]\u25cb pending[/]",
            "failed":      f"[{BRAND['red']}]\u2717 failed[/]",
            "blocked":     f"[{BRAND['orange']}]\u23f8 blocked[/]",
        }

        for t in tasks:
            risk = t.metadata.get("risk", "")
            risk_style = {
                "high": f"[{BRAND['red']}]high[/]",
                "medium": f"[{BRAND['orange']}]med[/]",
                "low": f"[{BRAND['green']}]low[/]",
            }.get(risk, "[muted]-[/]")
            wave_str = str(task_wave_map[t.id]) if t.id in task_wave_map else "-"
            table.add_row(
                str(t.id),
                f"[dim]{wave_str}[/]",
                t.subject,
                status_icons.get(t.status, t.status),
                risk_style,
            )

        console.print()
        console.print(table)
        self._suggest_next_action()

    # ── /models ──────────────────────────────────────────────────────────

    def _cmd_models(self) -> None:
        from forge_models import MODEL_CAPABILITIES, PHASE_DEFAULTS, format_cost as fmt_cost
        providers = _check_all_providers()

        table = Table(
            box=box.ROUNDED, show_header=True,
            header_style="bold cyan", padding=(0, 1),
        )
        table.add_column("Model", style="bold", min_width=15)
        table.add_column("Provider", width=12)
        table.add_column("Cost/1K in", width=12)
        table.add_column("Context", width=10)
        table.add_column("Strengths", min_width=20)
        table.add_column("Escalates to", width=14)
        table.add_column("Status", width=12)

        for alias, cap in MODEL_CAPABILITIES.items():
            prov_name = {"bedrock": "Bedrock", "openai": "OpenRouter", "anthropic": "Anthropic"}.get(cap.provider, cap.provider)
            prov = _provider_for_model(alias)
            ready = providers.get(prov, False)
            if cap.model_id == self.model:
                status = "[success]active[/]"
            elif ready:
                status = "[success]ready[/]"
            else:
                status = "[muted]needs /login[/]"
            ctx = f"{cap.context_window // 1000}K"
            esc = cap.escalation_target or "-"
            table.add_row(alias, prov_name, f"${cap.cost_per_1k_input:.5f}", ctx, ", ".join(cap.strengths), esc, status)

        console.print()
        console.print(table)
        console.print()
        console.print("  [step]Smart Defaults:[/]")
        for phase, alias in PHASE_DEFAULTS.items():
            console.print(f"    {phase:12s} -> [accent]{alias}[/]")
        console.print(f"\n  [hint]Switch: /model <alias>  |  Set up: /login <provider>[/]")

    # ── /formation ───────────────────────────────────────────────────────

    async def _cmd_formation(self, arg: str) -> None:
        from formations import FORMATIONS, select_formation, get_formation

        if not arg:
            # Interactive formation selection
            choices = [
                Choice(
                    title=f"{name:24s}  {len(f.roles)} roles, {len(f.wave_order)} waves",
                    value=name,
                    description=f.description,
                )
                for name, f in FORMATIONS.items()
            ]

            selected = await ask_select(
                "View formation details",
                choices,
                use_search_filter=True,
            )
            if selected is None:
                return
            arg = selected

        try:
            f = get_formation(arg)
        except (KeyError, ValueError):
            console.print(f"  [error]Unknown formation:[/] {arg}")
            console.print(f"  [muted]Available: {', '.join(FORMATIONS.keys())}[/]")
            return
        console.print(f"  [bold]{f.name}[/] -- {f.description}")
        for role in f.roles:
            console.print(
                f"    [accent]{role.name:20s}[/] model={_short_model(role.model)} "
                f"policy={role.tool_policy}"
            )
        console.print(f"  [muted]Waves: {len(f.wave_order)}[/]")
        for i, wave in enumerate(f.wave_order):
            console.print(f"    Wave {i}: {', '.join(wave)}")

    # ── /builds ──────────────────────────────────────────────────────────

    def _cmd_builds(self, arg: str) -> None:
        """Show past build logs with detailed proof-of-work."""
        builds_dir = self.project_path / ".forge" / "builds"
        if not builds_dir.exists():
            console.print("  [muted]No build logs yet. Run /build first![/]")
            return

        log_files = sorted(builds_dir.glob("build_*.jsonl"), reverse=True)
        if not log_files:
            console.print("  [muted]No build logs found.[/]")
            return

        # Show specific build or list
        if arg and arg.isdigit():
            idx = int(arg) - 1
            if idx < 0 or idx >= len(log_files):
                console.print(f"  [error]Build #{arg} not found. Have {len(log_files)} builds.[/]")
                return
            self._show_build_detail(log_files[idx])
        else:
            console.print(f"  [info]Build History[/] ({len(log_files)} builds)")
            console.print()
            table = Table(
                box=box.SIMPLE, show_header=True,
                header_style="bold", padding=(0, 1),
            )
            table.add_column("#", width=3)
            table.add_column("Date", width=19)
            table.add_column("Result", width=12)
            table.add_column("Tasks", width=6)
            table.add_column("Duration", width=9)
            table.add_column("Turns", width=6)
            table.add_column("Tools", width=6)
            table.add_column("Tokens", width=12)
            table.add_column("Model", width=15)

            for i, lf in enumerate(log_files[:20]):
                try:
                    first_line = lf.read_text().split("\n")[0]
                    h = json.loads(first_line)
                    if h.get("type") != "build_summary":
                        continue
                    ts = h.get("timestamp", "")[:19].replace("T", " ")
                    p = h.get("tasks_passed", 0)
                    f = h.get("tasks_failed", 0)
                    result = f"[success]{p}p[/]" + (f" [error]{f}f[/]" if f else "")
                    tok_in = h.get("total_tokens_in", 0)
                    tok_out = h.get("total_tokens_out", 0)
                    tok = f"{tok_in + tok_out:,}" if tok_in + tok_out > 0 else "-"
                    model = h.get("model", "")
                    if "/" in model:
                        model = model.split("/")[-1][:15]
                    table.add_row(
                        str(i + 1),
                        ts,
                        result,
                        str(h.get("tasks_total", "?")),
                        f"{h.get('duration_s', 0):.0f}s",
                        str(h.get("total_turns", "-")),
                        str(h.get("total_tool_calls", "-")),
                        tok,
                        model,
                    )
                except Exception:
                    continue

            console.print(table)
            console.print()
            console.print("  [hint]Type /builds N for detailed view of build #N[/]")

    def _show_build_detail(self, log_file: Path) -> None:
        """Show detailed view of a single build log."""
        lines = [ln for ln in log_file.read_text().strip().split("\n") if ln.strip()]
        if not lines:
            console.print("  [muted]Empty build log.[/]")
            return

        try:
            header = json.loads(lines[0])
        except json.JSONDecodeError:
            console.print("  [error]Corrupt build log — cannot parse header.[/]")
            return
        console.print()
        console.print(f"  [bold]Build Detail[/]  {header.get('timestamp', '')[:19]}")
        console.print(f"  Model:    {header.get('model', '?')}")
        console.print(f"  Duration: {header.get('duration_s', 0):.1f}s")
        console.print(f"  Tasks:    {header.get('tasks_passed', 0)} passed, {header.get('tasks_failed', 0)} failed")
        console.print(f"  Turns:    {header.get('total_turns', 0)}")
        console.print(f"  Tools:    {header.get('total_tool_calls', 0)} calls")
        tok_in = header.get("total_tokens_in", 0)
        tok_out = header.get("total_tokens_out", 0)
        if tok_in or tok_out:
            console.print(f"  Tokens:   {tok_in:,} in / {tok_out:,} out")
        model_ms = header.get("total_model_ms", 0)
        tool_ms = header.get("total_tool_ms", 0)
        if model_ms or tool_ms:
            console.print(f"  Time:     LLM {model_ms/1000:.1f}s / Tools {tool_ms/1000:.1f}s")

        files = header.get("files_created", [])
        if files:
            console.print(f"  Created:  {', '.join(files[:10])}")

        # Per-task breakdown
        console.print()
        table = Table(
            box=box.SIMPLE, show_header=True,
            header_style="bold", padding=(0, 1),
        )
        table.add_column("Task", min_width=30)
        table.add_column("Status", width=7)
        table.add_column("Time", width=7)
        table.add_column("Turns", width=6)
        table.add_column("Tools", width=6)
        table.add_column("Files", min_width=20)

        for line in lines[1:]:
            try:
                t = json.loads(line)
                if t.get("type") != "task_trace":
                    continue
                status = "[success]pass[/]" if t.get("status") == "passed" else "[error]fail[/]"
                written = t.get("files_written", [])
                edited = t.get("files_edited", [])
                file_str = ", ".join(
                    [f"+{f.split('/')[-1]}" for f in written[:3]] +
                    [f"~{f.split('/')[-1]}" for f in edited[:2]]
                )
                table.add_row(
                    t.get("subject", "?")[:35],
                    status,
                    f"{t.get('duration_s', 0):.0f}s",
                    str(t.get("turns", "-")),
                    str(t.get("tool_calls", "-")),
                    file_str,
                )
            except Exception:
                continue

        console.print(table)

    # ── /audit ───────────────────────────────────────────────────────────

    def _cmd_audit(self) -> None:
        project = ForgeProject(root=self.project_path)
        audit_file = project.audit_dir / "audit.jsonl"

        if not audit_file.exists():
            console.print("  [muted]No audit log yet. Build something first![/]")
            return

        lines = [ln for ln in audit_file.read_text().strip().split("\n") if ln.strip()]
        console.print(f"  [info]Audit log:[/] {len(lines)} entries")
        console.print()

        table = Table(
            box=box.SIMPLE, show_header=True,
            header_style="bold", padding=(0, 1),
        )
        table.add_column("Time", width=10)
        table.add_column("Tool", width=12)
        table.add_column("Outcome", width=10)
        table.add_column("Agent", min_width=20)

        for line in lines[-15:]:
            try:
                entry = json.loads(line)
                ts = entry.get("timestamp", "?")
                if "T" in ts:
                    ts = ts.split("T")[1][:8]
                table.add_row(
                    ts,
                    entry.get("tool", "?"),
                    entry.get("outcome", "?"),
                    entry.get("agent_id", "?"),
                )
            except json.JSONDecodeError:
                continue

        console.print(table)

    # ── Auto-preview ──────────────────────────────────────────────────

    async def _auto_preview(self) -> None:
        """Automatically start preview after a successful build."""
        from forge_preview import PreviewManager, PreviewError, detect_stack

        try:
            si = detect_stack(self.project_path)
            if si.kind == "unknown":
                return  # No servable app — silently skip

            console.print()
            console.print("  [info]Starting preview...[/]")

            if self._preview_mgr is None:
                self._preview_mgr = PreviewManager(self.project_path)

            preview_url = self._preview_mgr.start(stack_info=si)
            is_local = preview_url.startswith("http://localhost")

            # Verify root route works, agent-repair if 404
            import urllib.request
            import urllib.error
            check_url = f"http://localhost:{si.port}/"
            try:
                req = urllib.request.Request(check_url, headers={"User-Agent": "NovaForge/1.0"})
                resp = urllib.request.urlopen(req, timeout=5)
                root_status = resp.status
            except urllib.error.HTTPError as e:
                root_status = e.code
            except Exception:
                root_status = 0

            if root_status >= 400:
                console.print(f"  [warning]GET / returned {root_status} — running repair agent...[/]")
                repaired = await self._agent_repair_preview(si.port)
                if repaired:
                    self._preview_mgr.stop()
                    self._preview_mgr = PreviewManager(self.project_path)
                    preview_url = self._preview_mgr.start(stack_info=si)
                    is_local = preview_url.startswith("http://localhost")
                    console.print("  [info]Repair agent fixed the issue[/]")

            title = "[bold green] Local Preview [/]" if is_local else "[bold green] Live Preview [/]"
            console.print(Panel(
                f"[bold green]{preview_url}[/]\n\n"
                f"  [muted]Stack: {si.kind} ({si.entry})[/]\n"
                f"  [muted]Type [accent]/preview stop[/] to shut down.[/]",
                border_style="green",
                title=title,
                padding=(1, 2),
            ))
        except PreviewError as e:
            console.print(f"  [warning]Preview: {e}[/]")
        except Exception as e:
            console.print(f"  [warning]Preview failed: {e}[/]")

    # ── /preview ────────────────────────────────────────────────────────

    async def _cmd_preview(self, arg: str) -> None:
        """Launch Cloudflare Tunnel for live preview."""
        from forge_preview import PreviewManager, PreviewError

        if arg == "stop":
            if self._preview_mgr and self._preview_mgr.is_running:
                self._preview_mgr.stop()
                self._preview_mgr = None
                console.print("  [success]Preview stopped.[/]")
            else:
                console.print("  [muted]No preview running.[/]")
            return

        if arg == "status":
            if self._preview_mgr:
                h = self._preview_mgr.health()
                status = "[success]healthy[/]" if h["running"] else "[error]unhealthy[/]"
                console.print(f"  Preview: {status}  |  {h.get('stack', '?')} on port {h.get('port', '?')}")
                if h.get("url"):
                    console.print(f"  URL: [bold green]{h['url']}[/]")
            else:
                console.print("  [muted]No preview running.[/]")
            return

        # Smart recovery: if preview already running, try ensure_healthy() first
        if self._preview_mgr and self._preview_mgr.is_running:
            console.print("  [info]Preview already running — checking health...[/]")
            if self._preview_mgr.ensure_healthy():
                console.print(f"  [success]Preview healthy:[/] {self._preview_mgr.url}")
                return
            console.print("  [warning]Preview unhealthy — restarting...[/]")
            self._preview_mgr.stop()
            self._preview_mgr = None

        # Create (or replace) the manager
        if self._preview_mgr is None:
            self._preview_mgr = PreviewManager(self.project_path)

        try:
            from forge_preview import detect_stack
            si = detect_stack(self.project_path)

            rel = si.cwd.relative_to(self.project_path) if si.cwd != self.project_path else Path(".")
            loc = f" from {rel}/" if str(rel) != "." else ""
            console.print(f"  [info]Preview:[/] {self.project_path.name} ({si.kind}{loc}) on port {si.port}")
            console.print(f"  [muted]Server: {si.server_cmd}[/]")
            from forge_preview import _ensure_cloudflared
            cf_path = _ensure_cloudflared()
            if cf_path:
                console.print("  Starting Cloudflare Tunnel (up to 3 retries)...")
            else:
                console.print("  [muted]cloudflared unavailable — starting local preview[/]")

            preview_url = self._preview_mgr.start(stack_info=si)
            is_local = preview_url.startswith("http://localhost")

            # Verify root route actually works before showing success
            import urllib.request
            import urllib.error
            check_url = f"http://localhost:{si.port}/"
            try:
                req = urllib.request.Request(check_url, headers={"User-Agent": "NovaForge/1.0"})
                resp = urllib.request.urlopen(req, timeout=5)
                root_status = resp.status
            except urllib.error.HTTPError as e:
                root_status = e.code
            except Exception:
                root_status = 0

            if root_status >= 400:
                label = "404 — route/file mismatch" if root_status == 404 else f"{root_status} — server error"
                console.print(f"  [warning]GET / returned {label} — running repair agent...[/]")
                repaired = await self._agent_repair_preview(si.port)
                if repaired:
                    # Restart server with fixed code
                    self._preview_mgr.stop()
                    self._preview_mgr = PreviewManager(self.project_path)
                    preview_url = self._preview_mgr.start(stack_info=si)
                    is_local = preview_url.startswith("http://localhost")
                    try:
                        req = urllib.request.Request(check_url, headers={"User-Agent": "NovaForge/1.0"})
                        resp = urllib.request.urlopen(req, timeout=5)
                        root_status = resp.status
                    except urllib.error.HTTPError as e:
                        root_status = e.code
                    except Exception:
                        root_status = 0
                    if 200 <= root_status < 400:
                        console.print("  [success]Repair agent fixed the issue![/]")
                    else:
                        console.print(f"  [warning]Repair agent ran but GET / still returns {root_status}[/]")
                else:
                    console.print("  [warning]Repair agent could not fix — app may not serve correctly[/]")

            console.print()
            if is_local:
                console.print(Panel(
                    f"[bold green]{preview_url}[/]\n\n"
                    f"  [muted]Local preview — install cloudflared for a public URL.\n"
                    f"  Type [accent]/preview stop[/] to shut down.\n"
                    f"  Type [accent]/preview status[/] to check health.[/]",
                    border_style="green",
                    title="[bold green] Local Preview [/]",
                    padding=(1, 2),
                ))
            else:
                console.print(Panel(
                    f"[bold green]{preview_url}[/]\n\n"
                    f"  [muted]Type [accent]/preview stop[/] to shut down.\n"
                    f"  Type [accent]/preview status[/] to check health.[/]",
                    border_style="green",
                    title="[bold green] Live Preview [/]",
                    padding=(1, 2),
                ))
        except PreviewError as e:
            err = str(e)
            console.print(f"  [error]{err}[/]")
            # Actionable suggestions based on error
            if "cloudflared not found" in err:
                console.print("  [hint]Install: sudo apt install cloudflared  or  brew install cloudflare-warp[/]")
            elif "port" in err.lower():
                console.print(f"  [hint]Port conflict? Try: /preview stop  then  /preview[/]")
            elif "entry point" in err.lower():
                console.print("  [hint]Create app.py (Flask/FastAPI), package.json, index.html, or Dockerfile[/]")
            elif "tunnel" in err.lower():
                console.print("  [hint]Network issue? Check internet connection and try again.[/]")

    # ── /competition ────────────────────────────────────────────────────

    def _cmd_competition(self) -> None:
        """Run Amazon Nova Hackathon submission validation."""
        try:
            from forge_competition import CompetitionValidator
            validator = CompetitionValidator(self.project_path)
            checks = validator.run_all()

            table = Table(title="Competition Readiness", box=box.ROUNDED, border_style="dim")
            table.add_column("Check", style="bold")
            table.add_column("Status")
            table.add_column("Detail", style="dim")
            table.add_column("Fix", style="italic")

            passed = 0
            for check in checks:
                icon = "[green]✓[/]" if check.passed else "[red]✗[/]"
                if check.passed:
                    passed += 1
                table.add_row(check.name, icon, check.detail, check.fix_suggestion or "")

            console.print()
            console.print(table)
            pct = int(passed / len(checks) * 100) if checks else 0
            color = "green" if pct >= 80 else "yellow" if pct >= 60 else "red"
            console.print(f"\n  [{color}]{passed}/{len(checks)} checks passed ({pct}%)[/]")
            console.print()
        except ImportError:
            console.print("  [error]forge_competition.py not found.[/]")

    # ── /deploy ─────────────────────────────────────────────────────────

    def _cmd_deploy(self, arg: str) -> None:
        """Deploy project with Docker + nginx."""
        console.print("  [info]Deploy:[/] Coming soon!")
        console.print()
        console.print("  [muted]For now, use the CLI command:[/]")
        console.print(f"    [accent]forge deploy --domain yourapp.example.com[/]")
        console.print()
        console.print("  [hint]Or try /preview for a quick shareable URL.[/]")

    # ── /autonomy ────────────────────────────────────────────────────────

    async def _cmd_autonomy(self, arg: str) -> None:
        """View, explain, or change the autonomy level (A0-A5)."""
        from forge_display import display_autonomy_panel
        from forge_guards import _LEVEL_NAMES

        arg = arg.strip()

        # /autonomy ? or /autonomy explain
        if arg in ("?", "explain", "help"):
            console.print()
            console.print(Panel(
                self.assistant.explain_all_autonomy_levels(),
                title="[bold] All Autonomy Levels [/]",
                border_style="cyan",
                padding=(1, 2),
            ))
            console.print()
            console.print("  [hint]Set with: /autonomy 0  through  /autonomy 5[/]")
            return

        # /autonomy <number>
        if arg and arg.isdigit():
            new_level = int(arg)
        elif not arg:
            # No argument — show current state then offer interactive selection
            current = self.assistant.read_autonomy_level()
            console.print()
            display_autonomy_panel(current, self.assistant.skill_level)
            console.print()

            level_descriptions = {
                0: "Ask for everything",
                1: "Read freely, ask before writing",
                2: "Read/write freely, ask for risky commands",
                3: "Handle most things independently",
                4: "Full autopilot",
                5: "Background/CI execution with audit logging",
            }

            choices = [
                Choice(
                    title=f"A{i}  {_LEVEL_NAMES[i]:14s}",
                    value=str(i),
                    description=level_descriptions[i],
                )
                for i in range(6)
            ]

            selected = await ask_select(
                "Set autonomy level",
                choices,
                default=str(current),
            )
            if selected is None or selected == str(current):
                return
            new_level = int(selected)
        else:
            console.print(f"  [error]Usage: /autonomy [0-5 | ? | explain][/]")
            return

        if new_level not in range(6):
            console.print(f"  [error]Level must be 0-5[/]")
            return

        if self.assistant.set_autonomy_level(new_level):
            console.print()
            console.print(f"  [success]Autonomy set to A{new_level}[/]")
            console.print()
            explanation = self.assistant.explain_autonomy(new_level)
            console.print(f"  {explanation}")
            console.print()

            # For beginners: show a note about what changes
            if self.assistant.skill_level == "beginner" and new_level >= 3:
                console.print(
                    "  [warning]Note:[/] At A3+, Nova handles most things without asking.\n"
                    "  Lower back to A2 with [accent]/autonomy 2[/] if you want more control."
                )
                console.print()
        else:
            console.print("  [error]Failed to set autonomy level.[/]")

    # ── /guide ────────────────────────────────────────────────────────────

    async def _cmd_guide(self, arg: str) -> None:
        """Smart project setup wizard — conversational alternative to /interview."""
        console.print()
        console.print(Panel(
            "Answer a few questions and Nova will set up the optimal configuration.\n"
            "  [muted]Press Enter to accept suggestions · Ctrl-C to cancel[/]",
            title="[brand] Project Guide [/]",
            border_style="bright_magenta",
            padding=(0, 2),
        ))
        console.print()

        # Step 1: skill level check
        if not self.assistant._skill_detected:
            skill_choice = await ask_select(
                "What's your experience level?",
                [
                    Choice(
                        title="Beginner",
                        value="beginner",
                        description="New to coding or Nova Forge — extra guidance",
                    ),
                    Choice(
                        title="Intermediate",
                        value="intermediate",
                        description="Comfortable with code and CLIs",
                    ),
                    Choice(
                        title="Expert",
                        value="expert",
                        description="Experienced developer — minimal hand-holding",
                    ),
                ],
                default="intermediate",
                use_shortcuts=True,
            )
            if skill_choice:
                self.assistant.set_skill_level(skill_choice)
            else:
                console.print("  [muted]Cancelled.[/]")
                return

        from forge_display import display_skill_detection, display_assistant_hint
        display_skill_detection(self.assistant.skill_level)
        console.print()

        # Step 2: What to build
        goal = await ask_text(
            "What do you want to build?",
            instruction='e.g. "a REST API for recipes" or "a habit tracker with SQLite"',
        )
        if not goal:
            console.print("  [muted]Cancelled.[/]")
            return
        goal = goal.strip()

        # Step 3: Formation recommendation
        rec_formation, rec_reason = self.assistant.get_formation_recommendation(goal)
        console.print()
        console.print(f"  [step]Recommended formation:[/] [accent]{rec_formation}[/]")
        console.print(f"  [muted]{rec_reason}[/]")
        if self.assistant.skill_level != "expert":
            console.print(f"  [hint]{self.assistant.explain_formation(rec_formation)}[/]")
        console.print()

        # Step 4: Autonomy recommendation
        rec_level, rec_aut_reason = self.assistant.get_autonomy_recommendation()
        current_level = self.assistant.read_autonomy_level()
        console.print(f"  [step]Autonomy recommendation:[/] A{rec_level} — {rec_aut_reason}")
        if self.assistant.skill_level == "beginner":
            console.print(f"  [hint]Type /autonomy to see what each level means[/]")
        console.print()

        # Confirm
        from forge_models import get_active_preset
        active_model = next((a for a, fid in MODEL_ALIASES.items() if fid == self.model), "nova-lite")
        console.print(f"  [step]Summary:[/]")
        console.print(f"    Goal:       {goal}")
        console.print(f"    Formation:  {rec_formation}")
        console.print(f"    Autonomy:   A{rec_level}")
        console.print(f"    Model:      {active_model}")
        console.print()

        proceed = await ask_confirm("Apply these settings and start planning?")
        if not proceed:
            console.print("  [muted]Cancelled — your settings are unchanged.[/]")
            return

        # Apply autonomy
        self.assistant.set_autonomy_level(rec_level)
        console.print(f"  [success]Autonomy set to A{rec_level}[/]")

        # Show hint about formation
        hint = self.assistant.contextual_hint("formation_intro")
        if hint:
            display_assistant_hint(hint)

        console.print()
        await self._guided_build(goal)

    # ── Smart Planning ─────────────────────────────────────────────────

    async def _smart_planning(self, goal: str) -> str | None:
        """LLM-driven planning: Nova analyzes the goal, proposes a plan, user confirms.

        Returns scope context string, or None on cancel.
        """
        from config import get_model_config
        from model_router import ModelRouter

        console.print(Panel(
            "Nova analyzes your idea and proposes a plan with recommended options.\n"
            "  [muted]Press Enter to accept recommendations \u00b7 Ctrl-C to cancel[/]",
            title=f"[bold {BRAND['accent2']}] Smart Planning [/]",
            border_style=BRAND["accent2"],
            padding=(0, 2),
        ))
        console.print()

        # Step 1: Nova analyzes the goal and proposes recommendations
        ctx = self.assistant.analyze_goal(goal)
        stack_rec = "flask" if ctx.get("has_api") or ctx.get("has_data") else "static"
        stack_label = "Python + Flask" if stack_rec == "flask" else "Static HTML/CSS/JS"

        # Show Nova's analysis
        console.print(f"  [step]Goal:[/]  {goal}")
        console.print()

        analysis_parts = []
        if ctx.get("has_frontend"):
            analysis_parts.append("frontend UI")
        if ctx.get("has_api"):
            analysis_parts.append("backend API")
        if ctx.get("has_data"):
            analysis_parts.append("data storage")
        if ctx.get("has_auth"):
            analysis_parts.append("authentication")
        if ctx.get("has_realtime"):
            analysis_parts.append("real-time features")
        if analysis_parts:
            console.print(f"  [muted]Detected:[/] {', '.join(analysis_parts)}")
            console.print()

        # Step 2: Propose recommendations with option to change
        console.print(f"  [accent]Recommended plan:[/]")
        console.print(f"    Stack:     [bold]{stack_label}[/]")
        console.print(f"    Risk:      [bold]Low[/] (prototype)")
        console.print(f"    Formation: [bold]Auto-select[/]")
        console.print(f"    Model:     [bold]{next((a for a, fid in MODEL_ALIASES.items() if fid == self.model), 'nova-lite')}[/]")
        console.print()

        # Quick confirm or customize
        action = await ask_select("How does this look?", [
            Choice(
                title="Looks good — start building",
                value="accept",
                description="Accept recommendations and start planning",
            ),
            Choice(
                title="Let me customize",
                value="customize",
                description="Change stack, model, or other options",
            ),
        ], default="accept")

        if action is None:
            return None

        answers = {
            "scope": goal,
            "stack": stack_rec,
            "risk": "low",
            "model": next((a for a, fid in MODEL_ALIASES.items() if fid == self.model), "nova-lite"),
        }

        if action == "customize":
            # Only ask the questions the user wants to change
            console.print()
            stack_result = await self._interview_step("stack", answers)
            if stack_result is None:
                return None
            answers["stack"] = stack_result

            model_result = await self._interview_step("model", answers)
            if model_result is None:
                return None
            answers["model"] = model_result

            risk_result = await self._interview_step("risk", answers)
            if risk_result is None:
                return None
            answers["risk"] = risk_result

        # Apply model choice
        self.model = resolve_model(answers.get("model", "nova-lite"))

        # Step 3: Generate dynamic deep-dive questions based on the goal
        # Use the LLM to ask 2-4 smart questions instead of hardcoded categories
        console.print()
        console.print(Rule(f"[phase.deep] Quick Scope [/]", style=BRAND["accent"]))
        console.print("  [muted]A few questions to nail the details.[/]")

        deep_answers = await self._dynamic_deep_dive(goal, answers)
        if deep_answers is None:
            return None

        # Step 4: Build scope summary and confirm
        scope_summary = self.assistant.build_scope_summary(
            {"goal": goal, "stack": answers["stack"], "risk": answers["risk"]},
            deep_answers,
        )

        console.print()
        console.print(Panel(
            scope_summary,
            title=f"[bold {BRAND['cyan']}] Project Scope [/]",
            border_style=BRAND["cyan"],
            padding=(1, 2),
        ))
        console.print()

        confirmed = await ask_confirm("Ready to plan?", default=True)
        if not confirmed:
            return None

        return scope_summary

    async def _dynamic_deep_dive(self, goal: str, answers: dict) -> dict | None:
        """LLM-driven deep dive — ask 2-4 targeted questions based on the goal.

        Instead of hardcoded question categories, Nova picks what matters most
        and proposes recommended answers the user can accept with Enter.
        """
        ctx = self.assistant.analyze_goal(goal, answers.get("stack", ""))
        deep_answers: dict = {}

        # Pick targeted questions from the catalog based on goal analysis
        from forge_assistant import DEEP_DIVE_QUESTIONS, _feature_choices, _data_entity_choices

        # Prioritize: features always, then only categories that apply
        priority_categories = ["features"]
        if ctx.get("has_frontend"):
            priority_categories.append("visual_aesthetic")
        if ctx.get("has_data"):
            priority_categories.append("data")
        if ctx.get("has_auth"):
            priority_categories.append("auth")

        # Limit to max 6 questions total for speed
        questions = []
        for cat in priority_categories:
            cat_qs = DEEP_DIVE_QUESTIONS.get(cat, [])
            for q in cat_qs:
                if q["condition"](ctx) and len(questions) < 6:
                    questions.append({**q, "category": cat})

        if not questions:
            console.print("  [muted]No additional questions needed — straightforward build.[/]")
            return {}

        q_num = 0
        current_category = ""

        for q in questions:
            q_num += 1
            cat = q["category"]

            if cat != current_category:
                current_category = cat
                console.print()
                cat_labels = {
                    "features":         "\u2728 Features",
                    "data":             "\U0001f4be Data",
                    "auth":             "\U0001f512 Auth",
                    "visual_aesthetic": "\U0001f3a8 Design",
                }
                label = cat_labels.get(cat, cat.replace("_", " ").title())
                console.print(f"  [phase.deep]{label}[/]  [muted]({q_num}/{len(questions)})[/]")

            key = q["key"]
            q_type = q["type"]

            if q_type == "select":
                choices_raw = q.get("choices", [])
                choices = [Choice(title=label, value=value) for label, value in choices_raw]
                # Default to the recommended option (marked with *)
                default = choices_raw[0][1] if choices_raw else None
                for label, value in choices_raw:
                    if "*" in label:
                        default = value
                        break
                result = await ask_select(q["question"], choices, default=default)
                if result is None:
                    return None
                deep_answers[key] = result

            elif q_type == "checkbox":
                choices_fn_name = q.get("choices_fn")
                if choices_fn_name == "_feature_choices":
                    choices_raw = _feature_choices(ctx)
                elif choices_fn_name == "_data_entity_choices":
                    choices_raw = _data_entity_choices(ctx)
                else:
                    choices_raw = q.get("choices", [])
                choices = [Choice(title=label, value=value) for label, value in choices_raw]
                result = await ask_checkbox(q["question"], choices)
                if result is None:
                    return None
                deep_answers[key] = result

            elif q_type == "text":
                result = await ask_text_optional(q["question"])
                deep_answers[key] = result or ""

            elif q_type == "confirm":
                default = q.get("default", True)
                result = await ask_confirm(q["question"], default=default)
                deep_answers[key] = "Yes" if result else "No"

        return deep_answers

    # ── /interview ─────────────────────────────────────────────────────

    async def _cmd_interview(self) -> None:
        """Multi-phase guided project setup: core -> deep dive -> review -> build."""
        from formations import FORMATIONS

        console.print()
        console.print(Panel(
            "Nova interviews you to understand your project in depth.\n"
            f"  [phase.core]Phase 1: Core setup[/] \u00b7 [phase.deep]Phase 2: Deep dive[/] \u00b7 [phase.review]Phase 3: Review[/]\n"
            "  [muted]Enter to accept defaults \u00b7 Esc to go back \u00b7 Ctrl-C to quit[/]",
            title=f"[bold {BRAND['accent2']}] Interview [/]",
            border_style=BRAND["accent2"],
            padding=(0, 2),
        ))

        # -- Phase 1: Core 5 steps (same as before) --
        console.print()
        console.print(Rule(f"[phase.core] Phase 1: Core Setup [/]", style=BRAND["accent2"]))

        answers: dict[str, str] = {}
        steps = ["scope", "stack", "risk", "formation", "model"]
        i = 0

        while i < len(steps):
            step = steps[i]
            console.print()
            console.print(Rule(f" Step {i + 1}/{len(steps)} ", style="dim", align="right"))

            # Breadcrumbs for completed steps
            for prev in steps[:i]:
                console.print(f"  [muted]{prev:12s}[/] {answers[prev]}")
            if i > 0:
                console.print()

            result = await self._interview_step(step, answers)
            if result is None:
                console.print("  [muted]Interview cancelled.[/]")
                return
            answers[step] = result
            i += 1

        # -- Phase 2: Deep Dive --
        console.print()
        console.print(Rule(f"[phase.deep] Phase 2: Deep Dive [/]", style=BRAND["accent"]))
        console.print("  [muted]Nova asks deeper questions to set the right scope.[/]")

        deep_answers = await self._interview_deep_dive(answers)
        if deep_answers is None:
            console.print("  [muted]Interview cancelled.[/]")
            return

        # -- Phase 3: Review & Approve --
        console.print()
        console.print(Rule(f"[phase.review] Phase 3: Scope Review [/]", style=BRAND["cyan"]))

        scope_summary = self.assistant.build_scope_summary(
            {"goal": answers["scope"], "stack": answers["stack"], "risk": answers["risk"]},
            deep_answers,
        )
        final_scope = await self._scope_review_loop(scope_summary, answers, deep_answers)

        if final_scope is None:
            # User chose "Start over"
            return await self._cmd_interview()

        # Summary table (core config)
        console.print()
        table = Table(
            box=box.ROUNDED, show_header=False, border_style="bright_magenta",
            title="[bold] Your Build Config [/]", padding=(0, 2),
        )
        table.add_column("", style="bold", width=12)
        table.add_column("")
        for k, v in answers.items():
            table.add_row(k.capitalize(), v)
        console.print(table)
        console.print()

        if not await ask_confirm("Build now?"):
            console.print("  [muted]Config saved. Run /build when ready.[/]")
            return

        # Apply model selection and build with rich scope context
        self.model = resolve_model(answers.get("model", "nova-lite"))
        console.print()
        console.print(f"  [nova]Nova[/] [muted]--[/] Great, let\'s build that!")
        await self._guided_build(answers["scope"], scope_context=final_scope)

    async def _interview_step(self, step: str, answers: dict) -> str | None:
        """Execute a single interview step. Returns value or None on cancel."""
        from formations import FORMATIONS

        if step == "scope":
            return await ask_text(
                "What do you want to build?",
                instruction='e.g. "A REST API for recipes with auth"',
            )
        elif step == "stack":
            return await ask_select("Tech stack", [
                Choice(
                    title="Auto-detect",
                    value="auto",
                    description="Let Nova pick the best stack for your project",
                ),
                Separator("── Frameworks ──"),
                Choice(
                    title="Python + Flask",
                    value="flask",
                    description="REST APIs, web apps — lightweight and flexible",
                ),
                Choice(
                    title="Node.js + Express",
                    value="node",
                    description="JavaScript backend — async I/O, npm ecosystem",
                ),
                Choice(
                    title="Static site",
                    value="static",
                    description="HTML/CSS/JS — no server needed, fast to deploy",
                ),
                Separator("── Other ──"),
                Choice(
                    title="Custom",
                    value="custom",
                    description="Describe your own stack in the next step",
                ),
            ], default="auto", use_shortcuts=True)
        elif step == "risk":
            return await ask_select("Risk level", [
                Choice(
                    title="Low",
                    value="low",
                    description="Prototypes, internal tools — Nova acts freely",
                ),
                Choice(
                    title="Medium",
                    value="medium",
                    description="Filesystem writes, configs — Nova asks before risky actions",
                ),
                Choice(
                    title="High",
                    value="high",
                    description="Deployment, auth, networking — Nova asks before every action",
                ),
            ], default="low", use_shortcuts=True)
        elif step == "formation":
            formation_choices = [
                Choice(
                    title=f"{name}  ({len(f.roles)} roles)",
                    value=name,
                    description=f.description,
                )
                for name, f in FORMATIONS.items()
            ]
            return await ask_select(
                "Agent formation",
                formation_choices,
                default="feature-impl",
                use_search_filter=True,
            )
        elif step == "model":
            providers = _check_all_providers()
            current = next(
                (a for a, fid in MODEL_ALIASES.items() if fid == self.model),
                "nova-lite",
            )
            model_choices = build_model_choices(
                available_providers=providers,
                current_model=current,
            )
            return await ask_select(
                "AI model",
                model_choices,
                default=current,
                use_search_filter=True,
            )
        return None

    # ── Deep Dive Interview ───────────────────────────────────────────

    async def _interview_deep_dive(self, core_answers: dict) -> dict | None:
        """Phase 2: Dynamic interview rounds driven by goal analysis.

        Returns deep_answers dict, or None on cancel.
        """
        from forge_assistant import DEEP_DIVE_QUESTIONS, _feature_choices, _data_entity_choices

        # Analyze the goal to determine which question categories apply
        ctx = self.assistant.analyze_goal(
            core_answers.get("scope", ""),
            core_answers.get("stack", ""),
        )

        # Get applicable questions
        questions = self.assistant.get_deep_dive_questions(ctx)
        if not questions:
            console.print("  [muted]No additional questions needed for this project.[/]")
            return {}

        deep_answers: dict = {}
        current_category = ""
        q_num = 0
        total_q = len(questions)

        for q in questions:
            q_num += 1
            cat = q["category"]

            # Show category header on change
            if cat != current_category:
                current_category = cat
                console.print()
                cat_labels = {
                    "features":         "\u2728 Features & Functionality",
                    "data":             "\U0001f4be Data & Storage",
                    "auth":             "\U0001f512 Authentication & Security",
                    "visual_aesthetic": "\U0001f3a8 Visual Design & UX",
                    "api_design":       "\U0001f310 API Design",
                    "realtime":         "\u26a1 Real-time Features",
                    "deployment":       "\U0001f680 Deployment",
                    "testing":          "\U0001f9ea Testing Strategy",
                }
                label = cat_labels.get(cat, cat.replace("_", " ").title())
                console.print(f"  [phase.deep]{label}[/]  [muted]({q_num}/{total_q})[/]")

            key = q["key"]
            q_type = q["type"]

            if q_type == "select":
                choices_raw = q.get("choices", [])
                choices = [
                    Choice(title=label, value=value)
                    for label, value in choices_raw
                ]
                result = await ask_select(
                    q["question"],
                    choices,
                    default=choices_raw[0][1] if choices_raw else None,
                )
                if result is None:
                    return None
                deep_answers[key] = result

            elif q_type == "checkbox":
                # Dynamic choices from function
                choices_fn_name = q.get("choices_fn")
                if choices_fn_name == "_feature_choices":
                    choices_raw = _feature_choices(ctx)
                elif choices_fn_name == "_data_entity_choices":
                    choices_raw = _data_entity_choices(ctx)
                else:
                    choices_raw = q.get("choices", [])

                choices = [
                    Choice(title=label, value=value)
                    for label, value in choices_raw
                ]
                result = await ask_checkbox(q["question"], choices)
                if result is None:
                    return None
                deep_answers[key] = result

            elif q_type == "text":
                result = await ask_text_optional(q["question"])
                deep_answers[key] = result or ""

            elif q_type == "confirm":
                default = q.get("default", True)
                result = await ask_confirm(q["question"], default=default)
                deep_answers[key] = "Yes" if result else "No"

        return deep_answers

    # ── Scope Review Loop ─────────────────────────────────────────────

    async def _scope_review_loop(
        self,
        scope_summary: str,
        core_answers: dict,
        deep_answers: dict,
    ) -> str | None:
        """Phase 3: Display scope, let user approve/modify/restart.

        Returns final scope string, or None for "Start over".
        """
        while True:
            console.print()
            console.print(Panel(
                scope_summary,
                title=f"[bold {BRAND['cyan']}] Project Scope [/]",
                border_style=BRAND["cyan"],
                padding=(1, 2),
            ))
            console.print()

            action = await ask_select("How does this look?", [
                Choice(
                    title="Approve \u2014 proceed to planning *",
                    value="approve",
                    description="Generate spec.md and tasks from this scope",
                ),
                Choice(
                    title="Add more \u2014 type additional requirements",
                    value="add",
                    description="Append extra notes to the scope",
                ),
                Choice(
                    title="Start over \u2014 reset the interview",
                    value="restart",
                    description="Go back to step 1",
                ),
            ], default="approve")

            if action is None or action == "restart":
                return None
            elif action == "approve":
                return scope_summary
            elif action == "add":
                extra = await ask_text(
                    "Additional requirements",
                    instruction="Type anything to add to the scope",
                )
                if extra and extra.strip():
                    prev = deep_answers.get("extra_notes", "")
                    deep_answers["extra_notes"] = (prev + "\n" + extra.strip()).strip()
                    # Rebuild summary with new notes
                    scope_summary = self.assistant.build_scope_summary(
                        {"goal": core_answers["scope"], "stack": core_answers["stack"], "risk": core_answers["risk"]},
                        deep_answers,
                    )

    # ── Chat context builder ───────────────────────────────────────────

    def _build_chat_context(self, user_input: str) -> tuple[str, str]:
        """Build (system_prompt, enriched_user_prompt) for chat agent."""
        from prompt_builder import PromptBuilder

        mc = get_model_config(self.model)
        ctx_window = mc.context_window

        # V11-grade system prompt with chat role profile + environment context
        pb = PromptBuilder(self.project_path)
        system = pb.build_enriched_system_prompt(
            role="chat",
            max_tokens=ctx_window,
            autonomy_level=self.assistant.read_autonomy_level(),
        )

        # ── User prompt context sections ──────────────────────────────
        parts = [user_input]

        # Chat history for continuity
        history_ctx = self.chat_history.to_context(ctx_window)
        if history_ctx:
            parts.append(history_ctx)

        # Task state
        summary = self._get_task_summary()
        if summary and summary["total"] > 0:
            done = summary["completed"]
            total = summary["total"]
            failed = summary.get("failed", 0)
            pending = summary.get("pending", 0)
            task_line = f"## Project State\n{done}/{total} tasks complete"
            if failed:
                task_line += f", {failed} failed"
            if pending:
                task_line += f", {pending} pending"
            parts.append(task_line)

        # Preview URL
        if self._preview_mgr and self._preview_mgr.url:
            parts.append(f"## Live Preview\nURL: {self._preview_mgr.url}")

        # Project files — budget-aware content inclusion
        existing = self._gather_project_files()
        if existing:
            file_tree = "\n".join(f"  {k}" for k in sorted(existing.keys()))
            parts.append(f"## Project File Tree\n{file_tree}")
            # Include key file contents (budget-aware)
            max_content = 2000 if ctx_window <= 32_000 else 4000
            ui_exts = (".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".json", ".py")
            shown = 0
            max_files = 5 if ctx_window <= 32_000 else 8
            for rel_path, content in existing.items():
                if shown >= max_files:
                    break
                if any(rel_path.endswith(ext) for ext in ui_exts):
                    parts.append(f"## File: {rel_path}\n```\n{content[:max_content]}\n```")
                    shown += 1

        user_prompt = "\n\n---\n\n".join(parts)
        return system, user_prompt

    # ── Natural language handler ─────────────────────────────────────────

    async def _handle_natural(self, user_input: str) -> None:
        """Natural language — detect intent and be eager to help."""
        lower = user_input.lower()

        # Detect "build me X" intent → full guided pipeline
        build_triggers = [
            "build me", "create a", "make a", "build a", "i want",
            "i need", "make me", "create me", "write me", "generate",
            "scaffold", "set up", "setup", "start a",
        ]
        if any(kw in lower for kw in build_triggers):
            if not self._has_tasks():
                console.print()
                console.print(f"  [nova]Nova[/] [muted]--[/] Great, let's build that!")
                await self._guided_build(user_input)
                return
            else:
                # User wants to build something new but has an existing project
                console.print()
                console.print(f"  [nova]Nova[/] [muted]--[/] You already have a project here "
                              f"([bold]{self.project_path.name}[/]).")
                action = await ask_select(
                    f"You already have a project ({self.project_path.name})",
                    [
                        Choice(
                            title="Start fresh",
                            value="fresh",
                            description="Create a new project directory for this build",
                        ),
                        Choice(
                            title="Add to current",
                            value="add",
                            description="Chat with Nova about the existing project",
                        ),
                        Choice(
                            title="Cancel",
                            value="cancel",
                            description="Go back to the prompt",
                        ),
                    ],
                    use_shortcuts=True,
                )
                if action == "fresh":
                    console.print()
                    console.print(f"  [nova]Nova[/] [muted]--[/] Great, let's build that!")
                    await self._guided_build(user_input)
                    return
                elif action == "add":
                    pass  # Fall through to chat agent below
                else:
                    console.print("  [muted]Cancelled.[/]")
                    return

        # If we have tasks and user says "build" / "go" / "start" / "yes"
        if lower in ("build", "go", "start", "yes", "y", "do it", "run it", "let's go"):
            if self._has_tasks():
                await self._cmd_build("")
                self._celebrate()
                self.state["builds_completed"] = self.state.get("builds_completed", 0) + 1
                _save_state(self.state)
                return

        # If user says something like "add X to..." with existing project
        if any(kw in lower for kw in ["add ", "fix ", "change ", "update ", "modify "]) and self._has_tasks():
            console.print()
            console.print(f"  [nova]Nova[/] [muted]--[/] Let me work on that...")
            console.print()

        # General agent interaction
        from forge_agent import ForgeAgent, BUILT_IN_TOOLS
        from forge_display import ChatDisplay

        # Build rich context (V11-grade system prompt + history + task state + files)
        system_prompt, user_prompt = self._build_chat_context(user_input)

        # Scale max_tokens — respect per-provider output limits
        # Bedrock Nova: 5K (Lite), 10K (Pro/Premier) hard caps
        # OpenRouter/Anthropic: much higher limits
        mc = get_model_config(self.model)
        provider = get_provider(self.model)
        if provider == "bedrock":
            chat_max_tokens = min(5000, max(4096, mc.context_window // 16))
        else:
            chat_max_tokens = min(16384, max(4096, mc.context_window // 8))
        mc = get_model_config(self.model, max_tokens=chat_max_tokens)

        # Create display for real-time feedback
        chat_display = ChatDisplay()

        # Create agent with event wiring
        agent = ForgeAgent(
            model_config=mc,
            project_root=self.project_path,
            tools=BUILT_IN_TOOLS,
            max_turns=30,
            agent_id="forge-chat",
            on_event=chat_display.on_event,
        )

        # Run with real-time display
        with chat_display.create_progress():
            result = await agent.run(prompt=user_prompt, system=system_prompt)

        # Display result
        console.print()
        if result.error:
            if result.error == "max_turns_exceeded":
                console.print(f"\n  [warning]Nova used all available turns ({result.turns}).[/]")
                console.print(f"  [hint]Your request may need more steps. Try breaking it into smaller parts,[/]")
                console.print(f"  [hint]or just ask Nova to continue where it left off.[/]")
            else:
                console.print(f"  [error]Something went wrong:[/] {result.error}")
                console.print(f"  [hint]Try rephrasing, or check your credentials.[/]")
        elif result.output:
            console.print(f"  [nova]Nova[/] [muted]--[/]", end=" ")
            if any(c in result.output for c in ["```", "##", "- "]):
                console.print()
                console.print(Markdown(result.output))
            else:
                console.print(result.output)
        elif not result.output and not result.artifacts:
            console.print("\n  [muted]Nova completed but produced no output. Try rephrasing your request.[/]")

        if result.artifacts:
            for path in result.artifacts:
                console.print(f"  [success]{Path(path).name}[/] [muted]created[/]")

        # Save conversation turn for continuity
        self.chat_history.add_turn(
            user=user_input,
            assistant=result.output[:1000] if result.output else "",
            build_result={
                "files_created": list(result.artifacts.keys())[:10],
                "status": "error" if result.error else "ok",
            } if result.artifacts else None,
        )
        self.chat_history.save()

        # Show footer with tool/file summary
        chat_display.print_footer(result)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _has_tasks(self) -> bool:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        try:
            store = TaskStore(project.tasks_file)
            return len(store.list()) > 0
        except Exception:
            return False

    def _suggest_next_action(self) -> None:
        """Show contextual next-step guidance based on current project state."""
        summary = self._get_task_summary()

        if not summary or summary["total"] == 0:
            console.print()
            console.print(f"  [hint]Tell me what you want to build, or try [{BRAND['accent2']}]/interview[/] for guided setup[/]")
            return

        total = summary["total"]
        done = summary["completed"]
        failed = summary.get("failed", 0)
        pending = summary.get("pending", 0)

        in_prog = summary.get("in_progress", 0)

        console.print()
        if done == total and in_prog == 0:
            console.print(f"  [{BRAND['accent2']}]/preview[/] [muted]share a live URL[/]  \u2502  [{BRAND['cyan']}]/deploy[/] [muted]ship to production[/]")
        elif failed > 0:
            console.print(f"  [{BRAND['orange']}]{failed}[/] task(s) failed \u2014 [{BRAND['cyan']}]/build[/] [muted]to retry[/]")
        elif pending > 0 or in_prog > 0:
            remaining = pending + in_prog
            console.print(f"  [{BRAND['accent']}]{remaining}[/] task(s) remaining \u2014 [{BRAND['cyan']}]/build[/] [muted]to continue[/]")

    def _get_task_summary(self) -> dict | None:
        return self._get_task_summary_for(self.project_path)

    def _get_task_summary_for(self, path: Path) -> dict | None:
        """Get task summary for any project path."""
        from forge_tasks import TaskStore
        try:
            project = ForgeProject(root=path)
            store = TaskStore(project.tasks_file)
            tasks = store.list()
            if not tasks:
                return None
            return {
                "total": len(tasks),
                "completed": sum(1 for t in tasks if t.status == "completed"),
                "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
                "pending": sum(1 for t in tasks if t.status == "pending"),
                "failed": sum(1 for t in tasks if t.status == "failed"),
                "blocked": sum(1 for t in tasks if t.status == "blocked"),
            }
        except Exception:
            return None

    def _sync_task_state(self) -> None:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()
        state = {
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t.status == "completed"),
            "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
            "pending": sum(1 for t in tasks if t.status == "pending"),
            "failed": sum(1 for t in tasks if t.status == "failed"),
            "blocked": sum(1 for t in tasks if t.status == "blocked"),
        }
        state_file = project.state_dir / "task-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2) + "\n")

    def _render_task_table(self, tasks_data: list[dict]) -> None:
        table = Table(
            box=box.ROUNDED, show_header=True,
            header_style="bold cyan", padding=(0, 1),
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Task", min_width=30)
        table.add_column("Risk", width=8)

        for i, t in enumerate(tasks_data, 1):
            risk = t.get("risk", "")
            risk_style = {
                "high": "[red]high[/]",
                "medium": "[yellow]med[/]",
                "low": "[green]low[/]",
            }.get(risk, "[muted]-[/]")
            table.add_row(str(i), t.get("subject", "?"), risk_style)

        console.print(table)

    def _derive_project_name(self, goal: str) -> str:
        """Turn a goal like 'Build me a REST API for bookmarks' into 'bookmarks-api'."""
        # Strip common prefixes
        lower = goal.lower()
        for prefix in [
            "build me ", "create a ", "make a ", "build a ", "i want ",
            "i need ", "make me ", "create me ", "write me ", "generate ",
            "set up ", "setup ", "start a ",
        ]:
            if lower.startswith(prefix):
                lower = lower[len(prefix):]
                break

        # Take first few meaningful words
        stop_words = {
            "a", "an", "the", "with", "using", "that", "which", "for",
            "and", "or", "in", "on", "to", "from", "by", "of", "my",
        }
        words = []
        for word in lower.split():
            clean = "".join(c for c in word if c.isalnum())
            if clean and clean not in stop_words and len(words) < 4:
                words.append(clean)

        if not words:
            words = ["project"]

        return "-".join(words)

    def _gather_project_files(self) -> dict[str, str]:
        """Gather non-forge project files with content previews."""
        files = {}
        skip = {"forge_cli.py", "challenge_build.py", "demo_nova_e2e.py"}
        skip_dirs = {".forge", "__pycache__", "node_modules", ".git", "venv", ".venv"}
        for ext in ("*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.html", "*.css",
                     "*.json", "*.yml", "*.yaml", "*.md"):
            for f in self.project_path.rglob(ext):
                if f.name in skip:
                    continue
                if any(d in f.parts for d in skip_dirs):
                    continue
                rel = str(f.relative_to(self.project_path))
                try:
                    files[rel] = f.read_text()[:4000]
                except Exception:
                    pass
                if len(files) >= 25:
                    return files
        return files

    def _list_project_files(self) -> list[str]:
        """List meaningful project files (not forge internals)."""
        files = []
        skip = {"forge_cli.py", "challenge_build.py", "demo_nova_e2e.py"}
        skip_dirs = {".forge", "__pycache__", "node_modules", ".git", "venv", ".venv"}
        for ext in ("*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.html", "*.css",
                     "*.json", "*.yml", "*.yaml"):
            for f in self.project_path.rglob(ext):
                if f.name in skip:
                    continue
                if any(d in f.parts for d in skip_dirs):
                    continue
                files.append(str(f.relative_to(self.project_path)))
        return sorted(files)[:30]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _short_model(model_id: str) -> str:
    for alias, full_id in MODEL_ALIASES.items():
        if full_id == model_id or model_id == alias:
            return alias
    return model_id.split("/")[-1][:30]


# ── Entry point ──────────────────────────────────────────────────────────────

def main(project_path: str = "."):
    logging.basicConfig(level=logging.WARNING)
    shell = ForgeShell(project_path)
    try:
        asyncio.run(shell.run())
    except KeyboardInterrupt:
        console.print("\n  [muted]Interrupted.[/]")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    main(path)
