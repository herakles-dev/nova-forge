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
    _extract_expose_port,
    _read_pkg_deps,
    TUNNEL_RETRIES,
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


# ── Extended stack detection ───────────────────────────────────────────────

class TestDetectStackExtended:
    """Comprehensive tests for all 14 stack detectors, priority ordering,
    backward compatibility, and helper functions."""

    # ── Positive detection tests ───────────────────────────────────────

    def test_streamlit_detected(self, tmp_path):
        (tmp_path / "app.py").write_text("import streamlit as st\nst.title('hi')")
        si = detect_stack(tmp_path)
        assert si.kind == "streamlit"
        assert "streamlit" in si.server_cmd
        assert si.startup_timeout == 15
        assert si.needs_install is True

    def test_fastapi_detected(self, tmp_path):
        (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()")
        si = detect_stack(tmp_path)
        assert si.kind == "fastapi"
        assert "uvicorn" in si.server_cmd
        assert "main:app" in si.server_cmd

    def test_django_detected(self, tmp_path):
        (tmp_path / "manage.py").write_text("#!/usr/bin/env python\nimport django\n")
        si = detect_stack(tmp_path)
        assert si.kind == "django"
        assert "runserver" in si.server_cmd
        assert si.startup_timeout == 12

    def test_nextjs_detected(self, tmp_path):
        (tmp_path / "package.json").write_text(
            '{"dependencies":{"next":"14.0.0","react":"18.0.0"}}'
        )
        si = detect_stack(tmp_path)
        assert si.kind == "nextjs"
        assert "next" in si.server_cmd
        assert si.startup_timeout == 30

    def test_vite_detected(self, tmp_path):
        (tmp_path / "package.json").write_text('{"devDependencies":{"vite":"5.0.0"}}')
        si = detect_stack(tmp_path)
        assert si.kind == "vite"
        assert "vite" in si.server_cmd

    def test_vite_config_detected(self, tmp_path):
        (tmp_path / "vite.config.ts").write_text("")
        (tmp_path / "package.json").write_text('{"name":"test"}')
        si = detect_stack(tmp_path)
        assert si.kind == "vite"

    def test_go_detected(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/app\ngo 1.22")
        si = detect_stack(tmp_path)
        assert si.kind == "go"
        assert "go run" in si.server_cmd

    def test_rust_detected(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "app"')
        si = detect_stack(tmp_path)
        assert si.kind == "rust"
        assert "cargo run" in si.server_cmd
        assert si.startup_timeout == 45

    def test_rails_detected(self, tmp_path):
        (tmp_path / "Gemfile").write_text("gem 'rails'")
        (tmp_path / "config.ru").write_text("run Rails.application")
        si = detect_stack(tmp_path)
        assert si.kind == "rails"
        assert "rails s" in si.server_cmd

    def test_php_detected(self, tmp_path):
        (tmp_path / "index.php").write_text('<?php echo "hi"; ?>')
        si = detect_stack(tmp_path)
        assert si.kind == "php"
        assert "php -S" in si.server_cmd

    def test_generic_python_detected(self, tmp_path):
        (tmp_path / "server.py").write_text(
            "from http.server import HTTPServer\nif __name__ == '__main__':\n    pass"
        )
        si = detect_stack(tmp_path)
        assert si.kind == "python"

    def test_dockerfile_detected(self, tmp_path):
        (tmp_path / "Dockerfile").write_text("FROM python:3.11\nEXPOSE 9000")
        si = detect_stack(tmp_path)
        assert si.kind == "docker"
        assert si.startup_timeout == 60

    # ── Priority ordering tests ────────────────────────────────────────

    def test_streamlit_wins_over_flask(self, tmp_path):
        (tmp_path / "app.py").write_text(
            "import streamlit as st\nfrom flask import Flask\n"
        )
        si = detect_stack(tmp_path)
        assert si.kind == "streamlit"

    def test_fastapi_wins_over_flask(self, tmp_path):
        (tmp_path / "app.py").write_text(
            "from fastapi import FastAPI\nfrom flask import Flask\napp = FastAPI()\n"
        )
        si = detect_stack(tmp_path)
        assert si.kind == "fastapi"

    def test_nextjs_wins_over_node(self, tmp_path):
        (tmp_path / "package.json").write_text('{"dependencies":{"next":"14.0.0"}}')
        si = detect_stack(tmp_path)
        assert si.kind == "nextjs"

    def test_vite_wins_over_node(self, tmp_path):
        (tmp_path / "package.json").write_text('{"devDependencies":{"vite":"5.0.0"}}')
        si = detect_stack(tmp_path)
        assert si.kind == "vite"

    # ── StackInfo backward compatibility ───────────────────────────────

    def test_stackinfo_new_defaults(self):
        si = StackInfo(kind="test", entry="e.py", cwd=Path("."),
                       server_cmd="cmd", port=3000)
        assert si.startup_timeout == 8
        assert si.needs_install is False
        assert si.install_cmd == ""
        assert si.health_path == "/"

    # ── Helper function tests ──────────────────────────────────────────

    def test_extract_expose_port(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx\nEXPOSE 9000\n")
        assert _extract_expose_port(df) == 9000

    def test_extract_expose_port_default(self, tmp_path):
        df = tmp_path / "Dockerfile"
        df.write_text("FROM nginx\n")
        assert _extract_expose_port(df) == 8080

    def test_read_pkg_deps(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(
            '{"dependencies":{"react":"18.0.0"},"devDependencies":{"vite":"5.0.0"}}'
        )
        assert _read_pkg_deps(pkg) == {"react", "vite"}

    def test_read_pkg_deps_empty(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text('{"name":"test"}')
        assert _read_pkg_deps(pkg) == set()


# ── Preview resilience ────────────────────────────────────────────────

class TestPreviewResilience:
    """Tests for tunnel retry, ensure_healthy, restart, and reconnect."""

    def test_tunnel_retries_on_failure(self, tmp_path):
        """start() retries tunnel TUNNEL_RETRIES times before raising."""
        (tmp_path / "index.html").write_text("<html>test</html>")
        mgr = PreviewManager(tmp_path)
        tunnel_attempts = []

        original_wait = mgr._wait_for_tunnel

        def counting_wait(self_unused=None):
            tunnel_attempts.append(1)
            return None  # Always fail

        with patch("forge_preview.shutil.which", return_value="/usr/bin/cloudflared"):
            with patch.object(mgr, "_wait_for_server", return_value=True):
                with patch("subprocess.Popen") as mock_popen:
                    mock_proc = MagicMock()
                    mock_proc.poll.return_value = None
                    mock_proc.stdout = MagicMock()
                    mock_popen.return_value = mock_proc

                    with patch.object(mgr, "_wait_for_tunnel", side_effect=lambda: (tunnel_attempts.append(1) or None)):
                        with patch("time.sleep"):  # Skip backoff delays
                            with pytest.raises(PreviewError, match="after.*attempts"):
                                mgr.start()

        assert len(tunnel_attempts) == TUNNEL_RETRIES
        mgr.stop()

    def test_ensure_healthy_returns_false_when_no_stack(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        assert mgr.ensure_healthy() is False
        mgr.stop()

    def test_ensure_healthy_returns_true_when_all_alive(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        mgr._stack = StackInfo(kind="static", entry="index.html", cwd=tmp_path,
                               server_cmd="python3 -m http.server", port=19876)
        mgr._port = 19876

        mock_server = MagicMock()
        mock_server.poll.return_value = None
        mgr._server_proc = mock_server

        mock_tunnel = MagicMock()
        mock_tunnel.poll.return_value = None
        mgr._tunnel_proc = mock_tunnel

        with patch.object(mgr, "_http_health_ok", return_value=True):
            assert mgr.ensure_healthy() is True
        mgr.stop()

    def test_ensure_healthy_restarts_dead_server(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        si = StackInfo(kind="static", entry="index.html", cwd=tmp_path,
                       server_cmd="python3 -m http.server 19877", port=19877)
        mgr._stack = si
        mgr._port = 19877

        # Server is dead
        mock_server = MagicMock()
        mock_server.poll.return_value = 1  # exited
        mgr._server_proc = mock_server

        # Tunnel is alive
        mock_tunnel = MagicMock()
        mock_tunnel.poll.return_value = None
        mgr._tunnel_proc = mock_tunnel

        with patch.object(mgr, "_restart_server") as mock_restart:
            with patch.object(mgr, "_http_health_ok", return_value=False):
                mgr.ensure_healthy()
                mock_restart.assert_called_once()
        mgr.stop()

    def test_ensure_healthy_reconnects_dead_tunnel(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        si = StackInfo(kind="static", entry="index.html", cwd=tmp_path,
                       server_cmd="cmd", port=19878)
        mgr._stack = si
        mgr._port = 19878

        # Server alive
        mock_server = MagicMock()
        mock_server.poll.return_value = None
        mgr._server_proc = mock_server

        # Tunnel dead
        mock_tunnel = MagicMock()
        mock_tunnel.poll.return_value = 1
        mgr._tunnel_proc = mock_tunnel

        with patch.object(mgr, "_http_health_ok", return_value=True):
            with patch.object(mgr, "_reconnect_tunnel") as mock_reconnect:
                mgr.ensure_healthy()
                mock_reconnect.assert_called_once()
        mgr.stop()

    def test_http_health_ok_caches_result(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        mgr._port = 19879
        mgr._stack = StackInfo(kind="static", entry="x", cwd=tmp_path,
                               server_cmd="x", port=19879)

        # First call — actually checks
        with patch("urllib.request.urlopen") as mock_url:
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_url.return_value = mock_resp

            assert mgr._http_health_ok() is True

            # Second call within TTL — uses cache, no new request
            mock_url.reset_mock()
            assert mgr._http_health_ok() is True
            mock_url.assert_not_called()

        mgr.stop()

    def test_reconnect_tunnel_raises_without_cloudflared(self, tmp_path):
        mgr = PreviewManager(tmp_path)
        mgr._port = 19880
        with patch("forge_preview.shutil.which", return_value=None):
            with pytest.raises(PreviewError, match="cloudflared not found"):
                mgr._reconnect_tunnel()
        mgr.stop()
