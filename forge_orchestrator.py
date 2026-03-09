"""Nova Forge Orchestrator — wires CLI commands to pipeline execution.

Handles the plan → build → deploy → status flow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import (
    ForgeProject, ModelConfig, get_model_config, init_forge_dir,
    resolve_model, DEFAULT_MODELS,
)
from forge_agent import ForgeAgent, AgentResult, BUILT_IN_TOOLS
from forge_guards import PathSandbox
from forge_hooks import HookSystem
from forge_tasks import TaskStore, Task

logger = logging.getLogger(__name__)


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
                f"{template_hint}\n"
                f"Be concise. Write the spec.md file now."
            ),
            system=(
                "You are a project planner. Generate clear, actionable specifications. "
                "Write files using the write_file tool. Be concise — spec should be under 80 lines."
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
            max_turns=10,
            agent_id="forge-decomposer",
        )

        decomp_result = await decomp_agent.run(
            prompt=(
                "Read spec.md and create a tasks.json file with the implementation tasks.\n\n"
                "Format: JSON array of objects, each with:\n"
                '  {"subject": "...", "description": "...", "sprint": "sprint-01", '
                '"risk": "low|medium|high", "blocked_by": []}\n\n'
                "Order tasks by dependency. Use blocked_by to reference earlier task indices.\n"
                "Keep it to 5-15 tasks. Write the tasks.json file now."
            ),
            system=(
                "You are a task decomposer. Read the spec and break it into implementable tasks. "
                "Write the tasks.json file using write_file tool."
            ),
        )

        tasks_path = self.project_path / "tasks.json"
        task_count = 0
        if tasks_path.exists():
            try:
                tasks_data = json.loads(tasks_path.read_text())
                if isinstance(tasks_data, list):
                    task_count = len(tasks_data)
                    # Load into TaskStore
                    store = TaskStore(self.project.tasks_file)
                    for i, t in enumerate(tasks_data):
                        blocked = t.get("blocked_by", [])
                        # Convert int indices to string task IDs
                        blocked_str = [str(b) for b in blocked] if blocked else None
                        store.create(
                            subject=t.get("subject", f"Task {i+1}"),
                            description=t.get("description", ""),
                            metadata={
                                "project": self.project.name,
                                "sprint": t.get("sprint", "sprint-01"),
                                "risk": t.get("risk", "low"),
                            },
                            blocked_by=blocked_str,
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
