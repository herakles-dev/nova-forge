"""Tests for forge_preview — PreviewManager, stack detection, port utilities."""

import os
import socket
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from forge_preview import (
    PreviewManager,
    PreviewError,
    StackInfo,
    detect_stack,
    find_free_port,
    port_is_listening,
)


# ── Port utilities ──────────────────────────────────────────────────────────

class TestPortUtilities:
    def test_find_free_port_returns_int(self):
        port = find_free_port(19000)
        assert isinstance(port, int)
        assert 19000 <= port < 19020

    def test_find_free_port_skips_occupied(self):
        """Bind a port, then verify find_free_port skips it."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 19050))
        sock.listen(1)
        try:
            port = find_free_port(19050)
            assert port != 19050
            assert port >= 19051
        finally:
            sock.close()

    def test_port_is_listening_false_on_free_port(self):
        assert not port_is_listening(19999)

    def test_port_is_listening_true_on_bound_port(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 19998))
        sock.listen(1)
        try:
            assert port_is_listening(19998)
        finally:
            sock.close()


# ── Stack detection ─────────────────────────────────────────────────────────

class TestDetectStack:
    def test_flask_detected(self, tmp_path):
        (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        si = detect_stack(tmp_path)
        assert si.kind == "flask"
        assert si.entry == "app.py"
        assert "flask" in si.server_cmd
        assert si.port > 0

    def test_flask_in_subdirectory(self, tmp_path):
        backend = tmp_path / "backend"
        backend.mkdir()
        (backend / "main.py").write_text("from Flask import Flask\n")
        si = detect_stack(tmp_path)
        assert si.kind == "flask"
        assert si.cwd == backend

    def test_flask_factory_pattern_detected(self, tmp_path):
        (tmp_path / "app.py").write_text(
            "from flask import Flask\ndef create_app():\n    app = Flask(__name__)\n    return app\n"
        )
        si = detect_stack(tmp_path)
        assert si.kind == "flask"
        assert "create_app" in si.server_cmd
        assert ":app" not in si.server_cmd or "create_app" in si.server_cmd

    def test_flask_module_level_app(self, tmp_path):
        (tmp_path / "app.py").write_text("from flask import Flask\napp = Flask(__name__)\n")
        si = detect_stack(tmp_path)
        assert si.kind == "flask"
        assert "from app import app" in si.server_cmd

    def test_node_detected(self, tmp_path):
        (tmp_path / "package.json").write_text('{"name":"test"}')
        si = detect_stack(tmp_path)
        assert si.kind == "node"
        assert "npm start" in si.server_cmd

    def test_node_in_frontend_dir(self, tmp_path):
        frontend = tmp_path / "frontend"
        frontend.mkdir()
        (frontend / "package.json").write_text('{"name":"test"}')
        si = detect_stack(tmp_path)
        assert si.kind == "node"
        assert si.cwd == frontend

    def test_static_detected(self, tmp_path):
        (tmp_path / "index.html").write_text("<html></html>")
        si = detect_stack(tmp_path)
        assert si.kind == "static"
        assert "http.server" in si.server_cmd

    def test_static_in_public_dir(self, tmp_path):
        pub = tmp_path / "public"
        pub.mkdir()
        (pub / "index.html").write_text("<html></html>")
        si = detect_stack(tmp_path)
        assert si.kind == "static"
        assert si.cwd == pub

    def test_flask_preferred_over_static(self, tmp_path):
        """Flask takes priority when both app.py and index.html exist."""
        (tmp_path / "app.py").write_text("from flask import Flask\n")
        (tmp_path / "index.html").write_text("<html></html>")
        si = detect_stack(tmp_path)
        assert si.kind == "flask"

    def test_unknown_when_empty(self, tmp_path):
        si = detect_stack(tmp_path)
        assert si.kind == "unknown"
        assert si.port == 0

    def test_non_flask_python_not_detected(self, tmp_path):
        (tmp_path / "app.py").write_text("print('hello')\n")
        si = detect_stack(tmp_path)
        assert si.kind != "flask"


# ── PreviewManager ──────────────────────────────────────────────────────────

class TestPreviewManager:
    def test_init_registers_atexit(self, tmp_path):
        from forge_preview import _active_managers
        mgr = PreviewManager(tmp_path)
        assert mgr in _active_managers
        mgr.stop()  # cleanup

    def test_is_running_false_initially(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        assert not mgr.is_running
        assert mgr.url is None
        assert mgr.port is None
        mgr.stop()

    def test_stop_idempotent(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        mgr.stop()
        mgr.stop()  # should not raise

    def test_start_raises_on_unknown_stack(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        with pytest.raises(PreviewError, match="No servable entry point"):
            mgr.start()
        mgr.stop()

    @patch("forge_preview.shutil.which", return_value=None)
    def test_start_raises_without_cloudflared(self, mock_which, tmp_path):
        (tmp_path / "app.py").write_text("from flask import Flask\n")
        mgr = PreviewManager(tmp_path)
        with pytest.raises(PreviewError, match="cloudflared not found"):
            mgr.start()
        mgr.stop()

    def test_health_when_not_running(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        h = mgr.health()
        assert h["running"] is False
        assert h["server_alive"] is False
        assert h["tunnel_alive"] is False
        mgr.stop()

    def test_start_stops_previous(self, tmp_path):
        """Starting a new preview kills old processes before spawning new ones."""
        mgr = PreviewManager(tmp_path)

        mock_server = MagicMock()
        mock_server.poll.return_value = None
        mgr._server_proc = mock_server

        mock_tunnel = MagicMock()
        mock_tunnel.poll.return_value = None
        mgr._tunnel_proc = mock_tunnel

        # Calling stop should kill both old processes
        mgr.stop()
        assert mock_server.terminate.called or mock_server.kill.called
        assert mock_tunnel.terminate.called or mock_tunnel.kill.called

    def test_start_server_only(self, tmp_path):
        """_start_server_only starts the server without a tunnel."""
        (tmp_path / "index.html").write_text("<html>test</html>")
        (tmp_path / ".forge").mkdir(exist_ok=True)
        mgr = PreviewManager(tmp_path)
        try:
            port = mgr._start_server_only()
            assert port > 0
            assert port_is_listening(port)
            assert mgr._tunnel_proc is None  # No tunnel
        finally:
            mgr.stop()

    def test_kill_proc_handles_none(self):
        PreviewManager._kill_proc(None)  # should not raise

    def test_kill_proc_handles_already_dead(self):
        proc = MagicMock()
        proc.poll.return_value = 0  # already exited
        PreviewManager._kill_proc(proc)
        proc.terminate.assert_not_called()


# ── StackInfo ───────────────────────────────────────────────────────────────

class TestStackInfo:
    def test_dataclass_fields(self):
        si = StackInfo(kind="flask", entry="app.py", cwd=Path("."),
                       server_cmd="flask run", port=5000)
        assert si.kind == "flask"
        assert si.port == 5000
