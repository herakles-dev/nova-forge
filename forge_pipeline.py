"""Nova Forge Pipeline — WaveExecutor, ArtifactManager, GateReviewer.

Orchestrates multi-wave agent execution with per-agent isolation (AD-5),
semaphore-gated parallelism (AD-2), and an LLM-backed gate reviewer.

Wave sequencing:
  - Waves execute strictly sequentially.
  - Within each wave all agents run in parallel (gated by asyncio.Semaphore).
  - Artifacts are merged AFTER asyncio.gather completes, never during.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from config import get_model_config, ForgeProject
from forge_agent import ForgeAgent, AgentResult, BUILT_IN_TOOLS
from forge_guards import PathSandbox
from forge_hooks import HookSystem
from forge_tasks import Task, TaskStore
from formations import Formation, Role, TOOL_PROFILES
from prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

# ── Result dataclasses ────────────────────────────────────────────────────────

@dataclass
class WaveResult:
    """Outcome of a single execution wave."""
    wave_index: int
    agent_results: dict[str, AgentResult]  # role_name -> result
    artifacts: dict[str, Any]              # merged artifacts from all agents in wave
    errors: list[str]                       # any errors encountered
    duration: float                         # seconds


@dataclass
class GateResult:
    """Verdict from a GateReviewer evaluation."""
    status: str                  # "PASS", "FAIL", "CONDITIONAL"
    reasons: list[str]           # why pass/fail
    recommendations: list[str]   # suggestions


@dataclass
class PipelineResult:
    """Full outcome of an execute_all_waves() run."""
    waves_completed: int
    total_waves: int
    wave_results: list[WaveResult]
    gate_result: GateResult | None
    artifacts: dict[str, Any]    # all artifacts merged across all waves
    errors: list[str]
    duration: float


# ── ArtifactManager ───────────────────────────────────────────────────────────

# Inline threshold: artifacts <= 2 KB are embedded directly; larger ones are
# stored as file references with a truncated preview.
_INLINE_THRESHOLD_BYTES = 2048


class ArtifactManager:
    """Per-agent artifact isolation with post-gather merging (AD-5).

    Directory layout::

        artifacts_dir/
          wave-0/
            role-name/
              <artifact files>
          wave-1/
            ...
          index.json   ← master index of all stored artifacts
    """

    def __init__(self, artifacts_dir: Path) -> None:
        self.artifacts_dir = Path(artifacts_dir)
        self._index: dict[str, Any] = {}   # master index: ref_key -> metadata

    # ── Workspace management ─────────────────────────────────────────────────

    def create_agent_workspace(self, wave_index: int, role_name: str) -> Path:
        """Create isolated workspace: artifacts_dir/wave-{N}/{role_name}/"""
        workspace = self.artifacts_dir / f"wave-{wave_index}" / role_name
        workspace.mkdir(parents=True, exist_ok=True)
        return workspace

    # ── Artifact storage / retrieval ─────────────────────────────────────────

    def store(
        self,
        wave_index: int,
        role_name: str,
        name: str,
        content: str,
    ) -> dict:
        """Write an artifact file and record it in the master index.

        Returns a ref dict that can be passed back to :meth:`read`.
        """
        workspace = self.create_agent_workspace(wave_index, role_name)
        # Sanitise name to a safe filename.
        safe_name = re.sub(r"[^\w.\-]", "_", name)
        artifact_path = workspace / safe_name
        artifact_path.write_text(content, encoding="utf-8")

        ref_key = f"wave-{wave_index}/{role_name}/{safe_name}"
        ref = {
            "ref_key": ref_key,
            "path": str(artifact_path),
            "wave_index": wave_index,
            "role_name": role_name,
            "name": safe_name,
            "size_bytes": len(content.encode("utf-8")),
        }
        self._index[ref_key] = ref
        logger.debug("Stored artifact %s (%d bytes)", ref_key, ref["size_bytes"])
        return ref

    def read(self, ref: dict) -> str:
        """Read artifact content from a ref dict returned by :meth:`store`."""
        path = Path(ref["path"])
        if not path.exists():
            return f"[artifact not found: {ref.get('ref_key', '?')}]"
        return path.read_text(encoding="utf-8", errors="replace")

    # ── Wave-level artifact merging ───────────────────────────────────────────

    def merge_wave_artifacts(
        self,
        wave_index: int,
        agent_results: dict[str, AgentResult],
    ) -> dict:
        """Merge artifacts from all agents AFTER asyncio.gather completes.

        Each agent's ``AgentResult.artifacts`` is a plain dict whose values
        are either file-write records (produced by ForgeAgent tools) or
        arbitrary data set by the agent.  We store text-like values as
        managed artifacts and surface them in the merged dict.

        Returns a flat merged dict: key -> content (str) or ref dict.
        """
        merged: dict[str, Any] = {}

        for role_name, result in agent_results.items():
            if result.error:
                # Still capture any partial artifacts before the error.
                pass

            for key, value in (result.artifacts or {}).items():
                # ForgeAgent write_file / edit_file stores dicts like:
                #   {"action": "write", "size": N}
                # We surface both the record and the file content.
                if isinstance(value, dict) and "action" in value:
                    merged_key = f"{role_name}:{key}"
                    merged[merged_key] = value
                    # Try reading the file if path is available.
                    try:
                        file_content = Path(key).read_text(encoding="utf-8", errors="replace")
                        content_key = f"{role_name}:content:{Path(key).name}"
                        merged[content_key] = file_content
                    except (OSError, ValueError):
                        pass
                elif isinstance(value, str):
                    merged_key = f"{role_name}:{key}"
                    merged[merged_key] = value
                else:
                    merged_key = f"{role_name}:{key}"
                    merged[merged_key] = value

        # Record that we merged this wave.
        merged["_wave_index"] = wave_index
        return merged

    # ── Upstream artifact injection ───────────────────────────────────────────

    def inject_upstream(self, task: Task, store: TaskStore) -> dict:
        """Collect artifacts from completed blocking tasks.

        Inline  <=2 KB: embed content directly as a string.
        Reference >2 KB: embed a truncated preview + path reference.

        Returns a context dict suitable for PromptBuilder.build(context=...).
        """
        context: dict[str, Any] = {}

        for dep_id in task.blocked_by:
            dep_task = store.get(dep_id)
            if dep_task is None:
                continue
            if dep_task.status != "completed":
                continue

            for artifact_key, artifact_value in (dep_task.artifacts or {}).items():
                context_key = f"task-{dep_id}:{artifact_key}"

                if isinstance(artifact_value, str):
                    byte_size = len(artifact_value.encode("utf-8"))
                    if byte_size <= _INLINE_THRESHOLD_BYTES:
                        context[context_key] = artifact_value
                    else:
                        preview = artifact_value[:_INLINE_THRESHOLD_BYTES]
                        context[context_key] = (
                            f"{preview}\n\n... [truncated: {byte_size} bytes total]"
                        )
                elif isinstance(artifact_value, dict) and "path" in artifact_value:
                    # File reference from store() / ForgeAgent write
                    path = Path(artifact_value["path"])
                    size = artifact_value.get("size_bytes", 0)
                    if path.exists():
                        try:
                            content = path.read_text(encoding="utf-8", errors="replace")
                            byte_size = len(content.encode("utf-8"))
                            if byte_size <= _INLINE_THRESHOLD_BYTES:
                                context[context_key] = content
                            else:
                                preview = content[:_INLINE_THRESHOLD_BYTES]
                                context[context_key] = (
                                    f"{preview}\n\n... [truncated: {byte_size} bytes, "
                                    f"full path: {path}]"
                                )
                        except OSError:
                            context[context_key] = f"[file reference: {path} ({size} bytes)]"
                    else:
                        context[context_key] = f"[file reference: {path} ({size} bytes)]"
                else:
                    context[context_key] = str(artifact_value)

        return context

    # ── Persistence ───────────────────────────────────────────────────────────

    def save_index(self) -> None:
        """Write master index to artifacts_dir/index.json."""
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.artifacts_dir / "index.json"
        index_path.write_text(
            json.dumps(self._index, indent=2, default=str),
            encoding="utf-8",
        )
        logger.debug("Saved artifact index to %s (%d entries)", index_path, len(self._index))


# ── WaveExecutor ──────────────────────────────────────────────────────────────

class WaveExecutor:
    """Execute all waves of a task plan sequentially, each wave in parallel.

    Design:
      - asyncio.Semaphore(max_concurrent) throttles parallel agent launches.
      - ArtifactManager merges after gather, never during.
      - Failed agents don't crash the pipeline; dependent tasks are marked blocked.
    """

    def __init__(
        self,
        project_root: Path,
        formation: Formation,
        store: TaskStore,
        hooks: HookSystem | None = None,
        max_concurrent: int = 6,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.formation = formation
        self.store = store
        self.hooks = hooks or HookSystem()
        self.max_concurrent = max_concurrent
        self.semaphore = asyncio.Semaphore(max_concurrent)

        self._prompt_builder = PromptBuilder(project_root)
        self._artifact_manager = ArtifactManager(
            self.project_root / ".forge" / "state" / "artifacts"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    async def execute_all_waves(self) -> PipelineResult:
        """Execute all waves sequentially.

        For each wave:
          1. Build prompts for each task (PromptBuilder).
          2. Create ForgeAgent per task with the appropriate role's model.
          3. asyncio.gather all agents in the wave (semaphore-gated).
          4. Merge artifacts via ArtifactManager.
          5. Update task statuses (completed / failed).
          6. Mark tasks that depended on failed tasks as blocked.
        """
        pipeline_start = time.monotonic()
        all_errors: list[str] = []
        all_artifacts: dict[str, Any] = {}
        wave_results: list[WaveResult] = []

        try:
            waves = self.store.compute_waves()
        except ValueError as exc:
            # Cycle detected — return immediately.
            logger.error("Cycle detected in task graph: %s", exc)
            return PipelineResult(
                waves_completed=0,
                total_waves=0,
                wave_results=[],
                gate_result=None,
                artifacts={},
                errors=[f"Cycle in task dependency graph: {exc}"],
                duration=time.monotonic() - pipeline_start,
            )

        total_waves = len(waves)
        logger.info("Pipeline: %d wave(s) with formation %r", total_waves, self.formation.name)

        if total_waves == 0:
            return PipelineResult(
                waves_completed=0,
                total_waves=0,
                wave_results=[],
                gate_result=None,
                artifacts={},
                errors=[],
                duration=time.monotonic() - pipeline_start,
            )

        for wave_index, wave_tasks in enumerate(waves):
            logger.info(
                "Wave %d/%d: %d task(s) — %s",
                wave_index + 1,
                total_waves,
                len(wave_tasks),
                [t.subject for t in wave_tasks],
            )

            wave_result = await self._execute_wave(wave_index, wave_tasks)
            wave_results.append(wave_result)
            all_errors.extend(wave_result.errors)
            all_artifacts.update(wave_result.artifacts)

            # Propagate failures to dependent tasks.
            failed_task_ids = {
                task.id
                for task in wave_tasks
                if wave_result.agent_results.get(task.metadata.get("agent", task.id), AgentResult()).error
                   or task.status == "failed"
            }
            if failed_task_ids:
                self._block_dependents(failed_task_ids)

        self._artifact_manager.save_index()
        self._sync_task_state()

        return PipelineResult(
            waves_completed=len(wave_results),
            total_waves=total_waves,
            wave_results=wave_results,
            gate_result=None,   # Caller may run GateReviewer separately.
            artifacts=all_artifacts,
            errors=all_errors,
            duration=time.monotonic() - pipeline_start,
        )

    # ── Wave execution ────────────────────────────────────────────────────────

    async def _execute_wave(
        self,
        wave_index: int,
        tasks: list[Task],
    ) -> WaveResult:
        """Run all tasks in a wave in parallel (semaphore-gated)."""
        wave_start = time.monotonic()
        errors: list[str] = []

        # Re-read task statuses to skip any tasks that were blocked by earlier waves
        runnable_tasks = []
        for task in tasks:
            fresh = self.store.get(task.id)
            if fresh and fresh.status == "blocked":
                logger.info("Skipping blocked task %s in wave %d", task.id, wave_index)
                continue
            runnable_tasks.append(task)
        tasks = runnable_tasks

        if not tasks:
            return WaveResult(
                wave_index=wave_index,
                agent_results={},
                artifacts={},
                errors=[],
                duration=0.0,
            )

        # Build coroutines for each task.
        async def run_one(task: Task) -> tuple[str, Task, AgentResult]:
            # Transition pending → in_progress before agent runs
            try:
                self.store.update(task.id, status="in_progress")
            except (KeyError, ValueError) as exc:
                logger.warning("Could not mark task %s in_progress: %s", task.id, exc)
            role = self._find_role_for_task(task)
            upstream_context = self._artifact_manager.inject_upstream(task, self.store)
            result = await self._execute_agent(task, role, upstream_context)
            return role.name, task, result

        coros = [run_one(t) for t in tasks]
        raw_results = await asyncio.gather(*coros, return_exceptions=True)

        # Build agent_results keyed by role name + task id to avoid collisions
        # when multiple tasks use the same role.
        agent_results: dict[str, AgentResult] = {}
        for i, raw in enumerate(raw_results):
            task = tasks[i]
            if isinstance(raw, BaseException):
                # Task raised an unhandled exception — wrap as a failed AgentResult
                logger.error("Task %s raised exception: %s", task.id, raw)
                role_name = "unknown"
                result = AgentResult(error=str(raw))
                errors.append(f"Task {task.id} ({task.subject}): {raw}")
                try:
                    self.store.update(task.id, status="failed")
                except (KeyError, ValueError) as exc:
                    logger.warning("Could not mark task %s failed: %s", task.id, exc)
                key = f"{role_name}:{task.id}"
                agent_results[key] = result
                continue
            role_name, task, result = raw
            # Key format: role_name if unique, else role_name:task_id
            key = role_name if role_name not in agent_results else f"{role_name}:{task.id}"
            agent_results[key] = result

            # Persist outcome to task store.
            if result.error:
                errors.append(f"Task {task.id} ({task.subject}): {result.error}")
                try:
                    self.store.update(task.id, status="failed")
                except (KeyError, ValueError) as exc:
                    logger.warning("Could not mark task %s failed: %s", task.id, exc)
            else:
                try:
                    self.store.update(
                        task.id,
                        status="completed",
                        artifacts=result.artifacts,
                    )
                except (KeyError, ValueError) as exc:
                    logger.warning("Could not mark task %s completed: %s", task.id, exc)

        # Merge artifacts AFTER gather, never during.
        wave_artifacts = self._artifact_manager.merge_wave_artifacts(
            wave_index, agent_results
        )

        return WaveResult(
            wave_index=wave_index,
            agent_results=agent_results,
            artifacts=wave_artifacts,
            errors=errors,
            duration=time.monotonic() - wave_start,
        )

    async def _execute_agent(
        self,
        task: Task,
        role: Role,
        context: dict,
    ) -> AgentResult:
        """Run a single agent with semaphore throttling."""
        async with self.semaphore:
            # Build prompts.
            formation_dict: dict[str, Any] = {
                "ownership": (
                    role.ownership.get("directories", [])
                    + role.ownership.get("files", [])
                    + role.ownership.get("patterns", [])
                ),
                "forbidden_paths": [],
                "tool_policy": {
                    "restricted": [],
                    "available": sorted(
                        TOOL_PROFILES.get(role.tool_policy, TOOL_PROFILES["full"])
                    ),
                },
            }
            tool_policy_dict: dict[str, Any] = {
                "available": sorted(
                    TOOL_PROFILES.get(role.tool_policy, TOOL_PROFILES["full"])
                ),
                "restricted": [],
            }
            system_prompt, user_prompt = self._prompt_builder.build(
                role=role.name,
                task={
                    "subject": task.subject,
                    "description": task.description,
                    "metadata": task.metadata,
                },
                context=context if context else None,
                formation=formation_dict,
                tool_policy=tool_policy_dict,
            )

            tools = self._filter_tools(role.tool_policy)

            agent = ForgeAgent(
                model_config=get_model_config(role.model),
                project_root=self.project_root,
                hooks=self.hooks,
                sandbox=PathSandbox(self.project_root),
                tools=tools,
                agent_id=f"forge-{role.name}",
            )

            logger.info(
                "Running agent forge-%s for task %s (%s)",
                role.name,
                task.id,
                task.subject,
            )
            try:
                result = await agent.run(
                    prompt=user_prompt,
                    system=system_prompt,
                )
            except Exception as exc:
                logger.error(
                    "Agent forge-%s raised unexpected exception: %s",
                    role.name,
                    exc,
                    exc_info=True,
                )
                result = AgentResult(
                    output=f"Agent exception: {exc}",
                    error=str(exc),
                )

            return result

    # ── Tool filtering ────────────────────────────────────────────────────────

    def _filter_tools(self, tool_policy: str) -> list[dict]:
        """Return BUILT_IN_TOOLS filtered to the role's tool policy profile."""
        allowed: set[str] = TOOL_PROFILES.get(tool_policy, TOOL_PROFILES["full"])
        return [t for t in BUILT_IN_TOOLS if t["name"] in allowed]

    # ── Role matching ─────────────────────────────────────────────────────────

    def _find_role_for_task(self, task: Task) -> Role:
        """Match a task to a formation role.

        Precedence:
          1. task.metadata["agent"] matches a role name exactly.
          2. First role whose name appears in the current wave order.
          3. Fallback to the first role defined in the formation.
        """
        agent_hint: str = task.metadata.get("agent", "")

        if agent_hint:
            for role in self.formation.roles:
                if role.name == agent_hint:
                    return role

        # Find which wave this task belongs to by checking active wave.
        # Use the first role in the formation wave_order as a practical default.
        if self.formation.wave_order:
            for wave_roles in self.formation.wave_order:
                for role_name in wave_roles:
                    for role in self.formation.roles:
                        if role.name == role_name:
                            return role

        # Absolute fallback.
        if self.formation.roles:
            return self.formation.roles[0]

        # Synthetic minimal role if formation has no roles (shouldn't happen).
        from formations import Role as _Role
        return _Role(
            name="default",
            model="bedrock/us.amazon.nova-2-lite-v1:0",
            tool_policy="full",
            ownership={"files": [], "directories": [], "patterns": []},
            description="Default fallback role",
        )

    # ── Task state sync ──────────────────────────────────────────────────────

    def _sync_task_state(self) -> None:
        """Sync TaskStore counts to .forge/state/task-state.json for SessionManager."""
        tasks = self.store.list()
        state = {
            "total": len(tasks),
            "completed": sum(1 for t in tasks if t.status == "completed"),
            "in_progress": sum(1 for t in tasks if t.status == "in_progress"),
            "pending": sum(1 for t in tasks if t.status == "pending"),
            "failed": sum(1 for t in tasks if t.status == "failed"),
            "blocked": sum(1 for t in tasks if t.status == "blocked"),
            "last_updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        state_file = self.project_root / ".forge" / "state" / "task-state.json"
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2) + "\n")
        logger.info("Synced task state: %d/%d completed", state["completed"], state["total"])

    # ── Failure propagation ───────────────────────────────────────────────────

    def _block_dependents(self, failed_task_ids: set[str]) -> None:
        """Block all tasks (direct AND transitive) that depend on failed tasks."""
        blocked_ids = set()
        to_process = list(failed_task_ids)

        while to_process:
            current_id = to_process.pop(0)
            for task in self.store.list():
                if task.id in blocked_ids:
                    continue
                if task.status not in ("pending", "in_progress"):
                    continue
                if current_id in (task.blocked_by or []):
                    try:
                        self.store.update(task.id, status="blocked")
                        blocked_ids.add(task.id)
                        to_process.append(task.id)  # Process this task's dependents too
                        logger.info(
                            "Blocked task %s (%s) — depends on failed %s",
                            task.id,
                            task.subject,
                            current_id,
                        )
                    except (KeyError, ValueError) as exc:
                        logger.warning(
                            "Could not block task %s: %s", task.id, exc
                        )


# ── GateReviewer ──────────────────────────────────────────────────────────────

_GATE_REVIEW_SYSTEM = (
    "You are a quality gate reviewer. Your job is to evaluate whether the "
    "artifacts produced by an agent wave meet the specified gate criteria. "
    "Be precise and evidence-based. Read the actual files before judging."
)

_GATE_REVIEW_PROMPT_TEMPLATE = """\
## Gate Review Task

You must evaluate whether the following gate criteria have been met by the
artifacts produced in the previous execution wave.

## Gate Criteria
{criteria}

## Artifacts Summary
{artifacts_summary}

## Instructions
1. Use the read_file and glob_files tools to inspect relevant files.
2. For each criterion, determine whether it is satisfied, partially satisfied,
   or not satisfied, based on the actual file contents.
3. Return your verdict as a JSON object (and ONLY that JSON object — no prose
   before or after) with this exact structure:

{{
  "status": "PASS" | "FAIL" | "CONDITIONAL",
  "reasons": ["<reason 1>", "<reason 2>"],
  "recommendations": ["<suggestion 1>"]
}}

Rules:
- PASS  → all criteria met with evidence.
- FAIL  → one or more criteria not met.
- CONDITIONAL → criteria partially met; further action needed.
"""


class GateReviewer:
    """LLM-backed gate reviewer that reads artifacts and returns a verdict.

    Uses a ForgeAgent with read_file and glob_files tools only.
    Falls back to CONDITIONAL when the agent fails or returns un-parseable output.
    """

    def __init__(
        self,
        model: str | None = None,
        project_root: Path | None = None,
    ) -> None:
        self.model = model or "bedrock/us.amazon.nova-2-lite-v1:0"
        self.project_root = Path(project_root or ".").resolve()

    async def review(
        self,
        wave_results: list[WaveResult],
        gate_criteria: list[str],
    ) -> GateResult:
        """Spawn a ForgeAgent to review artifacts against gate criteria.

        The reviewer agent gets read_file and glob_files tools only.
        If the agent fails or returns un-parseable output, returns CONDITIONAL.
        """
        if not gate_criteria:
            return GateResult(
                status="PASS",
                reasons=["No gate criteria specified — auto-pass."],
                recommendations=[],
            )

        if not wave_results:
            return GateResult(
                status="CONDITIONAL",
                reasons=["No wave results available to review."],
                recommendations=["Ensure at least one wave has completed before gate review."],
            )

        # Build a summary of artifacts for the prompt.
        artifacts_summary = self._build_artifacts_summary(wave_results)
        criteria_text = "\n".join(f"- {c}" for c in gate_criteria)

        prompt = _GATE_REVIEW_PROMPT_TEMPLATE.format(
            criteria=criteria_text,
            artifacts_summary=artifacts_summary,
        )

        # Reviewer tools: read and glob only.
        reviewer_tools = [
            t for t in BUILT_IN_TOOLS
            if t["name"] in {"read_file", "glob_files"}
        ]

        agent = ForgeAgent(
            model_config=get_model_config(self.model),
            project_root=self.project_root,
            sandbox=PathSandbox(self.project_root),
            tools=reviewer_tools,
            agent_id="forge-gate-reviewer",
        )

        logger.info(
            "GateReviewer: evaluating %d criteria across %d wave(s)",
            len(gate_criteria),
            len(wave_results),
        )

        try:
            result = await agent.run(
                prompt=prompt,
                system=_GATE_REVIEW_SYSTEM,
            )
        except Exception as exc:
            logger.error("GateReviewer agent raised exception: %s", exc, exc_info=True)
            return GateResult(
                status="CONDITIONAL",
                reasons=[f"Reviewer agent raised an exception: {exc}"],
                recommendations=["Inspect the pipeline logs and re-run the gate review."],
            )

        if result.error:
            logger.warning("GateReviewer agent returned error: %s", result.error)
            return GateResult(
                status="CONDITIONAL",
                reasons=[f"Reviewer agent error: {result.error}"],
                recommendations=["Check agent configuration and model availability."],
            )

        return self._parse_verdict(result.output)

    # ── Verdict parsing ───────────────────────────────────────────────────────

    def _parse_verdict(self, output: str) -> GateResult:
        """Extract JSON verdict from agent output text.

        Accepts:
          - A bare JSON object.
          - JSON embedded inside a markdown code fence.
          - JSON anywhere in the output text.

        Falls back to CONDITIONAL on any parsing failure.
        """
        if not output or not output.strip():
            return GateResult(
                status="CONDITIONAL",
                reasons=["Reviewer returned empty output."],
                recommendations=["Verify the reviewer model is responding correctly."],
            )

        # Try to extract JSON from a code fence first.
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", output, re.DOTALL)
        candidate = fence_match.group(1) if fence_match else None

        # Fall back to finding the first { ... } block.
        if candidate is None:
            brace_match = re.search(r"\{.*\}", output, re.DOTALL)
            candidate = brace_match.group(0) if brace_match else None

        if candidate is None:
            logger.warning("GateReviewer: no JSON found in agent output")
            return GateResult(
                status="CONDITIONAL",
                reasons=["Reviewer output contained no parseable JSON verdict."],
                recommendations=[
                    "The reviewer agent may need a different prompt or model.",
                    f"Raw output (first 500 chars): {output[:500]}",
                ],
            )

        try:
            data = json.loads(candidate)
        except json.JSONDecodeError as exc:
            logger.warning("GateReviewer: JSON parse failed: %s", exc)
            return GateResult(
                status="CONDITIONAL",
                reasons=[f"Reviewer output JSON was malformed: {exc}"],
                recommendations=[
                    "Inspect the raw reviewer output for issues.",
                    f"Raw candidate (first 500 chars): {candidate[:500]}",
                ],
            )

        status = str(data.get("status", "CONDITIONAL")).upper()
        if status not in {"PASS", "FAIL", "CONDITIONAL"}:
            logger.warning("GateReviewer: unexpected status value %r — treating as CONDITIONAL", status)
            status = "CONDITIONAL"

        reasons = data.get("reasons", [])
        if not isinstance(reasons, list):
            reasons = [str(reasons)]
        reasons = [str(r) for r in reasons]

        recommendations = data.get("recommendations", [])
        if not isinstance(recommendations, list):
            recommendations = [str(recommendations)]
        recommendations = [str(r) for r in recommendations]

        return GateResult(
            status=status,
            reasons=reasons,
            recommendations=recommendations,
        )

    # ── Artifacts summary ─────────────────────────────────────────────────────

    def _build_artifacts_summary(self, wave_results: list[WaveResult]) -> str:
        """Summarise wave artifacts for the reviewer's prompt."""
        lines: list[str] = []
        for wr in wave_results:
            lines.append(f"### Wave {wr.wave_index}")
            lines.append(f"Duration: {wr.duration:.1f}s")
            lines.append(f"Agents completed: {len(wr.agent_results)}")
            if wr.errors:
                lines.append(f"Errors: {len(wr.errors)}")
                for err in wr.errors[:3]:
                    lines.append(f"  - {err[:200]}")
            if wr.artifacts:
                non_meta = {k: v for k, v in wr.artifacts.items() if not k.startswith("_")}
                lines.append(f"Artifacts produced: {len(non_meta)}")
                for key, value in list(non_meta.items())[:8]:
                    if isinstance(value, str) and len(value) > 120:
                        value_preview = value[:120] + "..."
                    elif isinstance(value, dict):
                        value_preview = str(value)[:120]
                    else:
                        value_preview = str(value)[:120]
                    lines.append(f"  {key}: {value_preview}")
            lines.append("")
        return "\n".join(lines)
