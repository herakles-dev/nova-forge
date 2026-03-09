"""Nova Forge CLI — entry point for the forge command.

Usage:
    forge plan "weather dashboard" --model nova-lite
    forge build --model gemini-flash
    forge deploy --domain weather.herakles.dev
    forge status
    forge list
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import click

from config import resolve_model, MODEL_ALIASES

# ── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ── CLI Group ────────────────────────────────────────────────────────────────

@click.group()
@click.option("--project", "-p", default=".", help="Project directory (default: current)")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.pass_context
def cli(ctx: click.Context, project: str, verbose: bool) -> None:
    """Nova Forge — open-source agent orchestration. Any LLM."""
    _setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["project"] = Path(project).resolve()


# ── forge plan ───────────────────────────────────────────────────────────────

@cli.command()
@click.argument("goal")
@click.option("--model", "-m", default=None, help="Model for planning (e.g. nova-lite, gemini-flash)")
@click.option("--template", "-t", default=None, help="App template (flask-api, streamlit-dash, static-site, nova-chat)")
@click.pass_context
def plan(ctx: click.Context, goal: str, model: str | None, template: str | None) -> None:
    """Plan a new project from a goal description."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    click.echo(f"Planning: {goal}")
    click.echo(f"Model: {resolve_model(model) if model else 'default (Nova 2 Lite)'}")
    click.echo(f"Project: {project_path}")
    click.echo("---")

    orch = ForgeOrchestrator(project_path, model=model)
    result = asyncio.run(orch.plan(goal, model=model, template=template))

    if result.error:
        click.echo(f"Error: {result.error}", err=True)
        sys.exit(1)

    click.echo(f"Spec: {result.spec_path}")
    click.echo(f"Tasks: {result.tasks_path} ({result.task_count} tasks)")
    click.echo("---")
    click.echo("Run 'forge build' to execute the plan.")


# ── forge build ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--model", "-m", default=None, help="Model for coding agents")
@click.option("--formation", "-f", default=None, help="Formation name (auto-selected if omitted)")
@click.option("--max-concurrent", default=6, help="Max parallel agents per wave")
@click.pass_context
def build(ctx: click.Context, model: str | None, formation: str | None, max_concurrent: int) -> None:
    """Execute the build pipeline (waves + gate review)."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    click.echo(f"Building: {project_path.name}")
    click.echo(f"Model: {resolve_model(model) if model else 'default'}")
    click.echo(f"Max concurrent: {max_concurrent}")
    click.echo("---")

    orch = ForgeOrchestrator(project_path, model=model)
    result = asyncio.run(orch.build(model=model, formation_name=formation, max_concurrent=max_concurrent))

    click.echo(f"Waves: {result.waves_completed}/{result.total_waves}")
    click.echo(f"Gate: {'PASS' if result.gate_passed else 'FAIL'}")
    click.echo(f"Duration: {result.duration:.1f}s")

    if result.errors:
        click.echo("\nErrors:")
        for err in result.errors:
            click.echo(f"  - {err}")

    if not result.success:
        sys.exit(1)

    click.echo("\nBuild successful. Run 'forge deploy' to deploy.")


# ── forge deploy ─────────────────────────────────────────────────────────────

@cli.command()
@click.option("--port", "-p", type=int, default=None, help="Port to deploy on")
@click.option("--domain", "-d", default=None, help="Domain name (e.g. weather.herakles.dev)")
@click.pass_context
def deploy(ctx: click.Context, port: int | None, domain: str | None) -> None:
    """Deploy the built project to a live URL."""
    project_path = ctx.obj["project"]
    click.echo(f"Deploying: {project_path.name}")

    try:
        from forge_deployer import ForgeDeployer
        deployer = ForgeDeployer(project_path)
        result = asyncio.run(deployer.deploy(port=port, domain=domain))
        click.echo(f"URL: {result.url}")
        click.echo(f"Port: {result.port}")
        click.echo(f"Health: {'OK' if result.health_status else 'FAIL'}")
    except ImportError:
        click.echo("Deployer not yet implemented (Sprint 3)", err=True)
        sys.exit(1)


# ── forge status ─────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show project build status."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)
    report = orch.status()

    click.echo(f"Project: {report.project_name}")
    click.echo(f"Progress: {report.completed}/{report.total_tasks} ({report.percent:.0f}%)")
    click.echo(f"  Completed:   {report.completed}")
    click.echo(f"  In Progress: {report.in_progress}")
    click.echo(f"  Pending:     {report.pending}")
    click.echo(f"  Failed:      {report.failed}")
    click.echo(f"  Blocked:     {report.blocked}")


# ── forge list ───────────────────────────────────────────────────────────────

@cli.command("list")
@click.option("--status", "-s", default=None, help="Filter by status (pending, in_progress, completed, failed)")
@click.pass_context
def list_tasks(ctx: click.Context, status: str | None) -> None:
    """List all tasks."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)
    tasks = orch.list_tasks(status=status)

    if not tasks:
        click.echo("No tasks found. Run 'forge plan' first.")
        return

    for t in tasks:
        marker = {"completed": "+", "in_progress": ">", "pending": " ", "failed": "!", "blocked": "x"}.get(t.status, "?")
        click.echo(f"  [{marker}] #{t.id} {t.subject} ({t.status})")


# ── forge new ────────────────────────────────────────────────────────────────

@cli.command("new")
@click.argument("name")
@click.option("--template", "-t", default=None, help="Template (flask-api, streamlit-dash, static-site, nova-chat)")
@click.pass_context
def new_project(ctx: click.Context, name: str, template: str | None) -> None:
    """Initialize a new project with .forge/ directory."""
    from config import init_forge_dir

    project_path = ctx.obj["project"] / name
    project_path.mkdir(parents=True, exist_ok=True)
    project = init_forge_dir(project_path)
    click.echo(f"Created: {project_path}")
    click.echo(f"  .forge/ initialized")

    if template:
        templates_dir = Path(__file__).parent / "templates" / template
        if templates_dir.exists():
            import shutil
            for item in templates_dir.iterdir():
                dest = project_path / item.name
                if item.is_file():
                    shutil.copy2(item, dest)
                elif item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
            click.echo(f"  Template '{template}' applied")
        else:
            click.echo(f"  Warning: template '{template}' not found")

    click.echo(f"\nNext: cd {name} && forge plan \"your goal\"")


# ── forge audit ──────────────────────────────────────────────────────────────

@cli.command()
@click.option("--since", default=None, help="Show entries since (e.g. 1h, 30m)")
@click.pass_context
def audit(ctx: click.Context, since: str | None) -> None:
    """Query the audit log."""
    from config import ForgeProject
    project_path = ctx.obj["project"]
    project = ForgeProject(root=project_path)
    audit_file = project.audit_dir / "audit.jsonl"

    if not audit_file.exists():
        click.echo("No audit log found.")
        return

    import json
    lines = audit_file.read_text().strip().split("\n")
    click.echo(f"Audit log: {len(lines)} entries")
    for line in lines[-20:]:  # Show last 20
        try:
            entry = json.loads(line)
            click.echo(f"  {entry.get('timestamp', '?')} | {entry.get('tool', '?')} | {entry.get('agent_id', '?')}")
        except json.JSONDecodeError:
            continue


# ── forge handoff ────────────────────────────────────────────────────────────

@cli.command()
@click.pass_context
def handoff(ctx: click.Context) -> None:
    """Generate continuation context for session handoff."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)
    context = orch.handoff()
    click.echo(context)


# ── forge models ─────────────────────────────────────────────────────────────

@cli.command()
def models() -> None:
    """List available model aliases."""
    click.echo("Available model aliases:")
    for alias, full_id in sorted(MODEL_ALIASES.items()):
        click.echo(f"  {alias:20s} -> {full_id}")


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
