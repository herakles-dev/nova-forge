"""Nova Forge Build Display — Rich live UI for agent execution.

Subscribes to ForgeAgent events and renders real-time progress:
- Current tool call with spinner
- File creates/edits as they happen
- Per-task summary with timing breakdown
- Final build report with token usage

Designed to feel like Claude Code / Gemini CLI:
- Details stream in real-time (not hidden behind a blank spinner)
- Collapsed by default, key info visible
- Color-coded status indicators
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TaskID,
    TextColumn, TimeElapsedColumn, MofNCompleteColumn,
)
from rich.table import Table
from rich.text import Text
from rich import box

from forge_agent import AgentEvent, AgentResult
from forge_theme import console, TOOL_ICONS, SPINNERS, BRAND

TOOL_VERBS = {
    "read_file":  "Reading",
    "write_file": "Writing",
    "edit_file":  "Editing",
    "bash":       "Running",
    "glob_files": "Searching",
    "grep":       "Searching",
}

ACTION_STYLES = {
    "read":   "file.read",
    "write":  "file.write",
    "edit":   "file.edit",
    "run":    "file.run",
    "search": "muted",
}


def _short_path(path: str, max_len: int = 40) -> str:
    """Shorten a file path for display."""
    if not path:
        return ""
    p = Path(path)
    name = p.name
    if len(str(p)) <= max_len:
        return str(p)
    return f".../{p.parent.name}/{name}" if p.parent.name else name


def _format_size(size: int) -> str:
    if size == 0:
        return ""
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    return f"{size / 1024 / 1024:.1f}MB"


def _format_tokens(n: int) -> str:
    if n == 0:
        return "0"
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


def _format_ms(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def _suggest_fix(error: str) -> str:
    """Classify error string and suggest a specific fix."""
    err = error.lower()
    if "import" in err and ("no module" in err or "cannot find" in err):
        return "Missing dependency? Check requirements.txt or run pip install"
    if "syntax" in err:
        return "Syntax error — check the file for typos, unclosed brackets, or bad indentation"
    if "timeout" in err or "timed out" in err:
        return "Timeout — try /preview stop first, or increase timeout in config"
    if "permission" in err or "errno 13" in err:
        return "Permission denied — check file permissions or run with appropriate access"
    if "max_turns" in err:
        return "Agent hit turn limit — try a simpler task description or use a larger model"
    if "429" in err or "rate" in err:
        return "Rate limited — wait a moment and retry, or switch to a different model"
    if "context" in err and ("length" in err or "exceed" in err):
        return "Context window exceeded — try breaking the task into smaller pieces"
    if "paused" in err:
        return "Build paused by user — use /build to resume"
    if "conflict" in err:
        return "File ownership conflict — another agent owns this file"
    return ""


# ── Task-level tracker ──────────────────────────────────────────────────────

@dataclass
class TaskTrace:
    """Accumulated events for a single build task."""
    task_id: int = 0
    subject: str = ""
    status: str = "pending"     # pending, running, passed, failed
    start_time: float = 0.0
    end_time: float = 0.0
    turns: int = 0
    tool_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    model_ms: int = 0
    tool_ms: int = 0
    files_written: list = field(default_factory=list)
    files_read: list = field(default_factory=list)
    files_edited: list = field(default_factory=list)
    commands_run: list = field(default_factory=list)
    error: str = ""
    tool_log: list = field(default_factory=list)  # Per-tool-call detail for proof-of-work
    self_corrections: int = 0
    _current_tool: str = ""
    _current_file: str = ""
    _tool_start_ms: int = 0

    @property
    def duration(self) -> float:
        end = self.end_time or time.monotonic()
        return end - self.start_time if self.start_time else 0.0

    @property
    def files_touched(self) -> int:
        return len(set(self.files_written + self.files_edited))


# ── Build display ───────────────────────────────────────────────────────────

class BuildDisplay:
    """Live build display — subscribes to ForgeAgent events.

    Usage:
        display = BuildDisplay(total_tasks=5)
        display.start_task(1, "Setup project")

        # Pass display.on_event as the agent callback:
        agent = ForgeAgent(..., on_event=display.on_event)
        result = await agent.run(...)

        display.end_task(1, passed=True, result=result)
    """

    def __init__(self, total_tasks: int = 0, verbose: bool = False):
        self.total_tasks = total_tasks
        self.verbose = verbose
        self.traces: dict[int, TaskTrace] = {}
        self._active_task_id: int = 0
        self._progress: Progress | None = None
        self._overall_task: TaskID | None = None
        self._current_task: TaskID | None = None
        self._completed = 0
        self._blocked = 0
        self._build_start = time.monotonic()

    # ── Task lifecycle ──────────────────────────────────────────────────

    def start_task(self, task_id: int, subject: str) -> None:
        """Called when a build task begins."""
        trace = TaskTrace(
            task_id=task_id, subject=subject,
            status="running", start_time=time.monotonic(),
        )
        self.traces[task_id] = trace
        self._active_task_id = task_id

        # Update progress display
        if self._progress and self._current_task is not None:
            self._progress.update(
                self._current_task,
                description=f"[step]{subject}[/]",
                completed=0,
            )

    def end_task(self, task_id: int, passed: bool, result: Any = None, suppress_output: bool = False) -> None:
        """Called when a build task finishes.

        Args:
            task_id: The task identifier.
            passed: Whether the task passed.
            result: Optional AgentResult with token/turn details.
            suppress_output: If True, skip printing the task result line
                (useful when the caller prints its own detailed output).
        """
        trace = self.traces.get(task_id)
        if not trace:
            return

        trace.end_time = time.monotonic()
        trace.status = "passed" if passed else "failed"
        if result:
            trace.turns = getattr(result, "turns", 0)
            trace.tokens_in += getattr(result, "tokens_in", 0)
            trace.tokens_out += getattr(result, "tokens_out", 0)
            if not passed and getattr(result, "error", None):
                trace.error = result.error
            trace.self_corrections = getattr(result, "self_corrections", 0)

        self._completed += 1

        # Update overall progress
        if self._progress and self._overall_task is not None:
            self._progress.update(self._overall_task, completed=self._completed)

        # Print task result line (unless caller handles its own output)
        if not suppress_output:
            self._print_task_result(trace)

    def mark_blocked(self, task_id: int, subject: str, reason: str) -> None:
        """Called when a task is blocked due to failed dependencies."""
        self._blocked += 1
        self._completed += 1

        # Update overall progress
        if self._progress and self._overall_task is not None:
            self._progress.update(self._overall_task, completed=self._completed)

        console.print(
            f"  [blocked]SKIP[/]  {subject[:38]:38s}  "
            f"[muted]{reason}[/]"
        )

    def mark_retry(self, task_id: int, attempt: int, max_retries: int) -> None:
        """Show retry attempt in progress bar."""
        trace = self.traces.get(task_id)
        if not trace:
            return
        desc = f"[retry]Retry {attempt}/{max_retries}[/] [muted]{trace.subject[:30]}[/]"
        if self._progress and self._current_task is not None:
            self._progress.update(self._current_task, description=desc)

    # ── Event callback (pass to ForgeAgent) ─────────────────────────────

    def on_event(self, event: AgentEvent) -> None:
        """Callback for ForgeAgent events — updates the live display."""
        trace = self.traces.get(self._active_task_id)
        if not trace:
            return

        if event.kind == "turn_start":
            trace.turns = event.turn

        elif event.kind == "model_response":
            trace.tokens_in += event.tokens_in
            trace.tokens_out += event.tokens_out
            trace.model_ms += event.duration_ms

        elif event.kind == "tool_start":
            trace.tool_calls += 1
            trace._current_tool = event.tool_name
            trace._current_file = event.file_path
            trace._tool_start_ms = int(time.monotonic() * 1000)
            # Capture bash commands at start (tool_end lacks tool_args)
            if event.tool_name == "bash":
                cmd = event.tool_args.get("command", "")[:60]
                if cmd:
                    trace.commands_run.append(cmd)
            self._update_tool_status(event, trace)

        elif event.kind == "tool_end":
            trace.tool_ms += event.duration_ms
            # Record per-tool-call detail for proof-of-work log
            tool_entry = {
                "tool": trace._current_tool,
                "file": trace._current_file or event.file_path or "",
                "action": event.file_action or "",
                "duration_ms": event.duration_ms,
                "error": event.error or "",
            }
            trace.tool_log.append(tool_entry)
            # Track files
            if event.file_action in ("write", "append") and event.file_path:
                if event.file_path not in trace.files_written:
                    trace.files_written.append(event.file_path)
            elif event.file_action == "edit" and event.file_path:
                if event.file_path not in trace.files_edited:
                    trace.files_edited.append(event.file_path)
            elif event.file_action == "read" and event.file_path:
                if event.file_path not in trace.files_read:
                    trace.files_read.append(event.file_path)
            elif event.file_action == "run":
                pass  # commands captured at tool_start (tool_end lacks tool_args)

            if event.error:
                trace.error = event.error

        elif event.kind == "compact":
            if self.verbose:
                console.print(
                    f"    [muted]context compacted: "
                    f"{_format_tokens(event.tokens_in)} -> {_format_tokens(event.tokens_out)} tokens[/]"
                )

        elif event.kind == "file_claimed":
            if self.verbose:
                console.print(f"    [accent]claim[/] {event.file_path}")
        elif event.kind == "file_conflict":
            console.print(f"    [warning]CONFLICT[/] {event.file_path} — {event.error}")
        elif event.kind == "announcement":
            if self.verbose:
                detail = event.tool_args.get("detail", "")
                console.print(f"    [info]announce[/] {detail}")
        elif event.kind == "pause_requested":
            if self._progress and self._current_task is not None:
                self._progress.update(
                    self._current_task,
                    description="[warning]Pausing after current operation...[/]",
                )
        elif event.kind == "turn_limit_warning":
            if self._progress and self._current_task is not None:
                self._progress.update(
                    self._current_task,
                    description="[warning]Turn limit reached — extending...[/]",
                )
        elif event.kind == "model_escalation":
            if self._progress and self._current_task is not None:
                self._progress.update(
                    self._current_task,
                    description="[warning]Escalating to stronger model...[/]",
                )
            console.print(f"    [warning]Model escalated[/] [muted]→ switching to more capable model[/]")
        elif event.kind == "error":
            trace.error = event.error

    # ── Progress bar management ─────────────────────────────────────────

    def create_progress(self) -> Progress:
        """Create and return the Progress instance for use as context manager."""
        self._progress = Progress(
            SpinnerColumn(SPINNERS["building"]),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=30, complete_style=BRAND["accent"], finished_style=BRAND["green"]),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("[dim]Ctrl-C to pause[/]"),
            console=console,
            transient=False,
        )
        self._overall_task = self._progress.add_task(
            "[bold]Building[/]", total=self.total_tasks,
        )
        self._current_task = self._progress.add_task(
            "[muted]waiting...[/]", total=None,
        )
        return self._progress

    def update_pause_visual(self) -> None:
        """Show pause indicator in the progress bar."""
        if self._progress and self._current_task is not None:
            self._progress.update(
                self._current_task,
                description="[warning]\u23f8 Pausing...[/]",
            )

    # ── Display helpers ─────────────────────────────────────────────────

    def _update_tool_status(self, event: AgentEvent, trace: TaskTrace) -> None:
        """Update the progress bar description with current tool call."""
        icon = TOOL_ICONS.get(event.tool_name, "")
        verb = TOOL_VERBS.get(event.tool_name, event.tool_name)
        target = _short_path(event.file_path, 35)

        if event.tool_name == "bash":
            cmd = event.tool_args.get("command", "")[:40]
            desc = f"{icon}[tool]{verb}[/] [muted]{cmd}[/]"
        elif target:
            desc = f"{icon}[tool]{verb}[/] [muted]{target}[/]"
        else:
            desc = f"{icon}[tool]{verb}...[/]"

        if self._progress and self._current_task is not None:
            self._progress.update(self._current_task, description=desc)

    def _print_task_result(self, trace: TaskTrace) -> None:
        """Print a rich single-line task result after completion."""
        dur = trace.duration
        icon = "[success] ok [/]" if trace.status == "passed" else "[error]FAIL[/]"

        # Build detail fragments
        parts = []
        if trace.turns:
            parts.append(f"{trace.turns}t")
        if trace.tool_calls:
            parts.append(f"{trace.tool_calls} calls")
        if trace.files_touched:
            parts.append(f"{trace.files_touched} files")

        tokens_total = trace.tokens_in + trace.tokens_out
        if tokens_total > 0:
            parts.append(f"{_format_tokens(tokens_total)} tok")

        detail = " · ".join(parts)

        # Truncate subject to prevent line wrapping
        subject = trace.subject[:36]

        # Compose the line
        console.print(
            f"  {icon}  {subject:36s}  "
            f"[muted]{dur:5.1f}s[/]  [dim]{detail}[/]"
        )

        # Show files created/edited
        if trace.files_written or trace.files_edited:
            file_parts = []
            for f in trace.files_written[:5]:
                file_parts.append(f"[file.write]+{_short_path(f, 30)}[/]")
            for f in trace.files_edited[:3]:
                file_parts.append(f"[file.edit]~{_short_path(f, 30)}[/]")
            if file_parts:
                console.print(f"       {' '.join(file_parts)}")

        # Show error detail for failures with actionable suggestions
        if trace.status == "failed" and trace.error:
            err_preview = trace.error[:120].replace("\n", " ")
            console.print(f"       [error]{err_preview}[/]")
            suggestion = _suggest_fix(trace.error)
            if suggestion:
                console.print(f"       [hint]{suggestion}[/]")

    # ── Build log ──────────────────────────────────────────────────────

    def save_build_log(self, project_path: Path, model_id: str = "", goal: str = "") -> Path | None:
        """Persist all task traces to a JSONL build log for proof-of-work.

        Returns the path to the log file, or None on error.
        """
        import json
        from datetime import datetime, timezone

        log_dir = Path(project_path) / ".forge" / "builds"
        log_dir.mkdir(parents=True, exist_ok=True)

        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"build_{ts}.jsonl"

        build_dur = time.monotonic() - self._build_start
        passed = sum(1 for t in self.traces.values() if t.status == "passed")
        failed = sum(1 for t in self.traces.values() if t.status == "failed")

        try:
            with open(log_file, "w") as f:
                # Build summary header
                header = {
                    "type": "build_summary",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "model": model_id,
                    "goal": goal[:200],
                    "duration_s": round(build_dur, 1),
                    "tasks_passed": passed,
                    "tasks_failed": failed,
                    "tasks_total": len(self.traces),
                    "total_turns": sum(t.turns for t in self.traces.values()),
                    "total_tool_calls": sum(t.tool_calls for t in self.traces.values()),
                    "total_tokens_in": sum(t.tokens_in for t in self.traces.values()),
                    "total_tokens_out": sum(t.tokens_out for t in self.traces.values()),
                    "total_model_ms": sum(t.model_ms for t in self.traces.values()),
                    "total_tool_ms": sum(t.tool_ms for t in self.traces.values()),
                    "files_created": sorted(set(
                        fp for t in self.traces.values() for fp in t.files_written
                    )),
                    "files_edited": sorted(set(
                        fp for t in self.traces.values() for fp in t.files_edited
                    )),
                }
                f.write(json.dumps(header) + "\n")

                # Per-task detail
                for task_id, trace in sorted(self.traces.items()):
                    entry = {
                        "type": "task_trace",
                        "task_id": trace.task_id,
                        "subject": trace.subject,
                        "status": trace.status,
                        "duration_s": round(trace.duration, 1),
                        "turns": trace.turns,
                        "tool_calls": trace.tool_calls,
                        "tokens_in": trace.tokens_in,
                        "tokens_out": trace.tokens_out,
                        "model_ms": trace.model_ms,
                        "tool_ms": trace.tool_ms,
                        "files_written": trace.files_written,
                        "files_read": trace.files_read,
                        "files_edited": trace.files_edited,
                        "commands_run": trace.commands_run[:10],
                        "error": trace.error or "",
                        "tool_log": trace.tool_log[:50],
                        "self_corrections": trace.self_corrections,
                    }
                    f.write(json.dumps(entry) + "\n")

            return log_file
        except Exception as exc:
            import logging
            logging.getLogger("forge.display").warning("Failed to save build log: %s", exc)
            console.print(f"  [warning]Could not save build log: {exc}[/]")
            return None


# ── Chat display ─────────────────────────────────────────────────────────────

class ChatDisplay:
    """Real-time display for chat agent — shows tool calls and streams text."""

    def __init__(self):
        self.tool_calls = 0
        self.files_written: list[str] = []
        self.files_edited: list[str] = []
        self.files_read: list[str] = []
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None
        self._start_time = time.monotonic()

    def on_event(self, event: AgentEvent) -> None:
        """Callback for ForgeAgent events — updates the live spinner."""
        if event.kind == "tool_start":
            self.tool_calls += 1
            verb = TOOL_VERBS.get(event.tool_name, event.tool_name)
            target = _short_path(event.file_path, 35)

            if event.tool_name == "bash":
                cmd = event.tool_args.get("command", "")[:40]
                desc = f"[tool]{verb}[/] [muted]{cmd}[/]"
            elif target:
                desc = f"[tool]{verb}[/] [muted]{target}[/]"
            else:
                desc = f"[tool]{verb}...[/]"

            if self._progress and self._task_id is not None:
                self._progress.update(self._task_id, description=desc)

        elif event.kind == "tool_end":
            if event.file_action == "write" and event.file_path:
                if event.file_path not in self.files_written:
                    self.files_written.append(event.file_path)
            elif event.file_action == "edit" and event.file_path:
                if event.file_path not in self.files_edited:
                    self.files_edited.append(event.file_path)
            elif event.file_action == "read" and event.file_path:
                if event.file_path not in self.files_read:
                    self.files_read.append(event.file_path)

        elif event.kind == "turn_start":
            if self._progress and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description=f"[nova]Nova[/] [muted]turn {event.turn}...[/]",
                )

        elif event.kind == "turn_limit_warning":
            if self._progress and self._task_id is not None:
                self._progress.update(
                    self._task_id,
                    description="[warning]Turn limit reached — extending...[/]",
                )

    def create_progress(self) -> Progress:
        """Create and return the Progress instance for use as context manager."""
        self._progress = Progress(
            SpinnerColumn(SPINNERS["thinking"]),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        )
        self._task_id = self._progress.add_task(
            f"[nova]Nova[/] [{BRAND['accent']}]is thinking...[/]", total=None,
        )
        return self._progress

    def print_footer(self, result: AgentResult) -> None:
        """Print a one-line summary after chat agent completes."""
        dur = time.monotonic() - self._start_time
        parts = []
        if result.turns:
            parts.append(f"{result.turns} turns")
        if self.tool_calls:
            parts.append(f"{self.tool_calls} tool calls")
        files_modified = len(set(self.files_written + self.files_edited))
        if files_modified:
            parts.append(f"{files_modified} files modified")
        parts.append(f"{dur:.1f}s")
        console.print(f"  [muted]({' · '.join(parts)})[/]")

        # Show files created/edited
        if self.files_written or self.files_edited:
            file_parts = []
            for f in self.files_written[:5]:
                file_parts.append(f"[file.write]+{_short_path(f, 30)}[/]")
            for f in self.files_edited[:5]:
                file_parts.append(f"[file.edit]~{_short_path(f, 30)}[/]")
            if file_parts:
                console.print(f"  {' '.join(file_parts)}")


# ── Assistant display helpers ────────────────────────────────────────────────

def display_autonomy_panel(level: int, skill_level: str = "intermediate") -> None:
    """Print a Rich panel showing the current autonomy level with capabilities.

    Verbosity adapts to skill_level: beginners get full capability lists,
    experts get a compact summary.
    """
    from rich.columns import Columns
    from rich.text import Text

    _level_names = {0: "Manual", 1: "Guided", 2: "Supervised", 3: "Trusted", 4: "Autonomous", 5: "Unattended"}
    _caps = {
        0: ([], ["read files", "write files", "run commands", "all operations"]),
        1: (["read files freely"], ["write files", "run commands", "destructive ops"]),
        2: (["read files freely", "write files freely", "run safe commands"],
            ["destructive commands", "system-level operations"]),
        3: (["read files freely", "write files freely", "run most commands"],
            ["permanent data deletion"]),
        4: (["everything — no interruptions"], []),
        5: (["everything — CI/CD optimized, no interruptions"], []),
    }

    name = _level_names.get(level, str(level))
    can_do, asks_about = _caps.get(level, ([], []))

    # Visual bar
    filled = min(level, 5)
    empty = max(0, 5 - filled)
    bar = "[cyan]" + "█" * filled + "[/][dim]" + "░" * empty + "[/]"

    lines: list[str] = [
        f"   Current: [bold]A{level} ({name})[/]",
        f"   {bar} {level}/5",
        "",
    ]

    if skill_level != "expert" or can_do or asks_about:
        for cap in can_do:
            lines.append(f"   [success]✓[/] {cap}")
        for ask in asks_about:
            lines.append(f"   [muted]✗[/] {ask} [dim](asks first)[/]")
        lines.append("")

    lines.append("   [dim]Set: /autonomy 0-5   Explain: /autonomy ?[/]")

    console.print(Panel(
        "\n".join(lines),
        title="[bold] Autonomy Level [/]",
        border_style="cyan",
        padding=(0, 1),
    ))


def display_skill_detection(level: str) -> None:
    """Show the detected (or chosen) skill level with a brief description."""
    descriptions = {
        "beginner":     "new to coding or Nova Forge",
        "intermediate": "comfortable with code and CLIs",
        "expert":       "experienced developer — minimal hints",
    }
    desc = descriptions.get(level, level)
    icon = {"beginner": "seedling", "intermediate": "wrench", "expert": "star"}.get(level, "")
    console.print(f"  [muted]Skill level:[/] [bold]{level.capitalize()}[/] [dim]— {desc}[/]")


def display_assistant_hint(hint: str) -> None:
    """Print a contextual hint in styled italic dim cyan with a leading icon."""
    console.print(f"  [hint]  {hint}[/]")
