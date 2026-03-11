"""Nova Forge hook implementations — Python ports of V11's 12 shell hooks.

Each function follows the HookSystem signature::

    def hook_fn(tool_name: str, args: dict, result: str | None) -> HookResult

Pre-tool hooks return HookResult(blocked=True) to block an operation.
Post-tool hooks are informational (blocked is advisory only).

Registration is handled by :func:`wire_all_hooks`.

Usage::

    from forge_hooks import HookSystem, HookEvent
    from forge_hooks_impl import wire_all_hooks

    hs = HookSystem()
    wire_all_hooks(hs, project_root=Path("."))
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from filelock import FileLock

from config import (
    ForgeProject,
    detect_project,
    is_metadata_file,
    FORGE_DIR_NAME,
    NON_PROJECTS,
)
from forge_hooks import HookResult, HookEvent, HookSystem
from forge_guards import RiskClassifier, RiskLevel, AutonomyManager, SyntaxVerifier

logger = logging.getLogger(__name__)

# ── Tool name normalization ──────────────────────────────────────────────────
# ForgeAgent uses lowercase names (write_file, edit_file, bash, read_file, ...)
# V11 hooks use capitalized names (Write, Edit, Bash, Read, ...).
# Normalize ForgeAgent names to V11 names so hooks match correctly.
_TOOL_NAME_MAP: dict[str, str] = {
    "write_file": "Write",
    "append_file": "Write",
    "edit_file": "Edit",
    "read_file": "Read",
    "bash": "Bash",
    "glob_files": "Glob",
    "grep": "Grep",
}


def _normalize_tool_name(tool_name: str) -> str:
    """Map ForgeAgent tool names to V11 hook names."""
    return _TOOL_NAME_MAP.get(tool_name, tool_name)


# ── Shared state for hooks within a session ──────────────────────────────────

@dataclass
class _HookState:
    """Mutable state shared across hooks during a session."""
    active_project: Optional[str] = None
    project_root: Optional[Path] = None
    session_writes: int = 0
    files_modified: set = field(default_factory=set)
    risk_classifier: RiskClassifier = field(default_factory=RiskClassifier)
    syntax_verifier: Optional[SyntaxVerifier] = None
    autonomy_manager: Optional[AutonomyManager] = None
    task_state: Optional[dict] = None
    _task_state_loaded: bool = False


# ── 1. detect-project (PreToolUse, any tool) ────────────────────────────────

def _detect_project_hook(state: _HookState) -> callable:
    """Create detect-project hook bound to shared state."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        # Extract file path from common tool input patterns
        file_path = (
            args.get("file_path")
            or args.get("path")
            or args.get("command", "")  # Bash commands may reference files
        )
        if not file_path or not isinstance(file_path, str):
            return HookResult()

        # Skip metadata files
        if is_metadata_file(file_path):
            return HookResult()

        project = detect_project(file_path)
        if project and project != state.active_project:
            state.active_project = project
            logger.debug("detect-project: active project → %s", project)

        return HookResult()

    return hook


# ── 2. guard-write-gates (PreToolUse, Write/Edit) ───────────────────────────

def _guard_write_gates_hook(state: _HookState) -> callable:
    """Enforce task-state requirement before writes."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if tool_name not in ("Write", "Edit"):
            return HookResult()

        file_path = args.get("file_path", "")

        # Detect project from file path
        project = detect_project(file_path) if file_path else state.active_project
        if project:
            state.active_project = project

        if not state.active_project:
            return HookResult()

        # Part A: File ownership check (if formation registry exists)
        ownership_result = _check_file_ownership(state, file_path, args)
        if ownership_result.blocked:
            return ownership_result

        # Part B: Task state enforcement
        # Only block writes when there are pending tasks waiting to be started
        # (i.e., a build should be running). Allow writes when:
        #   - All tasks are complete (post-build chat editing)
        #   - Only failed tasks remain (user manually fixing)
        #   - No tasks exist yet
        task_state = _load_task_state(state)
        if task_state:
            total = task_state.get("total", 0)
            pending = task_state.get("pending", 0)
            in_progress = task_state.get("in_progress", 0)

            if total >= 2 and in_progress == 0 and pending > 0:
                return HookResult(
                    blocked=True,
                    reason=(
                        f"No tasks in_progress for project '{state.active_project}'. "
                        "Start a task with TaskUpdate(status='in_progress') before writing."
                    ),
                )

        # Part C: Track writes for plan-mode advisory
        state.session_writes += 1
        state.files_modified.add(file_path)

        if len(state.files_modified) >= 6 and state.session_writes % 10 == 0:
            logger.info(
                "guard-write-gates advisory: %d files modified in session — "
                "consider entering plan mode for large changes",
                len(state.files_modified),
            )

        return HookResult()

    return hook


# ── 3. guard-enforcement (PreToolUse, Write/Edit/Bash) ──────────────────────

def _guard_enforcement_hook(state: _HookState) -> callable:
    """Block high-risk operations and enforce tool policies."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if tool_name not in ("Write", "Edit", "Bash"):
            return HookResult()

        command = args.get("command", "")
        file_path = args.get("file_path", "")

        # Part A: High-risk blocking (Bash only)
        if tool_name == "Bash" and command:
            risk = state.risk_classifier.classify("Bash", command=command)
            if risk == RiskLevel.HIGH:
                # Check autonomy — A4 may auto-approve some high-risk
                if state.autonomy_manager:
                    auto_result = state.autonomy_manager.check(
                        tool_name="Bash", risk_level=risk,
                        file_path="", command=command,
                    )
                    if auto_result.allowed:
                        return HookResult()

                return HookResult(
                    blocked=True,
                    reason=f"High-risk command blocked: {command[:100]}",
                )

        # Part B: Tool policy cascade (formation teams)
        policy_result = _check_tool_policy(state, tool_name)
        if policy_result.blocked:
            return policy_result

        return HookResult()

    return hook


# ── 4. guard-effort (PreToolUse, agent launches — advisory) ─────────────────

# Effort keyword patterns
_EFFORT_KEYWORDS = {
    "max": re.compile(
        r"\b(?:design|architect|security|threat|novel|complex|scalab)", re.I
    ),
    "high": re.compile(
        r"\b(?:implement|fix|refactor|add|review|build|write|create|modify|debug)\b", re.I
    ),
    "medium": re.compile(
        r"\b(?:test|coordinate|validate|optimize|integrate)\b", re.I
    ),
    "low": re.compile(
        r"\b(?:format|lint|scan|check|list|count|find|read)\b", re.I
    ),
}

# Complexity × scope → effort matrix
_EFFORT_MATRIX = {
    ("novel", "large"): "max",
    ("novel", "medium"): "high",
    ("novel", "small"): "high",
    ("complex", "large"): "max",
    ("complex", "medium"): "high",
    ("complex", "small"): "medium",
    ("medium", "large"): "high",
    ("medium", "medium"): "medium",
    ("medium", "small"): "low",
    ("routine", "large"): "high",
    ("routine", "medium"): "medium",
    ("routine", "small"): "low",
}


def _guard_effort_hook(state: _HookState) -> callable:
    """Advisory effort-level guidance for agent tasks."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        # Only advise on agent/task launches
        prompt = args.get("prompt", args.get("description", ""))
        if not prompt:
            return HookResult()

        # Keyword analysis
        keyword_effort = "medium"  # default
        for level in ("max", "high", "medium", "low"):
            if _EFFORT_KEYWORDS[level].search(prompt):
                keyword_effort = level
                break

        # Metadata-based routing
        metadata = args.get("metadata", {})
        complexity = metadata.get("complexity", "")
        scope = metadata.get("scope", "")
        matrix_effort = _EFFORT_MATRIX.get((complexity, scope))

        recommended = matrix_effort or keyword_effort

        # Contradiction detection
        risk = metadata.get("risk", "")
        if complexity == "routine" and risk in ("medium", "high"):
            logger.info(
                "guard-effort advisory: routine task has %s risk — verify classification",
                risk,
            )
        if complexity == "novel" and risk == "low":
            logger.info(
                "guard-effort advisory: novel task has low risk — verify classification"
            )

        logger.debug(
            "guard-effort: recommended effort=%s (keyword=%s, matrix=%s)",
            recommended, keyword_effort, matrix_effort,
        )

        return HookResult()

    return hook


# ── 5. enforce-test-coverage (PreToolUse, Bash deploy commands) ─────────────

_DEPLOY_PATTERNS = re.compile(
    r"docker[\s-]+compose\s+up|"
    r"kubectl\s+apply|"
    r"./deploy\.sh|"
    r"forge\s+deploy|"
    r"docker\s+build\s+.*-t|"
    r"npm\s+run\s+deploy",
    re.IGNORECASE,
)

# Test framework detection
_TEST_FRAMEWORKS = {
    "pytest": {
        "markers": ["pytest.ini", "pyproject.toml", "setup.cfg"],
        "command": "python3 -m pytest --tb=no -q 2>&1 | tail -1",
    },
    "jest": {
        "markers": ["jest.config.js", "jest.config.ts"],
        "command": "npx jest --ci --coverage 2>&1 | tail -5",
    },
    "go": {
        "markers": ["go.mod"],
        "command": "go test -cover ./... 2>&1 | tail -1",
    },
}


def _enforce_test_coverage_hook(state: _HookState) -> callable:
    """Block deploys if test coverage is below threshold."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if tool_name != "Bash":
            return HookResult()

        command = args.get("command", "")
        if not _DEPLOY_PATTERNS.search(command):
            return HookResult()

        # Detect test framework
        project_root = state.project_root or Path.cwd()
        framework = _detect_test_framework(project_root)
        if not framework:
            logger.debug("enforce-test-coverage: no test framework detected — skipping")
            return HookResult()

        # Run test coverage check
        threshold = int(os.environ.get("TEST_COVERAGE_THRESHOLD", "80"))
        try:
            result_proc = subprocess.run(
                ["bash", "-c", _TEST_FRAMEWORKS[framework]["command"]],
                cwd=str(project_root),
                capture_output=True, text=True, timeout=120,
            )
            output = result_proc.stdout + result_proc.stderr

            # Extract coverage percentage
            coverage_match = re.search(r"(\d+)%", output)
            if coverage_match:
                coverage = int(coverage_match.group(1))
                if coverage < threshold:
                    return HookResult(
                        blocked=True,
                        reason=(
                            f"Test coverage {coverage}% is below threshold {threshold}%. "
                            f"Write more tests before deploying."
                        ),
                    )
                logger.debug("enforce-test-coverage: %d%% >= %d%% — OK", coverage, threshold)
        except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
            logger.warning("enforce-test-coverage: test runner failed — %s", exc)

        return HookResult()

    return hook


def _detect_test_framework(project_root: Path) -> Optional[str]:
    """Auto-detect test framework from config files."""
    for name, cfg in _TEST_FRAMEWORKS.items():
        for marker in cfg["markers"]:
            if (project_root / marker).exists():
                return name
    return None


# ── 6. verify-syntax (PostToolUse, Write/Edit) ──────────────────────────────

# Extension → syntax check command template ({file} is replaced)
_SYNTAX_CHECKERS: dict[str, list[str]] = {
    ".py": ["python3", "-m", "py_compile", "{file}"],
    ".js": ["node", "--check", "{file}"],
    ".jsx": ["node", "--check", "{file}"],
    ".json": ["python3", "-c", "import json; json.load(open('{file}'))"],
    ".yml": ["python3", "-c", "import yaml; yaml.safe_load(open('{file}'))"],
    ".yaml": ["python3", "-c", "import yaml; yaml.safe_load(open('{file}'))"],
    ".sh": ["bash", "-n", "{file}"],
    ".bash": ["bash", "-n", "{file}"],
}


def _verify_syntax_hook(state: _HookState) -> callable:
    """Advisory syntax check after file writes."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if tool_name not in ("Write", "Edit"):
            return HookResult()

        file_path = args.get("file_path", "")
        if not file_path or not Path(file_path).exists():
            return HookResult()

        if is_metadata_file(file_path):
            return HookResult()

        ext = Path(file_path).suffix.lower()
        checker_template = _SYNTAX_CHECKERS.get(ext)
        if not checker_template:
            return HookResult()

        checker = [arg.replace("{file}", str(file_path)) for arg in checker_template]

        try:
            proc = subprocess.run(
                checker, capture_output=True, text=True, timeout=10,
            )
            if proc.returncode != 0:
                error_msg = (proc.stderr or proc.stdout).strip()[:200]
                logger.warning(
                    "verify-syntax: %s has syntax error: %s",
                    Path(file_path).name, error_msg,
                )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass  # Tool not available — skip silently

        return HookResult()

    return hook


# ── 7. track-autonomy (PostToolUse, Write/Edit/Bash) ────────────────────────

def _track_autonomy_hook(state: _HookState) -> callable:
    """Update autonomy trust score and append audit trail."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if tool_name not in ("Write", "Edit", "Bash"):
            return HookResult()

        command = args.get("command", "")
        file_path = args.get("file_path", "")

        # Skip low-risk operations
        risk = state.risk_classifier.classify(tool_name, command=command, file_path=file_path)
        if risk == RiskLevel.LOW:
            return HookResult()

        if not state.active_project or not state.project_root:
            return HookResult()

        # Determine success/failure from result — only count severe failures,
        # not normal tool errors like "old_string not found" or "File not found"
        is_error = False
        if result and isinstance(result, str):
            is_error = bool(re.search(
                r"(?:SANDBOX VIOLATION|Traceback|BLOCKED)", result
            ))

        # Update autonomy manager
        if state.autonomy_manager:
            try:
                state.autonomy_manager.track(
                    tool_name=tool_name,
                    risk_level=risk,
                    outcome="error" if is_error else "success",
                )
            except Exception as exc:
                logger.debug("autonomy track failed: %s", exc)

        # Append audit entry
        _append_audit_entry(state, tool_name, args, risk, is_error)

        return HookResult()

    return hook


def _append_audit_entry(
    state: _HookState, tool_name: str, args: dict,
    risk: RiskLevel, is_error: bool,
) -> None:
    """Append JSONL audit entry to .forge/audit/audit.jsonl."""
    if not state.project_root:
        return

    audit_dir = state.project_root / FORGE_DIR_NAME / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_file = audit_dir / "audit.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": state.active_project or "",
        "tool": tool_name,
        "file": args.get("file_path", args.get("command", ""))[:200],
        "risk": str(risk),
        "outcome": "error" if is_error else "success",
        "autonomy_level": (
            state.autonomy_manager._state.get("level", 0)
            if state.autonomy_manager else 0
        ),
    }

    lock = FileLock(str(audit_file) + ".lock", timeout=2)
    try:
        with lock:
            with open(audit_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
    except Exception as exc:
        logger.warning("track-autonomy: failed to write audit entry — %s", exc)


# ── 8. sync-tasks (PostToolUse, TaskCreate/TaskUpdate/TaskList) ─────────────

def _sync_tasks_hook(state: _HookState) -> callable:
    """Sync task state to .forge/state/ on task operations."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        # Only act on task-related tools
        if tool_name not in ("TaskCreate", "TaskUpdate", "TaskList", "TaskGet"):
            return HookResult()

        if not state.active_project or not state.project_root:
            # Try to detect from metadata
            metadata = args.get("metadata", {})
            if isinstance(metadata, dict):
                project = metadata.get("project", "")
                if project:
                    state.active_project = project

        if not state.active_project:
            return HookResult()

        # Parse task info from result
        task_state = _load_task_state(state)
        if task_state is None:
            task_state = {
                "project": state.active_project,
                "total": 0, "completed": 0, "pending": 0,
                "in_progress": 0, "blocked": 0,
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

        # Update counts based on tool type
        if tool_name == "TaskCreate":
            task_state["total"] = task_state.get("total", 0) + 1
            task_state["pending"] = task_state.get("pending", 0) + 1

        elif tool_name == "TaskUpdate":
            new_status = args.get("status", "")
            if new_status == "in_progress":
                task_state["pending"] = max(0, task_state.get("pending", 0) - 1)
                task_state["in_progress"] = task_state.get("in_progress", 0) + 1
            elif new_status == "completed":
                task_state["in_progress"] = max(0, task_state.get("in_progress", 0) - 1)
                task_state["completed"] = task_state.get("completed", 0) + 1
            elif new_status == "blocked":
                task_state["in_progress"] = max(0, task_state.get("in_progress", 0) - 1)
                task_state["blocked"] = task_state.get("blocked", 0) + 1

        task_state["last_updated"] = datetime.now(timezone.utc).isoformat()

        # Save task state
        _save_task_state(state, task_state)
        state.task_state = task_state
        state._task_state_loaded = True

        return HookResult()

    return hook


# ── 9. guard-teammate-timeout (PostToolUse, TaskList) ────────────────────────

# Configurable thresholds (minutes)
_STALL_WARNING = int(os.environ.get("STALL_WARNING_MINUTES", "15"))
_STALL_TIMEOUT = int(os.environ.get("STALL_TIMEOUT_MINUTES", "30"))
_STALL_CRITICAL = int(os.environ.get("STALL_CRITICAL_MINUTES", "45"))


def _guard_teammate_timeout_hook(state: _HookState) -> callable:
    """Detect stalled teammates via task state timestamps."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if tool_name != "TaskList":
            return HookResult()

        task_state = _load_task_state(state)
        if not task_state:
            return HookResult()

        in_progress = task_state.get("in_progress", 0)
        if in_progress == 0:
            return HookResult()

        # Check staleness
        last_updated = task_state.get("last_updated", "")
        if not last_updated:
            return HookResult()

        try:
            last_dt = datetime.fromisoformat(last_updated)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
        except (ValueError, TypeError):
            return HookResult()

        if elapsed >= _STALL_CRITICAL:
            logger.warning(
                "guard-teammate-timeout CRITICAL: %d task(s) in_progress, "
                "no update for %.0f minutes — consider restarting stalled agents",
                in_progress, elapsed,
            )
        elif elapsed >= _STALL_TIMEOUT:
            logger.warning(
                "guard-teammate-timeout ALERT: %d task(s) in_progress, "
                "no update for %.0f minutes",
                in_progress, elapsed,
            )
        elif elapsed >= _STALL_WARNING:
            logger.info(
                "guard-teammate-timeout WARNING: %d task(s) in_progress, "
                "no update for %.0f minutes",
                in_progress, elapsed,
            )

        return HookResult()

    return hook


# ── 10. track-agents (PostToolUse, agent launches) ──────────────────────────

_AGENT_CATEGORIES = {
    "database": re.compile(r"\b(?:database|schema|migration|sql)\b", re.I),
    "backend": re.compile(r"\b(?:backend|api|endpoint|server)\b", re.I),
    "frontend": re.compile(r"\b(?:frontend|react|component|ui)\b", re.I),
    "testing": re.compile(r"\b(?:test|coverage|unit|integration)\b", re.I),
    "deployment": re.compile(r"\b(?:deploy|production|docker)\b", re.I),
    "security": re.compile(r"\b(?:security|auth|owasp)\b", re.I),
    "debugging": re.compile(r"\b(?:debug|error|fix)\b", re.I),
}


def _track_agents_hook(state: _HookState) -> callable:
    """Track agent usage metrics and detect escalation patterns."""

    _consecutive_failures: list[int] = [0]  # mutable counter in closure

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        description = args.get("description", "")
        prompt = args.get("prompt", "")
        subagent_type = args.get("subagent_type", "")

        if not description and not prompt and not subagent_type:
            return HookResult()

        # Determine success
        success = "unknown"
        if result and isinstance(result, str):
            if re.search(r"(?:error|failed|exception|traceback)", result, re.I):
                success = "false"
                _consecutive_failures[0] += 1
            elif re.search(r"(?:success|complete|done|finished)", result, re.I):
                success = "true"
                _consecutive_failures[0] = 0
            else:
                _consecutive_failures[0] = 0

        # Detect category
        text = f"{description} {prompt}"
        category = "general"
        for cat, pattern in _AGENT_CATEGORIES.items():
            if pattern.search(text):
                category = cat
                break

        # Log metric
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": subagent_type or "unknown",
            "description": description[:200],
            "success": success,
            "category": category,
            "project": state.active_project or "",
        }

        if state.project_root:
            metrics_file = state.project_root / FORGE_DIR_NAME / "audit" / "agent-usage.jsonl"
            metrics_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(metrics_file, "a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass

        # Escalation advisory after consecutive failures
        if _consecutive_failures[0] >= 2:
            logger.warning(
                "track-agents ESCALATION: %d consecutive agent failures — "
                "try higher effort level or different model",
                _consecutive_failures[0],
            )

        return HookResult()

    return hook


# ── 11. fix-team-model (Pre+Post, TeamCreate/Agent — advisory) ──────────────

def _fix_team_model_hook(state: _HookState) -> callable:
    """Advisory check for team model configuration issues."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        # This hook is primarily relevant in Claude Code's Agent Teams
        # In Nova Forge, model selection is explicit per agent launch
        # We keep it as an advisory: warn if formation registry agent
        # doesn't match the spawned subagent_type
        subagent_type = args.get("subagent_type", "")
        if not subagent_type:
            return HookResult()

        if not state.project_root:
            return HookResult()

        formation_file = state.project_root / FORGE_DIR_NAME / "state" / "formation-registry.json"
        if not formation_file.exists():
            return HookResult()

        try:
            registry = json.loads(formation_file.read_text())
            teammates = registry.get("teammates", {})
            for role, info in teammates.items():
                expected_agent = info.get("agent", "")
                if expected_agent and expected_agent != subagent_type:
                    # Check if this spawn is for a role that expects a different agent
                    logger.info(
                        "fix-team-model advisory: role '%s' expects agent '%s' "
                        "but spawning '%s'",
                        role, expected_agent, subagent_type,
                    )
        except (json.JSONDecodeError, OSError):
            pass

        return HookResult()

    return hook


# ── 12. session-end (Stop) ──────────────────────────────────────────────────

def _session_end_hook(state: _HookState) -> callable:
    """Save session summary on stop."""

    def hook(tool_name: str, args: dict, result: str | None) -> HookResult:
        if not state.active_project or not state.project_root:
            return HookResult()

        task_state = _load_task_state(state)
        if not task_state:
            return HookResult()

        # Update last_session_end timestamp
        task_state["last_session_end"] = datetime.now(timezone.utc).isoformat()
        _save_task_state(state, task_state)

        # Append session log
        session_log = state.project_root / FORGE_DIR_NAME / "audit" / "session-log.jsonl"
        session_log.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "project": state.active_project,
            "total": task_state.get("total", 0),
            "completed": task_state.get("completed", 0),
            "pending": task_state.get("pending", 0),
            "in_progress": task_state.get("in_progress", 0),
            "session_writes": state.session_writes,
            "files_modified": len(state.files_modified),
        }

        try:
            with open(session_log, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as exc:
            logger.warning("session-end: failed to write session log — %s", exc)

        logger.info(
            "session-end: project=%s completed=%d/%d writes=%d",
            state.active_project,
            task_state.get("completed", 0),
            task_state.get("total", 0),
            state.session_writes,
        )

        return HookResult()

    return hook


# ── Helper functions ─────────────────────────────────────────────────────────

def _load_task_state(state: _HookState) -> Optional[dict]:
    """Load task state from .forge/state/task-state.json."""
    if state._task_state_loaded and state.task_state is not None:
        return state.task_state

    if not state.project_root:
        return None

    state_file = state.project_root / FORGE_DIR_NAME / "state" / "task-state.json"
    if not state_file.exists():
        return None

    try:
        lock = FileLock(str(state_file) + ".lock", timeout=2)
        with lock:
            data = json.loads(state_file.read_text())
        state.task_state = data
        state._task_state_loaded = True
        return data
    except (json.JSONDecodeError, OSError, Exception) as exc:
        logger.warning("Failed to load task state: %s", exc)
        return None


def _save_task_state(state: _HookState, task_state: dict) -> None:
    """Save task state to .forge/state/task-state.json with file locking."""
    if not state.project_root:
        return

    state_dir = state.project_root / FORGE_DIR_NAME / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "task-state.json"

    lock = FileLock(str(state_file) + ".lock", timeout=2)
    try:
        with lock:
            state_file.write_text(json.dumps(task_state, indent=2) + "\n")
    except Exception as exc:
        logger.warning("Failed to save task state: %s", exc)


def _check_file_ownership(state: _HookState, file_path: str, args: dict) -> HookResult:
    """Check file ownership against formation registry."""
    if not state.project_root or not file_path:
        return HookResult()

    registry_file = state.project_root / FORGE_DIR_NAME / "state" / "formation-registry.json"
    if not registry_file.exists():
        return HookResult()

    try:
        registry = json.loads(registry_file.read_text())
    except (json.JSONDecodeError, OSError):
        return HookResult()

    agent_id = args.get("agent_id", os.environ.get("FORGE_AGENT_ID", ""))
    teammates = registry.get("teammates", {})

    for role, info in teammates.items():
        if info.get("agent_id") == agent_id:
            # This is our agent — no conflict
            continue

        ownership = info.get("ownership", {})
        owned_dirs = ownership.get("directories", [])
        owned_files = ownership.get("files", [])
        owned_patterns = ownership.get("patterns", [])

        # Check if file is owned by another teammate
        is_owned = False
        for d in owned_dirs:
            if file_path.startswith(d) or str(Path(file_path)).startswith(str(Path(d))):
                is_owned = True
                break
        if not is_owned:
            for f in owned_files:
                if file_path == f or Path(file_path).name == Path(f).name:
                    is_owned = True
                    break
        if not is_owned:
            for p in owned_patterns:
                if fnmatch.fnmatch(file_path, p) or fnmatch.fnmatch(Path(file_path).name, p):
                    is_owned = True
                    break

        if is_owned and info.get("agent_id") and info["agent_id"] != agent_id:
            return HookResult(
                blocked=True,
                reason=(
                    f"File '{Path(file_path).name}' is owned by teammate "
                    f"'{role}' (agent: {info.get('agent', 'unknown')}). "
                    f"Do not modify files outside your ownership."
                ),
            )

    return HookResult()


def _check_tool_policy(state: _HookState, tool_name: str) -> HookResult:
    """Check tool against formation tool policy cascade."""
    if not state.project_root:
        return HookResult()

    registry_file = state.project_root / FORGE_DIR_NAME / "state" / "formation-registry.json"
    if not registry_file.exists():
        return HookResult()

    agent_id = os.environ.get("FORGE_AGENT_ID", "")
    if not agent_id:
        return HookResult()

    try:
        registry = json.loads(registry_file.read_text())
    except (json.JSONDecodeError, OSError):
        return HookResult()

    # Find our role
    teammates = registry.get("teammates", {})
    our_role = None
    for role, info in teammates.items():
        if info.get("agent_id") == agent_id:
            our_role = role
            break

    if not our_role:
        return HookResult()  # Not in formation — no policy

    # Resolve effective profile
    # Layer 1: formation defaults
    policies = registry.get("tool_policies", {})
    defaults = policies.get("defaults", {})
    profile = defaults.get("profile", "full")

    # Layer 2: per-role override
    per_role = policies.get("per_role", {})
    if our_role in per_role:
        profile = per_role[our_role].get("profile", profile)

    # Layer 3: teammate level
    teammate_info = teammates.get(our_role, {})
    teammate_policies = teammate_info.get("tool_policies", {})
    if teammate_policies:
        profile = teammate_policies.get("profile", profile)

    # Expand profile to allowed tools
    allowed = _expand_tool_profile(profile)

    # Check deny lists (deny-wins)
    deny_list: set[str] = set()
    for layer in [defaults, per_role.get(our_role, {}), teammate_policies]:
        deny_list.update(layer.get("deny", []))

    if tool_name in deny_list:
        return HookResult(
            blocked=True,
            reason=f"Tool '{tool_name}' denied by policy for role '{our_role}'",
        )

    if allowed and tool_name not in allowed:
        return HookResult(
            blocked=True,
            reason=f"Tool '{tool_name}' not in allowed set for profile '{profile}' (role: {our_role})",
        )

    return HookResult()


# Tool profile expansion (matches V11 common.sh)
_TOOL_PROFILES: dict[str, set[str]] = {
    "full": {
        "Read", "Write", "Edit", "Grep", "Glob", "Bash",
        "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
        "WebSearch", "WebFetch", "Agent",
    },
    "coding": {
        "Read", "Write", "Edit", "Grep", "Glob", "Bash",
        "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    },
    "testing": {
        "Read", "Grep", "Glob", "Bash",
        "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    },
    "readonly": {
        "Read", "Grep", "Glob",
        "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    },
    "minimal": {
        "TaskCreate", "TaskUpdate", "TaskList", "TaskGet",
    },
}


def _expand_tool_profile(profile: str) -> set[str]:
    """Expand a profile name to a set of allowed tool names."""
    return _TOOL_PROFILES.get(profile, _TOOL_PROFILES["full"])


# ── Public API: wire all hooks ───────────────────────────────────────────────

def wire_all_hooks(
    hook_system: HookSystem,
    project_root: Path | None = None,
    autonomy_manager: AutonomyManager | None = None,
) -> _HookState:
    """Register all 12 V11 hook implementations into a HookSystem.

    Args:
        hook_system: The HookSystem to register hooks into.
        project_root: Project root directory (for task state, audit, etc.).
        autonomy_manager: Optional AutonomyManager for trust-level checks.

    Returns:
        The shared _HookState (useful for testing/inspection).
    """
    state = _HookState(
        project_root=project_root,
        autonomy_manager=autonomy_manager,
    )

    if project_root:
        state.active_project = project_root.name

    def _wrap(fn):
        """Wrap a hook function to normalize ForgeAgent tool names to V11 names."""
        def wrapper(tool_name: str, args: dict, result: str | None = None) -> HookResult:
            return fn(_normalize_tool_name(tool_name), args, result)
        return wrapper

    # Pre-tool hooks (order matters — detect first, then gates, then enforcement)
    hook_system.register(HookEvent.PRE_TOOL_USE, _wrap(_detect_project_hook(state)))
    hook_system.register(HookEvent.PRE_TOOL_USE, _wrap(_guard_write_gates_hook(state)))
    hook_system.register(HookEvent.PRE_TOOL_USE, _wrap(_guard_enforcement_hook(state)))
    hook_system.register(HookEvent.PRE_TOOL_USE, _wrap(_guard_effort_hook(state)))
    hook_system.register(HookEvent.PRE_TOOL_USE, _wrap(_enforce_test_coverage_hook(state)))

    # Post-tool hooks
    hook_system.register(HookEvent.POST_TOOL_USE, _wrap(_verify_syntax_hook(state)))
    hook_system.register(HookEvent.POST_TOOL_USE, _wrap(_track_autonomy_hook(state)))
    hook_system.register(HookEvent.POST_TOOL_USE, _wrap(_sync_tasks_hook(state)))
    hook_system.register(HookEvent.POST_TOOL_USE, _wrap(_guard_teammate_timeout_hook(state)))
    hook_system.register(HookEvent.POST_TOOL_USE, _wrap(_track_agents_hook(state)))
    hook_system.register(HookEvent.POST_TOOL_USE, _wrap(_fix_team_model_hook(state)))

    # Stop hooks
    hook_system.register(HookEvent.STOP, _session_end_hook(state))

    logger.info("wire_all_hooks: 12 hooks registered (5 pre, 6 post, 1 stop)")
    return state
