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

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn, Progress, SpinnerColumn, TaskID,
    TextColumn, TimeElapsedColumn, MofNCompleteColumn,
)
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich import box

from forge_agent import AgentEvent

# ── Theme (shared with forge_cli.py) ────────────────────────────────────────

THEME = Theme({
    "info": "cyan",
    "success": "bold green",
    "warning": "bold yellow",
    "error": "bold red",
    "muted": "dim",
    "accent": "bold cyan",
    "nova": "bold magenta",
    "tool": "dim cyan",
    "step": "bold white",
    "blocked": "dim yellow",
    "retry": "yellow",
    "file.read": "dim",
    "file.write": "green",
    "file.edit": "yellow",
    "file.run": "cyan",
})

console = Console(theme=THEME)

# ── Tool display helpers ────────────────────────────────────────────────────

TOOL_ICONS = {
    "read_file":  "eye",
    "write_file": "pencil",
    "edit_file":  "wrench",
    "bash":       "terminal",
    "glob_files": "magnifying_glass",
    "grep":       "magnifying_glass",
}

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
    _current_tool: str = ""
    _current_file: str = ""

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
        display.print_summary()
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

    def end_task(self, task_id: int, passed: bool, result: Any = None) -> None:
        """Called when a build task finishes."""
        trace = self.traces.get(task_id)
        if not trace:
            return

        trace.end_time = time.monotonic()
        trace.status = "passed" if passed else "failed"
        if result:
            trace.turns = getattr(result, "turns", 0)
            trace.tokens_in += getattr(result, "token_usage", {}).get("input", 0)
            trace.tokens_out += getattr(result, "token_usage", {}).get("output", 0)
            if not passed and getattr(result, "error", None):
                trace.error = result.error

        self._completed += 1

        # Update overall progress
        if self._progress and self._overall_task is not None:
            self._progress.update(self._overall_task, completed=self._completed)

        # Print task result line
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
            self._update_tool_status(event, trace)

        elif event.kind == "tool_end":
            trace.tool_ms += event.duration_ms
            # Track files
            if event.file_action == "write" and event.file_path:
                if event.file_path not in trace.files_written:
                    trace.files_written.append(event.file_path)
            elif event.file_action == "edit" and event.file_path:
                if event.file_path not in trace.files_edited:
                    trace.files_edited.append(event.file_path)
            elif event.file_action == "read" and event.file_path:
                if event.file_path not in trace.files_read:
                    trace.files_read.append(event.file_path)
            elif event.file_action == "run":
                cmd = event.tool_args.get("command", "")[:60]
                if cmd:
                    trace.commands_run.append(cmd)

            if event.error:
                trace.error = event.error

        elif event.kind == "compact":
            if self.verbose:
                console.print(
                    f"    [muted]context compacted: "
                    f"{_format_tokens(event.tokens_in)} -> {_format_tokens(event.tokens_out)} tokens[/]"
                )

        elif event.kind == "error":
            trace.error = event.error

    # ── Progress bar management ─────────────────────────────────────────

    def create_progress(self) -> Progress:
        """Create and return the Progress instance for use as context manager."""
        self._progress = Progress(
            SpinnerColumn("dots"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=20, complete_style="bright_magenta", finished_style="green"),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
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

    # ── Display helpers ─────────────────────────────────────────────────

    def _update_tool_status(self, event: AgentEvent, trace: TaskTrace) -> None:
        """Update the progress bar description with current tool call."""
        verb = TOOL_VERBS.get(event.tool_name, event.tool_name)
        target = _short_path(event.file_path, 35)

        if event.tool_name == "bash":
            cmd = event.tool_args.get("command", "")[:40]
            desc = f"[tool]{verb}[/] [muted]{cmd}[/]"
        elif target:
            desc = f"[tool]{verb}[/] [muted]{target}[/]"
        else:
            desc = f"[tool]{verb}...[/]"

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

        # Show error detail for failures
        if trace.status == "failed" and trace.error:
            err_preview = trace.error[:120].replace("\n", " ")
            console.print(f"       [error]{err_preview}[/]")

    # ── Build summary ───────────────────────────────────────────────────

    def print_summary(self, preview_url: str = "") -> None:
        """Print the final build summary report."""
        build_dur = time.monotonic() - self._build_start

        passed = sum(1 for t in self.traces.values() if t.status == "passed")
        failed = sum(1 for t in self.traces.values() if t.status == "failed")
        blocked = self._blocked
        total_tools = sum(t.tool_calls for t in self.traces.values())
        total_tokens_in = sum(t.tokens_in for t in self.traces.values())
        total_tokens_out = sum(t.tokens_out for t in self.traces.values())
        total_model_ms = sum(t.model_ms for t in self.traces.values())
        total_tool_ms = sum(t.tool_ms for t in self.traces.values())

        all_written = []
        all_edited = []
        for t in self.traces.values():
            all_written.extend(t.files_written)
            all_edited.extend(t.files_edited)
        files_created = sorted(set(all_written))
        files_modified = sorted(set(all_edited))

        console.print()

        # ── Summary table ────────────────────────────────────────────────
        table = Table(
            box=box.ROUNDED, show_header=False, padding=(0, 2),
            border_style="bright_magenta" if failed == 0 else "yellow",
            title="[bold] Build Summary [/]",
        )
        table.add_column("Key", style="bold", width=12)
        table.add_column("Value")

        # Status line
        status_parts = []
        if passed:
            status_parts.append(f"[success]{passed} passed[/]")
        if failed:
            status_parts.append(f"[error]{failed} failed[/]")
        if blocked:
            status_parts.append(f"[blocked]{blocked} blocked[/]")
        table.add_row("Result", " ".join(status_parts))
        table.add_row("Duration", f"{build_dur:.1f}s")
        table.add_row("Tool calls", str(total_tools))

        # Token usage
        if total_tokens_in or total_tokens_out:
            table.add_row(
                "Tokens",
                f"{_format_tokens(total_tokens_in)} in / {_format_tokens(total_tokens_out)} out"
            )

        # Timing breakdown
        if total_model_ms and total_tool_ms:
            table.add_row(
                "Time split",
                f"LLM {_format_ms(total_model_ms)} | Tools {_format_ms(total_tool_ms)}"
            )

        # Files
        if files_created:
            names = [_short_path(f, 25) for f in files_created[:8]]
            remainder = len(files_created) - 8
            line = ", ".join(names)
            if remainder > 0:
                line += f" +{remainder} more"
            table.add_row("Created", f"[file.write]{line}[/]")

        if files_modified:
            names = [_short_path(f, 25) for f in files_modified[:5]]
            table.add_row("Modified", f"[file.edit]{', '.join(names)}[/]")

        if preview_url:
            table.add_row("Preview", f"[accent]{preview_url}[/]")

        console.print(table)
