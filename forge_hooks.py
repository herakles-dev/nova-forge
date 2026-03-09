"""Nova Forge HookSystem — V11-compatible pre/post/stop hook execution.

Hook protocol (mirrors V11 .claude/settings.json):
  - Exit 0  → allow
  - Exit 2  → block (read stderr for human-readable reason)
  - Anything else → treat as allow, log warning

Shell hooks receive a JSON payload on stdin. Python hooks are registered
functions that run in-process. Both types run in order; the first block
wins and short-circuits the rest.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from config import FORGE_DIR_NAME

logger = logging.getLogger(__name__)

# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class HookResult:
    """Result returned by any hook execution."""
    blocked: bool = False
    reason: str = ""
    modified_args: dict | None = None  # hooks can modify tool args


class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"


# Internal representation of a configured shell hook
@dataclass
class _ShellHook:
    command: str
    timeout: int = 5  # seconds


# ── HookSystem ───────────────────────────────────────────────────────────────


class HookSystem:
    """Runs pre/post/stop hooks using the V11-compatible protocol.

    Shell hooks are loaded from ``.forge/settings.json`` (same format as
    V11's ``.claude/settings.json``).  Python hooks are registered at
    runtime via :meth:`register`.
    """

    def __init__(self, settings_file: Path | None = None) -> None:
        # session_id is stable for the lifetime of this HookSystem instance
        self._session_id: str = f"forge-{uuid.uuid4()}"

        # shell hooks: event -> list of _ShellHook
        self._shell_hooks: dict[HookEvent, list[_ShellHook]] = {
            e: [] for e in HookEvent
        }
        # python hooks: event -> list of Callable
        self._python_hooks: dict[HookEvent, list[Callable]] = {
            e: [] for e in HookEvent
        }

        self._load_settings(settings_file)

    # ── Configuration loading ────────────────────────────────────────────────

    def _load_settings(self, settings_file: Path | None) -> None:
        """Load shell hook config from settings.json if it exists."""
        if settings_file is None:
            # Resolve relative to cwd's .forge/ directory
            settings_file = Path.cwd() / FORGE_DIR_NAME / "settings.json"

        if not settings_file.exists():
            logger.debug("No settings file found at %s — running with no shell hooks", settings_file)
            return

        try:
            raw = json.loads(settings_file.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to parse %s: %s — no shell hooks loaded", settings_file, exc)
            return

        hooks_cfg: dict = raw.get("hooks", {})
        for event in HookEvent:
            entries = hooks_cfg.get(event.value, [])
            for entry in entries:
                command = entry.get("command", "").strip()
                if not command:
                    continue
                # timeout in settings is milliseconds (V11 convention); convert to seconds
                timeout_ms = entry.get("timeout", 5000)
                self._shell_hooks[event].append(
                    _ShellHook(command=command, timeout=max(1, timeout_ms // 1000))
                )

        total = sum(len(v) for v in self._shell_hooks.values())
        logger.debug("Loaded %d shell hook(s) from %s", total, settings_file)

    # ── Python hook registration ──────────────────────────────────────────────

    def register(self, event: HookEvent, fn: Callable) -> None:
        """Register a Python hook function for the given event.

        PreToolUse / PostToolUse signature::

            def my_hook(tool_name: str, args: dict, result: str | None) -> HookResult: ...

        Stop signature::

            def my_stop_hook(tool_name: str, args: dict, result: str | None) -> HookResult: ...
        """
        self._python_hooks[event].append(fn)

    # ── Public async API ──────────────────────────────────────────────────────

    async def pre_tool_use(self, tool_name: str, args: dict, project: str = "") -> HookResult:
        """Run all PreToolUse hooks. Returns first blocking result or allow."""
        payload = self._build_payload(tool_name, args, project=project)

        for hook in self._shell_hooks[HookEvent.PRE_TOOL_USE]:
            result = await self._run_shell_hook(hook.command, payload, timeout=hook.timeout)
            if result.blocked:
                return result

        for fn in self._python_hooks[HookEvent.PRE_TOOL_USE]:
            result = await _call_python_hook(fn, tool_name, args, result=None)
            if result.blocked:
                return result

        return HookResult()

    async def post_tool_use(
        self, tool_name: str, args: dict, result: str, project: str = ""
    ) -> HookResult:
        """Run all PostToolUse hooks. Blocked is respected but informational."""
        payload = self._build_payload(tool_name, args, project=project, tool_result=result)

        for hook in self._shell_hooks[HookEvent.POST_TOOL_USE]:
            hook_result = await self._run_shell_hook(hook.command, payload, timeout=hook.timeout)
            if hook_result.blocked:
                return hook_result

        for fn in self._python_hooks[HookEvent.POST_TOOL_USE]:
            hook_result = await _call_python_hook(fn, tool_name, args, result=result)
            if hook_result.blocked:
                return hook_result

        return HookResult()

    async def on_stop(self, project: str = "") -> None:
        """Run all Stop hooks. No blocking semantics."""
        payload = self._build_payload("", {}, project=project)

        for hook in self._shell_hooks[HookEvent.STOP]:
            await self._run_shell_hook(hook.command, payload, timeout=hook.timeout)

        for fn in self._python_hooks[HookEvent.STOP]:
            await _call_python_hook(fn, "", {}, result=None)

    # ── Internal shell hook runner ────────────────────────────────────────────

    async def _run_shell_hook(
        self, command: str, payload: dict, timeout: int = 5
    ) -> HookResult:
        """Execute a single shell hook and interpret the exit code.

        Exit 0  → allow
        Exit 2  → block (stderr = reason)
        Other   → allow with warning logged
        Timeout → allow with warning logged
        """
        stdin_bytes = json.dumps(payload).encode()

        try:
            proc = await asyncio.create_subprocess_exec(
                *_split_command(command),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except (FileNotFoundError, PermissionError) as exc:
            logger.warning("Hook command not found or not executable: %s — %s", command, exc)
            return HookResult()

        try:
            _, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_bytes), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Hook timed out after %ds: %s — treating as allow", timeout, command)
            return HookResult()

        exit_code = proc.returncode

        if exit_code == 0:
            return HookResult()
        elif exit_code == 2:
            reason = stderr_bytes.decode(errors="replace").strip()
            logger.info("Hook blocked action: %s — %s", command, reason)
            return HookResult(blocked=True, reason=reason or "Blocked by hook")
        else:
            logger.warning(
                "Hook exited with unexpected code %d: %s — treating as allow", exit_code, command
            )
            return HookResult()

    # ── Integrity verification ────────────────────────────────────────────────

    def verify_integrity(self) -> dict[str, str]:
        """Compute SHA-256 of each registered shell hook script.

        Returns a mapping of ``script_path -> hex_digest``. Call at startup
        to establish a baseline, then call again later to detect tampering.
        """
        hashes: dict[str, str] = {}
        seen: set[str] = set()

        for hooks in self._shell_hooks.values():
            for hook in hooks:
                cmd_path = _split_command(hook.command)[0]
                if cmd_path in seen:
                    continue
                seen.add(cmd_path)
                p = Path(cmd_path)
                if p.exists():
                    digest = hashlib.sha256(p.read_bytes()).hexdigest()
                    hashes[str(p)] = digest
                else:
                    hashes[str(p)] = "FILE_NOT_FOUND"

        return hashes

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_payload(
        self,
        tool_name: str,
        args: dict,
        *,
        project: str = "",
        tool_result: str | None = None,
    ) -> dict:
        payload: dict = {
            "tool_name": tool_name,
            "tool_input": args,
            "session_id": self._session_id,
            "project": project,
        }
        if tool_result is not None:
            payload["tool_result"] = tool_result
        return payload


# ── Module-level helpers ─────────────────────────────────────────────────────


def _split_command(command: str) -> list[str]:
    """Split a command string into argv list, respecting quoted tokens."""
    import shlex
    return shlex.split(command)


async def _call_python_hook(
    fn: Callable, tool_name: str, args: dict, result: str | None
) -> HookResult:
    """Invoke a Python hook, supporting both sync and async callables."""
    try:
        ret = fn(tool_name, args, result)
        if asyncio.isfuture(ret) or asyncio.iscoroutine(ret):
            ret = await ret
        if isinstance(ret, HookResult):
            return ret
        logger.warning("Python hook %r returned non-HookResult value — treating as allow", fn)
        return HookResult()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Python hook %r raised %s — treating as allow", fn, exc)
        return HookResult()
