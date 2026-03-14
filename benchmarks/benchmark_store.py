"""Benchmark storage, regression detection, and optimization hints.

Handles all persistence, metadata collection, historical analysis, and
actionable feedback for the Nova Forge benchmark suite.
"""

import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class RunMetadata:
    """Git, system, and context metadata for a benchmark run."""
    git_commit: str = ""
    git_branch: str = ""
    git_dirty: bool = False
    git_changed_files: list[str] = field(default_factory=list)
    python_version: str = ""
    platform: str = ""
    spec_hash: str = ""
    system_prompt_hash: str = ""
    models_run: list[str] = field(default_factory=list)
    trigger: str = ""


@dataclass
class RegressionAlert:
    """A detected regression between two benchmark runs."""
    model: str
    dimension: str  # "grade", "score", "server", or a dimension name
    old_value: str
    new_value: str
    severity: str  # "critical", "warning", "info"


@dataclass
class CheckDiff:
    """A per-check state change between two runs."""
    check_name: str
    model: str
    old_state: bool  # True = passed
    new_state: bool
    dimension: str = ""


@dataclass
class OptimizationHint:
    """An actionable tuning suggestion based on dimension scores."""
    dimension: str
    score: float
    suggestion: str
    files: list[str] = field(default_factory=list)


# ── Metadata collection ─────────────────────────────────────────────────────

def _git(args: list[str], cwd: Optional[str] = None) -> str:
    """Run a git command and return stripped stdout, or '' on failure."""
    try:
        r = subprocess.run(
            ["git"] + args, capture_output=True, text=True,
            timeout=5, cwd=cwd or str(Path(__file__).resolve().parent.parent),
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def collect_metadata(
    spec_text: str = "",
    system_prompt: str = "",
) -> RunMetadata:
    """Gather git, system, and hash metadata for the current run."""
    changed = _git(["diff", "--name-only", "HEAD"])
    changed_files = [f for f in changed.splitlines() if f] if changed else []

    return RunMetadata(
        git_commit=_git(["rev-parse", "--short", "HEAD"]),
        git_branch=_git(["rev-parse", "--abbrev-ref", "HEAD"]),
        git_dirty=bool(changed_files) or bool(_git(["status", "--porcelain"])),
        git_changed_files=changed_files[:50],
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        platform=platform.platform(),
        spec_hash=hashlib.sha256(spec_text.encode()).hexdigest()[:12] if spec_text else "",
        system_prompt_hash=hashlib.sha256(system_prompt.encode()).hexdigest()[:12] if system_prompt else "",
    )


# ── BenchmarkStore ───────────────────────────────────────────────────────────

class BenchmarkStore:
    """Manages benchmark run persistence, symlinks, and the run index."""

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.runs_dir = self.base_dir / "runs"

    def save_run(
        self,
        results: list[dict],
        metadata: RunMetadata,
        run_name: Optional[str] = None,
    ) -> Path:
        """Save a benchmark run to disk. Returns the path to the saved JSON.

        Args:
            results: list of ModelBenchmark.to_dict() dicts
            metadata: RunMetadata for this run
            run_name: optional human-readable name
        """
        now = datetime.now()
        ts = now.strftime("%Y%m%d_%H%M%S")
        month_dir = self.runs_dir / now.strftime("%Y-%m")
        month_dir.mkdir(parents=True, exist_ok=True)

        # Build summary
        models = [r.get("model_alias", "?") for r in results]
        grades = {r.get("model_alias", "?"): r.get("grade", "?") for r in results}
        scores = {r.get("model_alias", "?"): r.get("overall_score", 0) for r in results}
        duration_total = sum(r.get("duration_secs", 0) for r in results)
        cost_total = sum(r.get("total_cost", 0) for r in results)

        run_data = {
            "schema_version": 2,
            "timestamp": now.isoformat(),
            "run_name": run_name or "",
            "metadata": asdict(metadata),
            "summary": {
                "models": models,
                "grades": grades,
                "scores": scores,
                "duration_total": round(duration_total, 1),
                "cost_total": round(cost_total, 6),
            },
            "results": results,
        }

        # Write run file
        run_path = month_dir / f"run_{ts}.json"
        run_path.write_text(json.dumps(run_data, indent=2))

        # Update symlinks
        self._update_symlinks(run_path, models)

        # Append to index
        self._append_index(run_path, run_data)

        return run_path

    def _update_symlinks(self, run_path: Path, models: list[str]) -> None:
        """Update latest.json and per-model symlinks."""
        # Compute relative path from base_dir to run_path
        rel = os.path.relpath(run_path, self.base_dir)

        # latest.json
        latest = self.base_dir / "latest.json"
        if latest.exists() or latest.is_symlink():
            latest.unlink()
        latest.symlink_to(rel)

        # Per-model symlinks
        for model in models:
            link = self.base_dir / f"latest_{model}.json"
            if link.exists() or link.is_symlink():
                link.unlink()
            link.symlink_to(rel)

    def _append_index(self, run_path: Path, run_data: dict) -> None:
        """Append a one-line entry to runs/runs.jsonl."""
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        index_path = self.runs_dir / "runs.jsonl"
        entry = {
            "timestamp": run_data["timestamp"],
            "run_name": run_data.get("run_name", ""),
            "path": str(run_path.relative_to(self.base_dir)),
            "models": run_data["summary"]["models"],
            "grades": run_data["summary"]["grades"],
            "scores": run_data["summary"]["scores"],
        }
        with open(index_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def load_run(self, path: Path) -> Optional[dict]:
        """Load a run from a JSON file."""
        path = Path(path)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def load_latest(self, model: Optional[str] = None) -> Optional[dict]:
        """Load the latest run. If model specified, use per-model symlink."""
        if model:
            link = self.base_dir / f"latest_{model}.json"
        else:
            link = self.base_dir / "latest.json"
        if not link.exists():
            return None
        return self.load_run(link)

    def list_runs(self, limit: int = 20) -> list[dict]:
        """List runs from the JSONL index, newest first."""
        index_path = self.runs_dir / "runs.jsonl"
        if not index_path.exists():
            return []
        lines = index_path.read_text().strip().splitlines()
        entries = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(entries) >= limit:
                break
        return entries


# ── Regression detection ─────────────────────────────────────────────────────

GRADE_ORDER = {"S": 6, "A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def detect_regressions(
    current_results: list[dict],
    previous_data: dict,
) -> list[RegressionAlert]:
    """Compare current results vs previous run, return regression alerts.

    Args:
        current_results: list of ModelBenchmark.to_dict() dicts (current run)
        previous_data: full run dict from a previous save (schema v1 or v2)
    """
    alerts: list[RegressionAlert] = []
    prev_by_model = {
        r.get("model_alias", ""): r
        for r in previous_data.get("results", [])
    }

    for cur in current_results:
        model = cur.get("model_alias", "?")
        prev = prev_by_model.get(model)
        if not prev:
            continue

        # Grade drop
        cur_grade = cur.get("grade", "F")
        prev_grade = prev.get("grade", "F")
        cur_rank = GRADE_ORDER.get(cur_grade, 0)
        prev_rank = GRADE_ORDER.get(prev_grade, 0)
        if cur_rank < prev_rank:
            severity = "critical" if (prev_rank - cur_rank) >= 2 else "warning"
            alerts.append(RegressionAlert(
                model=model, dimension="grade",
                old_value=prev_grade, new_value=cur_grade,
                severity=severity,
            ))

        # Score drop > 5%
        cur_score = cur.get("overall_score", 0)
        prev_score = prev.get("overall_score", 0)
        if prev_score - cur_score > 5:
            alerts.append(RegressionAlert(
                model=model, dimension="score",
                old_value=f"{prev_score:.1f}%", new_value=f"{cur_score:.1f}%",
                severity="warning",
            ))

        # Per-dimension drops > 10%
        cur_dims = cur.get("dimension_scores", {})
        prev_dims = prev.get("dimension_scores", {})
        for dim in cur_dims:
            if dim in prev_dims:
                delta = prev_dims[dim] - cur_dims[dim]
                if delta > 10:
                    alerts.append(RegressionAlert(
                        model=model, dimension=dim,
                        old_value=f"{prev_dims[dim]:.1f}%",
                        new_value=f"{cur_dims[dim]:.1f}%",
                        severity="warning",
                    ))

        # Server flip (was OK, now not)
        if prev.get("server_ok") and not cur.get("server_ok"):
            alerts.append(RegressionAlert(
                model=model, dimension="server",
                old_value="OK", new_value="FAIL",
                severity="critical",
            ))

    return alerts


# ── Check-level diffs ────────────────────────────────────────────────────────

def diff_checks(
    current_results: list[dict],
    previous_data: dict,
) -> list[CheckDiff]:
    """Find per-check state changes (pass↔fail) between two runs."""
    diffs: list[CheckDiff] = []
    prev_by_model = {
        r.get("model_alias", ""): r
        for r in previous_data.get("results", [])
    }

    for cur in current_results:
        model = cur.get("model_alias", "?")
        prev = prev_by_model.get(model)
        if not prev:
            continue

        # Build lookup for previous checks
        prev_checks = {
            c["name"]: c for c in prev.get("checks", [])
        }

        for check in cur.get("checks", []):
            name = check["name"]
            prev_check = prev_checks.get(name)
            if prev_check is None:
                continue
            if check["passed"] != prev_check["passed"]:
                diffs.append(CheckDiff(
                    check_name=name,
                    model=model,
                    old_state=prev_check["passed"],
                    new_state=check["passed"],
                    dimension=check.get("dimension", ""),
                ))

    return diffs


# ── Optimization hints ───────────────────────────────────────────────────────

# Maps dimension → (threshold, suggestion, relevant files)
_HINT_MAP = {
    "task_completion": (
        70,
        "Files aren't being created reliably. Check prompt_builder.py's "
        "task-scoped 'Your Files' section and forge_agent.py's write_file tool. "
        "Ensure the spec clearly lists expected files.",
        ["prompt_builder.py", "forge_agent.py"],
    ),
    "code_quality": (
        70,
        "Generated code has quality issues (syntax errors, wrong patterns, stubs). "
        "Review system prompt in prompt_builder.py for clearer coding instructions. "
        "Check forge_agent.py retry logic for stub detection.",
        ["prompt_builder.py", "forge_agent.py"],
    ),
    "interface_fidelity": (
        60,
        "Models are hallucinating APIs or mismatching imports/exports. "
        "Strengthen mandatory read instructions in benchmark task prompts. "
        "Check upstream artifact handoff in forge_cli.py _gather_upstream_artifacts.",
        ["prompt_builder.py", "forge_cli.py"],
    ),
    "runtime_viability": (
        60,
        "Generated code doesn't run. Check if models are creating proper Flask app "
        "structure. Review forge_preview.py detect_stack for detection accuracy. "
        "Consider adding a syntax-check retry step.",
        ["forge_preview.py", "forge_agent.py", "forge_verify.py"],
    ),
    "efficiency": (
        60,
        "Too many turns, retries, or tokens. Check context window settings in "
        "config.py, ensure SLIM_TOOLS is used for small-context models, and review "
        "chunk_hint guidance for output limits.",
        ["config.py", "forge_agent.py", "forge_models.py"],
    ),
}


def generate_optimization_hints(results: list[dict]) -> list[OptimizationHint]:
    """Map dimension scores to actionable tuning suggestions."""
    hints: list[OptimizationHint] = []

    # Aggregate scores across all models (use worst score per dimension)
    worst_by_dim: dict[str, tuple[float, str]] = {}
    for r in results:
        dims = r.get("dimension_scores", {})
        model = r.get("model_alias", "?")
        for dim, score in dims.items():
            if dim not in worst_by_dim or score < worst_by_dim[dim][0]:
                worst_by_dim[dim] = (score, model)

    for dim, (threshold, suggestion, files) in _HINT_MAP.items():
        if dim in worst_by_dim:
            score, model = worst_by_dim[dim]
            if score < threshold:
                hints.append(OptimizationHint(
                    dimension=dim,
                    score=score,
                    suggestion=f"[{model}] {suggestion}",
                    files=files,
                ))

    return hints


# ── Changelog ────────────────────────────────────────────────────────────────

def append_changelog(
    base_dir: Path,
    run_data: dict,
    previous: Optional[dict],
    regressions: list[RegressionAlert],
) -> None:
    """Prepend an entry to CHANGELOG.md."""
    changelog_path = Path(base_dir) / "CHANGELOG.md"

    ts = run_data.get("timestamp", datetime.now().isoformat())
    summary = run_data.get("summary", {})
    metadata = run_data.get("metadata", {})
    run_name = run_data.get("run_name", "")

    # Build entry
    lines = []
    date_str = ts[:10] if len(ts) >= 10 else ts
    title = f"## {date_str}"
    if run_name:
        title += f" — {run_name}"
    lines.append(title)
    lines.append("")

    # Grades
    grades = summary.get("grades", {})
    scores = summary.get("scores", {})
    grade_parts = []
    for model in summary.get("models", []):
        g = grades.get(model, "?")
        s = scores.get(model, 0)
        grade_parts.append(f"**{model}**: {g} ({s:.0f}%)")
    if grade_parts:
        lines.append("| " + " | ".join(grade_parts) + " |")
        lines.append("")

    # Git context
    commit = metadata.get("git_commit", "")
    branch = metadata.get("git_branch", "")
    if commit:
        lines.append(f"Git: `{commit}` on `{branch}`" +
                      (" (dirty)" if metadata.get("git_dirty") else ""))

    # Trigger
    trigger = metadata.get("trigger", "")
    if trigger:
        lines.append(f"Trigger: {trigger}")

    # Duration + cost
    dur = summary.get("duration_total", 0)
    cost = summary.get("cost_total", 0)
    lines.append(f"Duration: {dur:.0f}s | Cost: ${cost:.4f}")

    # Regressions
    if regressions:
        lines.append("")
        lines.append("**Regressions:**")
        for r in regressions:
            icon = "!!!" if r.severity == "critical" else "!"
            lines.append(f"- {icon} {r.model} {r.dimension}: {r.old_value} -> {r.new_value}")

    # Deltas from previous
    if previous:
        prev_summary = previous.get("summary", {})
        prev_grades = prev_summary.get("grades", {})
        delta_parts = []
        for model in summary.get("models", []):
            pg = prev_grades.get(model)
            cg = grades.get(model, "?")
            if pg and pg != cg:
                delta_parts.append(f"{model}: {pg} -> {cg}")
        if delta_parts:
            lines.append("")
            lines.append("**Deltas:** " + ", ".join(delta_parts))

    lines.append("")
    lines.append("---")
    lines.append("")

    new_entry = "\n".join(lines)

    # Read existing content
    existing = ""
    header = "# Benchmark Changelog\n\n"
    if changelog_path.exists():
        existing = changelog_path.read_text()
        # Strip header if present
        if existing.startswith("# Benchmark Changelog"):
            header_end = existing.index("\n\n") + 2 if "\n\n" in existing else len(existing)
            existing = existing[header_end:]

    changelog_path.write_text(header + new_entry + existing)


# ── History formatting ───────────────────────────────────────────────────────

def format_history(
    base_dir: Path,
    model_filter: Optional[str] = None,
    limit: int = 20,
) -> str:
    """Render a grade/score trend table from the run index."""
    store = BenchmarkStore(Path(base_dir))
    runs = store.list_runs(limit=limit)

    if not runs:
        return "  No benchmark history found."

    # Collect all models seen
    all_models: list[str] = []
    for entry in runs:
        for m in entry.get("models", []):
            if m not in all_models:
                all_models.append(m)

    if model_filter:
        all_models = [m for m in all_models if m == model_filter]
        if not all_models:
            return f"  No history for model '{model_filter}'."

    # Build table
    lines = []
    lines.append("")

    # Header
    header = f"  {'Date':12s} {'Name':20s}"
    for m in all_models:
        header += f"  {m:>14s}"
    lines.append(header)
    lines.append("  " + "-" * (12 + 20 + 16 * len(all_models)))

    # Rows (newest first)
    for entry in runs:
        ts = entry.get("timestamp", "?")
        date_str = ts[:10] if len(ts) >= 10 else ts
        name = entry.get("run_name", "")[:18]
        grades = entry.get("grades", {})
        scores = entry.get("scores", {})

        row = f"  {date_str:12s} {name:20s}"
        for m in all_models:
            g = grades.get(m, "-")
            s = scores.get(m, 0)
            if g != "-":
                row += f"  {g:>2s} {s:>5.0f}%    "
            else:
                row += f"  {'--':>14s}"
        lines.append(row)

    lines.append("")
    lines.append(f"  {len(runs)} runs shown (limit {limit})")
    return "\n".join(lines)
