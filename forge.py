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
    """Initialize a new project with full Forge ecosystem."""
    from forge_compliance import ComplianceChecker

    project_path = ctx.obj["project"] / name
    project_path.mkdir(parents=True, exist_ok=True)

    # Full ecosystem scaffold
    cc = ComplianceChecker(project_path)
    cc.fix()  # Creates .forge/, settings.json, autonomy, FORGE.md

    click.echo(f"Created: {project_path}")
    click.echo(f"  .forge/ initialized with full ecosystem")

    # Apply template
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

    # Run compliance check
    report = cc.check()
    click.echo(f"  Compliance: {report.passed_count}/{report.total} gates pass")

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

# ── forge agent ─────────────────────────────────────────────────────────────

@cli.group("agent")
def agent_group() -> None:
    """Manage agent definitions (list, info, discover)."""
    pass


@agent_group.command("list")
@click.option("--category", "-c", default=None, help="Filter by category (spec, specialist, meta)")
def agent_list(category: str | None) -> None:
    """List all registered agents."""
    from forge_registry import AgentRegistry

    registry = AgentRegistry()
    agents = registry.list_by_category(category) if category else registry.list_all()

    if not agents:
        click.echo("No agents found. Check agents/ directory.")
        return

    click.echo(f"{'Name':30s} {'Category':12s} {'Model':8s} {'Policy':10s} Description")
    click.echo("-" * 100)
    for a in agents:
        click.echo(f"{a.name:30s} {a.category:12s} {a.model_preference:8s} {a.tool_policy:10s} {a.description[:40]}")
    click.echo(f"\n{len(agents)} agents registered.")


@agent_group.command("info")
@click.argument("name")
def agent_info(name: str) -> None:
    """Show full details for an agent."""
    from forge_registry import AgentRegistry

    registry = AgentRegistry()
    try:
        a = registry.get(name)
    except KeyError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    click.echo(f"Name:        {a.name}")
    click.echo(f"Description: {a.description}")
    click.echo(f"Category:    {a.category}")
    click.echo(f"Model:       {a.model_preference}")
    click.echo(f"Effort:      {a.effort_level}")
    click.echo(f"Tool Policy: {a.tool_policy}")

    if a.formation_roles:
        click.echo("Formations:")
        for fr in a.formation_roles:
            click.echo(f"  - {fr.formation} / {fr.role}")

    if a.ownership.directories or a.ownership.patterns:
        click.echo("Ownership:")
        for d in a.ownership.directories:
            click.echo(f"  dir: {d}")
        for p in a.ownership.patterns:
            click.echo(f"  pat: {p}")

    if a.system_prompt:
        click.echo(f"\nSystem Prompt ({len(a.system_prompt)} chars):")
        lines = a.system_prompt.strip().split("\n")
        for line in lines[:5]:
            click.echo(f"  {line}")
        if len(lines) > 5:
            click.echo(f"  ... ({len(lines) - 5} more lines)")


@agent_group.command("discover")
@click.argument("keyword")
@click.option("--max", "-n", "max_results", default=10, help="Max results")
def agent_discover(keyword: str, max_results: int) -> None:
    """Fuzzy search agents by keyword."""
    from forge_registry import AgentRegistry

    registry = AgentRegistry()
    results = registry.discover(keyword, max_results=max_results)

    if not results:
        click.echo(f"No agents matching '{keyword}'.")
        return

    click.echo(f"Agents matching '{keyword}':")
    for a in results:
        click.echo(f"  {a.name:30s} {a.description[:60]}")


# ── forge session ────────────────────────────────────────────────────────────

@cli.group("session")
def session_group() -> None:
    """Session management (detect, configure, compliance, handoff)."""
    pass


@session_group.command("detect")
@click.pass_context
def session_detect(ctx: click.Context) -> None:
    """Detect project state and readiness."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)
    state = orch.detect()

    click.echo(f"Project: {state['project']}")
    click.echo(f"Path:    {state['project_path']}")
    click.echo(f"Initialized: {'yes' if state['initialized'] else 'no'}")
    click.echo(f"Compliant:   {'yes' if state['compliant'] else 'no'}")

    if state["compliance_gates"]:
        click.echo("\nCompliance Gates:")
        for gate, passed, detail in state["compliance_gates"]:
            marker = "PASS" if passed else "FAIL"
            click.echo(f"  [{marker}] {gate}: {detail}")

    ts = state["task_summary"]
    if ts.get("total", 0) > 0:
        click.echo(f"\nTasks: {ts['completed']}/{ts['total']} completed")
        click.echo(f"  In Progress: {ts['in_progress']}")
        click.echo(f"  Pending:     {ts['pending']}")

    auto = state.get("autonomy", {})
    if auto:
        click.echo(f"\nAutonomy: A{auto.get('level', 0)} ({auto.get('successful', 0)} successful)")

    if state.get("formation"):
        fm = state["formation"]
        click.echo(f"\nFormation: {fm.get('formation', '?')} ({len(fm.get('teammates', {}))} teammates)")

    if state["needs_setup"]:
        click.echo("\nNeeds setup:")
        for issue in state["needs_setup"]:
            click.echo(f"  - {issue}")


@session_group.command("configure")
@click.option("--fix/--no-fix", default=True, help="Auto-fix compliance issues")
@click.pass_context
def session_configure(ctx: click.Context, fix: bool) -> None:
    """Configure project for Forge compliance."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)

    if fix:
        fixes = orch.configure(auto_fix=True)
        if fixes:
            click.echo("Fixes applied:")
            for f in fixes:
                click.echo(f"  - {f}")
        else:
            click.echo("No fixes needed — project is already configured.")
    else:
        gates = orch.check_compliance()
        for gate, passed, detail in gates:
            marker = "PASS" if passed else "FAIL"
            click.echo(f"  [{marker}] {gate}: {detail}")


@session_group.command("compliance")
@click.pass_context
def session_compliance(ctx: click.Context) -> None:
    """Run compliance check."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)
    gates = orch.check_compliance()

    passed_count = sum(1 for _, p, _ in gates if p)
    total = len(gates)

    click.echo(f"Compliance: {passed_count}/{total} gates pass")
    click.echo("")
    for gate, passed, detail in gates:
        marker = "PASS" if passed else "FAIL"
        click.echo(f"  [{marker}] {gate}: {detail}")

    if passed_count == total:
        click.echo(f"\nProject is COMPLIANT.")
    else:
        click.echo(f"\nProject is NOT COMPLIANT. Run 'forge session configure' to fix.")


@session_group.command("dashboard")
@click.pass_context
def session_dashboard(ctx: click.Context) -> None:
    """Show full session dashboard."""
    from forge_orchestrator import ForgeOrchestrator

    project_path = ctx.obj["project"]
    orch = ForgeOrchestrator(project_path)
    ss = orch.session_status()

    click.echo(f"Forge Session: {ss.project_name}")
    click.echo(f"  Tasks:     {ss.completed}/{ss.total_tasks} ({ss.percent:.0f}%)")
    click.echo(f"  Active:    {ss.in_progress}")
    click.echo(f"  Pending:   {ss.pending}")
    click.echo(f"  Failed:    {ss.failed}")
    click.echo(f"  Blocked:   {ss.blocked}")
    click.echo(f"  Autonomy:  A{ss.autonomy_level}")
    click.echo(f"  Formation: {ss.formation or 'none'}")
    click.echo(f"  Updated:   {ss.last_updated or 'never'}")


# ── forge init ──────────────────────────────────────────────────────────────

@cli.command("init")
@click.pass_context
def init_project(ctx: click.Context) -> None:
    """Initialize .forge/ in the current project directory."""
    from forge_session import SessionManager

    project_path = ctx.obj["project"]
    sm = SessionManager(project_path)

    if sm.is_initialized():
        click.echo(f".forge/ already exists at {project_path}")
    else:
        sm.init()
        click.echo(f".forge/ initialized at {project_path}")

    fixes = sm.auto_fix()
    if fixes:
        for f in fixes:
            click.echo(f"  - {f}")


# ── forge formation ─────────────────────────────────────────────────────────

@cli.command("formation")
@click.option("--complexity", "-c", default="medium", help="Complexity: routine/medium/complex/novel")
@click.option("--scope", "-s", default="medium", help="Scope: small/medium/large")
def formation_select(complexity: str, scope: str) -> None:
    """Select a formation using DAAO routing."""
    from formations import select_formation

    try:
        formation = select_formation(complexity, scope)
    except (KeyError, ValueError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(f"Formation: {formation.name}")
    click.echo(f"Description: {formation.description}")
    click.echo(f"\nRoles:")
    for role in formation.roles:
        click.echo(f"  {role.name:20s} model={role.model:6s} policy={role.tool_policy}")
    click.echo(f"\nWaves: {len(formation.wave_order)}")
    for i, wave in enumerate(formation.wave_order):
        click.echo(f"  Wave {i}: {', '.join(wave)}")
    if formation.gate_criteria:
        click.echo(f"\nGate: {', '.join(formation.gate_criteria)}")


if __name__ == "__main__":
    cli()
