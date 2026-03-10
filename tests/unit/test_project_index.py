"""Tests for forge_index.py — Project Indexer."""

import json
import pytest
from pathlib import Path

from forge_index import ProjectIndex, FileEntry, get_or_create_index, _format_size


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_project(tmp_path):
    """Create a sample project directory."""
    # Python files
    (tmp_path / "app.py").write_text('"""Flask app."""\nfrom flask import Flask\napp = Flask(__name__)\n')
    (tmp_path / "models.py").write_text('"""Database models."""\nclass User:\n    pass\n')

    # Subdirectory
    (tmp_path / "routes").mkdir()
    (tmp_path / "routes" / "auth.py").write_text('"""Auth routes."""\ndef login():\n    pass\n')
    (tmp_path / "routes" / "api.py").write_text('"""API routes."""\ndef get_items():\n    pass\n')

    # Frontend
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "index.html").write_text('<html><body>Hello</body></html>\n')
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "style.css").write_text('body { margin: 0; }\n')

    # Config
    (tmp_path / "requirements.txt").write_text('flask==3.0\nsqlalchemy==2.0\n')
    (tmp_path / "Dockerfile").write_text('FROM python:3.11\nCOPY . /app\n')

    # .forge directory (should be skipped)
    (tmp_path / ".forge").mkdir()
    (tmp_path / ".forge" / "settings.json").write_text('{}')

    # Hidden file (should be skipped)
    (tmp_path / ".gitignore").write_text('*.pyc\n')

    return tmp_path


@pytest.fixture
def node_project(tmp_path):
    """Create a Node.js project."""
    pkg = {"name": "test-app", "dependencies": {"express": "^4.18"}, "scripts": {"start": "node server.js"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    (tmp_path / "server.js").write_text('const express = require("express");\nconst app = express();\n')
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text('<html></html>')
    return tmp_path


@pytest.fixture
def react_project(tmp_path):
    """Create a React project."""
    pkg = {"name": "react-app", "dependencies": {"react": "^18", "next": "^14"}}
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text('export default function App() {}')
    return tmp_path


# ── Scan Tests ───────────────────────────────────────────────────────────────

class TestProjectScan:
    def test_scan_counts_files(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        # app.py, models.py, auth.py, api.py, index.html, style.css, requirements.txt, Dockerfile
        assert idx.total_files >= 7
        assert idx.total_files <= 10  # Some variance based on type detection

    def test_scan_detects_python_stack(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        assert any("Flask" in s or "Python" in s for s in idx.stack)

    def test_scan_detects_node_stack(self, node_project):
        idx = ProjectIndex(node_project)
        idx.scan()
        assert any("Express" in s or "Node" in s for s in idx.stack)

    def test_scan_detects_react_stack(self, react_project):
        idx = ProjectIndex(react_project)
        idx.scan()
        assert any("Next" in s for s in idx.stack)

    def test_scan_finds_entry_points(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        assert "app.py" in idx.entry_points

    def test_scan_finds_config_files(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        config_paths = [f.path for f in idx.files.values() if f.is_config]
        assert "requirements.txt" in config_paths

    def test_scan_skips_forge_dir(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        forge_files = [f for f in idx.files if ".forge" in f]
        assert len(forge_files) == 0

    def test_scan_counts_lines(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        assert idx.total_lines > 0
        # app.py has 3 lines
        app_entry = idx.files.get("app.py")
        assert app_entry is not None
        assert app_entry.lines == 3

    def test_scan_detects_languages(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        assert "Python" in idx.languages
        assert "HTML" in idx.languages

    def test_scan_tracks_directories(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        assert "routes" in idx.dirs
        assert "templates" in idx.dirs


# ── Incremental Update Tests ─────────────────────────────────────────────────

class TestIncrementalUpdate:
    def test_update_new_file(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        old_count = idx.total_files

        # Add a new file
        new_file = sample_project / "utils.py"
        new_file.write_text("def helper():\n    return True\n")
        idx.update(new_file)

        assert idx.total_files == old_count + 1
        assert "utils.py" in idx.files

    def test_update_modified_file(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        old_lines = idx.files["app.py"].lines

        # Modify existing file
        (sample_project / "app.py").write_text("# Extended\n" * 10)
        idx.update(sample_project / "app.py")

        assert idx.files["app.py"].lines == 10

    def test_update_deleted_file(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        old_count = idx.total_files

        # Delete a file
        (sample_project / "models.py").unlink()
        idx.update(sample_project / "models.py")

        assert idx.total_files == old_count - 1
        assert "models.py" not in idx.files


# ── Context Rendering Tests ──────────────────────────────────────────────────

class TestContextRendering:
    def test_to_context_includes_stack(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        ctx = idx.to_context(budget_chars=6000)
        assert "Stack:" in ctx

    def test_to_context_includes_entry_points(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        ctx = idx.to_context(budget_chars=6000)
        assert "app.py" in ctx

    def test_to_context_respects_budget(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        ctx = idx.to_context(budget_chars=200)
        assert len(ctx) <= 400  # Some overshoot acceptable for headers

    def test_empty_project_returns_empty(self, tmp_path):
        idx = ProjectIndex(tmp_path)
        idx.scan()
        assert idx.to_context() == ""


# ── Persistence Tests ────────────────────────────────────────────────────────

class TestPersistence:
    def test_save_and_load(self, sample_project):
        idx = ProjectIndex(sample_project)
        idx.scan()
        idx.save()

        loaded = ProjectIndex.load(sample_project)
        assert loaded is not None
        assert loaded.total_files == idx.total_files
        assert loaded.stack == idx.stack
        assert loaded.total_lines == idx.total_lines

    def test_load_missing_returns_none(self, tmp_path):
        result = ProjectIndex.load(tmp_path)
        assert result is None

    def test_get_or_create_scans_fresh(self, sample_project):
        idx = get_or_create_index(sample_project)
        assert idx.total_files > 0

    def test_get_or_create_uses_cache(self, sample_project):
        idx1 = get_or_create_index(sample_project)
        idx1.save()
        idx2 = get_or_create_index(sample_project)
        assert idx2.scanned_at == idx1.scanned_at  # Same cached scan


# ── Helper Tests ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_format_size_bytes(self):
        assert _format_size(500) == "500 B"

    def test_format_size_kb(self):
        assert "KB" in _format_size(2048)

    def test_format_size_mb(self):
        assert "MB" in _format_size(2 * 1024 * 1024)
