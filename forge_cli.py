#!/usr/bin/env python3
"""Nova Forge Interactive CLI — conversational agent shell powered by Amazon Nova.

Launch with: forge chat
         or: python forge_cli.py

An interactive REPL where you describe what you want and Nova builds it.
Slash commands for direct control, natural language for everything else.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich import box

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
})

console = Console(theme=THEME)

PT_STYLE = PTStyle.from_dict({
    "prompt": "#00d7ff bold",
    "": "#ffffff",
})

VERSION = "0.2.0"

# ── Banner ───────────────────────────────────────────────────────────────────

BANNER = r"""[bold cyan]
  ╔═══════════════════════════════════════════════════╗
  ║   ◆ Nova Forge v{version}                           ║
  ║   Brain: Amazon Nova Lite (AWS Bedrock)           ║
  ║   Type a goal, or /help for commands              ║
  ╚═══════════════════════════════════════════════════╝[/]"""

HELP_TEXT = """
[bold cyan]Slash Commands[/]
  [accent]/plan[/] <goal>       Plan a project from a description
  [accent]/build[/]             Build the planned project (wave execution)
  [accent]/status[/]            Show project progress
  [accent]/tasks[/]             List all tasks
  [accent]/models[/]            Show available models
  [accent]/formation[/]         Show current formation
  [accent]/audit[/]             Show recent audit entries
  [accent]/new[/] <name>         Create a new project directory
  [accent]/cd[/] <path>          Change project directory
  [accent]/pwd[/]               Show current project
  [accent]/clear[/]             Clear the screen
  [accent]/quit[/]              Exit

[bold cyan]Natural Language[/]
  Just type what you want — Nova will figure it out.
  Examples:
    "Build me a weather API with Flask"
    "Add search functionality to the bookmarks endpoint"
    "Run the tests and fix any failures"
    "What files are in this project?"
"""


# ── Interactive Shell ────────────────────────────────────────────────────────

class ForgeShell:
    """Interactive CLI shell for Nova Forge."""

    def __init__(self, project_path: str | Path = ".", default_model: str | None = None):
        self.project_path = Path(project_path).resolve()
        self.model = resolve_model(default_model) if default_model else DEFAULT_MODELS["planning"]
        self.history: list[dict] = []  # conversation history for Nova
        self._ensure_project()

    def _ensure_project(self) -> None:
        """Ensure .forge/ exists in the project directory."""
        forge_dir = self.project_path / ".forge"
        if not forge_dir.exists():
            init_forge_dir(self.project_path)

    # ── Main loop ────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Main REPL loop."""
        console.print(BANNER.format(version=VERSION))
        console.print(f"  [muted]Project: {self.project_path}[/]")
        console.print(f"  [muted]Model:   {self.model}[/]")
        console.print()

        history_file = Path.home() / ".forge_history"
        session = PromptSession(
            history=FileHistory(str(history_file)),
            style=PT_STYLE,
        )

        while True:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: session.prompt(
                        HTML("<prompt>you → </prompt>"),
                    ),
                )
            except (EOFError, KeyboardInterrupt):
                console.print("\n  [muted]Goodbye.[/]")
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

    # ── Slash command router ─────────────────────────────────────────────

    async def _handle_slash(self, raw: str) -> bool:
        """Route slash commands. Returns True if /quit."""
        parts = raw.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""

        match cmd:
            case "/quit" | "/exit" | "/q":
                console.print("  [muted]Goodbye.[/]")
                return True
            case "/help" | "/h" | "/?":
                console.print(HELP_TEXT)
            case "/clear" | "/cls":
                console.clear()
                console.print(BANNER.format(version=VERSION))
            case "/pwd":
                console.print(f"  [info]Project:[/] {self.project_path}")
            case "/cd":
                self._cmd_cd(arg)
            case "/new":
                await self._cmd_new(arg)
            case "/plan":
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
                console.print(f"  [warning]Unknown command:[/] {cmd}. Type /help for commands.")

        return False

    # ── /cd ──────────────────────────────────────────────────────────────

    def _cmd_cd(self, path: str) -> None:
        if not path:
            console.print(f"  [info]Current:[/] {self.project_path}")
            return
        new_path = Path(path).resolve()
        if not new_path.is_dir():
            console.print(f"  [error]Not a directory:[/] {new_path}")
            return
        self.project_path = new_path
        self._ensure_project()
        self.history.clear()
        console.print(f"  [success]Switched to:[/] {self.project_path}")

    # ── /new ─────────────────────────────────────────────────────────────

    async def _cmd_new(self, name: str) -> None:
        if not name:
            console.print("  [warning]Usage:[/] /new <project-name>")
            return

        project_dir = self.project_path / name
        project_dir.mkdir(parents=True, exist_ok=True)

        from forge_compliance import ComplianceChecker
        cc = ComplianceChecker(project_dir)
        cc.fix()

        self.project_path = project_dir
        self.history.clear()

        console.print(f"  [success]Created:[/] {project_dir}")
        console.print(f"  [muted].forge/ initialized with full ecosystem[/]")
        console.print(f"  [info]Next:[/] /plan \"your goal\" or just describe what you want")

    # ── /plan ────────────────────────────────────────────────────────────

    async def _cmd_plan(self, goal: str) -> None:
        if not goal:
            console.print("  [warning]Usage:[/] /plan <goal description>")
            return

        console.print()
        console.print(f"  [nova]nova[/] [muted]←[/] Planning with {_short_model(self.model)}...")

        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            spec_task = progress.add_task("Generating spec.md...", total=None)

            from forge_orchestrator import ForgeOrchestrator
            orch = ForgeOrchestrator(self.project_path, model=self.model)

            # Phase 1: Plan
            result = await orch.plan(goal, model=self.model)
            progress.update(spec_task, description="Done.")

        if result.error and not result.spec_path:
            console.print(f"  [error]Planning failed:[/] {result.error}")
            return

        # Show spec summary
        if result.spec_path and result.spec_path.exists():
            spec_text = result.spec_path.read_text()
            lines = spec_text.strip().split("\n")
            console.print(f"  [success]✓[/] spec.md — {len(lines)} lines")

        # Show tasks
        if result.tasks_path and result.tasks_path.exists():
            try:
                tasks_data = json.loads(result.tasks_path.read_text())
                console.print(f"  [success]✓[/] tasks.json — {len(tasks_data)} tasks")
                console.print()
                self._render_task_table(tasks_data)
            except json.JSONDecodeError:
                console.print(f"  [success]✓[/] tasks.json — {result.task_count} tasks")
        elif result.task_count > 0:
            console.print(f"  [success]✓[/] {result.task_count} tasks loaded into TaskStore")

        console.print()
        console.print(f"  [info]Next:[/] /build to execute, or describe changes to the plan")

    # ── /build ───────────────────────────────────────────────────────────

    async def _cmd_build(self, arg: str) -> None:
        from forge_tasks import TaskStore
        from config import ForgeProject as FP

        project = FP(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()

        if not tasks:
            console.print("  [warning]No tasks found.[/] Run /plan first.")
            return

        pending = [t for t in tasks if t.status == "pending"]
        if not pending:
            console.print("  [warning]No pending tasks.[/] All tasks already completed or failed.")
            return

        console.print()
        console.print(f"  [nova]nova[/] [muted]←[/] Building with {_short_model(self.model)}...")
        console.print(f"  [muted]     {len(pending)} tasks across {self.project_path.name}[/]")
        console.print()

        # Compute waves for display
        try:
            waves = store.compute_waves()
        except ValueError as exc:
            console.print(f"  [error]Dependency cycle:[/] {exc}")
            return

        total_start = time.time()
        total_tool_calls = 0
        total_files = 0
        wave_statuses: list[tuple[int, str, str, float]] = []  # (idx, name, status, duration)

        from forge_agent import ForgeAgent, BUILT_IN_TOOLS
        from forge_guards import PathSandbox
        from forge_hooks import HookSystem

        for wave_idx, wave_tasks in enumerate(waves):
            runnable = [t for t in wave_tasks if store.get(t.id) and store.get(t.id).status not in ("completed", "blocked")]
            if not runnable:
                for t in wave_tasks:
                    wave_statuses.append((wave_idx, t.subject, "skip", 0.0))
                continue

            for task in runnable:
                store.update(task.id, status="in_progress")

                # Show progress
                label = f"  Wave {wave_idx}/{len(waves)-1} "
                console.print(f"  [muted]Wave {wave_idx}/{len(waves)-1}[/]  ", end="")

                with Progress(
                    SpinnerColumn("dots"),
                    TextColumn(f"[bold]{task.subject}[/]"),
                    BarColumn(bar_width=20),
                    TimeElapsedColumn(),
                    console=console,
                    transient=True,
                ) as progress:
                    ptask = progress.add_task(task.subject, total=None)

                    # Build agent
                    mc = get_model_config(self.model, max_tokens=4096)
                    agent = ForgeAgent(
                        model_config=mc,
                        project_root=self.project_path,
                        tools=BUILT_IN_TOOLS,
                        max_turns=15,
                        agent_id=f"forge-wave{wave_idx}-{task.id}",
                    )

                    # Build prompt with spec context
                    spec_text = ""
                    spec_path = self.project_path / "spec.md"
                    if spec_path.exists():
                        spec_text = spec_path.read_text()[:4000]

                    # Gather existing files for context
                    existing = [f.name for f in self.project_path.glob("*.py")
                               if f.name not in ("forge_cli.py", "challenge_build.py", "demo_nova_e2e.py")]
                    context_hint = ""
                    if existing:
                        context_hint = f"\n\nExisting files: {', '.join(existing)}"
                        for fname in existing[:3]:
                            fpath = self.project_path / fname
                            try:
                                content = fpath.read_text()[:2000]
                                context_hint += f"\n\n--- {fname} ---\n{content}"
                            except Exception:
                                pass

                    prompt = (
                        f"## Project Spec\n{spec_text}\n\n"
                        f"## Your Task\n{task.subject}: {task.description}\n\n"
                        f"## Instructions\n"
                        f"Implement this task. Use write_file to create files. "
                        f"Read existing files first with read_file. "
                        f"Write complete working code, not stubs."
                        f"{context_hint}"
                    )

                    wave_start = time.time()
                    try:
                        result = await agent.run(
                            prompt=prompt,
                            system="You are a Python developer. Write complete, production code. Use tools to create files.",
                        )
                        duration = time.time() - wave_start
                        total_tool_calls += result.tool_calls_made
                        files_written = len(result.artifacts) if result.artifacts else 0
                        total_files += files_written

                        if result.error:
                            store.update(task.id, status="failed")
                            wave_statuses.append((wave_idx, task.subject, "fail", duration))
                        else:
                            store.update(task.id, status="completed", artifacts=result.artifacts)
                            wave_statuses.append((wave_idx, task.subject, "pass", duration))

                    except Exception as exc:
                        duration = time.time() - wave_start
                        store.update(task.id, status="failed")
                        wave_statuses.append((wave_idx, task.subject, "fail", duration))

                # Print result line (after progress bar clears)
                last = wave_statuses[-1]
                _, name, status, dur = last
                if status == "pass":
                    console.print(f"  [success]✓[/] {name} [muted]({dur:.0f}s)[/]")
                elif status == "fail":
                    console.print(f"  [error]✗[/] {name} [muted]({dur:.0f}s)[/]")
                else:
                    console.print(f"  [muted]⊘[/] {name} [muted](skipped)[/]")

        total_duration = time.time() - total_start

        # Sync task state
        self._sync_task_state()

        # Summary
        passed = sum(1 for _, _, s, _ in wave_statuses if s == "pass")
        failed = sum(1 for _, _, s, _ in wave_statuses if s == "fail")

        console.print()
        if failed == 0:
            console.print(f"  [success]✓ Build complete[/] — {passed} tasks, {total_tool_calls} tool calls, {total_duration:.0f}s")
        else:
            console.print(f"  [warning]Build finished[/] — {passed} passed, {failed} failed, {total_duration:.0f}s")

        # List files created
        py_files = sorted(self.project_path.glob("*.py"))
        project_files = [f for f in py_files if f.name not in
                        ("forge_cli.py", "challenge_build.py", "demo_nova_e2e.py")]
        if project_files:
            console.print(f"  [muted]Files: {', '.join(f.name for f in project_files)}[/]")

    # ── /status ──────────────────────────────────────────────────────────

    def _cmd_status(self) -> None:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()

        if not tasks:
            console.print("  [muted]No tasks. Run /plan first.[/]")
            return

        completed = sum(1 for t in tasks if t.status == "completed")
        in_progress = sum(1 for t in tasks if t.status == "in_progress")
        pending = sum(1 for t in tasks if t.status == "pending")
        failed = sum(1 for t in tasks if t.status == "failed")
        blocked = sum(1 for t in tasks if t.status == "blocked")
        total = len(tasks)
        pct = (completed / total * 100) if total > 0 else 0

        # Progress bar
        bar_width = 30
        filled = int(bar_width * completed / total) if total > 0 else 0
        bar = "█" * filled + "░" * (bar_width - filled)

        console.print(f"  [bold]{self.project_path.name}[/]")
        console.print(f"  [success]{bar}[/] {pct:.0f}% ({completed}/{total})")
        if in_progress:
            console.print(f"  [info]In Progress:[/] {in_progress}")
        if pending:
            console.print(f"  [muted]Pending:     {pending}[/]")
        if failed:
            console.print(f"  [error]Failed:      {failed}[/]")
        if blocked:
            console.print(f"  [warning]Blocked:     {blocked}[/]")

        # List files
        py_files = [f for f in self.project_path.glob("*.py")
                    if f.name not in ("forge_cli.py", "challenge_build.py", "demo_nova_e2e.py")]
        if py_files:
            total_bytes = sum(f.stat().st_size for f in py_files)
            console.print(f"  [muted]Files: {len(py_files)} ({total_bytes:,} bytes)[/]")

    # ── /tasks ───────────────────────────────────────────────────────────

    def _cmd_tasks(self) -> None:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        store = TaskStore(project.tasks_file)
        tasks = store.list()

        if not tasks:
            console.print("  [muted]No tasks.[/]")
            return

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
        table.add_column("#", style="dim", width=4)
        table.add_column("Task", min_width=30)
        table.add_column("Status", width=12)
        table.add_column("Risk", width=8)

        status_styles = {
            "completed": "[green]✓ done[/]",
            "in_progress": "[cyan]▸ active[/]",
            "pending": "[dim]○ pending[/]",
            "failed": "[red]✗ failed[/]",
            "blocked": "[yellow]⊘ blocked[/]",
        }

        for t in tasks:
            risk = t.metadata.get("risk", "—")
            risk_style = {"high": "[red]high[/]", "medium": "[yellow]med[/]", "low": "[green]low[/]"}.get(risk, risk)
            table.add_row(
                str(t.id),
                t.subject,
                status_styles.get(t.status, t.status),
                risk_style,
            )

        console.print()
        console.print(table)

    # ── /models ──────────────────────────────────────────────────────────

    def _cmd_models(self) -> None:
        table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
        table.add_column("Alias", style="bold", min_width=15)
        table.add_column("Model ID", min_width=40)
        table.add_column("Provider", width=12)

        for alias, model_id in sorted(MODEL_ALIASES.items()):
            provider = "Bedrock" if "bedrock" in model_id else "OpenRouter" if "openrouter" in model_id else "Anthropic"
            style = "magenta" if "bedrock" in model_id else "blue" if "openrouter" in model_id else "green"
            table.add_row(alias, f"[{style}]{model_id}[/]", provider)

        console.print()
        console.print(table)
        console.print(f"\n  [muted]Active: {_short_model(self.model)}[/]")
        console.print(f"  [muted]Defaults — plan: {_short_model(DEFAULT_MODELS['planning'])}, "
                      f"code: {_short_model(DEFAULT_MODELS['coding'])}, "
                      f"review: {_short_model(DEFAULT_MODELS['review'])}[/]")

    # ── /formation ───────────────────────────────────────────────────────

    def _cmd_formation(self, arg: str) -> None:
        from formations import FORMATIONS, select_formation

        if arg:
            # Show specific formation
            from formations import get_formation
            try:
                f = get_formation(arg)
            except (KeyError, ValueError):
                console.print(f"  [error]Unknown formation:[/] {arg}")
                console.print(f"  [muted]Available: {', '.join(FORMATIONS.keys())}[/]")
                return
            console.print(f"  [bold]{f.name}[/] — {f.description}")
            for role in f.roles:
                console.print(f"    [accent]{role.name:20s}[/] model={_short_model(role.model)} policy={role.tool_policy}")
            console.print(f"  [muted]Waves: {len(f.wave_order)}[/]")
            for i, wave in enumerate(f.wave_order):
                console.print(f"    Wave {i}: {', '.join(wave)}")
        else:
            # List all
            table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
            table.add_column("Formation", min_width=20)
            table.add_column("Roles", width=6)
            table.add_column("Waves", width=6)
            table.add_column("Description", min_width=30)

            for name, f in FORMATIONS.items():
                table.add_row(name, str(len(f.roles)), str(len(f.wave_order)), f.description[:50])

            console.print()
            console.print(table)
            console.print(f"\n  [muted]Use: /formation <name> for details[/]")

    # ── /audit ───────────────────────────────────────────────────────────

    def _cmd_audit(self) -> None:
        project = ForgeProject(root=self.project_path)
        audit_file = project.audit_dir / "audit.jsonl"

        if not audit_file.exists():
            console.print("  [muted]No audit log yet.[/]")
            return

        lines = [l for l in audit_file.read_text().strip().split("\n") if l.strip()]
        console.print(f"  [info]Audit log:[/] {len(lines)} entries")
        console.print()

        table = Table(box=box.SIMPLE, show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("Time", width=10)
        table.add_column("Tool", width=10)
        table.add_column("Outcome", width=10)
        table.add_column("Agent", min_width=20)

        for line in lines[-15:]:  # Last 15
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

    # ── Natural language → Nova agent ────────────────────────────────────

    async def _handle_natural(self, user_input: str) -> None:
        """Send natural language to Nova and let it figure out what to do."""
        console.print()

        # Check for implicit plan/build intent
        lower = user_input.lower()
        if any(kw in lower for kw in ["build me", "create a", "make a", "build a", "i want"]) and not self._has_tasks():
            # Auto-plan then offer to build
            console.print(f"  [nova]nova[/] [muted]←[/] I'll plan that first...")
            console.print()
            await self._cmd_plan(user_input)
            return

        # General agent interaction
        with Progress(
            SpinnerColumn("dots"),
            TextColumn("[nova]nova[/] is thinking..."),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            ptask = progress.add_task("thinking", total=None)

            from forge_agent import ForgeAgent, BUILT_IN_TOOLS
            mc = get_model_config(self.model, max_tokens=4096)

            agent = ForgeAgent(
                model_config=mc,
                project_root=self.project_path,
                tools=BUILT_IN_TOOLS,
                max_turns=10,
                agent_id="forge-chat",
            )

            # Build conversation context
            spec_text = ""
            spec_path = self.project_path / "spec.md"
            if spec_path.exists():
                spec_text = spec_path.read_text()[:3000]

            context = ""
            if spec_text:
                context = f"\n\nProject spec:\n{spec_text}"

            existing = [f.name for f in self.project_path.glob("*.py")
                       if not f.name.startswith("forge_") and f.name != "challenge_build.py"]
            if existing:
                context += f"\n\nExisting files: {', '.join(existing)}"

            result = await agent.run(
                prompt=f"{user_input}{context}",
                system=(
                    "You are Nova, an AI coding assistant powered by Amazon Nova. "
                    "You help users build software projects. Use your tools to read files, "
                    "write code, and run commands. Be concise and direct. "
                    "When you write files, use write_file. When you need to check something, use read_file."
                ),
            )

        # Display response
        if result.error:
            console.print(f"  [error]Error:[/] {result.error}")
        elif result.output:
            console.print(f"  [nova]nova[/] [muted]←[/]", end=" ")
            # Show as markdown if it looks like it
            if any(c in result.output for c in ["```", "##", "- "]):
                console.print()
                console.print(Markdown(result.output))
            else:
                console.print(result.output)

        if result.tool_calls_made > 0:
            console.print(f"  [muted]({result.turns} turns, {result.tool_calls_made} tool calls)[/]")

        if result.artifacts:
            for path, info in result.artifacts.items():
                console.print(f"  [success]✓[/] {Path(path).name} [muted]created[/]")

    # ── Helpers ──────────────────────────────────────────────────────────

    def _has_tasks(self) -> bool:
        from forge_tasks import TaskStore
        project = ForgeProject(root=self.project_path)
        try:
            store = TaskStore(project.tasks_file)
            return len(store.list()) > 0
        except Exception:
            return False

    def _sync_task_state(self) -> None:
        """Sync TaskStore to task-state.json."""
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
        """Render tasks.json data as a rich table."""
        table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan", padding=(0, 1))
        table.add_column("#", style="dim", width=4)
        table.add_column("Task", min_width=30)
        table.add_column("Risk", width=8)
        table.add_column("Depends On", width=12)

        for i, t in enumerate(tasks_data):
            risk = t.get("risk", "—")
            risk_style = {"high": "[red]high[/]", "medium": "[yellow]med[/]", "low": "[green]low[/]"}.get(risk, risk)
            deps = t.get("blocked_by", [])
            deps_str = ", ".join(str(d) for d in deps) if deps else "—"
            table.add_row(str(i), t.get("subject", "?"), risk_style, deps_str)

        console.print(table)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _short_model(model_id: str) -> str:
    """Short display name for a model."""
    for alias, full_id in MODEL_ALIASES.items():
        if full_id == model_id or model_id == alias:
            return alias
    return model_id.split("/")[-1][:30]


# ── Entry point ──────────────────────────────────────────────────────────────

def main(project_path: str = "."):
    """Launch the interactive shell."""
    logging.basicConfig(level=logging.WARNING)
    shell = ForgeShell(project_path)
    try:
        asyncio.run(shell.run())
    except KeyboardInterrupt:
        console.print("\n  [muted]Interrupted.[/]")


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else "."
    main(path)
