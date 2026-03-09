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

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich import box
from rich.columns import Columns
from rich.rule import Rule

# ── Setup ────────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))

from config import (
    MODEL_ALIASES, DEFAULT_MODELS, resolve_model, get_model_config,
    ForgeProject, init_forge_dir,
)

logger = logging.getLogger("forge.cli")

# ── Theme ────────────────────────────────────────────────────────────────────

THEME = Theme({
    "info": "cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "muted": "dim",
    "accent": "bold cyan",
    "nova": "bold magenta",
    "hint": "italic dim cyan",
    "brand": "bold bright_magenta",
    "step": "bold white",
})

console = Console(theme=THEME)

PT_STYLE = PTStyle.from_dict({
    "prompt": "#ff66ff bold",
    "": "#e0e0e0",
})

VERSION = "0.3.0"

# ── State file for remembering recent projects ───────────────────────────────

STATE_DIR = Path.home() / ".forge"
STATE_FILE = STATE_DIR / "cli_state.json"
HISTORY_FILE = Path.home() / ".forge_history"

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
    # Remove if already exists, then prepend
    projects = [p for p in projects if p["path"] != path]
    projects.insert(0, {"path": path, "name": name, "last_used": time.strftime("%Y-%m-%d")})
    state["recent_projects"] = projects[:10]  # Keep last 10

# ── ASCII Art & Branding ─────────────────────────────────────────────────────

LOGO = r"""[bold bright_magenta]
    _   __                  ______
   / | / /___ _   ______ _ / ____/___  _________ ____
  /  |/ / __ \ | / / __ `// /_  / __ \/ ___/ __ `/ _ \
 / /|  / /_/ / |/ / /_/ // __/ / /_/ / /  / /_/ /  __/
/_/ |_/\____/|___/\__,_//_/    \____/_/   \__, /\___/
                                         /____/[/]"""

TAGLINE = "[muted]Describe it. Nova builds it.[/]"

WELCOME_FIRST_RUN = """
[bold bright_white]Welcome to Nova Forge![/]

Nova Forge is an open-source AI build system powered by [brand]Amazon Nova[/].
Tell it what you want to build, and it writes the code — start to finish.

[step]How it works:[/]
  [accent]1.[/]  You describe what you want        [muted]"a REST API for bookmarks"[/]
  [accent]2.[/]  Nova plans the project             [muted]spec, tasks, architecture[/]
  [accent]3.[/]  Nova builds it, wave by wave       [muted]real code, not stubs[/]
  [accent]4.[/]  You get a working project           [muted]ready to run[/]

[hint]No setup needed. Just describe your idea.[/]
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
[bold bright_white]Commands[/]                                [muted]Everything else is natural language[/]

  [accent]/plan[/] [muted]<goal>[/]        Plan a project from a description
  [accent]/build[/]              Execute the plan — Nova writes all the code
  [accent]/status[/]             Progress bar and project overview
  [accent]/tasks[/]              See all tasks with status and dependencies
  [accent]/models[/]             Available AI models
  [accent]/formation[/]          Agent team configurations
  [accent]/audit[/]              View the build audit log
  [accent]/new[/] [muted]<name>[/]          Start a fresh project directory
  [accent]/cd[/] [muted]<path>[/]           Switch project directory
  [accent]/pwd[/]                Show current project location
  [accent]/clear[/]              Clear the screen
  [accent]/help[/]               This screen
  [accent]/quit[/]               Exit Nova Forge

[bold bright_white]Quick Start[/]
  Just type what you want to build:
    [muted]>[/] Build me a REST API for managing recipes with Flask
    [muted]>[/] Create a CLI tool that converts CSV to JSON
    [muted]>[/] I need a todo app with a SQLite backend
"""


# ── Interactive Shell ────────────────────────────────────────────────────────

class ForgeShell:
    """Interactive CLI shell for Nova Forge — guided, eager, friendly."""

    def __init__(self, project_path: str | Path = ".", default_model: str | None = None):
        self.project_path = Path(project_path).resolve()
        self.model = resolve_model(default_model) if default_model else DEFAULT_MODELS["planning"]
        self.state = _load_state()
        self.session_builds = 0
        self._ensure_project()

    def _ensure_project(self) -> None:
        forge_dir = self.project_path / ".forge"
        if not forge_dir.exists():
            init_forge_dir(self.project_path)

    # ── Main entry ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main REPL — welcome, onboard, build."""
        console.clear()
        console.print(LOGO)
        console.print(f"  {TAGLINE}")
        console.print()

        is_first = self.state.get("first_run", True)

        if is_first:
            await self._onboard_first_run()
        else:
            await self._onboard_returning()

        # Main loop
        session = PromptSession(
            history=FileHistory(str(HISTORY_FILE)),
            style=PT_STYLE,
        )

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: session.prompt(HTML("<prompt>nova > </prompt>")),
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
        console.print(WELCOME_FIRST_RUN)

        # Mark first run complete
        self.state["first_run"] = False
        _save_state(self.state)

        # Immediately ask what to build
        console.print(Rule("[bold bright_white]Let's build something[/]", style="bright_magenta"))
        console.print()

        idea = random.choice(IDEAS)
        console.print(f"  [hint]Try something like: \"{idea}\"[/]")
        console.print()

        session = PromptSession(style=PT_STYLE)
        try:
            goal = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: session.prompt(
                    HTML("<prompt>What do you want to build? </prompt>"),
                ),
            )
        except (EOFError, KeyboardInterrupt):
            self._goodbye()
            return

        goal = goal.strip()
        if goal:
            await self._guided_build(goal)

    # ── Returning user onboarding ────────────────────────────────────────

    async def _onboard_returning(self) -> None:
        builds = self.state.get("builds_completed", 0)
        console.print(WELCOME_RETURNING.format(version=VERSION))
        if builds > 0:
            console.print(f"  [muted]{builds} project{'s' if builds != 1 else ''} built so far[/]")
        console.print()

        # Check for existing project with pending work
        existing_tasks = self._get_task_summary()
        if existing_tasks and existing_tasks["pending"] > 0:
            console.print(Panel(
                f"[bold]Resume: {self.project_path.name}[/]\n"
                f"  {existing_tasks['completed']}/{existing_tasks['total']} tasks done, "
                f"{existing_tasks['pending']} remaining\n\n"
                f"  [hint]Type [accent]/build[/][hint] to continue, or describe something new[/]",
                border_style="bright_magenta",
                padding=(1, 2),
            ))
            console.print()
            return

        # Show recent projects
        recent = self.state.get("recent_projects", [])
        if recent:
            console.print("  [step]Recent projects:[/]")
            for i, proj in enumerate(recent[:5], 1):
                exists = Path(proj["path"]).exists()
                marker = "[success]>[/]" if i == 1 else " "
                status = "" if exists else " [muted](deleted)[/]"
                console.print(f"  {marker} [accent]{i}.[/] {proj['name']}{status}  [muted]{proj['path']}[/]")
            console.print()

        # Prompt
        idea = random.choice(IDEAS)
        console.print(f"  [hint]Tip: {random.choice(TIPS)}[/]")
        console.print()

    # ── Guided build flow (the magic) ────────────────────────────────────

    async def _guided_build(self, goal: str) -> None:
        """The full guided pipeline: goal → name → plan → confirm → build → celebrate."""

        # Step 1: Derive project name
        name = self._derive_project_name(goal)
        console.print()
        console.print(f"  [step]Project:[/] [bold]{name}[/]")
        console.print(f"  [step]Goal:[/]    {goal}")
        console.print()

        # Create project directory
        project_dir = Path.home() / "projects" / name
        if project_dir.exists():
            # Add suffix if exists
            for i in range(2, 100):
                candidate = Path.home() / "projects" / f"{name}-{i}"
                if not candidate.exists():
                    project_dir = candidate
                    break

        project_dir.mkdir(parents=True, exist_ok=True)
        self.project_path = project_dir
        self._ensure_project()

        console.print(f"  [success]Created[/] {project_dir}")
        console.print()

        # Step 2: Plan
        console.print(Rule("[step] Step 1: Planning [/]", style="cyan"))
        console.print()
        await self._cmd_plan(goal)

        # Step 3: Confirm build
        if not self._has_tasks():
            console.print("  [warning]Planning didn't produce tasks. Try a more specific description.[/]")
            return

        console.print()
        console.print(Rule("[step] Step 2: Build [/]", style="cyan"))
        console.print()

        session = PromptSession(style=PT_STYLE)
        try:
            confirm = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: session.prompt(
                    HTML("<prompt>Ready to build? (Y/n) </prompt>"),
                ),
            )
        except (EOFError, KeyboardInterrupt):
            console.print("  [muted]Build cancelled.[/]")
            return

        if confirm.strip().lower() in ("n", "no"):
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
            console.print(Panel(
                f"[bold green]Build complete![/]\n\n"
                f"  [success]{done}/{total}[/] tasks finished\n"
                f"  Project: [bold]{self.project_path}[/]\n\n"
                f"  [step]What's next?[/]\n"
                f"    [accent]cd {self.project_path}[/]  and explore your new project\n"
                f"    Tell me to add features, fix bugs, or write tests\n"
                f"    Or just describe your next idea!",
                border_style="green",
                title="[bold green] Done! [/]",
                padding=(1, 2),
            ))
        elif done > 0:
            console.print(Panel(
                f"[bold yellow]Build partially complete[/]\n\n"
                f"  [success]{done}[/] passed  [error]{failed}[/] failed  "
                f"out of {total} tasks\n\n"
                f"  The core functionality is likely working.\n"
                f"  Type [accent]/build[/] to retry failed tasks,\n"
                f"  or tell me what to fix.",
                border_style="yellow",
                title="[bold yellow] Almost there [/]",
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
            console.print(f"  [muted]See you next time. {builds} project{'s' if builds != 1 else ''} built and counting.[/]")
        else:
            console.print(f"  [muted]Come back when you're ready to build something.[/]")
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
            case "/clear" | "/cls":
                console.clear()
                console.print(LOGO)
                console.print(f"  {TAGLINE}")
            case "/pwd":
                console.print(f"  [info]Project:[/] {self.project_path}")
            case "/cd":
                self._cmd_cd(arg)
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
            case "/models":
                self._cmd_models()
            case "/formation":
                self._cmd_formation(arg)
            case "/audit":
                self._cmd_audit()
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

        # Show project state if tasks exist
        summary = self._get_task_summary()
        if summary:
            console.print(f"  [muted]{summary['completed']}/{summary['total']} tasks done[/]")

    # ── /new ─────────────────────────────────────────────────────────────

    async def _cmd_new(self, name: str) -> None:
        if not name:
            console.print("  [hint]Give your project a name:[/]")
            console.print("  [muted]Example: /new my-cool-api[/]")
            return

        project_dir = self.project_path / name
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

    async def _cmd_plan(self, goal: str) -> None:
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
            result = await orch.plan(goal, model=self.model)

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
            except json.JSONDecodeError:
                console.print(f"  [success]Plan:[/] {result.task_count} tasks")
        elif result.task_count > 0:
            console.print(f"  [success]Plan:[/] {result.task_count} tasks ready")

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
        retryable = pending + failed_tasks  # Retry failed tasks too

        if not retryable:
            console.print("  [success]All tasks already complete![/]")
            self._cmd_status()
            return

        # Reset failed tasks to pending for retry
        for t in failed_tasks:
            store.update(t.id, status="pending")

        console.print(f"  [nova]Nova[/] is building your project...")
        console.print(f"  [muted]{len(retryable)} tasks to complete[/]")
        console.print()

        # Compute waves
        try:
            waves = store.compute_waves()
        except ValueError as exc:
            console.print(f"  [error]Dependency issue:[/] {exc}")
            return

        total_start = time.time()
        total_tool_calls = 0
        total_files = 0
        wave_results: list[tuple[int, str, str, float]] = []

        from forge_agent import ForgeAgent, BUILT_IN_TOOLS

        for wave_idx, wave_tasks in enumerate(waves):
            runnable = [
                t for t in wave_tasks
                if store.get(t.id) and store.get(t.id).status not in ("completed", "blocked")
            ]
            if not runnable:
                for t in wave_tasks:
                    wave_results.append((wave_idx, t.subject, "skip", 0.0))
                continue

            for task in runnable:
                store.update(task.id, status="in_progress")

                with Progress(
                    SpinnerColumn("dots"),
                    TextColumn(f"[step]{task.subject}[/]"),
                    TimeElapsedColumn(),
                    console=console,
                    transient=True,
                ) as progress:
                    progress.add_task(task.subject, total=None)

                    mc = get_model_config(self.model, max_tokens=4096)
                    agent = ForgeAgent(
                        model_config=mc,
                        project_root=self.project_path,
                        tools=BUILT_IN_TOOLS,
                        max_turns=15,
                        agent_id=f"forge-wave{wave_idx}-{task.id}",
                    )

                    spec_text = ""
                    spec_path = self.project_path / "spec.md"
                    if spec_path.exists():
                        spec_text = spec_path.read_text()[:4000]

                    # Gather existing project files for context
                    existing = self._gather_project_files()
                    context_hint = ""
                    if existing:
                        context_hint = f"\n\nExisting project files: {', '.join(existing.keys())}"
                        for fname, content in list(existing.items())[:3]:
                            context_hint += f"\n\n--- {fname} ---\n{content[:2000]}"

                    prompt = (
                        f"## Project Spec\n{spec_text}\n\n"
                        f"## Your Task\n{task.subject}: {task.description}\n\n"
                        f"## Instructions\n"
                        f"Implement this task. Use write_file to create files. "
                        f"Read existing files first with read_file if you need context. "
                        f"Write complete, working code — not stubs or placeholders."
                        f"{context_hint}"
                    )

                    wave_start = time.time()
                    try:
                        result = await agent.run(
                            prompt=prompt,
                            system=(
                                "You are a skilled Python developer building a real project. "
                                "Write complete, production-quality code. Use tools to create files. "
                                "Build on existing files when they exist."
                            ),
                        )
                        duration = time.time() - wave_start
                        total_tool_calls += result.tool_calls_made
                        if result.artifacts:
                            total_files += len(result.artifacts)

                        if result.error:
                            store.update(task.id, status="failed")
                            wave_results.append((wave_idx, task.subject, "fail", duration))
                        else:
                            store.update(task.id, status="completed", artifacts=result.artifacts)
                            wave_results.append((wave_idx, task.subject, "pass", duration))

                    except Exception:
                        duration = time.time() - wave_start
                        store.update(task.id, status="failed")
                        wave_results.append((wave_idx, task.subject, "fail", duration))

                # Print result after progress bar clears
                _, name, status, dur = wave_results[-1]
                if status == "pass":
                    console.print(f"  [success]{name}[/]  [muted]{dur:.0f}s[/]")
                elif status == "fail":
                    console.print(f"  [error]{name}[/]  [muted]{dur:.0f}s[/]")
                else:
                    console.print(f"  [muted]{name}  (skipped)[/]")

        total_duration = time.time() - total_start
        self._sync_task_state()

        # Summary line
        passed = sum(1 for _, _, s, _ in wave_results if s == "pass")
        failed = sum(1 for _, _, s, _ in wave_results if s == "fail")
        console.print()
        console.print(
            f"  [muted]{passed} passed, {failed} failed, "
            f"{total_tool_calls} tool calls, {total_duration:.0f}s[/]"
        )

        # List generated files
        all_files = self._list_project_files()
        if all_files:
            console.print(f"  [muted]Files: {', '.join(all_files[:10])}[/]")

    # ── /status ──────────────────────────────────────────────────────────

    def _cmd_status(self) -> None:
        summary = self._get_task_summary()
        if not summary:
            console.print("  [hint]No project in progress. Tell me what you want to build![/]")
            return

        total = summary["total"]
        done = summary["completed"]
        pct = (done / total * 100) if total > 0 else 0

        bar_width = 30
        filled = int(bar_width * done / total) if total > 0 else 0
        bar_color = "green" if pct == 100 else "cyan" if pct > 50 else "yellow"
        bar = f"[{bar_color}]{'|' * filled}[/][muted]{'.' * (bar_width - filled)}[/]"

        console.print()
        console.print(f"  [bold]{self.project_path.name}[/]")
        console.print(f"  {bar} {pct:.0f}%")
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

    # ── /tasks ───────────────────────────────────────────────────────────

    def _cmd_tasks(self) -> None:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()

        if not tasks:
            console.print("  [hint]No tasks yet. Describe what you want to build![/]")
            return

        table = Table(
            box=box.ROUNDED, show_header=True,
            header_style="bold cyan", padding=(0, 1),
            title=f"[bold]{self.project_path.name}[/]",
        )
        table.add_column("#", style="dim", width=4)
        table.add_column("Task", min_width=30)
        table.add_column("Status", width=12)
        table.add_column("Risk", width=8)

        status_icons = {
            "completed": "[green]done[/]",
            "in_progress": "[cyan]active[/]",
            "pending": "[dim]pending[/]",
            "failed": "[red]failed[/]",
            "blocked": "[yellow]blocked[/]",
        }

        for t in tasks:
            risk = t.metadata.get("risk", "")
            risk_style = {
                "high": "[red]high[/]",
                "medium": "[yellow]med[/]",
                "low": "[green]low[/]",
            }.get(risk, "[muted]-[/]")
            table.add_row(
                str(t.id),
                t.subject,
                status_icons.get(t.status, t.status),
                risk_style,
            )

        console.print()
        console.print(table)

    # ── /models ──────────────────────────────────────────────────────────

    def _cmd_models(self) -> None:
        table = Table(
            box=box.ROUNDED, show_header=True,
            header_style="bold cyan", padding=(0, 1),
        )
        table.add_column("Alias", style="bold", min_width=15)
        table.add_column("Model ID", min_width=40)
        table.add_column("Provider", width=12)

        for alias, model_id in sorted(MODEL_ALIASES.items()):
            provider = (
                "Bedrock" if "bedrock" in model_id
                else "OpenRouter" if "openrouter" in model_id
                else "Anthropic"
            )
            style = (
                "magenta" if "bedrock" in model_id
                else "blue" if "openrouter" in model_id
                else "green"
            )
            marker = " [accent]*[/]" if model_id == self.model else ""
            table.add_row(f"{alias}{marker}", f"[{style}]{model_id}[/]", provider)

        console.print()
        console.print(table)
        console.print(f"\n  [muted]* = active model[/]")

    # ── /formation ───────────────────────────────────────────────────────

    def _cmd_formation(self, arg: str) -> None:
        from formations import FORMATIONS, select_formation

        if arg:
            from formations import get_formation
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
        else:
            table = Table(
                box=box.ROUNDED, show_header=True,
                header_style="bold cyan", padding=(0, 1),
            )
            table.add_column("Formation", min_width=20)
            table.add_column("Roles", width=6)
            table.add_column("Waves", width=6)
            table.add_column("Description", min_width=30)

            for name, f in FORMATIONS.items():
                table.add_row(
                    name, str(len(f.roles)),
                    str(len(f.wave_order)), f.description[:50],
                )

            console.print()
            console.print(table)
            console.print(f"\n  [hint]/formation <name> for details[/]")

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
        if any(kw in lower for kw in build_triggers) and not self._has_tasks():
            console.print()
            console.print(f"  [nova]Nova[/] [muted]--[/] Great, let's build that!")
            await self._guided_build(user_input)
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
        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[nova]Nova[/] is thinking..."),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            progress.add_task("thinking", total=None)

            from forge_agent import ForgeAgent, BUILT_IN_TOOLS
            mc = get_model_config(self.model, max_tokens=4096)

            agent = ForgeAgent(
                model_config=mc,
                project_root=self.project_path,
                tools=BUILT_IN_TOOLS,
                max_turns=10,
                agent_id="forge-chat",
            )

            spec_text = ""
            spec_path = self.project_path / "spec.md"
            if spec_path.exists():
                spec_text = spec_path.read_text()[:3000]

            context = ""
            if spec_text:
                context = f"\n\nProject spec:\n{spec_text}"

            existing = self._gather_project_files()
            if existing:
                context += f"\n\nExisting files: {', '.join(existing.keys())}"

            result = await agent.run(
                prompt=f"{user_input}{context}",
                system=(
                    "You are Nova, an AI build assistant powered by Amazon Nova. "
                    "You help users build software. Be friendly, concise, and proactive. "
                    "Use write_file to create files and read_file to check existing ones. "
                    "Always offer to help with next steps."
                ),
            )

        # Display
        console.print()
        if result.error:
            console.print(f"  [error]Something went wrong:[/] {result.error}")
            console.print(f"  [hint]Try rephrasing, or check your credentials.[/]")
        elif result.output:
            console.print(f"  [nova]Nova[/] [muted]--[/]", end=" ")
            if any(c in result.output for c in ["```", "##", "- "]):
                console.print()
                console.print(Markdown(result.output))
            else:
                console.print(result.output)

        if result.tool_calls_made > 0:
            console.print(
                f"  [muted]({result.turns} turns, {result.tool_calls_made} tool calls)[/]"
            )

        if result.artifacts:
            for path in result.artifacts:
                console.print(f"  [success]{Path(path).name}[/] [muted]created[/]")

    # ── Helpers ──────────────────────────────────────────────────────────

    def _has_tasks(self) -> bool:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        try:
            store = TaskStore(project.tasks_file)
            return len(store.list()) > 0
        except Exception:
            return False

    def _get_task_summary(self) -> dict | None:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        try:
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
        for ext in ("*.py", "*.js", "*.ts", "*.html", "*.json", "*.yml", "*.yaml"):
            for f in self.project_path.rglob(ext):
                if f.name in skip or ".forge" in f.parts or "__pycache__" in f.parts:
                    continue
                rel = str(f.relative_to(self.project_path))
                try:
                    files[rel] = f.read_text()[:2000]
                except Exception:
                    pass
                if len(files) >= 15:
                    return files
        return files

    def _list_project_files(self) -> list[str]:
        """List meaningful project files (not forge internals)."""
        files = []
        skip = {"forge_cli.py", "challenge_build.py", "demo_nova_e2e.py"}
        for ext in ("*.py", "*.js", "*.ts", "*.html", "*.css", "*.json", "*.yml"):
            for f in self.project_path.rglob(ext):
                if f.name in skip or ".forge" in f.parts or "__pycache__" in f.parts:
                    continue
                files.append(str(f.relative_to(self.project_path)))
        return sorted(files)[:20]


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
