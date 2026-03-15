"""Tests for benchmarks/benchmark_store.py — storage, regression detection, hints."""

import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from benchmarks.benchmark_store import (
    BenchmarkStore,
    CheckDiff,
    OptimizationHint,
    RegressionAlert,
    RunMetadata,
    append_changelog,
    collect_metadata,
    detect_regressions,
    diff_checks,
    format_history,
    generate_optimization_hints,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_check(name: str, dimension: str = "task_completion", passed: bool = True) -> dict:
    """Build a single check dict matching benchmark check format."""
    return {"name": name, "dimension": dimension, "passed": passed}


def _make_result(
    model: str = "nova-lite",
    grade: str = "A",
    score: float = 88.0,
    server_ok: bool = True,
    checks: list[dict] | None = None,
    dims: dict | None = None,
) -> dict:
    """Build a dict matching ModelBenchmark.to_dict() format."""
    return {
        "model_alias": model,
        "grade": grade,
        "overall_score": score,
        "server_ok": server_ok,
        "duration_secs": 60.0,
        "total_cost": 0.005,
        "checks": checks or [
            _make_check("file_exists", "task_completion", True),
            _make_check("syntax_ok", "code_quality", True),
        ],
        "dimension_scores": dims or {
            "task_completion": 90.0,
            "code_quality": 85.0,
            "runtime_viability": 80.0,
        },
    }


def _make_run_data(results: list[dict], schema_version: int = 2) -> dict:
    """Build a dict matching BenchmarkStore save format."""
    models = [r.get("model_alias", "?") for r in results]
    grades = {r["model_alias"]: r["grade"] for r in results}
    scores = {r["model_alias"]: r["overall_score"] for r in results}
    data = {
        "timestamp": "2026-03-12T10:00:00",
        "run_name": "test-run",
        "metadata": {
            "git_commit": "abc1234",
            "git_branch": "main",
            "git_dirty": False,
        },
        "summary": {
            "models": models,
            "grades": grades,
            "scores": scores,
            "duration_total": 60.0,
            "cost_total": 0.005,
        },
        "results": results,
    }
    if schema_version >= 2:
        data["schema_version"] = schema_version
    return data


# ── TestCollectMetadata ──────────────────────────────────────────────────────


class TestCollectMetadata:
    @patch("benchmarks.benchmark_store.subprocess.run")
    def test_collect_metadata_returns_dataclass(self, mock_run):
        """Verify fields populated, python_version present."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "abc1234"
        mock_run.return_value = mock_result

        meta = collect_metadata()
        assert isinstance(meta, RunMetadata)
        vi = sys.version_info
        assert meta.python_version == f"{vi.major}.{vi.minor}.{vi.micro}"
        assert meta.platform != ""

    @patch("benchmarks.benchmark_store.subprocess.run")
    def test_collect_metadata_with_hashes(self, mock_run):
        """spec_hash and system_prompt_hash computed from inputs."""
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_run.return_value = mock_result

        meta = collect_metadata(
            spec_text="Build an expense tracker",
            system_prompt="You are a coding agent",
        )
        assert len(meta.spec_hash) == 12
        assert len(meta.system_prompt_hash) == 12
        # Different inputs produce different hashes
        meta2 = collect_metadata(
            spec_text="Different spec",
            system_prompt="Different prompt",
        )
        assert meta2.spec_hash != meta.spec_hash
        assert meta2.system_prompt_hash != meta.system_prompt_hash


# ── TestBenchmarkStore ───────────────────────────────────────────────────────


class TestBenchmarkStore:
    def test_save_run_creates_directory_structure(self, tmp_path):
        """runs/ subdir, monthly dir, JSON written."""
        store = BenchmarkStore(tmp_path)
        results = [_make_result()]
        meta = RunMetadata(python_version="3.11.0")

        path = store.save_run(results, meta)
        assert path.exists()
        assert path.suffix == ".json"
        # Monthly directory exists under runs/
        assert "runs" in str(path.parent.parent.name) or "runs" in str(path.relative_to(tmp_path))
        assert (tmp_path / "runs").is_dir()

    def test_save_run_updates_symlinks(self, tmp_path):
        """latest.json + per-model symlinks point to correct file."""
        store = BenchmarkStore(tmp_path)
        results = [_make_result(model="nova-lite")]
        meta = RunMetadata()

        run_path = store.save_run(results, meta)

        latest = tmp_path / "latest.json"
        assert latest.is_symlink()
        assert latest.resolve() == run_path.resolve()

        model_link = tmp_path / "latest_nova-lite.json"
        assert model_link.is_symlink()
        assert model_link.resolve() == run_path.resolve()

    def test_save_run_appends_to_index(self, tmp_path):
        """runs.jsonl gets new entry line."""
        store = BenchmarkStore(tmp_path)
        results = [_make_result()]
        meta = RunMetadata()

        store.save_run(results, meta)
        store.save_run(results, meta, run_name="second")

        index_path = tmp_path / "runs" / "runs.jsonl"
        assert index_path.exists()
        lines = index_path.read_text().strip().splitlines()
        assert len(lines) == 2
        entry = json.loads(lines[1])
        assert "nova-lite" in entry["models"]

    def test_load_latest(self, tmp_path):
        """Roundtrip save -> load_latest."""
        store = BenchmarkStore(tmp_path)
        results = [_make_result(model="nova-lite", grade="A", score=92.0)]
        meta = RunMetadata()

        store.save_run(results, meta)
        loaded = store.load_latest()

        assert loaded is not None
        assert loaded["schema_version"] == 2
        assert loaded["results"][0]["model_alias"] == "nova-lite"
        assert loaded["results"][0]["grade"] == "A"

    def test_load_latest_model(self, tmp_path):
        """Per-model symlink load."""
        store = BenchmarkStore(tmp_path)
        results = [
            _make_result(model="nova-lite", grade="A"),
            _make_result(model="nova-pro", grade="B"),
        ]
        meta = RunMetadata()
        store.save_run(results, meta)

        loaded = store.load_latest(model="nova-pro")
        assert loaded is not None
        # Should contain both models (same run file)
        aliases = [r["model_alias"] for r in loaded["results"]]
        assert "nova-pro" in aliases

    def test_list_runs(self, tmp_path):
        """Returns correct count and newest-first order."""
        store = BenchmarkStore(tmp_path)
        meta = RunMetadata()

        store.save_run([_make_result(score=80.0)], meta, run_name="first")
        store.save_run([_make_result(score=90.0)], meta, run_name="second")
        store.save_run([_make_result(score=95.0)], meta, run_name="third")

        runs = store.list_runs()
        assert len(runs) == 3
        # Newest first
        assert runs[0]["run_name"] == "third"
        assert runs[2]["run_name"] == "first"

    def test_schema_v2_format(self, tmp_path):
        """Saved JSON has all required v2 fields."""
        store = BenchmarkStore(tmp_path)
        results = [_make_result()]
        meta = RunMetadata(git_commit="abc", python_version="3.11.0")

        path = store.save_run(results, meta)
        data = json.loads(path.read_text())

        assert data["schema_version"] == 2
        assert "metadata" in data
        assert "summary" in data
        assert "results" in data
        assert "timestamp" in data
        assert "git_commit" in data["metadata"]

    def test_backward_compat_v1(self, tmp_path):
        """Can load old v1 format (no schema_version field)."""
        store = BenchmarkStore(tmp_path)
        # Manually write a v1 file (no schema_version)
        v1_data = _make_run_data([_make_result()], schema_version=1)
        # v1 has no schema_version key
        assert "schema_version" not in v1_data

        v1_path = tmp_path / "v1_run.json"
        v1_path.write_text(json.dumps(v1_data))

        loaded = store.load_run(v1_path)
        assert loaded is not None
        assert "results" in loaded
        assert len(loaded["results"]) == 1


# ── TestRegressionDetection ──────────────────────────────────────────────────


class TestRegressionDetection:
    def test_no_regressions(self):
        """Identical results produce empty list."""
        results = [_make_result(model="nova-lite", grade="A", score=88.0)]
        prev = _make_run_data(results)
        alerts = detect_regressions(results, prev)
        assert alerts == []

    def test_grade_drop_detected(self):
        """A->B triggers warning alert."""
        current = [_make_result(model="nova-lite", grade="B", score=78.0)]
        prev = _make_run_data([_make_result(model="nova-lite", grade="A", score=88.0)])

        alerts = detect_regressions(current, prev)
        grade_alerts = [a for a in alerts if a.dimension == "grade"]
        assert len(grade_alerts) == 1
        assert grade_alerts[0].severity == "warning"
        assert grade_alerts[0].old_value == "A"
        assert grade_alerts[0].new_value == "B"

    def test_grade_drop_critical(self):
        """A->D triggers critical alert (drop >= 2 ranks)."""
        current = [_make_result(model="nova-lite", grade="D", score=40.0)]
        prev = _make_run_data([_make_result(model="nova-lite", grade="A", score=88.0)])

        alerts = detect_regressions(current, prev)
        grade_alerts = [a for a in alerts if a.dimension == "grade"]
        assert len(grade_alerts) == 1
        assert grade_alerts[0].severity == "critical"

    def test_score_drop_detected(self):
        """>5% drop triggers alert."""
        current = [_make_result(model="nova-lite", grade="A", score=80.0)]
        prev = _make_run_data([_make_result(model="nova-lite", grade="A", score=90.0)])

        alerts = detect_regressions(current, prev)
        score_alerts = [a for a in alerts if a.dimension == "score"]
        assert len(score_alerts) == 1
        assert score_alerts[0].severity == "warning"

    def test_server_flip_detected(self):
        """server_ok True->False triggers critical alert."""
        current = [_make_result(model="nova-lite", server_ok=False)]
        prev = _make_run_data([_make_result(model="nova-lite", server_ok=True)])

        alerts = detect_regressions(current, prev)
        server_alerts = [a for a in alerts if a.dimension == "server"]
        assert len(server_alerts) == 1
        assert server_alerts[0].severity == "critical"
        assert server_alerts[0].new_value == "FAIL"

    def test_no_alert_on_improvement(self):
        """B->A doesn't trigger regression."""
        current = [_make_result(model="nova-lite", grade="A", score=92.0)]
        prev = _make_run_data([_make_result(model="nova-lite", grade="B", score=78.0)])

        alerts = detect_regressions(current, prev)
        # No grade or score regressions (score improved)
        assert alerts == []


# ── TestCheckDiff ────────────────────────────────────────────────────────────


class TestCheckDiff:
    def test_no_changes(self):
        """Identical checks produce empty diffs."""
        checks = [_make_check("file_exists", "task_completion", True)]
        current = [_make_result(model="nova-lite", checks=checks)]
        prev = _make_run_data([_make_result(model="nova-lite", checks=checks)])

        diffs = diff_checks(current, prev)
        assert diffs == []

    def test_pass_to_fail(self):
        """Detected as regression."""
        cur_checks = [_make_check("syntax_ok", "code_quality", False)]
        prev_checks = [_make_check("syntax_ok", "code_quality", True)]

        current = [_make_result(model="nova-lite", checks=cur_checks)]
        prev = _make_run_data([_make_result(model="nova-lite", checks=prev_checks)])

        diffs = diff_checks(current, prev)
        assert len(diffs) == 1
        assert diffs[0].old_state is True
        assert diffs[0].new_state is False
        assert diffs[0].check_name == "syntax_ok"

    def test_fail_to_pass(self):
        """Detected as improvement."""
        cur_checks = [_make_check("syntax_ok", "code_quality", True)]
        prev_checks = [_make_check("syntax_ok", "code_quality", False)]

        current = [_make_result(model="nova-lite", checks=cur_checks)]
        prev = _make_run_data([_make_result(model="nova-lite", checks=prev_checks)])

        diffs = diff_checks(current, prev)
        assert len(diffs) == 1
        assert diffs[0].old_state is False
        assert diffs[0].new_state is True


# ── TestOptimizationHints ────────────────────────────────────────────────────


class TestOptimizationHints:
    def test_low_score_generates_hint(self):
        """Dimension score <70 generates a hint with suggestion and files."""
        results = [_make_result(
            model="nova-lite",
            dims={"task_completion": 50.0, "code_quality": 90.0, "runtime_viability": 90.0},
        )]
        hints = generate_optimization_hints(results)
        assert len(hints) >= 1
        tc_hints = [h for h in hints if h.dimension == "task_completion"]
        assert len(tc_hints) == 1
        assert tc_hints[0].score == 50.0
        assert "nova-lite" in tc_hints[0].suggestion

    def test_high_score_no_hint(self):
        """All scores >85 produces empty hints."""
        results = [_make_result(
            model="nova-lite",
            dims={"task_completion": 90.0, "code_quality": 90.0, "runtime_viability": 90.0, "efficiency": 90.0, "interface_fidelity": 90.0},
        )]
        hints = generate_optimization_hints(results)
        assert hints == []

    def test_hints_include_file_paths(self):
        """Each hint has non-empty files list."""
        results = [_make_result(
            model="nova-lite",
            dims={"task_completion": 30.0, "code_quality": 40.0, "runtime_viability": 20.0, "efficiency": 10.0, "interface_fidelity": 15.0},
        )]
        hints = generate_optimization_hints(results)
        assert len(hints) > 0
        for hint in hints:
            assert len(hint.files) > 0
            assert all(isinstance(f, str) and f.endswith(".py") for f in hint.files)


# ── TestChangelog ────────────────────────────────────────────────────────────


class TestChangelog:
    def test_append_creates_file(self, tmp_path):
        """First run creates CHANGELOG.md with header."""
        run_data = _make_run_data([_make_result()])
        append_changelog(tmp_path, run_data, previous=None, regressions=[])

        cl = tmp_path / "CHANGELOG.md"
        assert cl.exists()
        content = cl.read_text()
        assert "# Benchmark Changelog" in content
        assert "2026-03-12" in content

    def test_append_prepends(self, tmp_path):
        """Second entry appears before first."""
        run1 = _make_run_data([_make_result()])
        run1["timestamp"] = "2026-03-10T10:00:00"
        run1["run_name"] = "first-run"
        append_changelog(tmp_path, run1, previous=None, regressions=[])

        run2 = _make_run_data([_make_result()])
        run2["timestamp"] = "2026-03-12T10:00:00"
        run2["run_name"] = "second-run"
        append_changelog(tmp_path, run2, previous=run1, regressions=[])

        content = (tmp_path / "CHANGELOG.md").read_text()
        pos_second = content.index("second-run")
        pos_first = content.index("first-run")
        assert pos_second < pos_first, "Second entry should appear before first (prepended)"


# ── TestFormatHistory ────────────────────────────────────────────────────────


class TestFormatHistory:
    def test_renders_table(self, tmp_path):
        """Output contains model names and grades."""
        store = BenchmarkStore(tmp_path)
        meta = RunMetadata()
        store.save_run(
            [_make_result(model="nova-lite", grade="A", score=92.0)],
            meta,
            run_name="test-run",
        )

        output = format_history(tmp_path)
        assert "nova-lite" in output
        assert "A" in output

    def test_empty_history(self, tmp_path):
        """No runs produces appropriate message."""
        output = format_history(tmp_path)
        assert "No benchmark history found" in output

    def test_model_filter(self, tmp_path):
        """Only shows filtered model."""
        store = BenchmarkStore(tmp_path)
        meta = RunMetadata()
        store.save_run(
            [
                _make_result(model="nova-lite", grade="A"),
                _make_result(model="nova-pro", grade="B"),
            ],
            meta,
        )

        output = format_history(tmp_path, model_filter="nova-lite")
        assert "nova-lite" in output
        # nova-pro should not appear as a column header
        # (it may appear in the data row if both are in the same run,
        # but the column should be filtered)
        lines = output.splitlines()
        header_line = [l for l in lines if "nova-lite" in l and "Date" in l]
        if header_line:
            assert "nova-pro" not in header_line[0]

    def test_model_filter_nonexistent(self, tmp_path):
        """Filtering by a model not present returns appropriate message."""
        store = BenchmarkStore(tmp_path)
        meta = RunMetadata()
        store.save_run([_make_result(model="nova-lite", grade="A")], meta)

        output = format_history(tmp_path, model_filter="nova-premier")
        assert "No history for model" in output
        assert "nova-premier" in output


# ── TestRegressionDetection — edge cases ──────────────────────────────────


class TestRegressionEdgeCases:
    def test_dimension_drop_detected(self):
        """>10% dimension score drop triggers an alert."""
        cur_dims = {"task_completion": 60.0, "code_quality": 85.0}
        prev_dims = {"task_completion": 90.0, "code_quality": 85.0}
        current = [_make_result(model="nova-lite", dims=cur_dims)]
        prev = _make_run_data([_make_result(model="nova-lite", dims=prev_dims)])

        alerts = detect_regressions(current, prev)
        dim_alerts = [a for a in alerts if a.dimension == "task_completion"]
        assert len(dim_alerts) == 1
        assert dim_alerts[0].severity == "warning"
        assert "90.0" in dim_alerts[0].old_value
        assert "60.0" in dim_alerts[0].new_value

    def test_dimension_small_drop_no_alert(self):
        """<=10% dimension score drop does NOT trigger alert."""
        cur_dims = {"task_completion": 82.0, "code_quality": 85.0}
        prev_dims = {"task_completion": 90.0, "code_quality": 85.0}
        current = [_make_result(model="nova-lite", dims=cur_dims)]
        prev = _make_run_data([_make_result(model="nova-lite", dims=prev_dims)])

        alerts = detect_regressions(current, prev)
        dim_alerts = [a for a in alerts if a.dimension == "task_completion"]
        assert len(dim_alerts) == 0

    def test_multi_model_independent_regressions(self):
        """Regressions for each model are detected independently."""
        current = [
            _make_result(model="nova-lite", grade="B", score=78.0),
            _make_result(model="nova-pro", grade="D", score=40.0),
        ]
        prev = _make_run_data([
            _make_result(model="nova-lite", grade="A", score=88.0),
            _make_result(model="nova-pro", grade="A", score=90.0),
        ])

        alerts = detect_regressions(current, prev)
        lite_alerts = [a for a in alerts if a.model == "nova-lite" and a.dimension == "grade"]
        pro_alerts = [a for a in alerts if a.model == "nova-pro" and a.dimension == "grade"]
        assert len(lite_alerts) == 1
        assert lite_alerts[0].severity == "warning"
        assert len(pro_alerts) == 1
        assert pro_alerts[0].severity == "critical"

    def test_new_model_no_alert(self):
        """A model in current but not in previous produces no alert."""
        current = [_make_result(model="nova-premier", grade="A", score=95.0)]
        prev = _make_run_data([_make_result(model="nova-lite", grade="A", score=88.0)])

        alerts = detect_regressions(current, prev)
        assert alerts == []

    def test_empty_results_no_crash(self):
        """Empty current results produce no alerts and no crash."""
        prev = _make_run_data([_make_result()])
        alerts = detect_regressions([], prev)
        assert alerts == []

    def test_server_ok_to_ok_no_alert(self):
        """Server staying OK produces no server alert."""
        current = [_make_result(model="nova-lite", server_ok=True)]
        prev = _make_run_data([_make_result(model="nova-lite", server_ok=True)])
        alerts = detect_regressions(current, prev)
        server_alerts = [a for a in alerts if a.dimension == "server"]
        assert len(server_alerts) == 0

    def test_score_exactly_5_no_alert(self):
        """Score drop of exactly 5% does NOT trigger alert (needs >5)."""
        current = [_make_result(model="nova-lite", grade="A", score=85.0)]
        prev = _make_run_data([_make_result(model="nova-lite", grade="A", score=90.0)])
        alerts = detect_regressions(current, prev)
        score_alerts = [a for a in alerts if a.dimension == "score"]
        assert len(score_alerts) == 0


# ── TestCheckDiff — edge cases ────────────────────────────────────────────


class TestCheckDiffEdgeCases:
    def test_new_check_no_diff(self):
        """A check present in current but not previous is not a diff."""
        cur_checks = [
            _make_check("syntax_ok", "code_quality", True),
            _make_check("new_check", "task_completion", True),
        ]
        prev_checks = [_make_check("syntax_ok", "code_quality", True)]

        current = [_make_result(model="nova-lite", checks=cur_checks)]
        prev = _make_run_data([_make_result(model="nova-lite", checks=prev_checks)])

        diffs = diff_checks(current, prev)
        assert len(diffs) == 0

    def test_multi_model_diffs(self):
        """Check diffs are tracked per model."""
        cur = [
            _make_result(model="nova-lite", checks=[_make_check("syntax_ok", "code_quality", False)]),
            _make_result(model="nova-pro", checks=[_make_check("syntax_ok", "code_quality", True)]),
        ]
        prev = _make_run_data([
            _make_result(model="nova-lite", checks=[_make_check("syntax_ok", "code_quality", True)]),
            _make_result(model="nova-pro", checks=[_make_check("syntax_ok", "code_quality", False)]),
        ])

        diffs = diff_checks(cur, prev)
        assert len(diffs) == 2
        lite_diff = [d for d in diffs if d.model == "nova-lite"][0]
        pro_diff = [d for d in diffs if d.model == "nova-pro"][0]
        assert lite_diff.old_state is True and lite_diff.new_state is False
        assert pro_diff.old_state is False and pro_diff.new_state is True

    def test_diff_preserves_dimension(self):
        """CheckDiff.dimension is populated from check data."""
        cur_checks = [_make_check("file_exists", "task_completion", False)]
        prev_checks = [_make_check("file_exists", "task_completion", True)]
        current = [_make_result(model="nova-lite", checks=cur_checks)]
        prev = _make_run_data([_make_result(model="nova-lite", checks=prev_checks)])

        diffs = diff_checks(current, prev)
        assert len(diffs) == 1
        assert diffs[0].dimension == "task_completion"


# ── TestOptimizationHints — edge cases ────────────────────────────────────


class TestOptimizationHintEdgeCases:
    def test_uses_worst_score_across_models(self):
        """When multiple models have the same dimension, worst score triggers hint."""
        results = [
            _make_result(model="nova-lite", dims={"task_completion": 90.0, "code_quality": 90.0}),
            _make_result(model="nova-pro", dims={"task_completion": 50.0, "code_quality": 90.0}),
        ]
        hints = generate_optimization_hints(results)
        tc_hints = [h for h in hints if h.dimension == "task_completion"]
        assert len(tc_hints) == 1
        assert tc_hints[0].score == 50.0
        assert "nova-pro" in tc_hints[0].suggestion

    def test_empty_results_no_crash(self):
        """Empty results produce no hints."""
        hints = generate_optimization_hints([])
        assert hints == []

    def test_missing_dimension_scores_no_crash(self):
        """Results without dimension_scores key produce no hints."""
        results = [{"model_alias": "nova-lite", "grade": "A", "overall_score": 90.0}]
        hints = generate_optimization_hints(results)
        assert hints == []

    def test_hint_threshold_boundary(self):
        """Score exactly at threshold does NOT generate hint."""
        results = [_make_result(
            model="nova-lite",
            dims={"task_completion": 70.0, "code_quality": 70.0, "runtime_viability": 60.0, "efficiency": 60.0, "interface_fidelity": 60.0},
        )]
        hints = generate_optimization_hints(results)
        # All scores are AT threshold, not below
        assert hints == []


# ── TestChangelog — edge cases ────────────────────────────────────────────


class TestChangelogEdgeCases:
    def test_changelog_includes_regressions(self, tmp_path):
        """Regressions are listed in the changelog entry."""
        run_data = _make_run_data([_make_result(model="nova-lite", grade="B")])
        regressions = [RegressionAlert(
            model="nova-lite", dimension="grade",
            old_value="A", new_value="B", severity="warning",
        )]
        append_changelog(tmp_path, run_data, previous=None, regressions=regressions)
        content = (tmp_path / "CHANGELOG.md").read_text()
        assert "Regressions" in content
        assert "nova-lite grade" in content
        assert "A -> B" in content

    def test_changelog_includes_deltas(self, tmp_path):
        """Grade deltas vs previous run are shown."""
        run1 = _make_run_data([_make_result(model="nova-lite", grade="B")])
        append_changelog(tmp_path, run1, previous=None, regressions=[])

        run2 = _make_run_data([_make_result(model="nova-lite", grade="A")])
        append_changelog(tmp_path, run2, previous=run1, regressions=[])
        content = (tmp_path / "CHANGELOG.md").read_text()
        assert "Deltas" in content
        assert "B -> A" in content


# ── TestBenchmarkStore — edge cases ───────────────────────────────────────


class TestBenchmarkStoreEdgeCases:
    def test_load_latest_no_runs(self, tmp_path):
        """load_latest returns None when no runs exist."""
        store = BenchmarkStore(tmp_path)
        assert store.load_latest() is None

    def test_load_latest_model_no_runs(self, tmp_path):
        """load_latest with model filter returns None when no runs exist."""
        store = BenchmarkStore(tmp_path)
        assert store.load_latest(model="nova-pro") is None

    def test_load_run_nonexistent_path(self, tmp_path):
        """load_run on a missing path returns None."""
        store = BenchmarkStore(tmp_path)
        assert store.load_run(tmp_path / "does_not_exist.json") is None

    def test_load_run_invalid_json(self, tmp_path):
        """load_run on invalid JSON returns None."""
        store = BenchmarkStore(tmp_path)
        bad = tmp_path / "bad.json"
        bad.write_text("{invalid json...")
        assert store.load_run(bad) is None

    def test_list_runs_empty(self, tmp_path):
        """list_runs returns empty list when no index exists."""
        store = BenchmarkStore(tmp_path)
        assert store.list_runs() == []

    def test_list_runs_respects_limit(self, tmp_path):
        """list_runs with limit=2 only returns 2 even if more exist."""
        store = BenchmarkStore(tmp_path)
        meta = RunMetadata()
        for i in range(5):
            store.save_run([_make_result(score=80.0 + i)], meta, run_name=f"run-{i}")

        runs = store.list_runs(limit=2)
        assert len(runs) == 2

    def test_save_run_metadata_roundtrip(self, tmp_path):
        """Metadata fields survive save + load roundtrip."""
        store = BenchmarkStore(tmp_path)
        meta = RunMetadata(
            git_commit="abc1234",
            git_branch="feature/test",
            git_dirty=True,
            python_version="3.11.0",
            platform="Linux",
            spec_hash="aabbccddee12",
        )
        path = store.save_run([_make_result()], meta)
        data = json.loads(path.read_text())
        assert data["metadata"]["git_commit"] == "abc1234"
        assert data["metadata"]["git_branch"] == "feature/test"
        assert data["metadata"]["git_dirty"] is True
        assert data["metadata"]["spec_hash"] == "aabbccddee12"
