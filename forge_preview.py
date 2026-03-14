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
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

__all__ = ["PreviewManager", "PreviewError", "StackInfo"]

# ── Resilience constants ──────────────────────────────────────────────────
TUNNEL_RETRIES = 3
TUNNEL_BACKOFF_BASE = 2.0
HEALTH_CACHE_TTL = 5.0

# ── cloudflared download URLs ────────────────────────────────────────────
import platform as _platform

_CF_VERSION = "2025.2.1"
_CF_BASE = f"https://github.com/cloudflare/cloudflared/releases/download/{_CF_VERSION}"
_CF_URLS: dict[tuple[str, str], str] = {
    ("Linux", "x86_64"):  f"{_CF_BASE}/cloudflared-linux-amd64",
    ("Linux", "aarch64"): f"{_CF_BASE}/cloudflared-linux-arm64",
    ("Darwin", "x86_64"): f"{_CF_BASE}/cloudflared-darwin-amd64.tgz",
    ("Darwin", "arm64"):  f"{_CF_BASE}/cloudflared-darwin-arm64.tgz",
}


def _ensure_cloudflared() -> str | None:
    """Return path to cloudflared, downloading it if necessary.

    Downloads to ~/.forge/bin/cloudflared (user-local, no sudo needed).
    Returns None if platform is unsupported or download fails.
    """
    # Already on PATH?
    existing = shutil.which("cloudflared")
    if existing:
        return existing

    # Check our local install location
    local_bin = Path.home() / ".forge" / "bin"
    local_cf = local_bin / "cloudflared"
    if local_cf.exists() and os.access(local_cf, os.X_OK):
        return str(local_cf)

    # Determine download URL
    system = _platform.system()
    machine = _platform.machine()
    url = _CF_URLS.get((system, machine))
    if not url:
        return None

    # Download
    try:
        local_bin.mkdir(parents=True, exist_ok=True)
        if url.endswith(".tgz"):
            import tarfile
            import io
            data = urllib.request.urlopen(url, timeout=60).read()
            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
                for member in tar.getmembers():
                    if member.name.endswith("cloudflared"):
                        f = tar.extractfile(member)
                        if f:
                            local_cf.write_bytes(f.read())
                            break
        else:
            data = urllib.request.urlopen(url, timeout=60).read()
            local_cf.write_bytes(data)
        local_cf.chmod(0o755)
        return str(local_cf)
    except Exception:
        return None


class PreviewError(Exception):
    """Raised when preview cannot be started."""


@dataclass
class StackInfo:
    """Detected stack for the project."""
    kind: str            # "flask" | "node" | "static" | "unknown" | ...
    entry: str           # e.g. "app.py", "package.json", "index.html"
    cwd: Path            # Working directory for the server command
    server_cmd: str      # Shell command to start the server
    port: int            # Port the server will listen on
    startup_timeout: int = 8
    needs_install: bool = False
    install_cmd: str = ""
    health_path: str = "/"


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

def find_free_port(start: int, count: int = 100) -> int:
    """Find the first free port starting from `start`."""
    for p in range(start, start + count):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    # Fallback: bind to port 0 to let the OS pick
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_is_listening(port: int) -> bool:
    """Check if something is listening on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


# ── Stack detection helpers ────────────────────────────────────────────────

def _build_search_dirs(pp: Path) -> list[Path]:
    """Root + common backend subdirectories."""
    dirs = [pp]
    for sub in ("backend", "server", "api", "src", "app"):
        sd = pp / sub
        if sd.is_dir():
            dirs.append(sd)
    return dirs


def _frontend_dirs(pp: Path) -> list[Path]:
    """Frontend subdirectories."""
    return [pp / s for s in ("frontend", "client", "web", "ui") if (pp / s).is_dir()]


def _static_dirs(pp: Path) -> list[Path]:
    """Static asset subdirectories."""
    return [pp / s for s in ("static", "public", "dist", "build") if (pp / s).is_dir()]


def _read_pkg_deps(pkg_file: Path) -> set[str]:
    """Return set of all dependency names from a package.json."""
    import json
    try:
        pkg = json.loads(pkg_file.read_text())
        deps = set(pkg.get("dependencies", {}).keys())
        deps |= set(pkg.get("devDependencies", {}).keys())
        return deps
    except Exception:
        return set()


def _extract_expose_port(dockerfile: Path) -> int:
    """Parse EXPOSE line from Dockerfile, return port or 8080 default."""
    try:
        for line in dockerfile.read_text().splitlines():
            line = line.strip()
            if line.upper().startswith("EXPOSE"):
                parts = line.split()
                if len(parts) >= 2:
                    port_str = parts[1].split("/")[0]  # handle EXPOSE 8080/tcp
                    return int(port_str)
    except Exception:
        pass
    return 8080


# ── Individual stack detectors ─────────────────────────────────────────────
# Each takes (pp: Path, search_dirs: list[Path]) -> StackInfo | None
# Return None if not detected.  Order: most specific → least specific.

def _detect_streamlit(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Streamlit app — .py files with `import streamlit`."""
    for d in search_dirs:
        for f in sorted(d.glob("*.py")):
            try:
                src = f.read_text()
                if "import streamlit" in src or "from streamlit" in src:
                    port = find_free_port(5000)
                    return StackInfo(
                        kind="streamlit",
                        entry=f.name,
                        cwd=d,
                        server_cmd=(
                            f"streamlit run {f.name} --server.port={port} "
                            f"--server.address=0.0.0.0 --server.headless=true"
                        ),
                        port=port,
                        startup_timeout=15,
                        needs_install=True,
                        install_cmd="pip install -r requirements.txt",
                    )
            except Exception:
                pass
    return None


def _detect_fastapi(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect FastAPI app — .py files with `from fastapi` or `import fastapi`."""
    for d in search_dirs:
        for f in sorted(d.glob("*.py")):
            try:
                src = f.read_text()
                if "from fastapi" in src or "import fastapi" in src:
                    port = find_free_port(5000)
                    module = f.name[:-3]
                    app_var = "app"
                    m = re.search(r'(\w+)\s*=\s*FastAPI\s*\(', src)
                    if m:
                        app_var = m.group(1)
                    return StackInfo(
                        kind="fastapi",
                        entry=f.name,
                        cwd=d,
                        server_cmd=f"uvicorn {module}:{app_var} --host 0.0.0.0 --port {port}",
                        port=port,
                        startup_timeout=10,
                        needs_install=True,
                        install_cmd="pip install -r requirements.txt",
                    )
            except Exception:
                pass
    return None


def _detect_flask(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Flask app — factory pattern + app variable extraction."""
    for d in search_dirs:
        for fname in ("api.py", "app.py", "main.py", "server.py", "wsgi.py", "run.py"):
            fp = d / fname
            if fp.exists():
                try:
                    src = fp.read_text()
                    if "flask" in src.lower() or "Flask" in src:
                        port = find_free_port(5000)
                        module = fname[:-3]
                        # Detect factory pattern: def create_app() with Flask inside
                        factory_match = re.search(
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
                            m = re.search(r'(\w+)\s*=\s*Flask\s*\(', src)
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
                            startup_timeout=8,
                            needs_install=True,
                            install_cmd="pip install -r requirements.txt",
                        )
                except Exception:
                    pass
    return None


def _detect_django(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Django project — manage.py with 'django' in content."""
    for d in search_dirs:
        manage = d / "manage.py"
        if manage.exists():
            try:
                src = manage.read_text()
                if "django" in src.lower():
                    port = find_free_port(5000)
                    return StackInfo(
                        kind="django",
                        entry="manage.py",
                        cwd=d,
                        server_cmd=f"python3 manage.py runserver 0.0.0.0:{port} --noreload",
                        port=port,
                        startup_timeout=12,
                        needs_install=True,
                        install_cmd="pip install -r requirements.txt",
                    )
            except Exception:
                pass
    return None


def _detect_nextjs(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Next.js — package.json with 'next' dependency."""
    for d in list(search_dirs) + _frontend_dirs(pp):
        pkg_file = d / "package.json"
        if d.is_dir() and pkg_file.exists():
            deps = _read_pkg_deps(pkg_file)
            if "next" in deps:
                port = find_free_port(3000)
                return StackInfo(
                    kind="nextjs",
                    entry="package.json",
                    cwd=d,
                    server_cmd=f"PORT={port} npx next dev -p {port}",
                    port=port,
                    startup_timeout=30,
                    needs_install=True,
                    install_cmd="npm install",
                )
    return None


def _detect_vite(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Vite — devDeps with 'vite' or vite.config.{ts,js,mjs}."""
    for d in list(search_dirs) + _frontend_dirs(pp):
        if not d.is_dir():
            continue
        pkg_file = d / "package.json"
        has_vite = False
        if pkg_file.exists():
            deps = _read_pkg_deps(pkg_file)
            if "vite" in deps:
                has_vite = True
        if not has_vite:
            for ext in ("ts", "js", "mjs"):
                if (d / f"vite.config.{ext}").exists():
                    has_vite = True
                    break
        if has_vite:
            port = find_free_port(3000)
            return StackInfo(
                kind="vite",
                entry="package.json",
                cwd=d,
                server_cmd=f"npx vite --host 0.0.0.0 --port {port}",
                port=port,
                startup_timeout=20,
                needs_install=True,
                install_cmd="npm install",
            )
    return None


def _detect_node(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect generic Node.js — package.json exists (after nextjs/vite)."""
    for d in list(search_dirs) + _frontend_dirs(pp):
        if d.is_dir() and (d / "package.json").exists():
            port = find_free_port(3000)
            return StackInfo(
                kind="node",
                entry="package.json",
                cwd=d,
                server_cmd=f"PORT={port} npm start",
                port=port,
                startup_timeout=15,
                needs_install=True,
                install_cmd="npm install",
            )
    return None


def _detect_go(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Go project — go.mod or main.go."""
    for d in search_dirs:
        if (d / "go.mod").exists() or (d / "main.go").exists():
            entry = "go.mod" if (d / "go.mod").exists() else "main.go"
            port = find_free_port(8080)
            return StackInfo(
                kind="go",
                entry=entry,
                cwd=d,
                server_cmd=f"PORT={port} go run .",
                port=port,
                startup_timeout=20,
            )
    return None


def _detect_rust(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Rust project — Cargo.toml."""
    for d in search_dirs:
        if (d / "Cargo.toml").exists():
            port = find_free_port(8080)
            return StackInfo(
                kind="rust",
                entry="Cargo.toml",
                cwd=d,
                server_cmd=f"PORT={port} cargo run",
                port=port,
                startup_timeout=45,
            )
    return None


def _detect_rails(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Rails — Gemfile with 'rails' AND config.ru."""
    for d in search_dirs:
        gemfile = d / "Gemfile"
        config_ru = d / "config.ru"
        if gemfile.exists() and config_ru.exists():
            try:
                src = gemfile.read_text()
                if "rails" in src.lower():
                    port = find_free_port(8080)
                    return StackInfo(
                        kind="rails",
                        entry="config.ru",
                        cwd=d,
                        server_cmd=f"bundle exec rails s -b 0.0.0.0 -p {port}",
                        port=port,
                        startup_timeout=25,
                        needs_install=True,
                        install_cmd="bundle install",
                    )
            except Exception:
                pass
    return None


def _detect_php(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect PHP — index.php or composer.json."""
    for d in search_dirs:
        if (d / "index.php").exists() or (d / "composer.json").exists():
            entry = "index.php" if (d / "index.php").exists() else "composer.json"
            port = find_free_port(8080)
            return StackInfo(
                kind="php",
                entry=entry,
                cwd=d,
                server_cmd=f"php -S 0.0.0.0:{port}",
                port=port,
                startup_timeout=5,
            )
    return None


def _detect_generic_python(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect generic Python server — .py with __main__ + server patterns."""
    server_patterns = re.compile(
        r'http\.server|socket|\.listen\(|\.serve|BaseHTTPServer|HTTPServer|uvicorn|gunicorn'
    )
    for d in search_dirs:
        for f in sorted(d.glob("*.py")):
            try:
                src = f.read_text()
                if 'if __name__' in src and server_patterns.search(src):
                    port = find_free_port(5000)
                    return StackInfo(
                        kind="python",
                        entry=f.name,
                        cwd=d,
                        server_cmd=f"PORT={port} python3 {f.name}",
                        port=port,
                        startup_timeout=10,
                    )
            except Exception:
                pass
    return None


def _detect_dockerfile(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect Dockerfile — build and run container."""
    for d in search_dirs:
        dockerfile = d / "Dockerfile"
        if dockerfile.exists():
            exposed = _extract_expose_port(dockerfile)
            port = find_free_port(8080)
            return StackInfo(
                kind="docker",
                entry="Dockerfile",
                cwd=d,
                server_cmd=(
                    f"docker build -t forge-preview . && "
                    f"docker run --rm -p {port}:{exposed} forge-preview"
                ),
                port=port,
                startup_timeout=60,
            )
    return None


def _detect_static(pp: Path, search_dirs: list[Path]) -> StackInfo | None:
    """Detect static site — index.html in root, subdirs, frontend, or static dirs."""
    all_dirs = list(search_dirs) + _frontend_dirs(pp) + _static_dirs(pp)
    for d in all_dirs:
        if d.is_dir() and (d / "index.html").exists():
            port = find_free_port(8080)
            return StackInfo(
                kind="static",
                entry="index.html",
                cwd=d,
                server_cmd=f"python3 -m http.server {port}",
                port=port,
                startup_timeout=3,
            )
    return None


# ── Detector registry ──────────────────────────────────────────────────────

_STACK_DETECTORS: list = [
    _detect_streamlit, _detect_fastapi, _detect_flask, _detect_django,
    _detect_nextjs, _detect_vite, _detect_node,
    _detect_go, _detect_rust, _detect_rails, _detect_php,
    _detect_generic_python, _detect_dockerfile, _detect_static,
]


def detect_stack(project_path: Path) -> StackInfo:
    """Auto-detect the project stack and build a server command.

    Checks 14 detector functions in priority order; first match wins.
    """
    pp = project_path.resolve()
    search_dirs = _build_search_dirs(pp)
    for detector in _STACK_DETECTORS:
        result = detector(pp, search_dirs)
        if result is not None:
            return result
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
    _health_cache: tuple[float, bool] | None = None  # (timestamp, healthy)

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
        """True if server is alive (and tunnel too, if started)."""
        if self._server_proc is None or self._server_proc.poll() is not None:
            return False
        # Local-only mode: no tunnel proc
        if self._tunnel_proc is None:
            return True
        return self._tunnel_proc.poll() is None

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
                "Supported: Streamlit, FastAPI, Flask, Django, Next.js, Vite, Node.js, "
                "Go, Rust, Rails, PHP, Docker, Static. "
                "Hint: create app.py (Flask/FastAPI/Streamlit), package.json with start script, "
                "index.html, or a Dockerfile."
            )
        self._stack = si
        self._port = si.port

        # Find cloudflared (auto-downloads if missing, falls back to local-only)
        cf_path = _ensure_cloudflared()

        # Install deps if needed
        if si.needs_install and si.install_cmd:
            self._run_install(si)

        # Start server
        err_log = self.project_path / ".forge" / "preview-stderr.log"
        err_log.parent.mkdir(parents=True, exist_ok=True)
        self._err_log_fh = open(err_log, "w")

        self._server_proc = subprocess.Popen(
            si.server_cmd, shell=True, cwd=str(si.cwd),
            stdout=subprocess.DEVNULL, stderr=self._err_log_fh,
        )

        # Wait for server to listen (per-stack timeout)
        if not self._wait_for_server(si.port, timeout=si.startup_timeout):
            # Server may have crashed — read stderr
            self._err_log_fh.close()
            err_text = err_log.read_text().strip()
            self._kill_proc(self._server_proc)
            self._server_proc = None
            msg = f"Server failed to start on port {si.port}"
            if err_text:
                msg += f": {err_text[-300:]}"
            raise PreviewError(msg)

        # Start tunnel with retry + exponential backoff (or local-only fallback)
        if not cf_path:
            local_url = f"http://localhost:{si.port}"
            self._url = local_url
            self._health_cache = None
            return local_url

        tunnel_url = None
        last_err = "timed out"
        for attempt in range(TUNNEL_RETRIES):
            self._tunnel_proc = subprocess.Popen(
                [cf_path, "tunnel", "--url", f"http://localhost:{si.port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            tunnel_url = self._wait_for_tunnel()
            if tunnel_url:
                break
            # Kill failed tunnel before retrying
            self._kill_proc(self._tunnel_proc)
            self._tunnel_proc = None
            if attempt < TUNNEL_RETRIES - 1:
                delay = TUNNEL_BACKOFF_BASE * (2 ** attempt)
                time.sleep(delay)

        if not tunnel_url:
            self.stop()
            raise PreviewError(f"Could not establish Cloudflare tunnel after {TUNNEL_RETRIES} attempts ({last_err})")

        self._url = tunnel_url
        self._health_cache = None
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

        # Install deps if needed
        if si.needs_install and si.install_cmd:
            self._run_install(si)

        err_log = self.project_path / ".forge" / "preview-stderr.log"
        err_log.parent.mkdir(parents=True, exist_ok=True)
        self._err_log_fh = open(err_log, "w")

        self._server_proc = subprocess.Popen(
            si.server_cmd, shell=True, cwd=str(si.cwd),
            stdout=subprocess.DEVNULL, stderr=self._err_log_fh,
        )

        if not self._wait_for_server(si.port, timeout=si.startup_timeout):
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

    def ensure_healthy(self) -> bool:
        """Check server + tunnel health. Auto-restart components if dead.

        Returns True if preview is healthy (or was successfully recovered).
        Returns False if recovery failed.
        """
        if not self._stack or not self._port:
            return False

        server_alive = self._server_proc is not None and self._server_proc.poll() is None
        tunnel_alive = self._tunnel_proc is not None and self._tunnel_proc.poll() is None
        local_only = self._tunnel_proc is None and self._url and self._url.startswith("http://localhost")
        http_ok = self._http_health_ok()

        # Everything healthy
        if server_alive and (tunnel_alive or local_only) and http_ok:
            return True

        # Server died — restart it
        if not server_alive or not http_ok:
            try:
                self._restart_server()
            except PreviewError:
                return False

        # Tunnel died — reconnect (skip in local-only mode)
        if not tunnel_alive and not local_only:
            try:
                self._reconnect_tunnel()
            except PreviewError:
                return False

        return True

    def _restart_server(self) -> None:
        """Restart the dev server using the same StackInfo."""
        if not self._stack:
            raise PreviewError("No stack info — cannot restart server")

        self._kill_proc(self._server_proc)
        self._server_proc = None

        si = self._stack
        err_log = self.project_path / ".forge" / "preview-stderr.log"
        err_log.parent.mkdir(parents=True, exist_ok=True)
        if self._err_log_fh:
            try:
                self._err_log_fh.close()
            except Exception:
                pass
        self._err_log_fh = open(err_log, "w")

        self._server_proc = subprocess.Popen(
            si.server_cmd, shell=True, cwd=str(si.cwd),
            stdout=subprocess.DEVNULL, stderr=self._err_log_fh,
        )

        if not self._wait_for_server(si.port, timeout=si.startup_timeout):
            self._err_log_fh.close()
            err_text = err_log.read_text().strip()
            self._kill_proc(self._server_proc)
            self._server_proc = None
            msg = f"Server restart failed on port {si.port}"
            if err_text:
                msg += f": {err_text[-300:]}"
            raise PreviewError(msg)

    def _reconnect_tunnel(self) -> None:
        """Kill old tunnel and start a new one, extracting the new URL."""
        self._kill_proc(self._tunnel_proc)
        self._tunnel_proc = None

        cf_path = _ensure_cloudflared()
        if not cf_path:
            raise PreviewError("cloudflared not found — cannot reconnect tunnel")

        tunnel_url = None
        for attempt in range(TUNNEL_RETRIES):
            self._tunnel_proc = subprocess.Popen(
                [cf_path, "tunnel", "--url", f"http://localhost:{self._port}"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            )
            tunnel_url = self._wait_for_tunnel()
            if tunnel_url:
                break
            self._kill_proc(self._tunnel_proc)
            self._tunnel_proc = None
            if attempt < TUNNEL_RETRIES - 1:
                time.sleep(TUNNEL_BACKOFF_BASE * (2 ** attempt))

        if not tunnel_url:
            raise PreviewError(f"Tunnel reconnection failed after {TUNNEL_RETRIES} attempts")

        self._url = tunnel_url

    def _http_health_ok(self) -> bool:
        """HTTP GET to server health_path. Result cached for HEALTH_CACHE_TTL seconds."""
        if not self._port:
            return False

        # Return cached result if fresh
        if self._health_cache is not None:
            ts, result = self._health_cache
            if time.time() - ts < HEALTH_CACHE_TTL:
                return result

        health_path = self._stack.health_path if self._stack else "/"
        try:
            url = f"http://127.0.0.1:{self._port}{health_path}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                ok = resp.status < 500
        except Exception:
            ok = False

        self._health_cache = (time.time(), ok)
        return ok

    # ── Internal helpers ────────────────────────────────────────────────

    def _run_install(self, si: StackInfo) -> None:
        """Install dependencies if needed. Smart skip if already installed."""
        # Skip if node_modules exists for JS stacks
        if si.kind in ("node", "nextjs", "vite") and (si.cwd / "node_modules").is_dir():
            return
        # Skip if Python package already importable
        if si.kind in ("flask", "fastapi", "streamlit", "django"):
            pkg = {"streamlit": "streamlit", "fastapi": "fastapi",
                   "flask": "flask", "django": "django"}[si.kind]
            r = subprocess.run(
                ["python3", "-c", f"import {pkg}"],
                capture_output=True, timeout=5,
            )
            if r.returncode == 0:
                return
        # Run install
        r = subprocess.run(
            si.install_cmd, shell=True, cwd=str(si.cwd),
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            # PEP 668: retry with --break-system-packages for pip commands
            if "externally-managed" in r.stderr.lower() and "pip" in si.install_cmd:
                cmd_retry = si.install_cmd.replace(
                    "pip install", "pip install --break-system-packages"
                )
                r = subprocess.run(
                    cmd_retry, shell=True, cwd=str(si.cwd),
                    capture_output=True, text=True, timeout=120,
                )
                if r.returncode == 0:
                    return
            raise PreviewError(f"Install failed ({si.install_cmd}): {r.stderr[-300:]}")

    def _wait_for_server(self, port: int, timeout: int | None = None) -> bool:
        """Wait for the server to start listening."""
        deadline = time.time() + (timeout or self.SERVER_TIMEOUT)
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
