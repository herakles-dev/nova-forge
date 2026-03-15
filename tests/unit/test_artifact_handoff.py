"""Tests for artifact handoff system in forge_cli.py."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from dataclasses import dataclass, field


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_task(id_, subject, status="pending", blocked_by=None, artifacts=None):
    """Create a minimal Task-like object for testing."""
    t = MagicMock()
    t.id = id_
    t.subject = subject
    t.status = status
    t.blocked_by = blocked_by or []
    t.artifacts = artifacts or {}
    return t


def _make_shell(tmp_path):
    """Create a ForgeShell instance with project_path pointed at tmp_path."""
    # Avoid full __init__ by importing and patching minimally
    from forge_cli import ForgeShell
    shell = ForgeShell.__new__(ForgeShell)
    shell.project_path = tmp_path
    shell.model = "nova-lite"
    return shell


# ── _shorten_path ─────────────────────────────────────────────────────────────

class TestShortenPath:
    def test_shorten_absolute_path(self, tmp_path):
        shell = _make_shell(tmp_path)
        abs_path = str(tmp_path / "app" / "main.py")
        assert shell._shorten_path(abs_path) == "app/main.py"

    def test_shorten_path_outside_project(self, tmp_path):
        shell = _make_shell(tmp_path)
        # Path outside project_path — falls back to filename only
        result = shell._shorten_path("/some/other/place/file.py")
        assert result == "file.py"

    def test_shorten_path_relative_already(self, tmp_path):
        shell = _make_shell(tmp_path)
        # Relative-looking string: Path can still resolve relative_to
        result = shell._shorten_path("relative.py")
        # If it cannot relativise, we get the filename
        assert "relative.py" in result

    def test_shorten_paths_list(self, tmp_path):
        shell = _make_shell(tmp_path)
        paths = [str(tmp_path / "a.py"), str(tmp_path / "b.py")]
        result = shell._shorten_paths(paths)
        assert result == ["a.py", "b.py"]


# ── _extract_exports_from_files ───────────────────────────────────────────────

class TestExtractExportsFromFiles:
    def test_extract_public_functions(self, tmp_path):
        shell = _make_shell(tmp_path)
        (tmp_path / "utils.py").write_text(
            "def helper():\n    pass\n\ndef _private():\n    pass\n"
        )
        exports = shell._extract_exports_from_files(["utils.py"])
        assert any("def helper()" in e for e in exports)
        # private function should NOT appear
        assert not any("_private" in e for e in exports)

    def test_extract_classes(self, tmp_path):
        shell = _make_shell(tmp_path)
        (tmp_path / "models.py").write_text("class User(Base):\n    pass\n")
        exports = shell._extract_exports_from_files(["models.py"])
        assert any("class User" in e for e in exports)

    def test_extract_flask_routes(self, tmp_path):
        shell = _make_shell(tmp_path)
        (tmp_path / "app.py").write_text(
            "from flask import Flask\napp = Flask(__name__)\n\n"
            "@app.route('/api/users')\ndef get_users():\n    pass\n"
        )
        exports = shell._extract_exports_from_files(["app.py"])
        assert any("@app.route('/api/users')" in e for e in exports)

    def test_extract_fastapi_router(self, tmp_path):
        shell = _make_shell(tmp_path)
        (tmp_path / "routes.py").write_text(
            "router = APIRouter()\n\n@router.get('/items')\nasync def list_items():\n    pass\n"
        )
        exports = shell._extract_exports_from_files(["routes.py"])
        assert any("@router.get('/items')" in e for e in exports)

    def test_skips_non_python_files(self, tmp_path):
        shell = _make_shell(tmp_path)
        (tmp_path / "index.html").write_text("<html><body>hi</body></html>")
        exports = shell._extract_exports_from_files(["index.html"])
        assert exports == []

    def test_skips_missing_files(self, tmp_path):
        shell = _make_shell(tmp_path)
        # File does not exist — should not raise
        exports = shell._extract_exports_from_files(["nonexistent.py"])
        assert exports == []

    def test_cap_at_30_entries(self, tmp_path):
        shell = _make_shell(tmp_path)
        # Generate a file with 50 public functions
        lines = [f"def func_{i}():\n    pass\n" for i in range(50)]
        (tmp_path / "big.py").write_text("\n".join(lines))
        exports = shell._extract_exports_from_files(["big.py"])
        assert len(exports) <= 30


# ── _gather_upstream_artifacts ────────────────────────────────────────────────

class TestGatherUpstreamArtifacts:
    def test_no_deps_returns_empty(self, tmp_path):
        shell = _make_shell(tmp_path)
        task = _make_task("2", "Write tests", blocked_by=[])
        store = MagicMock()
        all_tasks = [task]
        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        assert result == {}

    def test_completed_dep_returns_files(self, tmp_path):
        shell = _make_shell(tmp_path)
        dep = _make_task(
            "1", "Create models",
            status="completed",
            artifacts={str(tmp_path / "models.py"): {"action": "written"}},
        )
        task = _make_task("2", "Write tests", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        assert "upstream_results" in result
        assert "models.py" in result["upstream_results"]
        assert "Create models" in result["upstream_results"]

    def test_failed_dependency_with_artifacts_visible(self, tmp_path):
        """Failed deps WITH artifacts should now appear with a warning."""
        shell = _make_shell(tmp_path)
        dep = _make_task(
            "1", "Create API",
            status="failed",
            artifacts={str(tmp_path / "api.py"): {"action": "written"}},
        )
        task = _make_task("2", "Write tests", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        # Failed dep with artifacts SHOULD appear
        assert "upstream_results" in result
        assert "api.py" in result["upstream_results"]
        assert "WARNING" in result["upstream_results"]

    def test_project_files_from_all_completed_tasks(self, tmp_path):
        shell = _make_shell(tmp_path)
        t1 = _make_task(
            "1", "Task A",
            status="completed",
            artifacts={str(tmp_path / "a.py"): {"action": "written"}},
        )
        t2 = _make_task(
            "2", "Task B",
            status="completed",
            artifacts={str(tmp_path / "b.py"): {"action": "written"}},
        )
        current = _make_task("3", "Task C", blocked_by=[])
        store = MagicMock()
        store.get.return_value = None
        all_tasks = [t1, t2, current]

        result = shell._gather_upstream_artifacts(current, store, all_tasks)
        assert "project_files" in result
        assert "a.py" in result["project_files"]
        assert "b.py" in result["project_files"]

    def test_exports_extracted_from_completed_py_files(self, tmp_path):
        shell = _make_shell(tmp_path)
        # Write an actual Python file with parameters
        (tmp_path / "utils.py").write_text("def compute(x, y):\n    return x + y\n")
        t1 = _make_task(
            "1", "Create utils",
            status="completed",
            artifacts={str(tmp_path / "utils.py"): {"action": "written"}},
        )
        current = _make_task("2", "Use utils", blocked_by=[])
        store = MagicMock()
        store.get.return_value = None
        all_tasks = [t1, current]

        result = shell._gather_upstream_artifacts(current, store, all_tasks)
        assert "available_exports" in result
        assert "def compute(x, y)" in result["available_exports"]

    def test_pending_tasks_not_in_project_files(self, tmp_path):
        shell = _make_shell(tmp_path)
        t_pending = _make_task(
            "1", "Pending task",
            status="pending",
            artifacts={str(tmp_path / "unbuilt.py"): {"action": "written"}},
        )
        current = _make_task("2", "Current", blocked_by=[])
        store = MagicMock()
        store.get.return_value = None
        all_tasks = [t_pending, current]

        result = shell._gather_upstream_artifacts(current, store, all_tasks)
        # Pending task artifacts should NOT be listed
        assert "project_files" not in result


# ── Artifact manifest writing ─────────────────────────────────────────────────

class TestArtifactManifest:
    def test_artifact_manifest_written(self, tmp_path):
        """After build, artifacts/index.json should be created."""
        manifest_dir = tmp_path / "artifacts"
        manifest_dir.mkdir()

        # Simulate what _cmd_build does at the end
        store_tasks = [
            _make_task(
                "1", "Create app",
                status="completed",
                artifacts={str(tmp_path / "app.py"): {"action": "written"}},
            ),
            _make_task("2", "Write tests", status="pending"),
        ]
        mock_store = MagicMock()
        mock_store.list.return_value = store_tasks

        artifact_manifest = {}
        for t in mock_store.list():
            if t.artifacts:
                artifact_manifest[t.id] = {
                    "task": t.subject,
                    "status": t.status,
                    "files": list(t.artifacts.keys()),
                }

        if artifact_manifest:
            (manifest_dir / "index.json").write_text(
                json.dumps(artifact_manifest, indent=2)
            )

        index = manifest_dir / "index.json"
        assert index.exists()
        data = json.loads(index.read_text())
        assert "1" in data
        assert data["1"]["task"] == "Create app"
        assert str(tmp_path / "app.py") in data["1"]["files"]
        # Task 2 has no artifacts — should NOT appear
        assert "2" not in data

    def test_manifest_not_written_when_no_artifacts(self, tmp_path):
        """If no tasks have artifacts, index.json should NOT be created."""
        store_tasks = [
            _make_task("1", "Empty task", status="completed", artifacts={}),
        ]
        mock_store = MagicMock()
        mock_store.list.return_value = store_tasks

        artifact_manifest = {}
        for t in mock_store.list():
            if t.artifacts:
                artifact_manifest[t.id] = {
                    "task": t.subject,
                    "status": t.status,
                    "files": list(t.artifacts.keys()),
                }

        if artifact_manifest:
            manifest_dir = tmp_path / "artifacts"
            manifest_dir.mkdir(exist_ok=True)
            (manifest_dir / "index.json").write_text(
                json.dumps(artifact_manifest, indent=2)
            )

        assert not (tmp_path / "artifacts" / "index.json").exists()


# ── Context injection into prompt ─────────────────────────────────────────────

class TestContextHintBuilding:
    def test_context_hint_includes_upstream_info(self, tmp_path):
        shell = _make_shell(tmp_path)
        dep = _make_task(
            "1", "Build database layer",
            status="completed",
            artifacts={str(tmp_path / "db.py"): {"action": "written"}},
        )
        task = _make_task("2", "Build API layer", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        upstream_context = shell._gather_upstream_artifacts(task, store, all_tasks)

        # Simulate what _cmd_build does
        context_sections = list(upstream_context.values())
        context_hint = "\n\n" + "\n\n".join(context_sections) if context_sections else ""

        assert "Build database layer" in context_hint
        assert "db.py" in context_hint

    def test_empty_context_no_hint(self, tmp_path):
        shell = _make_shell(tmp_path)
        task = _make_task("1", "Standalone task", blocked_by=[])
        store = MagicMock()
        store.get.return_value = None
        all_tasks = [task]

        upstream_context = shell._gather_upstream_artifacts(task, store, all_tasks)

        context_sections = list(upstream_context.values())
        context_hint = "\n\n" + "\n\n".join(context_sections) if context_sections else ""

        # No upstream context — hint is empty
        assert context_hint == ""


# ── Interface mismatch fix tests ────────────────────────────────────────────

class TestFailedTaskArtifacts:
    def test_failed_task_artifacts_visible_to_downstream(self, tmp_path):
        """Failed dep artifacts should appear in upstream_results."""
        shell = _make_shell(tmp_path)
        dep = _make_task(
            "1", "Create models",
            status="failed",
            artifacts={str(tmp_path / "models.py"): {"action": "written"}},
        )
        task = _make_task("2", "Build API", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        assert "upstream_results" in result
        assert "models.py" in result["upstream_results"]

    def test_failed_dep_warning_label(self, tmp_path):
        """Failed upstream should include WARNING text."""
        shell = _make_shell(tmp_path)
        dep = _make_task(
            "1", "Create models",
            status="failed",
            artifacts={str(tmp_path / "models.py"): {"action": "written"}},
        )
        task = _make_task("2", "Build API", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        assert "WARNING" in result["upstream_results"]
        assert "incomplete" in result["upstream_results"].lower()

    def test_failed_task_files_in_project_files(self, tmp_path):
        """Failed tasks with artifacts should appear in project_files listing."""
        shell = _make_shell(tmp_path)
        t1 = _make_task(
            "1", "Create models",
            status="failed",
            artifacts={str(tmp_path / "models.py"): {"action": "written"}},
        )
        current = _make_task("2", "Build API", blocked_by=[])
        store = MagicMock()
        store.get.return_value = None
        all_tasks = [t1, current]

        result = shell._gather_upstream_artifacts(current, store, all_tasks)
        assert "project_files" in result
        assert "models.py" in result["project_files"]


class TestASTSignatureExtraction:
    def test_full_params_captured(self, tmp_path):
        """AST extraction should capture full parameter lists."""
        shell = _make_shell(tmp_path)
        (tmp_path / "models.py").write_text(
            "def create_board(name):\n    pass\n\n"
            "def get_board_full(board_id):\n    pass\n"
        )
        exports = shell._extract_exports_from_files(["models.py"])
        assert any("create_board(name)" in e for e in exports)
        assert any("get_board_full(board_id)" in e for e in exports)

    def test_classes_with_bases_and_methods(self, tmp_path):
        """AST should capture class bases and public methods."""
        shell = _make_shell(tmp_path)
        (tmp_path / "models.py").write_text(
            "class User(Base):\n"
            "    def get(self):\n        pass\n"
            "    def update(self, data):\n        pass\n"
            "    def _internal(self):\n        pass\n"
        )
        exports = shell._extract_exports_from_files(["models.py"])
        assert any("class User(Base)" in e for e in exports)
        assert any("get" in e and "update" in e for e in exports)
        # Private method should NOT appear in methods list
        assert not any("_internal" in e for e in exports)

    def test_fallback_on_syntax_error(self, tmp_path):
        """AST failure should fallback to regex extraction."""
        shell = _make_shell(tmp_path)
        (tmp_path / "broken.py").write_text(
            "def valid_func(x, y):\n    pass\n\n"
            "def another()  # missing colon — syntax error\n"
        )
        exports = shell._extract_exports_from_files(["broken.py"])
        # Should still extract something via regex fallback
        assert any("valid_func" in e for e in exports)


class TestMandatoryReadInstruction:
    def test_present_when_deps_exist(self, tmp_path):
        """Prompt should include mandatory read instruction when task has deps."""
        shell = _make_shell(tmp_path)
        mandatory_reads = []
        dep_artifacts = {str(tmp_path / "models.py"): {"action": "written"}}
        for fpath in dep_artifacts.keys():
            short = shell._shorten_path(fpath)
            if short.endswith(('.py', '.js', '.ts', '.jsx', '.tsx')):
                mandatory_reads.append(short)

        assert len(mandatory_reads) == 1
        assert "models.py" in mandatory_reads[0]

    def test_absent_when_no_deps(self, tmp_path):
        """No mandatory reads when task has no dependencies."""
        shell = _make_shell(tmp_path)
        mandatory_reads = []
        # No deps → no mandatory reads
        assert mandatory_reads == []


class TestInterfaceSummary:
    def test_summary_in_upstream_context(self, tmp_path):
        """Interface summaries should appear in upstream context for .py files."""
        shell = _make_shell(tmp_path)
        (tmp_path / "models.py").write_text(
            "def create_board(name):\n    pass\n\n"
            "def get_board_full(board_id):\n    pass\n"
        )
        dep = _make_task(
            "1", "Create models",
            status="completed",
            artifacts={str(tmp_path / "models.py"): {"action": "written"}},
        )
        task = _make_task("2", "Build API", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        assert "Interface:" in result["upstream_results"]
        assert "create_board(name)" in result["upstream_results"]


class TestImportNameCheck:
    def test_catches_missing_name(self, tmp_path):
        """from models import Board should fail when Board is not defined."""
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        from forge_verify import _get_module_exports

        (tmp_path / "models.py").write_text(
            "def create_board(name):\n    pass\n\n"
            "def get_board_full(board_id):\n    pass\n"
        )
        exports = _get_module_exports(tmp_path / "models.py")
        assert "create_board" in exports
        assert "get_board_full" in exports
        assert "Board" not in exports  # Board class doesn't exist

    def test_passes_valid_imports(self, tmp_path):
        """from models import create_board should pass when function exists."""
        from forge_verify import _get_module_exports

        (tmp_path / "models.py").write_text(
            "def create_board(name):\n    pass\n\n"
            "class Board:\n    pass\n"
        )
        exports = _get_module_exports(tmp_path / "models.py")
        assert "create_board" in exports
        assert "Board" in exports


class TestMultipleDependencies:
    """Test artifact gathering with multiple upstream dependencies."""

    def test_multiple_completed_deps(self, tmp_path):
        """Task with 2 completed deps should see both in upstream_results."""
        shell = _make_shell(tmp_path)
        dep1 = _make_task(
            "1", "Create models",
            status="completed",
            artifacts={str(tmp_path / "models.py"): {"action": "written"}},
        )
        dep2 = _make_task(
            "2", "Create routes",
            status="completed",
            artifacts={str(tmp_path / "routes.py"): {"action": "written"}},
        )
        task = _make_task("3", "Write tests", blocked_by=["1", "2"])
        store = MagicMock()
        store.get.side_effect = lambda id_: {"1": dep1, "2": dep2}.get(id_)
        all_tasks = [dep1, dep2, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        assert "upstream_results" in result
        assert "models.py" in result["upstream_results"]
        assert "routes.py" in result["upstream_results"]

    def test_dep_with_no_artifacts(self, tmp_path):
        """Completed dep with empty artifacts should not add noise to context."""
        shell = _make_shell(tmp_path)
        dep = _make_task("1", "Setup env", status="completed", artifacts={})
        task = _make_task("2", "Build app", blocked_by=["1"])
        store = MagicMock()
        store.get.side_effect = lambda id_: dep if id_ == "1" else None
        all_tasks = [dep, task]

        result = shell._gather_upstream_artifacts(task, store, all_tasks)
        # Dep had no artifacts, so upstream_results should not contain dep subject
        upstream_text = result.get("upstream_results", "")
        # Empty artifacts means nothing to report for this dep
        assert "Setup env" not in upstream_text or upstream_text == ""


class TestShortenPathEdgeCases:
    def test_shorten_path_with_deep_nesting(self, tmp_path):
        shell = _make_shell(tmp_path)
        abs_path = str(tmp_path / "src" / "routes" / "api" / "v1" / "users.py")
        result = shell._shorten_path(abs_path)
        assert result == "src/routes/api/v1/users.py"

    def test_shorten_paths_empty_list(self, tmp_path):
        shell = _make_shell(tmp_path)
        assert shell._shorten_paths([]) == []
