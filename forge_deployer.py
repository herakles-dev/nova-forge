"""Nova Forge Deployer — Phase 5: Docker + nginx deployment.

Handles port assignment from PORT_REGISTRY, Dockerfile generation,
container lifecycle, nginx reverse-proxy config, and health verification.

Security invariant: all port bindings use 127.0.0.1 (never 0.0.0.0).
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib import request, error as urllib_error

from config import ForgeProject

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

PORT_REGISTRY_PATH = Path("/home/hercules/system-apps-config/PORT_REGISTRY.json")
NOVA_FORGE_PORT_START = 8161
NOVA_FORGE_PORT_END   = 8199
HERAKLES_DOMAIN       = "herakles.dev"
NGINX_SITES_ENABLED   = Path("/etc/nginx/sites-enabled")
HEALTH_POLL_INTERVAL  = 2   # seconds between health checks
HEALTH_TIMEOUT        = 30  # seconds before giving up

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class DeployResult:
    url: str            # e.g. "https://weather.herakles.dev"
    port: int           # e.g. 8162
    container_id: str   # Docker container ID (short)
    health_status: bool # True if /health (or /) responded 2xx within timeout
    error: Optional[str] = None  # set when deployment fails


# ── Port registry helpers ─────────────────────────────────────────────────────

def _load_registry() -> dict:
    with PORT_REGISTRY_PATH.open() as fh:
        return json.load(fh)


def _save_registry(data: dict) -> None:
    with PORT_REGISTRY_PATH.open("w") as fh:
        json.dump(data, fh, indent=2)


def _allocated_ports(registry: dict) -> set[int]:
    """Collect every numeric port key found anywhere in allocations."""
    ports: set[int] = set()
    for section in registry.get("allocations", {}).values():
        if not isinstance(section, dict):
            continue
        for key in section:
            if str(key).isdigit():
                ports.add(int(key))
    return ports


def assign_port(requested: Optional[int] = None, registry: Optional[dict] = None) -> int:
    """Return an available port in the Nova Forge range, updating the registry.

    Raises ValueError if the requested port is already allocated or no free
    port exists in the 8161-8199 range.
    """
    if registry is None:
        registry = _load_registry()

    allocated = _allocated_ports(registry)

    if requested is not None:
        if requested in allocated:
            raise ValueError(f"Port {requested} is already allocated in PORT_REGISTRY")
        port = requested
    else:
        port = next(
            (p for p in range(NOVA_FORGE_PORT_START, NOVA_FORGE_PORT_END + 1)
             if p not in allocated),
            None,
        )
        if port is None:
            raise ValueError(
                f"No free ports in Nova Forge range {NOVA_FORGE_PORT_START}-{NOVA_FORGE_PORT_END}"
            )

    # Register it immediately so parallel deploys cannot grab the same port.
    nova_forge_section = registry.setdefault("allocations", {}).setdefault("nova_forge", {})
    nova_forge_section[str(port)] = {
        "service": "nova-forge-app",
        "status": "deploying",
    }
    _save_registry(registry)
    return port


def update_registry_entry(port: int, service: str, domain: str, status: str) -> None:
    registry = _load_registry()
    nova_forge_section = registry.setdefault("allocations", {}).setdefault("nova_forge", {})
    nova_forge_section[str(port)] = {
        "service": service,
        "type": "web",
        "subdomain": domain,
        "status": status,
    }
    _save_registry(registry)


# ── Dockerfile generation ─────────────────────────────────────────────────────

def _detect_stack(project: ForgeProject) -> str:
    """Detect stack kind via unified detector, mapped to deployer categories."""
    from forge_preview import detect_stack as _unified_detect_stack
    si = _unified_detect_stack(project.root)
    _DEPLOYER_MAP = {
        "flask": "flask", "fastapi": "fastapi", "django": "django",
        "streamlit": "streamlit",
        "node": "node", "nextjs": "node", "vite": "static",
        "go": "go", "rust": "rust", "rails": "rails", "php": "php",
        "docker": "docker", "static": "static", "python": "flask",
    }
    return _DEPLOYER_MAP.get(si.kind, "static")


def _dockerfile_for_stack(stack: str, internal_port: int) -> str:
    if stack == "flask":
        return (
            f"FROM python:3.11-slim\n"
            f"WORKDIR /app\n"
            f"COPY requirements.txt* ./\n"
            f"RUN pip install --no-cache-dir -r requirements.txt gunicorn 2>/dev/null || pip install --no-cache-dir gunicorn\n"
            f"COPY . .\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["gunicorn", "--bind", "0.0.0.0:{internal_port}", "--workers", "2", "app:app"]\n'
        )
    if stack == "fastapi":
        return (
            f"FROM python:3.11-slim\n"
            f"WORKDIR /app\n"
            f"COPY requirements.txt* ./\n"
            f"RUN pip install --no-cache-dir -r requirements.txt uvicorn 2>/dev/null || pip install --no-cache-dir uvicorn\n"
            f"COPY . .\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "{internal_port}"]\n'
        )
    if stack == "streamlit":
        return (
            f"FROM python:3.11-slim\n"
            f"WORKDIR /app\n"
            f"COPY requirements.txt* ./\n"
            f"RUN pip install --no-cache-dir -r requirements.txt streamlit 2>/dev/null || pip install --no-cache-dir streamlit\n"
            f"COPY . .\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["streamlit", "run", "app.py", "--server.port={internal_port}", "--server.address=0.0.0.0", "--server.headless=true"]\n'
        )
    if stack == "django":
        return (
            f"FROM python:3.11-slim\n"
            f"WORKDIR /app\n"
            f"COPY requirements.txt* ./\n"
            f"RUN pip install --no-cache-dir -r requirements.txt gunicorn 2>/dev/null || pip install --no-cache-dir gunicorn\n"
            f"COPY . .\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["gunicorn", "--bind", "0.0.0.0:{internal_port}", "--workers", "2", "wsgi:application"]\n'
        )
    if stack == "go":
        return (
            f"FROM golang:1.22-alpine AS builder\n"
            f"WORKDIR /app\n"
            f"COPY go.* ./\n"
            f"RUN go mod download 2>/dev/null || true\n"
            f"COPY . .\n"
            f"RUN go build -o server .\n"
            f"FROM alpine:3.19\n"
            f"COPY --from=builder /app/server /server\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["/server"]\n'
        )
    if stack == "rust":
        return (
            f"FROM rust:1.77-slim AS builder\n"
            f"WORKDIR /app\n"
            f"COPY Cargo.* ./\n"
            f"COPY src/ src/\n"
            f"RUN cargo build --release\n"
            f"FROM debian:bookworm-slim\n"
            f"COPY --from=builder /app/target/release/* /usr/local/bin/\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["app"]\n'
        )
    if stack == "rails":
        return (
            f"FROM ruby:3.3-slim\n"
            f"WORKDIR /app\n"
            f"COPY Gemfile* ./\n"
            f"RUN bundle install\n"
            f"COPY . .\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["bundle", "exec", "rails", "s", "-b", "0.0.0.0", "-p", "{internal_port}"]\n'
        )
    if stack == "php":
        return (
            f"FROM php:8.3-apache\n"
            f"COPY . /var/www/html/\n"
            f"EXPOSE {internal_port}\n"
        )
    if stack == "node":
        return (
            f"FROM node:20-slim\n"
            f"WORKDIR /app\n"
            f"COPY package*.json ./\n"
            f"RUN npm ci --omit=dev\n"
            f"COPY . .\n"
            f"EXPOSE {internal_port}\n"
            f'CMD ["npm", "start"]\n'
        )
    # static fallback (includes vite build output)
    return (
        f"FROM nginx:alpine\n"
        f"COPY . /usr/share/nginx/html\n"
        f"EXPOSE {internal_port}\n"
    )


def ensure_dockerfile(project: ForgeProject, internal_port: int) -> str:
    """Write a minimal Dockerfile if absent. Returns detected stack."""
    dockerfile = project.root / "Dockerfile"
    stack = _detect_stack(project)
    if not dockerfile.exists():
        logger.info("No Dockerfile found; generating %s Dockerfile", stack)
        dockerfile.write_text(_dockerfile_for_stack(stack, internal_port))
    else:
        logger.info("Using existing Dockerfile")
    return stack


# ── Nginx configuration ───────────────────────────────────────────────────────

def _nginx_config(domain: str, port: int) -> str:
    return (
        f"server {{\n"
        f"    listen 80;\n"
        f"    server_name {domain};\n"
        f"\n"
        f"    location / {{\n"
        f"        proxy_pass http://127.0.0.1:{port};\n"
        f"        proxy_set_header Host $host;\n"
        f"        proxy_set_header X-Real-IP $remote_addr;\n"
        f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"        proxy_set_header X-Forwarded-Proto $scheme;\n"
        f"    }}\n"
        f"}}\n"
    )


def write_nginx_config(domain: str, port: int) -> Path:
    conf_path = NGINX_SITES_ENABLED / f"{domain}.conf"
    conf_path.write_text(_nginx_config(domain, port))
    logger.info("Wrote nginx config: %s", conf_path)

    result = subprocess.run(
        ["nginx", "-t"], capture_output=True, text=True, timeout=30
    )
    if result.returncode != 0:
        raise RuntimeError(f"nginx config test failed: {result.stderr}")

    subprocess.run(["nginx", "-s", "reload"], check=True, timeout=30)
    logger.info("nginx reloaded")
    return conf_path


def provision_ssl(domain: str) -> None:
    """Run certbot for *.herakles.dev domains (best-effort; logs on failure)."""
    logger.info("Requesting SSL certificate for %s", domain)
    result = subprocess.run(
        [
            "certbot", "--nginx",
            "-d", domain,
            "--non-interactive",
            "--agree-tos",
            "--email", "admin@herakles.dev",
        ],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        logger.warning("certbot failed (non-fatal): %s", result.stderr.strip())
    else:
        logger.info("SSL provisioned for %s", domain)


# ── Health check ──────────────────────────────────────────────────────────────

def wait_for_health(port: int, timeout: int = HEALTH_TIMEOUT) -> bool:
    """Poll http://127.0.0.1:{port}/health then / until 2xx or timeout."""
    deadline = time.monotonic() + timeout
    paths = ["/health", "/"]
    while time.monotonic() < deadline:
        for path in paths:
            try:
                with request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=5
                ) as resp:
                    if resp.status < 400:
                        logger.info("Health check passed on %s (HTTP %s)", path, resp.status)
                        return True
            except (urllib_error.URLError, OSError):
                pass
        time.sleep(HEALTH_POLL_INTERVAL)
    logger.warning("Health check timed out after %ds on port %d", timeout, port)
    return False


# ── Docker operations ─────────────────────────────────────────────────────────

def _run(args: list[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
    logger.debug("$ %s", " ".join(args))
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=120, cwd=cwd
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(args)}\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def docker_build(project: ForgeProject, image_tag: str) -> None:
    logger.info("Building Docker image: %s", image_tag)
    _run(["docker", "build", "-t", image_tag, "."], cwd=project.root)
    logger.info("Image built: %s", image_tag)


def docker_run(image_tag: str, container_name: str, port: int, internal_port: int) -> str:
    """Start container, return short container ID. Binding is 127.0.0.1 only."""
    # Remove stale container with same name if present.
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        capture_output=True, timeout=30
    )
    result = _run([
        "docker", "run", "-d",
        "--name", container_name,
        "--restart", "unless-stopped",
        "-p", f"127.0.0.1:{port}:{internal_port}",
        image_tag,
    ])
    container_id = result.stdout.strip()[:12]
    logger.info("Container started: %s (id=%s)", container_name, container_id)
    return container_id


# ── Main deployer ─────────────────────────────────────────────────────────────

class ForgeDeployer:
    """Deploy a ForgeProject as a Docker container behind nginx."""

    INTERNAL_PORT_MAP = {
        "flask":     8000,
        "fastapi":   8000,
        "django":    8000,
        "streamlit": 8501,
        "node":      3000,
        "go":        8080,
        "rust":      8080,
        "rails":     3000,
        "php":       80,
        "docker":    8080,
        "static":    80,
    }

    async def deploy(
        self,
        project: ForgeProject,
        domain: str,
        requested_port: Optional[int] = None,
    ) -> DeployResult:
        """End-to-end deployment: port → build → run → nginx → health.

        Args:
            project:        ForgeProject pointing at the built project root.
            domain:         Target hostname, e.g. "weather.herakles.dev".
            requested_port: Pin to a specific port (must be free) or None for
                            auto-assign from the Nova Forge range.

        Returns:
            DeployResult with URL, port, container ID, and health status.
        """
        loop = asyncio.get_running_loop()

        try:
            port = await loop.run_in_executor(None, assign_port, requested_port, None)
        except ValueError as exc:
            return DeployResult(url="", port=0, container_id="", health_status=False, error=str(exc))

        safe_name  = project.name.lower().replace("_", "-").replace(" ", "-")
        image_tag  = f"forge-{safe_name}"
        container_name = f"forge-{safe_name}"

        try:
            # 1. Dockerfile
            stack = await loop.run_in_executor(
                None, ensure_dockerfile, project, self.INTERNAL_PORT_MAP["flask"]
            )
            internal_port = self.INTERNAL_PORT_MAP.get(stack, 8000)

            # Regenerate Dockerfile with correct internal port if we auto-detected stack.
            if not (project.root / "Dockerfile").exists():
                (project.root / "Dockerfile").write_text(
                    _dockerfile_for_stack(stack, internal_port)
                )

            # 2. Docker build
            await loop.run_in_executor(None, docker_build, project, image_tag)

            # 3. Docker run (127.0.0.1 binding enforced inside docker_run)
            container_id = await loop.run_in_executor(
                None, docker_run, image_tag, container_name, port, internal_port
            )

            # 4. Nginx config + optional SSL
            await loop.run_in_executor(None, write_nginx_config, domain, port)
            if domain.endswith(f".{HERAKLES_DOMAIN}"):
                await loop.run_in_executor(None, provision_ssl, domain)

            # 5. Health check
            health_ok = await loop.run_in_executor(None, wait_for_health, port)

            # 6. Finalize registry
            status = "running" if health_ok else "deployed-unhealthy"
            await loop.run_in_executor(
                None, update_registry_entry, port, safe_name, domain, status
            )

            scheme = "https" if domain.endswith(f".{HERAKLES_DOMAIN}") else "http"
            url = f"{scheme}://{domain}"
            logger.info("Deploy complete: %s → %s (port %d)", safe_name, url, port)
            return DeployResult(
                url=url,
                port=port,
                container_id=container_id,
                health_status=health_ok,
            )

        except Exception as exc:  # noqa: BLE001
            logger.error("Deploy failed for %s: %s", project.name, exc)
            # Best-effort registry cleanup
            try:
                update_registry_entry(port, safe_name, domain, "failed")
            except Exception:
                pass
            return DeployResult(
                url="",
                port=port,
                container_id="",
                health_status=False,
                error=str(exc),
            )
