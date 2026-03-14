"""Tests for forge_verify — BuildVerifier, static checks, extraction helpers."""

import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from forge_verify import (
    BuildVerifier,
    VerifyResult,
    Check,
    _extract_ui_expectations,
    _extract_api_endpoints,
    _skip_path,
    _STDLIB_MODULES,
)


# ── VerifyResult ────────────────────────────────────────────────────────────

class TestVerifyResult:
    def test_initial_state(self):
        vr = VerifyResult()
        assert vr.status == "pass"
        assert vr.passed == 0
        assert vr.failed == 0
        assert vr.checks == []

    def test_add_passing_check(self):
        vr = VerifyResult()
        vr.add("syntax", True, "OK")
        assert vr.passed == 1
        assert vr.failed == 0
        assert vr.status == "pass"

    def test_add_failing_check_updates_status(self):
        vr = VerifyResult()
        vr.add("syntax", True, "OK")
        vr.add("imports", False, "broken")
        assert vr.failed == 1
        assert vr.status in ("fail", "partial")

    def test_summary_format(self):
        vr = VerifyResult()
        vr.add("a", True)
        vr.add("b", False)
        vr.add("c", True)
        assert vr.summary == "2/3 checks passed"

    def test_evidence_path_stored(self):
        vr = VerifyResult()
        vr.add("screenshot", True, "OK", evidence="/tmp/shot.png")
        assert vr.checks[0].evidence_path == "/tmp/shot.png"


# ── Check dataclass ────────────────────────────────────────────────────────

class TestCheck:
    def test_fields(self):
        c = Check(name="test", passed=True, detail="ok")
        assert c.name == "test"
        assert c.passed
        assert c.evidence_path is None


# ── Static checks (L1) ─────────────────────────────────────────────────────

class TestSyntaxCheck:
    def test_valid_python_passes(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_syntax(r)
        assert r.checks[0].passed
        assert "1 Python" in r.checks[0].detail

    def test_invalid_python_fails(self, tmp_path):
        (tmp_path / "bad.py").write_text("def broken(\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_syntax(r)
        assert not r.checks[0].passed
        assert "syntax error" in r.checks[0].detail.lower()

    def test_skips_venv_and_pycache(self, tmp_path):
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "bad.py").write_text("def broken(\n")
        (tmp_path / "good.py").write_text("x = 1\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_syntax(r)
        assert r.checks[0].passed  # Only checked good.py

    def test_no_python_files(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_syntax(r)
        assert len(r.checks) == 0  # No checks added


class TestImportCheck:
    def test_valid_imports_pass(self, tmp_path):
        (tmp_path / "config.py").write_text("DB = 'test.db'\n")
        (tmp_path / "app.py").write_text("from config import DB\nimport os\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_imports(r)
        assert r.checks[0].passed

    def test_broken_import_detected(self, tmp_path):
        (tmp_path / "app.py").write_text("from nonexistent_module_xyz import Foo\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_imports(r)
        assert not r.checks[0].passed
        assert "nonexistent_module_xyz" in r.checks[0].detail

    def test_stdlib_imports_ignored(self, tmp_path):
        (tmp_path / "app.py").write_text("import os\nimport json\nimport sys\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_imports(r)
        assert r.checks[0].passed

    def test_installed_package_not_flagged(self, tmp_path):
        # 'pytest' is installed, should not be flagged
        (tmp_path / "test.py").write_text("import pytest\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_imports(r)
        assert r.checks[0].passed


# ── UI extraction ───────────────────────────────────────────────────────────

class TestExtractUIExpectations:
    def test_empty_spec(self):
        assert _extract_ui_expectations("") == []

    def test_form_keyword(self):
        result = _extract_ui_expectations("The app has a form to add tasks")
        names = [r[0] for r in result]
        assert "form" in names

    def test_button_keyword(self):
        result = _extract_ui_expectations("Each task has a delete button")
        names = [r[0] for r in result]
        assert "buttons" in names
        assert "delete_button" in names

    def test_table_keyword(self):
        result = _extract_ui_expectations("Display data in a table")
        names = [r[0] for r in result]
        assert "table" in names

    def test_multiple_keywords(self):
        spec = "A form with input fields, a table of results, and navigation"
        result = _extract_ui_expectations(spec)
        names = [r[0] for r in result]
        assert "form" in names
        assert "table" in names
        assert "nav" in names


# ── API endpoint extraction ─────────────────────────────────────────────────

class TestExtractAPIEndpoints:
    def test_from_spec(self, tmp_path):
        spec = """
        GET /api/tasks
        POST /api/tasks
        DELETE /api/tasks/<id>
        """
        endpoints = _extract_api_endpoints(spec, tmp_path)
        # Should extract GET and POST (not DELETE with <id>)
        methods = [m for m, _ in endpoints]
        paths = [p for _, p in endpoints]
        assert "GET" in methods
        assert "POST" in methods
        assert "/api/tasks" in paths

    def test_skips_parameterized_paths(self, tmp_path):
        spec = "GET /api/tasks/<id>"
        endpoints = _extract_api_endpoints(spec, tmp_path)
        assert len(endpoints) == 0

    def test_from_flask_source(self, tmp_path):
        (tmp_path / "routes.py").write_text("""
from flask import Blueprint
bp = Blueprint('api', __name__)

@bp.route('/api/items', methods=['GET', 'POST'])
def items():
    pass
""")
        endpoints = _extract_api_endpoints("", tmp_path)
        assert len(endpoints) == 2
        methods = [m for m, _ in endpoints]
        assert "GET" in methods
        assert "POST" in methods

    def test_empty_spec_no_source(self, tmp_path):
        endpoints = _extract_api_endpoints("", tmp_path)
        assert endpoints == []


# ── Skip path helper ────────────────────────────────────────────────────────

class TestSkipPath:
    def test_skips_git(self, tmp_path):
        assert _skip_path(tmp_path / ".git" / "config", tmp_path)

    def test_skips_node_modules(self, tmp_path):
        assert _skip_path(tmp_path / "node_modules" / "foo.js", tmp_path)

    def test_skips_venv(self, tmp_path):
        assert _skip_path(tmp_path / ".venv" / "lib" / "foo.py", tmp_path)

    def test_allows_normal_file(self, tmp_path):
        assert not _skip_path(tmp_path / "app.py", tmp_path)

    def test_allows_src_dir(self, tmp_path):
        assert not _skip_path(tmp_path / "src" / "main.py", tmp_path)


# ── Stdlib modules ──────────────────────────────────────────────────────────

class TestStdlibModules:
    def test_common_modules_present(self):
        assert "os" in _STDLIB_MODULES
        assert "sys" in _STDLIB_MODULES
        assert "json" in _STDLIB_MODULES
        assert "pathlib" in _STDLIB_MODULES
        assert "sqlite3" in _STDLIB_MODULES

    def test_third_party_not_present(self):
        assert "flask" not in _STDLIB_MODULES
        assert "pytest" not in _STDLIB_MODULES
        assert "numpy" not in _STDLIB_MODULES


# ── BuildVerifier integration ───────────────────────────────────────────────

class TestBuildVerifierIntegration:
    def test_static_checks_on_valid_project(self, tmp_path):
        """End-to-end L1 checks on a small valid project."""
        (tmp_path / "config.py").write_text("DB = 'test.db'\nAPP = 'Test'\n")
        (tmp_path / "app.py").write_text("from config import DB\nimport os\nprint(DB)\n")
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_syntax(r)
        v._check_imports(r)
        assert r.passed == 2
        assert r.failed == 0

    def test_static_checks_on_broken_project(self, tmp_path):
        """L1 catches both syntax and import errors."""
        (tmp_path / "bad.py").write_text("def foo(\n")  # syntax error
        (tmp_path / "app.py").write_text("from nonexistent_xyz import bar\n")  # bad import
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_syntax(r)
        v._check_imports(r)
        assert r.failed == 2

    @pytest.mark.asyncio
    async def test_check_server_unknown_stack(self, tmp_path):
        """Server check returns False for unknown stack."""
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        ok, port, mgr = await v._check_server(r)
        assert not ok
        assert port is None
        assert not r.checks[0].passed

    @pytest.mark.asyncio
    async def test_verify_minimal_project(self, tmp_path):
        """Full verify on a project with no server — L1 passes, L2 fails gracefully."""
        (tmp_path / "main.py").write_text("print('hello')\n")
        v = BuildVerifier(tmp_path)
        result = await v.verify()
        # Should have syntax pass, import pass, server fail (no flask/node/static)
        names = [c.name for c in result.checks]
        assert "python_syntax" in names
        assert "imports" in names
        assert "server_start" in names


class TestFileReferenceCheck:
    """Tests for L1 file reference consistency check."""

    def test_send_static_file_correct(self, tmp_path):
        """send_static_file pointing to a file in static/ passes."""
        (tmp_path / "static").mkdir()
        (tmp_path / "static" / "index.html").write_text("<h1>hi</h1>")
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index(): return app.send_static_file('index.html')\n"
        )
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_file_references(r)
        ref_check = [c for c in r.checks if c.name == "file_references"]
        assert ref_check and ref_check[0].passed

    def test_send_static_file_wrong_dir(self, tmp_path):
        """send_static_file('x') but file is in templates/ — must fail."""
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "index.html").write_text("<h1>hi</h1>")
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index(): return app.send_static_file('index.html')\n"
        )
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_file_references(r)
        ref_check = [c for c in r.checks if c.name == "file_references"]
        assert ref_check and not ref_check[0].passed
        assert "templates/" in ref_check[0].detail

    def test_render_template_correct(self, tmp_path):
        """render_template pointing to templates/ passes."""
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "index.html").write_text("<h1>hi</h1>")
        (tmp_path / "app.py").write_text(
            "from flask import Flask, render_template\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index(): return render_template('index.html')\n"
        )
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_file_references(r)
        ref_check = [c for c in r.checks if c.name == "file_references"]
        assert ref_check and ref_check[0].passed

    def test_render_template_wrong_dir(self, tmp_path):
        """render_template('x') but file is in static/ — must fail."""
        (tmp_path / "static").mkdir()
        (tmp_path / "static" / "index.html").write_text("<h1>hi</h1>")
        (tmp_path / "app.py").write_text(
            "from flask import Flask, render_template\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index(): return render_template('index.html')\n"
        )
        v = BuildVerifier(tmp_path)
        r = VerifyResult()
        v._check_file_references(r)
        ref_check = [c for c in r.checks if c.name == "file_references"]
        assert ref_check and not ref_check[0].passed
        assert "static/" in ref_check[0].detail

    def test_diagnose_root_404_send_static_mismatch(self, tmp_path):
        """Diagnosis correctly identifies send_static_file vs templates/ mismatch."""
        (tmp_path / "templates").mkdir()
        (tmp_path / "templates" / "index.html").write_text("<h1>hi</h1>")
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/')\n"
            "def index(): return app.send_static_file('index.html')\n"
        )
        v = BuildVerifier(tmp_path)
        msg = v._diagnose_root_404(404)
        assert "send_static_file" in msg
        assert "templates/" in msg
