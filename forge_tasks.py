"""Nova Forge task lifecycle — TaskStore with CRUD, JSON persistence, and topological sort.

Port of V11 task system to the nova-forge framework.
All writes are protected by filelock; persistence is plain JSON.
"""

from __future__ import annotations

import json
import logging
import warnings
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from filelock import FileLock
from pydantic import BaseModel, ValidationError

from config import ForgeProject

logger = logging.getLogger(__name__)

# ── Valid statuses and allowed transitions ────────────────────────────────────

VALID_STATUSES = frozenset({"pending", "in_progress", "completed", "failed", "blocked"})

# Maps current status → set of statuses it may transition to
STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending":     frozenset({"in_progress", "blocked"}),
    "in_progress": frozenset({"completed", "failed", "blocked", "pending"}),  # pending: interrupted build retry
    "blocked":     frozenset({"pending", "in_progress"}),
    "completed":   frozenset(),            # terminal
    "failed":      frozenset({"pending"}), # allow retry
}


# ── Pydantic metadata validator ───────────────────────────────────────────────

class TaskMetadata(BaseModel):
    """Validated metadata shape for a Task.

    Required fields: project, sprint, risk.
    Optional fields: complexity, scope, agent, gate, parallelizable.
    """
    project: str
    sprint: str
    risk: Literal["low", "medium", "high"]
    complexity: Optional[Literal["routine", "medium", "complex", "novel"]] = None
    scope: Optional[Literal["small", "medium", "large"]] = None
    agent: Optional[str] = None
    gate: Optional[str] = None
    parallelizable: bool = True


# ── Task dataclass ────────────────────────────────────────────────────────────

@dataclass
class Task:
    """A unit of work tracked by TaskStore.

    IDs are sequential integers (1, 2, 3…) stored as strings for
    JSON compatibility and uniform handling.
    """
    id: str
    subject: str
    description: str
    status: str = "pending"
    metadata: dict = field(default_factory=dict)
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        """Serialize to plain dict for JSON persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Task":
        """Reconstruct a Task from a persisted dict."""
        return cls(
            id=str(data["id"]),
            subject=data.get("subject", ""),
            description=data.get("description", ""),
            status=data.get("status", "pending"),
            metadata=data.get("metadata", {}),
            blocked_by=[str(t) for t in data.get("blocked_by", [])],
            blocks=[str(t) for t in data.get("blocks", [])],
            artifacts=data.get("artifacts", {}),
            created_at=data.get("created_at", ""),
            updated_at=data.get("updated_at", ""),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string with Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _validate_metadata(metadata: dict, task_id: str) -> None:
    """Warn (don't raise) if metadata is missing required V11 fields."""
    try:
        TaskMetadata(**metadata)
    except ValidationError as exc:
        missing = [str(e["loc"][0]) for e in exc.errors() if e["type"] == "missing"]
        invalid = [
            f"{e['loc'][0]}={metadata.get(str(e['loc'][0]))!r}"
            for e in exc.errors()
            if e["type"] != "missing"
        ]
        parts: list[str] = []
        if missing:
            parts.append(f"missing fields: {', '.join(missing)}")
        if invalid:
            parts.append(f"invalid values: {', '.join(invalid)}")
        warnings.warn(
            f"Task {task_id} metadata validation: {'; '.join(parts)}",
            stacklevel=3,
        )


# ── TaskStore ─────────────────────────────────────────────────────────────────

class TaskStore:
    """Persistent, filelock-protected task store backed by JSON.

    Usage::

        from config import ForgeProject
        project = ForgeProject(root="/path/to/project")
        store = TaskStore(project.tasks_file)
        t = store.create("Implement auth", "JWT login endpoint", metadata={...})
        store.update(t.id, status="in_progress")
        waves = store.compute_waves()

    All public methods that mutate state call ``_save()`` before returning.
    All file I/O is wrapped by a ``FileLock`` on ``<tasks_file>.lock``.
    """

    def __init__(self, tasks_file: Path) -> None:
        self._tasks_file = Path(tasks_file)
        self._lock_file = self._tasks_file.with_suffix(".lock")
        self._tasks: dict[str, Task] = {}    # id → Task
        self._next_id: int = 1
        self._load()

    # ── Internal persistence ──────────────────────────────────────────────────

    def _load(self) -> None:
        """Load tasks from JSON. Silently starts fresh on missing/corrupt file."""
        if not self._tasks_file.exists():
            logger.debug("Tasks file not found at %s — starting fresh", self._tasks_file)
            return

        try:
            with FileLock(str(self._lock_file)):
                raw = self._tasks_file.read_text(encoding="utf-8")
                data: dict = json.loads(raw)

            tasks_raw: list[dict] = data.get("tasks", [])
            for item in tasks_raw:
                t = Task.from_dict(item)
                self._tasks[t.id] = t

            # Derive _next_id from highest existing numeric ID
            numeric_ids = [int(tid) for tid in self._tasks if tid.isdigit()]
            self._next_id = max(numeric_ids, default=0) + 1

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning(
                "Corrupt tasks file at %s (%s) — starting fresh", self._tasks_file, exc
            )
            self._tasks = {}
            self._next_id = 1

    def _save(self) -> None:
        """Persist current task state to JSON (caller must hold lock or be in locked block)."""
        self._tasks_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1",
            "updated_at": _now_iso(),
            "tasks": [t.to_dict() for t in sorted(self._tasks.values(), key=lambda t: int(t.id) if t.id.isdigit() else 0)],
        }
        self._tasks_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(
        self,
        subject: str,
        description: str,
        metadata: dict | None = None,
        blocked_by: list[str] | None = None,
    ) -> Task:
        """Create and persist a new task.

        Args:
            subject: Brief title for the task.
            description: Detailed description or acceptance criteria.
            metadata: Optional dict; validated against TaskMetadata (warns if invalid).
            blocked_by: List of task IDs this task depends on.

        Returns:
            The newly created Task.

        Raises:
            ValueError: If any task ID in blocked_by does not exist.
        """
        meta = metadata or {}
        deps = [str(tid) for tid in (blocked_by or [])]

        # Assign sequential ID
        task_id = str(self._next_id)
        now = _now_iso()

        task = Task(
            id=task_id,
            subject=subject,
            description=description,
            status="pending",
            metadata=meta,
            blocked_by=deps,
            blocks=[],
            artifacts={},
            created_at=now,
            updated_at=now,
        )

        # Validate metadata (warn, don't crash)
        if meta:
            _validate_metadata(meta, task_id)
        else:
            warnings.warn(
                f"Task {task_id} has no metadata — recommended fields: project, sprint, risk",
                stacklevel=2,
            )

        with FileLock(str(self._lock_file)):
            # Validate dependency references
            for dep_id in deps:
                if dep_id not in self._tasks:
                    raise ValueError(
                        f"blocked_by references unknown task '{dep_id}'"
                    )

            # Populate bidirectional blocks references
            for dep_id in deps:
                dep_task = self._tasks[dep_id]
                if task_id not in dep_task.blocks:
                    dep_task.blocks.append(task_id)

            self._tasks[task_id] = task
            self._next_id += 1
            self._save()

        logger.info("Created task %s: %r", task_id, subject)
        return task

    def get(self, task_id: str) -> Task | None:
        """Return task by ID, or None if not found."""
        return self._tasks.get(str(task_id))

    def list(
        self,
        status: str | None = None,
        sprint: str | None = None,
        project: str | None = None,
    ) -> list[Task]:
        """Return tasks filtered by optional criteria, sorted by ID.

        Args:
            status: Filter to tasks with this status.
            sprint: Filter to tasks whose metadata.sprint matches.
            project: Filter to tasks whose metadata.project matches.

        Returns:
            Sorted list of matching Task objects.
        """
        results: list[Task] = []
        for task in self._tasks.values():
            if status is not None and task.status != status:
                continue
            if sprint is not None and task.metadata.get("sprint") != sprint:
                continue
            if project is not None and task.metadata.get("project") != project:
                continue
            results.append(task)

        # Sort by numeric ID when possible, else lexicographic
        results.sort(key=lambda t: int(t.id) if t.id.isdigit() else t.id)
        return results

    def update(self, task_id: str, **kwargs: Any) -> Task:
        """Update fields on an existing task and persist.

        Valid kwargs:
            status (str): Must follow allowed status transitions.
            description (str): Replace description.
            metadata (dict): Merged into existing metadata (not replaced).
            artifacts (dict): Merged into existing artifacts.
            blocked_by (list[str]): Replace dependency list; bidirectional refs updated.
            blocks (list[str]): Replace reverse dependency list directly.

        Returns:
            Updated Task.

        Raises:
            KeyError: If task_id does not exist.
            ValueError: On invalid status transition or unknown dependency IDs.
        """
        task_id = str(task_id)
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")

        valid_kwargs = {"status", "description", "metadata", "artifacts", "blocked_by", "blocks"}
        unknown = set(kwargs) - valid_kwargs
        if unknown:
            raise ValueError(f"Unknown update fields: {', '.join(sorted(unknown))}")

        with FileLock(str(self._lock_file)):
            # Status transition validation
            if "status" in kwargs:
                new_status = kwargs["status"]
                if new_status not in VALID_STATUSES:
                    raise ValueError(
                        f"Invalid status '{new_status}'. Valid: {sorted(VALID_STATUSES)}"
                    )
                allowed = STATUS_TRANSITIONS.get(task.status, frozenset())
                if new_status != task.status and new_status not in allowed:
                    raise ValueError(
                        f"Cannot transition task {task_id} from '{task.status}' to '{new_status}'. "
                        f"Allowed transitions: {sorted(allowed) or 'none (terminal state)'}"
                    )
                task.status = new_status

            if "description" in kwargs:
                task.description = str(kwargs["description"])

            if "metadata" in kwargs:
                task.metadata = {**task.metadata, **kwargs["metadata"]}
                _validate_metadata(task.metadata, task_id)

            if "artifacts" in kwargs:
                task.artifacts = {**task.artifacts, **kwargs["artifacts"]}

            if "blocked_by" in kwargs:
                new_deps = [str(tid) for tid in kwargs["blocked_by"]]

                # Validate all new deps exist
                for dep_id in new_deps:
                    if dep_id not in self._tasks and dep_id != task_id:
                        raise ValueError(
                            f"blocked_by references unknown task '{dep_id}'"
                        )

                # Remove this task from old deps' blocks lists
                for old_dep_id in task.blocked_by:
                    if old_dep_id in self._tasks:
                        old_dep = self._tasks[old_dep_id]
                        if task_id in old_dep.blocks:
                            old_dep.blocks.remove(task_id)

                # Add this task to new deps' blocks lists
                for dep_id in new_deps:
                    if dep_id in self._tasks:
                        dep_task = self._tasks[dep_id]
                        if task_id not in dep_task.blocks:
                            dep_task.blocks.append(task_id)

                task.blocked_by = new_deps

            if "blocks" in kwargs:
                task.blocks = [str(tid) for tid in kwargs["blocks"]]

            task.updated_at = _now_iso()
            self._save()

        logger.info("Updated task %s: %s", task_id, list(kwargs.keys()))
        return task

    def delete(self, task_id: str) -> bool:
        """Delete a task and clean up all dependency references.

        Args:
            task_id: ID of the task to delete.

        Returns:
            True if deleted, False if task was not found.
        """
        task_id = str(task_id)

        with FileLock(str(self._lock_file)):
            task = self._tasks.get(task_id)
            if task is None:
                return False

            # Remove this task from other tasks' blocked_by lists
            for bid in task.blocks:
                blocker = self._tasks.get(bid)
                if blocker and task_id in blocker.blocked_by:
                    blocker.blocked_by.remove(task_id)

            # Remove this task from other tasks' blocks lists
            for dep_id in task.blocked_by:
                dep = self._tasks.get(dep_id)
                if dep and task_id in dep.blocks:
                    dep.blocks.remove(task_id)

            del self._tasks[task_id]
            self._save()

        logger.info("Deleted task %s", task_id)
        return True

    # ── Wave computation (topological sort) ───────────────────────────────────

    def compute_waves(self) -> list[list[Task]]:
        """Compute execution waves via Kahn's topological sort.

        Only pending and in_progress tasks are included in the wave graph.
        Tasks already completed or failed are treated as resolved dependencies.

        Wave 0 contains tasks with no unresolved dependencies.
        Wave N contains tasks whose dependencies are all in waves 0..N-1.

        Returns:
            List of waves, each wave a list of Task objects.

        Raises:
            ValueError: If the dependency graph contains a cycle.
        """
        active_statuses = {"pending", "in_progress"}
        active: dict[str, Task] = {
            tid: t for tid, t in self._tasks.items() if t.status in active_statuses
        }

        # Terminal statuses — treated as already resolved for dependency purposes
        resolved_statuses = {"completed", "failed"}

        # Build in-degree map counting only unresolved active dependencies
        in_degree: dict[str, int] = {}
        for tid in active:
            in_degree[tid] = 0

        adjacency: dict[str, list[str]] = {tid: [] for tid in active}

        for tid, task in active.items():
            for dep_id in task.blocked_by:
                dep = self._tasks.get(dep_id)
                if dep is None:
                    # Reference to unknown task — treat as resolved
                    continue
                if dep.status in resolved_statuses:
                    # Already done — not a blocking dependency
                    continue
                if dep_id not in active:
                    # Dependency is blocked/non-active but not resolved — still counts
                    # Include in degree bump but not in adjacency traversal
                    in_degree[tid] += 1
                    continue
                # Active dependency
                in_degree[tid] += 1
                adjacency[dep_id].append(tid)

        # Kahn's BFS
        queue: deque[str] = deque(
            tid for tid, deg in in_degree.items() if deg == 0
        )
        waves: list[list[Task]] = []
        processed = 0

        while queue:
            wave_size = len(queue)
            wave_tasks: list[Task] = []

            for _ in range(wave_size):
                tid = queue.popleft()
                wave_tasks.append(active[tid])
                processed += 1

                for dependent_id in adjacency.get(tid, []):
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        queue.append(dependent_id)

            # Sort within each wave for deterministic output
            wave_tasks.sort(key=lambda t: int(t.id) if t.id.isdigit() else t.id)
            waves.append(wave_tasks)

        if processed != len(active):
            # Some nodes were never added to a wave → cycle
            cycle_nodes = [tid for tid, deg in in_degree.items() if deg > 0]
            raise ValueError(
                f"Circular dependency detected among tasks: {cycle_nodes}"
            )

        return waves

    # ── Checkpoint / restore ──────────────────────────────────────────────────

    def checkpoint(self) -> dict:
        """Serialize full store state for context resume.

        Returns a plain dict that can be passed to ``restore()`` to recreate
        an identical TaskStore state (without touching the filesystem).
        """
        return {
            "version": "1",
            "checkpoint_at": _now_iso(),
            "next_id": self._next_id,
            "tasks": [t.to_dict() for t in self._tasks.values()],
        }

    def restore(self, data: dict) -> None:
        """Restore store state from a checkpoint dict.

        This replaces the in-memory state only; it does NOT write to disk.
        Call ``_save()`` explicitly (inside a lock) if persistence is needed.

        Args:
            data: Dict produced by ``checkpoint()``.
        """
        self._tasks = {}
        for item in data.get("tasks", []):
            t = Task.from_dict(item)
            self._tasks[t.id] = t
        self._next_id = data.get("next_id", self._next_id)
        logger.debug("Restored %d tasks from checkpoint", len(self._tasks))

    # ── Markdown state summary ────────────────────────────────────────────────

    def sync_state_md(self, output_path: Path) -> None:
        """Write a human-readable Markdown summary of the task store.

        Groups tasks by status and includes wave information for active tasks.

        Args:
            output_path: Destination path for the .md file (parent dirs created).
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        all_tasks = self.list()
        now = _now_iso()

        # Status counts
        counts: dict[str, int] = {}
        for t in all_tasks:
            counts[t.status] = counts.get(t.status, 0) + 1

        # Try to compute waves; skip if there's a cycle
        try:
            waves = self.compute_waves()
            wave_section = _render_waves(waves)
        except ValueError as exc:
            wave_section = f"> WARNING: {exc}\n"

        lines: list[str] = [
            "# Task State",
            f"\n_Generated: {now}_\n",
            "## Summary\n",
            "| Status | Count |",
            "|--------|-------|",
        ]
        for status in ("pending", "in_progress", "blocked", "completed", "failed"):
            n = counts.get(status, 0)
            lines.append(f"| {status} | {n} |")

        lines.append(f"\n**Total**: {len(all_tasks)} tasks\n")

        # Waves
        lines.append("## Execution Waves\n")
        lines.append(wave_section)

        # Full task list grouped by status
        lines.append("## All Tasks\n")
        for status in ("in_progress", "pending", "blocked", "completed", "failed"):
            group = [t for t in all_tasks if t.status == status]
            if not group:
                continue
            lines.append(f"### {status.replace('_', ' ').title()}\n")
            for t in group:
                meta_str = ""
                if t.metadata:
                    parts = []
                    for k in ("project", "sprint", "risk", "agent"):
                        v = t.metadata.get(k)
                        if v:
                            parts.append(f"{k}={v}")
                    meta_str = "  _" + ", ".join(parts) + "_" if parts else ""
                dep_str = f"  blocked_by={t.blocked_by}" if t.blocked_by else ""
                lines.append(f"- **[{t.id}]** {t.subject}{meta_str}{dep_str}")
            lines.append("")

        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Wrote state.md to %s", output_path)

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"TaskStore(tasks_file={self._tasks_file!r}, "
            f"tasks={len(self._tasks)}, next_id={self._next_id})"
        )

    def __len__(self) -> int:
        return len(self._tasks)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _render_waves(waves: list[list[Task]]) -> str:
    """Render wave list as a Markdown string."""
    if not waves:
        return "_No active tasks._\n"
    lines: list[str] = []
    for i, wave in enumerate(waves):
        labels = ", ".join(f"[{t.id}] {t.subject}" for t in wave)
        lines.append(f"**Wave {i}**: {labels}")
    return "\n".join(lines) + "\n"


def open_store(project_or_path: ForgeProject | Path) -> TaskStore:
    """Convenience factory: open TaskStore from a ForgeProject or explicit Path.

    Args:
        project_or_path: Either a ForgeProject instance (uses ``.tasks_file``)
                         or a direct Path to the tasks JSON file.

    Returns:
        A ready-to-use TaskStore.
    """
    if isinstance(project_or_path, ForgeProject):
        tasks_file = project_or_path.tasks_file
    else:
        tasks_file = Path(project_or_path)
    return TaskStore(tasks_file)
