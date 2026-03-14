"""Nova Forge Orchestrator — V11-equivalent decision tree + pipeline execution.

Implements the full V11 orchestration flow:
  detect → configure → plan → orchestrate → monitor → close

The orchestrator integrates SessionManager, formation selection, DAAO routing,
autonomy management, and the plan/build/deploy pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from config import (
    ForgeProject, ModelConfig, get_model_config, init_forge_dir,
    resolve_model, DEFAULT_MODELS,
)
from forge_agent import ForgeAgent, AgentResult, BUILT_IN_TOOLS
from forge_guards import PathSandbox
from forge_hooks import HookSystem
from forge_session import SessionManager, SessionStatus, AutonomyState, FormationState
from forge_tasks import TaskStore, Task

logger = logging.getLogger(__name__)


def _infer_files_from_subject(subject: str, description: str = "") -> list[str]:
    """Infer file paths from task subject like 'Create app.py — Flask server'.

    Handles patterns:
      'Create models.py — ...'       -> ['models.py']
      'Create static/style.css'      -> ['static/style.css']
      'Create templates/index.html'  -> ['templates/index.html']
      'Build app.js — ...'           -> ['app.js']
    """
    import re
    text = subject + " " + description

    # Direct file references in subject
    files = []
    for m in re.finditer(r'(?:Create|Build|Write|Add|Implement)\s+([a-zA-Z0-9_/.-]+\.\w{1,6})', subject, re.IGNORECASE):
        candidate = m.group(1)
        if candidate and not candidate.startswith('.'):
            files.append(candidate)

    if files:
        return files

    # Fall back to file-like patterns with known extensions only
    _KNOWN_EXTS = {"py", "js", "ts", "jsx", "tsx", "html", "css", "json", "yaml", "yml", "md", "sql", "sh", "toml", "cfg", "txt", "csv", "xml"}
    for m in re.finditer(r'([a-zA-Z0-9_]+(?:/[a-zA-Z0-9_]+)*\.(\w{1,6}))', subject):
        candidate, ext = m.group(1), m.group(2).lower()
        if ext in _KNOWN_EXTS and not candidate.startswith('.') and candidate not in files:
            files.append(candidate)

    return files


def _recover_json(raw: str) -> list | None:
    """Attempt to recover a JSON array from malformed LLM output.

    Handles common LLM failures: truncated arrays, markdown fences,
    trailing commas, missing closing brackets.
    Returns parsed list on success, None on total failure.
    """
    import re

    # Strip markdown fences
    raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
    raw = raw.rstrip("`").strip()

    # Try as-is first
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    # Remove trailing commas before ] or } (common LLM mistake)
    cleaned = re.sub(r",\s*([}\]])", r"\1", raw)
    if cleaned != raw:
        try:
            data = json.loads(cleaned)
            if isinstance(data, list):
                return data
        except json.JSONDecodeError:
            pass

    # Extract the outermost [...] if embedded in other text
    start = raw.find("[")
    if start >= 0:
        # Find matching bracket from end
        for end in range(len(raw), start, -1):
            candidate = raw[start:end]
            try:
                data = json.loads(candidate)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                continue

    # Try closing common truncation patterns
    if start >= 0:
        partial = raw[start:]
        # Remove trailing comma before close
        for suffix in ["]", "}]", '"}]', '"} ]']:
            cleaned = re.sub(r",\s*$", "", partial)
            try:
                data = json.loads(cleaned + suffix)
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                continue

    return None


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class PlanResult:
    spec_path: Path | None = None
    tasks_path: Path | None = None
    task_count: int = 0
    error: str | None = None


@dataclass
class BuildResult:
    success: bool = False
    waves_completed: int = 0
    total_waves: int = 0
    gate_passed: bool = False
    errors: list[str] = field(default_factory=list)
    duration: float = 0.0


@dataclass
class StatusReport:
    project_name: str = ""
    total_tasks: int = 0
    completed: int = 0
    in_progress: int = 0
    pending: int = 0
    failed: int = 0
    blocked: int = 0
    percent: float = 0.0


# ── Orchestrator ─────────────────────────────────────────────────────────────

class ForgeOrchestrator:
    """Ties CLI commands to the pipeline engine."""

    def __init__(self, project_path: str | Path, model: str | None = None):
        self.project_path = Path(project_path).resolve()
        self.model = model or DEFAULT_MODELS["planning"]
        self.project = self._ensure_forge_dir()

    def _ensure_forge_dir(self) -> ForgeProject:
        """Initialize .forge/ if it doesn't exist."""
        return init_forge_dir(self.project_path)

    # ── Plan ─────────────────────────────────────────────────────────────────

    async def plan(
        self,
        goal: str,
        model: str | None = None,
        template: str | None = None,
        extra_context: str | None = None,
        build_model: str | None = None,
    ) -> PlanResult:
        """Phase 1+2: Generate spec.md and tasks.json from a user goal."""
        plan_model = resolve_model(model or self.model)
        mc = get_model_config(plan_model, max_tokens=4096)
        hooks = HookSystem(self.project.settings_file)
        sandbox = PathSandbox(self.project_path)

        # Phase 1: Planning — generate spec.md
        planning_agent = ForgeAgent(
            model_config=mc,
            project_root=self.project_path,
            hooks=hooks,
            sandbox=sandbox,
            tools=[t for t in BUILT_IN_TOOLS if t["name"] in {"write_file", "read_file", "glob_files"}],
            max_turns=10,
            agent_id="forge-planner",
        )

        template_hint = ""
        if template:
            template_hint = f"\nUse the '{template}' template as a starting point."

        interview_block = ""
        if extra_context:
            interview_block = (
                "\n\n## Interview Context (User-Confirmed Decisions)\n"
                "The user has already made these decisions during the interview. "
                "Incorporate ALL of them into the spec — do NOT contradict or ignore them.\n\n"
                f"{extra_context}"
            )

        plan_result = await planning_agent.run(
            prompt=(
                f"Create a project specification for: {goal}\n\n"
                f"Write a file called 'spec.md' in the project root with:\n"
                f"- Project name and description\n"
                f"- Tech stack (pick appropriate defaults)\n"
                f"- API endpoints or pages\n"
                f"- Data models\n"
                f"- Dependencies\n"
                f"- Deployment notes\n"
                f"{template_hint}"
                f"{interview_block}\n"
                f"Be concise. Write the spec.md file now."
            ),
            system=(
                "You are a project planner. Generate clear, actionable specifications. "
                "Write files using the write_file tool. Be concise — spec should be under 80 lines. "
                "If interview context is provided, honor ALL user-confirmed decisions "
                "(database, auth, visual aesthetic, features, etc.) exactly as specified."
            ),
        )

        spec_path = self.project_path / "spec.md"
        if plan_result.error and not spec_path.exists():
            return PlanResult(error=f"Planning failed: {plan_result.error}")

        # Phase 2: Decomposition — generate tasks.json
        decomp_agent = ForgeAgent(
            model_config=mc,
            project_root=self.project_path,
            hooks=hooks,
            sandbox=sandbox,
            tools=[t for t in BUILT_IN_TOOLS if t["name"] in {"write_file", "read_file"}],
            max_turns=20,
            agent_id="forge-decomposer",
        )

        # Inject size constraint when build model has small context
        size_hint = ""
        if build_model:
            from config import get_context_window as _gcw
            build_ctx_window = _gcw(resolve_model(build_model))
            if build_ctx_window <= 32_000:
                size_hint = (
                    "\nSMALL MODEL CONSTRAINT:\n"
                    "The build model can write ~80 lines per tool call.\n"
                    "For files expected to be >150 lines, split into TWO tasks:\n"
                    "- Task A: create the file with initial structure (write_file)\n"
                    "- Task B: extend the file with remaining features (append_file, blocked_by Task A)\n\n"
                )

        decomp_result = await decomp_agent.run(
            prompt=(
                "Read spec.md and create a tasks.json file with the implementation tasks.\n\n"
                "Format: JSON array of objects, each with:\n"
                '  {"subject": "...", "description": "...", "sprint": "sprint-01", '
                '"risk": "low|medium|high", "blocked_by": [], "files": ["path/file.py"]}\n\n'
                + size_hint +
                "DECOMPOSITION STRATEGY — FILE-CENTRIC TASKS:\n"
                "Create ONE task per output file. Each task builds that file COMPLETELY with all "
                "features that belong in it. Do NOT create feature-based tasks (bad: 'Add dark mode', "
                "'Add timer', 'Add chart') — create file-based tasks (good: 'Create app.js with timer, "
                "chart, dark mode, and task management').\n\n"
                "CRITICAL RULES:\n"
                "1. Each task MUST list the files it will create in the 'files' field.\n"
                "2. ONE TASK PER FILE — each file appears in exactly one task.\n"
                "3. Backend files first (blocked_by: []), frontend files depend on backend.\n"
                "4. The task description MUST include ALL features that go into that file.\n"
                "5. If the spec says 'NOT X' or 'do NOT use X', repeat that constraint in the task description.\n"
                "6. Target 3-8 tasks total. More tasks = more interface mismatches.\n\n"
                "Example for a Flask+JS app:\n"
                '  [{"subject": "Create models.py — database layer", '
                '"description": "SQLite helper functions: create_tables(), get_tasks(), create_task(title, order), '
                'update_task(id, title, order), delete_task(id), create_session(task_id, start, end), get_stats(). '
                'Use raw sqlite3 — NOT SQLAlchemy.",\n'
                '    "files": ["models.py"], "sprint": "sprint-01", "risk": "low", "blocked_by": []},\n'
                '   {"subject": "Create app.py — Flask server with all API routes", '
                '"description": "Flask app with routes: GET/POST /api/tasks, PUT/DELETE /api/tasks/<id>, '
                'POST /api/sessions, GET /api/stats. Import helpers from models.py. Serve templates/index.html.",\n'
                '    "files": ["app.py"], "sprint": "sprint-01", "risk": "low", "blocked_by": [0]},\n'
                '   {"subject": "Create templates/index.html — complete single-page UI", '
                '"description": "Full HTML page with task list, timer, chart container, dark mode toggle. '
                'Links to static/style.css and static/app.js.",\n'
                '    "files": ["templates/index.html"], "sprint": "sprint-01", "risk": "medium", "blocked_by": [1]},\n'
                '   {"subject": "Create static/app.js — all frontend JavaScript", '
                '"description": "Complete JS: task CRUD with drag-and-drop, Pomodoro timer with SVG progress, '
                'Chart.js bar chart, dark mode toggle, Web Audio API beep. All in one file.",\n'
                '    "files": ["static/app.js"], "sprint": "sprint-01", "risk": "medium", "blocked_by": [1]},\n'
                '   {"subject": "Create static/style.css — all styles", '
                '"description": "Complete CSS: glassmorphism, gradients, dark mode CSS variables, responsive layout.",\n'
                '    "files": ["static/style.css"], "sprint": "sprint-01", "risk": "low", "blocked_by": []}]\n\n'
                "Write the tasks.json file now.\n\n"
                "VALIDATION: After writing tasks.json, read it back with read_file to verify "
                "it parses correctly. If you see a SYNTAX ERROR, fix it immediately with write_file."
            ),
            system=(
                "You are a task decomposer. Read the spec and break it into FILE-CENTRIC tasks. "
                "Each task creates ONE complete file with ALL features that belong in it. "
                "Write the tasks.json file using write_file tool. "
                "IMPORTANT: One task per file. Never split a single file across multiple tasks. "
                "Target 3-8 tasks total. "
                "After writing, read back tasks.json to confirm it is valid JSON."
            ),
        )

        if decomp_result.error:
            logger.warning("Decomposer error: %s", decomp_result.error)

        tasks_path = self.project_path / "tasks.json"

        # Retry once if decomposer didn't produce tasks.json
        # (Nova Lite sometimes responds with text instead of calling write_file)
        if not tasks_path.exists():
            logger.info("tasks.json not created on first attempt — retrying decomposer")
            retry_agent = ForgeAgent(
                model_config=mc,
                project_root=self.project_path,
                tools=[t for t in BUILT_IN_TOOLS if t["name"] in {"write_file", "read_file"}],
                max_turns=10,
                agent_id="forge-decomposer-retry",
            )
            decomp_result = await retry_agent.run(
                prompt=(
                    "Read spec.md and create a tasks.json file.\n"
                    "You MUST call the write_file tool to create tasks.json.\n"
                    "Format: JSON array, each with subject, description, files, sprint, risk, blocked_by.\n"
                    "Write tasks.json now using write_file."
                ),
                system=(
                    "You are a task decomposer. You MUST use the write_file tool to create tasks.json. "
                    "Do not just describe the tasks — actually write the file."
                ),
            )
            if decomp_result.error:
                logger.warning("Decomposer retry error: %s", decomp_result.error)
        task_count = 0
        if tasks_path.exists():
            try:
                raw_text = tasks_path.read_text()
                try:
                    tasks_data = json.loads(raw_text)
                except json.JSONDecodeError as parse_err:
                    logger.warning("tasks.json parse error: %s — attempting recovery", parse_err)
                    tasks_data = _recover_json(raw_text)
                    if tasks_data is not None:
                        logger.info("JSON recovery succeeded: %d tasks extracted", len(tasks_data))
                        # Write back the fixed JSON
                        tasks_path.write_text(json.dumps(tasks_data, indent=2))
                    else:
                        raise parse_err
                if isinstance(tasks_data, list):
                    # Infer missing 'files' field from task subject
                    for t in tasks_data:
                        if not t.get("files"):
                            inferred = _infer_files_from_subject(t.get("subject", ""), t.get("description", ""))
                            if inferred:
                                t["files"] = inferred
                    # Write back with inferred files
                    tasks_path.write_text(json.dumps(tasks_data, indent=2))

                    # Merge tasks that share files to prevent parallel conflicts
                    tasks_data = self._dedup_tasks(tasks_data)
                    task_count = len(tasks_data)
                    # Load into TaskStore
                    store = TaskStore(self.project.tasks_file)
                    # Build subject-to-index map for resolving non-integer blocked_by
                    subject_index: dict[str, int] = {}
                    for idx, td in enumerate(tasks_data):
                        subj = td.get("subject", "").lower().strip()
                        if subj:
                            subject_index[subj] = idx

                    for i, t in enumerate(tasks_data):
                        blocked = t.get("blocked_by", [])
                        blocked_str = None
                        if blocked:
                            resolved: list[str] = []
                            for b in blocked:
                                if isinstance(b, int):
                                    # 0-based index → 1-based task ID
                                    task_id = b + 1
                                    if task_id <= i:  # only reference prior tasks
                                        resolved.append(str(task_id))
                                elif isinstance(b, str):
                                    # Try parsing as integer first
                                    try:
                                        idx = int(b)
                                        task_id = idx + 1
                                        if task_id <= i:
                                            resolved.append(str(task_id))
                                    except ValueError:
                                        # Try matching by subject name
                                        key = b.lower().strip()
                                        if key in subject_index and subject_index[key] < i:
                                            resolved.append(str(subject_index[key] + 1))
                                        # Silently skip unresolvable references
                            blocked_str = resolved or None
                        # Normalize LLM-generated metadata values
                        raw_sprint = str(t.get("sprint", "sprint-01"))
                        if not raw_sprint.startswith("sprint-"):
                            # Normalize "1" -> "sprint-01", "2" -> "sprint-02", etc.
                            raw_sprint = f"sprint-{raw_sprint.zfill(2)}"
                        raw_risk = str(t.get("risk", "low")).lower()
                        if raw_risk not in ("low", "medium", "high"):
                            raw_risk = "low"

                        store.create(
                            subject=t.get("subject", f"Task {i+1}"),
                            description=t.get("description", ""),
                            metadata={
                                "project": self.project.name,
                                "sprint": raw_sprint,
                                "risk": raw_risk,
                                "files": t.get("files", []),
                            },
                            blocked_by=blocked_str,
                        )
                    # Warn about file ownership conflicts
                    file_owners: dict[str, list[str]] = {}
                    for t in tasks_data:
                        for f in t.get("files", []):
                            file_owners.setdefault(f, []).append(t.get("subject", "?"))
                    conflicts = {f: owners for f, owners in file_owners.items() if len(owners) > 1}
                    if conflicts:
                        logger.warning(
                            "File ownership conflicts detected: %s",
                            {f: owners for f, owners in conflicts.items()},
                        )
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Failed to parse tasks.json: %s", e)

        return PlanResult(
            spec_path=spec_path if spec_path.exists() else None,
            tasks_path=tasks_path if tasks_path.exists() else None,
            task_count=task_count,
            error=decomp_result.error,
        )

    # ── Build ────────────────────────────────────────────────────────────────

    async def build(
        self,
        model: str | None = None,
        formation_name: str | None = None,
        max_concurrent: int = 6,
    ) -> BuildResult:
        """Phase 3+4: Execute waves and run gate review."""
        from forge_pipeline import WaveExecutor, GateReviewer
        from formations import FORMATIONS, select_formation

        store = TaskStore(self.project.tasks_file)
        tasks = store.list()
        if not tasks:
            return BuildResult(errors=["No tasks found. Run 'forge plan' first."])

        # Select formation
        if formation_name:
            from formations import get_formation
            formation = get_formation(formation_name)
        else:
            formation = select_formation("medium", "medium")  # Default

        hooks = HookSystem(self.project.settings_file)

        # Execute waves
        start = time.time()
        executor = WaveExecutor(
            project_root=self.project_path,
            formation=formation,
            store=store,
            hooks=hooks,
            max_concurrent=max_concurrent,
        )

        pipeline_result = await executor.execute_all_waves()
        duration = time.time() - start

        # Post-build integration check (matches _cmd_build)
        from forge_verify import scan_file_references
        issues = scan_file_references(self.project_path)
        if issues:
            logger.warning("Integration issues detected: %s", issues[:3])

        # Verify expected files exist on disk
        missing_files = []
        for task_entry in store.list():
            if task_entry.status == "completed":
                for fpath in (task_entry.metadata or {}).get("files", []):
                    full = self.project_path / fpath
                    if not full.exists():
                        missing_files.append(fpath)
        if missing_files:
            logger.warning("Missing files after build: %s", missing_files[:5])

        # Gate review
        gate_passed = False
        if pipeline_result.wave_results:
            review_model = resolve_model(model or DEFAULT_MODELS["review"])
            reviewer = GateReviewer(
                model=review_model,
                project_root=self.project_path,
            )
            gate_result = await reviewer.review(
                wave_results=pipeline_result.wave_results,
                gate_criteria=formation.gate_criteria,
            )
            pipeline_result.gate_result = gate_result
            gate_passed = gate_result.status == "PASS"

        return BuildResult(
            success=gate_passed and not pipeline_result.errors,
            waves_completed=pipeline_result.waves_completed,
            total_waves=pipeline_result.total_waves,
            gate_passed=gate_passed,
            errors=pipeline_result.errors,
            duration=duration,
        )

    # ── Status ───────────────────────────────────────────────────────────────

    def status(self) -> StatusReport:
        """Show current project status from TaskStore."""
        store = TaskStore(self.project.tasks_file)
        tasks = store.list()

        completed = sum(1 for t in tasks if t.status == "completed")
        in_progress = sum(1 for t in tasks if t.status == "in_progress")
        pending = sum(1 for t in tasks if t.status == "pending")
        failed = sum(1 for t in tasks if t.status == "failed")
        blocked = sum(1 for t in tasks if t.status == "blocked")
        total = len(tasks)

        return StatusReport(
            project_name=self.project.name,
            total_tasks=total,
            completed=completed,
            in_progress=in_progress,
            pending=pending,
            failed=failed,
            blocked=blocked,
            percent=(completed / total * 100) if total > 0 else 0,
        )

    # ── List ─────────────────────────────────────────────────────────────────

    def list_tasks(self, status: str | None = None) -> list[Task]:
        """List tasks, optionally filtered by status."""
        store = TaskStore(self.project.tasks_file)
        return store.list(status=status)

    # ── Handoff ──────────────────────────────────────────────────────────────

    def handoff(self) -> str:
        """Generate continuation context for session handoff."""
        sm = SessionManager(self.project_path)
        if sm.is_initialized():
            return sm.handoff()

        # Fallback: basic handoff from TaskStore
        report = self.status()
        store = TaskStore(self.project.tasks_file)
        tasks = store.list()

        in_progress = [t for t in tasks if t.status == "in_progress"]
        pending = [t for t in tasks if t.status == "pending"]

        lines = [
            f"# Nova Forge Handoff: {report.project_name}",
            f"",
            f"## Progress: {report.completed}/{report.total_tasks} ({report.percent:.0f}%)",
            f"",
        ]

        if in_progress:
            lines.append("## In Progress")
            for t in in_progress:
                lines.append(f"- [{t.id}] {t.subject}")
            lines.append("")

        if pending[:5]:
            lines.append("## Next Up")
            for t in pending[:5]:
                lines.append(f"- [{t.id}] {t.subject}")
            lines.append("")

        return "\n".join(lines)

    # ── V2: Session-aware methods ────────────────────────────────────────────

    def detect(self) -> dict[str, Any]:
        """Phase 1: Detect project state — V11 decision tree entry point.

        Returns a state dict describing the project's readiness:
        - initialized: bool (has .forge/)
        - compliant: bool (all gates pass)
        - compliance_gates: list of (name, passed, detail)
        - has_tasks: bool
        - task_summary: dict with counts
        - autonomy: dict with level + history
        - formation: dict or None
        - needs_setup: list of issues to fix
        """
        sm = SessionManager(self.project_path)
        result: dict[str, Any] = {
            "project": self.project.name,
            "project_path": str(self.project_path),
            "initialized": sm.is_initialized(),
            "compliant": False,
            "compliance_gates": [],
            "has_tasks": False,
            "task_summary": {},
            "autonomy": {},
            "formation": None,
            "needs_setup": [],
        }

        if not sm.is_initialized():
            result["needs_setup"].append("Project not initialized — run 'forge new' or 'forge init'")
            return result

        # Compliance check
        gates = sm.check_compliance()
        result["compliance_gates"] = [(g, p, d) for g, p, d in gates]
        result["compliant"] = all(p for _, p, _ in gates)

        failed_gates = [g for g, p, _ in gates if not p]
        if failed_gates:
            result["needs_setup"].append(f"Failed gates: {', '.join(failed_gates)}")

        # Task state
        task_state = sm.load_task_state()
        result["has_tasks"] = task_state.get("total", 0) > 0
        result["task_summary"] = {
            "total": task_state.get("total", 0),
            "completed": task_state.get("completed", 0),
            "in_progress": task_state.get("in_progress", 0),
            "pending": task_state.get("pending", 0),
            "failed": task_state.get("failed", 0),
            "blocked": task_state.get("blocked", 0),
        }

        # Autonomy
        autonomy = sm.load_autonomy()
        result["autonomy"] = autonomy.to_dict()

        # Formation
        formation = sm.load_formation()
        if formation:
            result["formation"] = formation.to_dict()

        return result

    def configure(self, auto_fix: bool = True) -> list[str]:
        """Phase 2: Configure project for V11 compliance.

        Returns list of fixes applied.
        """
        sm = SessionManager(self.project_path)

        if not sm.is_initialized():
            sm.init()

        if auto_fix:
            return sm.auto_fix()

        return []

    def select_formation(
        self, complexity: str = "medium", scope: str = "medium"
    ) -> dict[str, Any]:
        """Phase 3g: DAAO formation selection.

        Returns formation info dict with name, roles, and recommended agents.
        """
        from formations import select_formation as _select, get_formation

        formation = _select(complexity, scope)
        return {
            "name": formation.name,
            "description": formation.description,
            "roles": [
                {"name": r.name, "model": r.model, "tool_policy": r.tool_policy}
                for r in formation.roles
            ],
            "waves": formation.wave_order,
            "gate_criteria": formation.gate_criteria,
        }

    def session_status(self) -> SessionStatus:
        """Phase 5a: Full session status dashboard."""
        sm = SessionManager(self.project_path)
        return sm.status()

    def session_handoff(self) -> str:
        """Phase 6a: Generate rich handoff context."""
        sm = SessionManager(self.project_path)
        return sm.handoff()

    def save_formation(self, formation_name: str, teammates: dict[str, dict]) -> None:
        """Save a formation registry for active team work."""
        sm = SessionManager(self.project_path)
        state = FormationState(
            name=formation_name,
            project=self.project.name,
            teammates=teammates,
        )
        sm.save_formation(state)

    @staticmethod
    def _dedup_tasks(tasks_data: list[dict]) -> list[dict]:
        """Merge tasks that share file ownership to prevent parallel write conflicts.

        Uses union-find to group tasks by shared files, then merges each group
        into a single task with combined descriptions and the union of their files.
        """
        if not tasks_data:
            return tasks_data

        n = len(tasks_data)
        # Build file → task indices mapping
        file_to_tasks: dict[str, list[int]] = {}
        for i, t in enumerate(tasks_data):
            for f in t.get("files", []):
                file_to_tasks.setdefault(f, []).append(i)

        # Check if any merging is needed
        has_conflicts = any(len(indices) > 1 for indices in file_to_tasks.values())
        if not has_conflicts:
            return tasks_data

        # Union-find
        parent = list(range(n))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra  # keep lower index as root

        # Union tasks that share files
        for indices in file_to_tasks.values():
            for j in range(1, len(indices)):
                union(indices[0], indices[j])

        # Group tasks by root
        groups: dict[int, list[int]] = {}
        for i in range(n):
            root = find(i)
            groups.setdefault(root, []).append(i)

        # Merge each group
        merged: list[dict] = []
        for root, members in sorted(groups.items()):
            if len(members) == 1:
                merged.append(tasks_data[members[0]])
            else:
                # Combine into single task
                subjects = [tasks_data[m].get("subject", "") for m in members]
                descriptions = [tasks_data[m].get("description", "") for m in members]
                all_files: list[str] = []
                seen_files: set[str] = set()
                for m in members:
                    for f in tasks_data[m].get("files", []):
                        if f not in seen_files:
                            all_files.append(f)
                            seen_files.add(f)

                # Use highest risk from any member (normalize case)
                risk_order = {"low": 0, "medium": 1, "high": 2}
                max_risk = max(
                    (str(tasks_data[m].get("risk", "low")).lower() for m in members),
                    key=lambda r: risk_order.get(r, 0),
                )

                combined = {
                    "subject": " + ".join(s for s in subjects if s),
                    "description": "\n\n".join(d for d in descriptions if d),
                    "files": all_files,
                    "sprint": tasks_data[members[0]].get("sprint", "sprint-01"),
                    "risk": max_risk,
                    "blocked_by": [],  # dependencies rebuilt after merge
                }
                merged.append(combined)
                logger.info(
                    "Merged %d tasks into '%s' (shared files: %s)",
                    len(members), combined["subject"][:60], ", ".join(all_files),
                )

        return merged

    def check_compliance(self) -> list[tuple[str, bool, str]]:
        """Run compliance gates and return results."""
        sm = SessionManager(self.project_path)
        return sm.check_compliance()
