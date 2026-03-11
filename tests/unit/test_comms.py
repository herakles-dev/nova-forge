"""Tests for forge_comms.py (BuildContext) and forge_index.py extensions."""

import os
import threading
import time
import tempfile
from pathlib import Path

import pytest

from forge_comms import BuildContext, FileClaim, AgentAnnouncement


# ── TestBuildContext ──────────────────────────────────────────────────────────


class TestBuildContext:
    def setup_method(self):
        self.ctx = BuildContext(Path("/tmp/test-project"))

    def test_claim_file_success(self):
        assert self.ctx.claim_file("app.py", "agent-1") is True

    def test_claim_file_conflict(self):
        self.ctx.claim_file("app.py", "agent-1")
        assert self.ctx.claim_file("app.py", "agent-2") is False

    def test_claim_by_same_agent(self):
        self.ctx.claim_file("app.py", "agent-1")
        assert self.ctx.claim_file("app.py", "agent-1") is True

    def test_release_file(self):
        self.ctx.claim_file("app.py", "agent-1")
        self.ctx.release_file("app.py", "agent-1")
        assert self.ctx.claim_file("app.py", "agent-2") is True

    def test_release_wrong_agent(self):
        self.ctx.claim_file("app.py", "agent-1")
        self.ctx.release_file("app.py", "agent-2")  # Should be ignored
        assert self.ctx.claim_file("app.py", "agent-2") is False

    def test_is_claimed(self):
        self.ctx.claim_file("app.py", "agent-1")
        claim = self.ctx.is_claimed("app.py")
        assert claim is not None
        assert claim.agent_id == "agent-1"
        assert self.ctx.is_claimed("other.py") is None

    def test_get_claims_by_agent(self):
        self.ctx.claim_file("a.py", "agent-1")
        self.ctx.claim_file("b.py", "agent-1")
        self.ctx.claim_file("c.py", "agent-2")
        claims = self.ctx.get_claims_by_agent("agent-1")
        assert len(claims) == 2
        paths = {c.path for c in claims}
        assert paths == {"a.py", "b.py"}

    def test_update_claim_status(self):
        self.ctx.claim_file("app.py", "agent-1")
        self.ctx.update_claim_status("app.py", "agent-1", "writing")
        claim = self.ctx.is_claimed("app.py")
        assert claim.status == "writing"

    def test_announce(self):
        self.ctx.announce("agent-1", "endpoint_ready", "POST /api/users")
        anns = self.ctx.get_announcements()
        assert len(anns) == 1
        assert anns[0].kind == "endpoint_ready"
        assert anns[0].detail == "POST /api/users"

    def test_get_announcements_since(self):
        self.ctx.announce("agent-1", "file_created", "app.py")
        cutoff = time.time()
        time.sleep(0.01)
        self.ctx.announce("agent-2", "endpoint_ready", "GET /api")
        anns = self.ctx.get_announcements(since=cutoff)
        assert len(anns) == 1
        assert anns[0].agent_id == "agent-2"

    def test_get_announcements_exclude_agent(self):
        self.ctx.announce("agent-1", "file_created", "a.py")
        self.ctx.announce("agent-2", "file_created", "b.py")
        anns = self.ctx.get_announcements(exclude_agent="agent-1")
        assert len(anns) == 1
        assert anns[0].agent_id == "agent-2"

    def test_set_status_and_get_status(self):
        self.ctx.set_status("agent-1", "building")
        self.ctx.set_status("agent-2", "idle")
        status = self.ctx.get_status()
        assert status == {"agent-1": "building", "agent-2": "idle"}

    def test_to_context_empty(self):
        result = self.ctx.to_context("agent-1")
        assert result == ""

    def test_to_context_with_data(self):
        self.ctx.claim_file("app.py", "agent-2")
        self.ctx.announce("agent-2", "file_created", "app.py")
        result = self.ctx.to_context("agent-1", budget_chars=3000)
        assert "app.py" in result
        assert "agent-2" in result
        assert "File Claims" in result

    def test_to_context_excludes_self(self):
        self.ctx.claim_file("my.py", "agent-1")
        self.ctx.claim_file("other.py", "agent-2")
        result = self.ctx.to_context("agent-1")
        assert "my.py" not in result
        assert "other.py" in result

    def test_stats(self):
        self.ctx.claim_file("a.py", "agent-1")
        self.ctx.claim_file("b.py", "agent-2")
        self.ctx.claim_file("a.py", "agent-2")  # conflict
        self.ctx.announce("agent-1", "file_created", "a.py")
        stats = self.ctx.stats()
        assert stats["claims"] == 2  # a.py (agent-1) + b.py (agent-2)
        assert stats["conflicts"] == 1
        assert stats["announcements"] == 1

    def test_thread_safety(self):
        """Concurrent claims from multiple threads shouldn't crash."""
        results = {"success": 0, "conflict": 0}

        def claim_worker(agent_id):
            for i in range(50):
                path = f"file_{i % 10}.py"
                if self.ctx.claim_file(path, agent_id):
                    results["success"] += 1
                else:
                    results["conflict"] += 1

        threads = [
            threading.Thread(target=claim_worker, args=(f"agent-{i}",))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 200 attempts should have resolved (no crashes)
        assert results["success"] + results["conflict"] == 200
        # At most 10 unique files can be successfully claimed by one agent
        assert results["success"] >= 10


# ── TestProjectIndexExtensions ───────────────────────────────────────────────


class TestProjectIndexExtensions:
    def _make_project(self, files: dict[str, str]) -> Path:
        tmpdir = Path(tempfile.mkdtemp())
        for name, content in files.items():
            fpath = tmpdir / name
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(content)
        return tmpdir

    def test_scan_exports_python(self):
        from forge_index import ProjectIndex
        root = self._make_project({
            "app.py": "def greet():\n    pass\n\nclass Server:\n    pass\n\ndef _private():\n    pass\n",
        })
        idx = ProjectIndex(root)
        idx.scan()
        assert "app.py" in idx.exports
        exports = idx.exports["app.py"]
        assert "greet" in exports
        assert "Server" in exports
        assert "_private" not in exports

    def test_scan_exports_js(self):
        from forge_index import ProjectIndex
        root = self._make_project({
            "index.js": "export function fetchData() {}\nexport const API_URL = 'x';\n",
        })
        idx = ProjectIndex(root)
        idx.scan()
        assert "index.js" in idx.exports
        exports = idx.exports["index.js"]
        assert "fetchData" in exports
        assert "API_URL" in exports

    def test_scan_imports_python(self):
        from forge_index import ProjectIndex
        root = self._make_project({
            "main.py": "from flask import Flask\nimport os\nfrom app import greet\n",
        })
        idx = ProjectIndex(root)
        idx.scan()
        assert "main.py" in idx.imports
        imports = idx.imports["main.py"]
        assert "flask" in imports
        assert "os" in imports
        assert "app" in imports

    def test_get_dependents(self):
        from forge_index import ProjectIndex
        root = self._make_project({
            "app.py": "def greet():\n    pass\n",
            "main.py": "from app import greet\n",
            "test.py": "import app\n",
        })
        idx = ProjectIndex(root)
        idx.scan()
        deps = idx.get_dependents("app.py")
        assert "main.py" in deps
        assert "test.py" in deps

    def test_to_dependency_context(self):
        from forge_index import ProjectIndex
        root = self._make_project({
            "app.py": "def greet():\n    pass\n\ndef serve():\n    pass\n",
            "main.py": "from app import greet\n",
        })
        idx = ProjectIndex(root)
        idx.scan()
        ctx = idx.to_dependency_context(["app.py"], budget_chars=2000)
        assert "greet" in ctx
        assert "serve" in ctx
        assert "main.py" in ctx

    def test_scan_imports_empty_project(self):
        from forge_index import ProjectIndex
        root = self._make_project({})
        idx = ProjectIndex(root)
        idx.scan()
        assert idx.imports == {}
        assert idx.exports == {}

    def test_scan_exports_no_code_files(self):
        from forge_index import ProjectIndex
        root = self._make_project({
            "readme.md": "# Hello\n",
            "config.yml": "key: value\n",
        })
        idx = ProjectIndex(root)
        idx.scan()
        # No Python/JS files → no exports
        assert len(idx.exports) == 0

    def test_dependency_context_truncation(self):
        from forge_index import ProjectIndex
        # Create many exports to test budget truncation
        code = "\n".join(f"def function_{i}():\n    pass\n" for i in range(50))
        root = self._make_project({"big.py": code})
        idx = ProjectIndex(root)
        idx.scan()
        ctx = idx.to_dependency_context(["big.py"], budget_chars=200)
        assert len(ctx) <= 300  # Budget + one line allowance
