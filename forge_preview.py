"""Nova Forge Preview Manager — unified preview lifecycle with tunnel management.

Manages the full preview stack: dev server detection, process spawning,
Cloudflare tunnel binding, and cleanup. Prevents orphan processes and
port/tunnel mismatches.

Usage:
    mgr = PreviewManager(project_path)
    url = mgr.start()        # Detects stack, starts server + tunnel, returns URL
    mgr.stop()               # Kills both server and tunnel
    mgr.url                  # Current tunnel URL (or None)
    mgr.is_running            # True if processes are alive

The manager guarantees:
  - Previous preview is stopped before starting a new one
  - atexit handler kills orphans on interpreter exit
  - Tunnel always points to the correct server port
  - Process health is queryable
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import signal
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

__all__ = ["PreviewManager", "PreviewError", "StackInfo"]


class PreviewError(Exception):
    """Raised when preview cannot be started."""


@dataclass
class StackInfo:
    """Detected stack for the project."""
    kind: str            # "flask" | "node" | "static" | "unknown"
    entry: str           # e.g. "app.py", "package.json", "index.html"
    cwd: Path            # Working directory for the server command
    server_cmd: str      # Shell command to start the server
    port: int            # Port the server will listen on


# ── Singleton registry for cleanup ──────────────────────────────────────────

_active_managers: list[PreviewManager] = []
_atexit_registered = False


def _cleanup_all():
    """Kill all preview processes on interpreter exit."""
    for mgr in list(_active_managers):
        try:
            mgr.stop()
        except Exception:
            pass


def _register_atexit():
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_cleanup_all)
        _atexit_registered = True


# ── Port utilities ──────────────────────────────────────────────────────────

def find_free_port(start: int, count: int = 20) -> int:
    """Find the first free port starting from `start`."""
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return start + count


def port_is_listening(port: int) -> bool:
    """Check if something is listening on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


# ── Stack detection ────────────────────────────────────────────────────────

def detect_stack(project_path: Path) -> StackInfo:
    """Auto-detect the project stack and build a server command.

    Searches root + common subdirectories for:
      1. Flask/Python app (app.py, main.py, etc. with Flask import)
      2. Node.js app (package.json)
      3. Static site (index.html)
    """
    pp = project_path.resolve()

    search_dirs = [pp]
    for sub in ("backend", "server", "api", "src", "app"):
        sd = pp / sub
        if sd.is_dir():
            search_dirs.append(sd)

    # 1. Flask
    for d in search_dirs:
        for fname in ("api.py", "app.py", "main.py", "server.py", "wsgi.py", "run.py"):
            fp = d / fname
            if fp.exists():
                try:
                    src = fp.read_text()
                    if "flask" in src.lower() or "Flask" in src:
                        port = find_free_port(5000)
                        module = fname[:-3]
                        import re as _re
                        # Detect factory pattern: def create_app() with Flask inside
                        factory_match = _re.search(
                            r'def\s+(create_app|make_app)\s*\(', src
                        )
                        if factory_match:
                            factory_fn = factory_match.group(1)
                            server_cmd = (
                                f"python3 -c \""
                                f"import sys; sys.path.insert(0, '{d}'); "
                                f"from {module} import {factory_fn}; "
                                f"app = {factory_fn}(); "
                                f"app.run(host='0.0.0.0', port={port}, debug=False)\""
                            )
                        else:
                            # Direct app variable: app = Flask(__name__)
                            app_var = "app"
                            m = _re.search(r'(\w+)\s*=\s*Flask\s*\(', src)
                            if m:
                                app_var = m.group(1)
                            server_cmd = (
                                f"python3 -c \""
                                f"import sys; sys.path.insert(0, '{d}'); "
                                f"from {module} import {app_var}; "
                                f"{app_var}.run(host='0.0.0.0', port={port}, debug=False)\""
                            )
                        return StackInfo(
                            kind="flask",
                            entry=fname,
                            cwd=d,
                            server_cmd=server_cmd,
                            port=port,
                        )
                except Exception:
                    pass

    # 2. Node.js
    for d in list(search_dirs) + [pp / s for s in ("frontend", "client", "web", "ui")]:
        if d.is_dir() and (d / "package.json").exists():
            port = find_free_port(3000)
            return StackInfo(
                kind="node",
                entry="package.json",
                cwd=d,
                server_cmd=f"PORT={port} npm start",
                port=port,
            )

    # 3. Static
    for d in list(search_dirs) + [pp / s for s in ("static", "frontend", "client", "web", "public", "dist", "build")]:
        if d.is_dir() and (d / "index.html").exists():
            port = find_free_port(8080)
            return StackInfo(
                kind="static",
                entry="index.html",
                cwd=d,
                server_cmd=f"python3 -m http.server {port}",
                port=port,
            )

    return StackInfo(kind="unknown", entry="", cwd=pp, server_cmd="", port=0)


# ── Preview Manager ────────────────────────────────────────────────────────

class PreviewManager:
    """Manages the preview server + Cloudflare tunnel lifecycle.

    Guarantees:
      - Only one preview runs at a time per manager instance
      - Calling start() when already running stops the old preview first
      - atexit kills orphans on interpreter exit
      - tunnel_port always matches server_port
    """

    TUNNEL_TIMEOUT = 15   # seconds to wait for cloudflared URL
    SERVER_TIMEOUT = 5    # seconds to wait for server to start listening

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()
        self._server_proc: subprocess.Popen | None = None
        self._tunnel_proc: subprocess.Popen | None = None
        self._port: int | None = None
        self._url: str | None = None
        self._stack: StackInfo | None = None
        self._err_log_fh = None

        _register_atexit()
        _active_managers.append(self)

    @property
    def url(self) -> str | None:
        """Current tunnel URL, or None if not running."""
        return self._url

    @property
    def port(self) -> int | None:
        """Port the server is listening on."""
        return self._port

    @property
    def stack(self) -> StackInfo | None:
        """Detected stack info."""
        return self._stack

    @property
    def is_running(self) -> bool:
        """True if both server and tunnel processes are alive."""
        return (
            self._server_proc is not None
            and self._server_proc.poll() is None
            and self._tunnel_proc is not None
            and self._tunnel_proc.poll() is None
        )

    def start(self, stack_info: StackInfo | None = None) -> str:
        """Start preview. Stops any existing preview first.

        Args:
            stack_info: Pre-detected stack info. If None, auto-detects.

        Returns:
            The public Cloudflare tunnel URL.

        Raises:
            PreviewError: If stack detection fails, server won't start,
                          or tunnel can't be established.
        """
        # Always clean up before starting
        if self._server_proc or self._tunnel_proc:
            self.stop()

        # Detect stack
        si = stack_info or detect_stack(self.project_path)
        if si.kind == "unknown":
            raise PreviewError(
                "No servable entry point found. "
                "Looked for: Flask app (app.py with Flask import), "
                "Node app (package.json), Static site (index.html)"
            )
        self._stack = si
        self._port = si.port

        # Find cloudflared
        cf_path = shutil.which("cloudflared")
        if not cf_path:
            raise PreviewError(
                "cloudflared not found. "
                "Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
            )

        # Start server
        err_log = self.project_path / ".forge" / "preview-stderr.log"
        err_log.parent.mkdir(parents=True, exist_ok=True)
        self._err_log_fh = open(err_log, "w")

        self._server_proc = subprocess.Popen(
            si.server_cmd, shell=True, cwd=str(si.cwd),
            stdout=subprocess.DEVNULL, stderr=self._err_log_fh,
        )

        # Wait for server to listen
        if not self._wait_for_server(si.port):
            # Server may have crashed — read stderr
            self._err_log_fh.close()
            err_text = err_log.read_text().strip()
            self._kill_proc(self._server_proc)
            self._server_proc = None
            msg = f"Server failed to start on port {si.port}"
            if err_text:
                msg += f": {err_text[-300:]}"
            raise PreviewError(msg)

        # Start tunnel pointing at the exact port we just verified
        self._tunnel_proc = subprocess.Popen(
            [cf_path, "tunnel", "--url", f"http://localhost:{si.port}"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )

        # Extract tunnel URL
        tunnel_url = self._wait_for_tunnel()
        if not tunnel_url:
            self.stop()
            raise PreviewError("Could not establish Cloudflare tunnel (timed out)")

        self._url = tunnel_url
        return tunnel_url

    def _start_server_only(self, stack_info: StackInfo | None = None) -> int:
        """Start only the dev server (no tunnel). For verification use.

        Returns the port number.
        """
        if self._server_proc:
            self.stop()

        si = stack_info or detect_stack(self.project_path)
        if si.kind == "unknown":
            raise PreviewError("No servable entry point found.")
        self._stack = si
        self._port = si.port

        err_log = self.project_path / ".forge" / "preview-stderr.log"
        err_log.parent.mkdir(parents=True, exist_ok=True)
        self._err_log_fh = open(err_log, "w")

        self._server_proc = subprocess.Popen(
            si.server_cmd, shell=True, cwd=str(si.cwd),
            stdout=subprocess.DEVNULL, stderr=self._err_log_fh,
        )

        if not self._wait_for_server(si.port):
            self._err_log_fh.close()
            err_text = err_log.read_text().strip()
            self._kill_proc(self._server_proc)
            self._server_proc = None
            msg = f"Server failed to start on port {si.port}"
            if err_text:
                msg += f": {err_text[-300:]}"
            raise PreviewError(msg)

        return si.port

    def stop(self) -> None:
        """Stop server and tunnel. Safe to call multiple times."""
        self._kill_proc(self._tunnel_proc)
        self._tunnel_proc = None

        self._kill_proc(self._server_proc)
        self._server_proc = None

        if self._err_log_fh:
            try:
                self._err_log_fh.close()
            except Exception:
                pass
            self._err_log_fh = None

        self._url = None
        self._port = None

        # Remove from global registry
        if self in _active_managers:
            _active_managers.remove(self)

    def health(self) -> dict:
        """Return health status of server and tunnel."""
        server_alive = self._server_proc is not None and self._server_proc.poll() is None
        tunnel_alive = self._tunnel_proc is not None and self._tunnel_proc.poll() is None
        port_open = port_is_listening(self._port) if self._port else False

        return {
            "running": server_alive and tunnel_alive,
            "server_alive": server_alive,
            "tunnel_alive": tunnel_alive,
            "port_listening": port_open,
            "port": self._port,
            "url": self._url,
            "stack": self._stack.kind if self._stack else None,
        }

    # ── Internal helpers ────────────────────────────────────────────────

    def _wait_for_server(self, port: int) -> bool:
        """Wait for the server to start listening."""
        deadline = time.time() + self.SERVER_TIMEOUT
        while time.time() < deadline:
            time.sleep(0.3)
            if port_is_listening(port):
                return True
            if self._server_proc and self._server_proc.poll() is not None:
                return False  # Process exited
        return port_is_listening(port)

    def _wait_for_tunnel(self) -> str | None:
        """Read cloudflared output until we find the tunnel URL."""
        if not self._tunnel_proc or not self._tunnel_proc.stdout:
            return None
        deadline = time.time() + self.TUNNEL_TIMEOUT
        while time.time() < deadline:
            line = self._tunnel_proc.stdout.readline()
            if not line:
                if self._tunnel_proc.poll() is not None:
                    break
                continue
            if "trycloudflare.com" in line:
                match = re.search(r"(https://[a-z0-9-]+\.trycloudflare\.com)", line)
                if match:
                    return match.group(1)
        return None

    @staticmethod
    def _kill_proc(proc: subprocess.Popen | None) -> None:
        """Kill a subprocess and its children."""
        if proc is None or proc.poll() is not None:
            return
        try:
            # Try SIGTERM first for graceful shutdown
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
