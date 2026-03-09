#!/usr/bin/env python3
"""Nova Forge Challenge Build — Real project, real Nova agents, real code.

Runs the full forge build pipeline on the bookmarks-api project using
Nova Lite as the coding brain for ALL agents. Tracks every step.
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

# Setup
sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("challenge")

from config import get_model_config, ForgeProject, resolve_model
from forge_agent import ForgeAgent, BUILT_IN_TOOLS
from forge_guards import PathSandbox
from forge_hooks import HookSystem
from forge_tasks import TaskStore

PROJECT_DIR = Path("/tmp/forge-challenge/bookmarks-api")
CODING_MODEL = "bedrock/us.amazon.nova-2-lite-v1:0"  # Nova Lite for ALL agents

# ── Helpers ─────────────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n\033[1;36m{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}\033[0m")

def result(label: str, passed: bool, detail: str = "") -> None:
    marker = "\033[92mPASS\033[0m" if passed else "\033[91mFAIL\033[0m"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{marker}] {label}{suffix}")

# ── Main ────────────────────────────────────────────────────────────────────

async def run_challenge():
    project = ForgeProject(root=PROJECT_DIR)
    store = TaskStore(project.tasks_file)
    tasks = store.list()

    section("Challenge: Build Bookmarks API with Nova Agents")
    print(f"  Project: {PROJECT_DIR}")
    print(f"  Model: {CODING_MODEL}")
    print(f"  Tasks: {len(tasks)}")

    waves = store.compute_waves()
    print(f"  Waves: {len(waves)}")
    for i, wave in enumerate(waves):
        print(f"    Wave {i}: {[t.subject for t in wave]}")

    total_start = time.time()
    total_files_written = 0
    total_tool_calls = 0
    wave_results = []

    for wave_idx, wave_tasks in enumerate(waves):
        section(f"Wave {wave_idx}/{len(waves)-1}: {[t.subject for t in wave_tasks]}")

        for task in wave_tasks:
            # Check if blocked
            fresh = store.get(task.id)
            if fresh and fresh.status in ("blocked", "completed"):
                print(f"  Skipping {task.id} ({fresh.status})")
                continue

            # Mark in_progress
            store.update(task.id, status="in_progress")

            print(f"  Executing: [{task.id}] {task.subject}")
            print(f"    Description: {task.description[:100]}...")

            # Build agent
            mc = get_model_config(CODING_MODEL, max_tokens=4096)
            hooks = HookSystem(project.settings_file)
            sandbox = PathSandbox(PROJECT_DIR)

            # Read existing files for context
            existing_files = list(PROJECT_DIR.glob("*.py")) + list(PROJECT_DIR.glob("*.txt"))
            existing_files = [f for f in existing_files if f.name not in ("challenge_build.py",)]

            context_hint = ""
            if existing_files:
                context_hint = f"\n\nExisting files in project: {', '.join(f.name for f in existing_files)}"
                # Read key files for context
                for ef in existing_files[:5]:
                    try:
                        content = ef.read_text()[:2000]
                        context_hint += f"\n\n--- {ef.name} ---\n{content}"
                    except Exception:
                        pass

            agent = ForgeAgent(
                model_config=mc,
                project_root=PROJECT_DIR,
                hooks=hooks,
                sandbox=sandbox,
                tools=BUILT_IN_TOOLS,
                max_turns=15,
                agent_id=f"forge-wave{wave_idx}-task{task.id}",
            )

            # Build prompt from spec + task
            spec_text = ""
            spec_path = PROJECT_DIR / "spec.md"
            if spec_path.exists():
                spec_text = spec_path.read_text()

            prompt = (
                f"## Project Spec\n{spec_text}\n\n"
                f"## Your Task\n"
                f"Subject: {task.subject}\n"
                f"Description: {task.description}\n\n"
                f"## Instructions\n"
                f"Implement this task by writing real Python code. "
                f"Use write_file to create files. Use read_file to check existing files first. "
                f"Write complete, working code — not stubs or placeholders. "
                f"Use Flask, SQLAlchemy, and SQLite as specified in the spec. "
                f"All files go in the project root directory."
                f"{context_hint}"
            )

            system = (
                "You are a Python backend developer. Write complete, production-quality code. "
                "Always use write_file to create files. Read existing files before modifying. "
                "Write real implementations, not TODOs or placeholders."
            )

            task_start = time.time()
            try:
                agent_result = await agent.run(prompt=prompt, system=system)
                duration = time.time() - task_start

                files_written = len(agent_result.artifacts) if agent_result.artifacts else 0
                total_files_written += files_written
                total_tool_calls += agent_result.tool_calls_made

                if agent_result.error:
                    result(f"Task {task.id}: {task.subject}", False,
                           f"error={agent_result.error[:100]}")
                    store.update(task.id, status="failed")
                    wave_results.append(("FAIL", task.id, task.subject, agent_result.error))
                else:
                    result(f"Task {task.id}: {task.subject}", True,
                           f"{agent_result.turns} turns, {agent_result.tool_calls_made} tool calls, "
                           f"{files_written} files, {duration:.1f}s")
                    store.update(task.id, status="completed", artifacts=agent_result.artifacts)
                    wave_results.append(("PASS", task.id, task.subject,
                                        f"{agent_result.turns}t/{agent_result.tool_calls_made}tc/{files_written}f"))

            except Exception as exc:
                duration = time.time() - task_start
                result(f"Task {task.id}: {task.subject}", False, f"exception: {exc}")
                store.update(task.id, status="failed")
                wave_results.append(("FAIL", task.id, task.subject, str(exc)))

    total_duration = time.time() - total_start

    # ── Post-build verification ─────────────────────────────────────────
    section("Post-Build Verification")

    # 1. Check files created
    py_files = list(PROJECT_DIR.glob("*.py"))
    txt_files = list(PROJECT_DIR.glob("*.txt")) + list(PROJECT_DIR.glob("requirements*.txt"))
    all_project_files = [f for f in (py_files + txt_files)
                         if f.name not in ("challenge_build.py",)]

    print(f"  Files created: {len(all_project_files)}")
    for f in sorted(all_project_files):
        size = f.stat().st_size
        print(f"    {f.name}: {size} bytes")

    # 2. Syntax check all .py files
    import py_compile
    syntax_ok = 0
    syntax_fail = 0
    for f in py_files:
        if f.name == "challenge_build.py":
            continue
        try:
            py_compile.compile(str(f), doraise=True)
            syntax_ok += 1
        except py_compile.PyCompileError as e:
            syntax_fail += 1
            result(f"Syntax: {f.name}", False, str(e)[:100])

    result(f"Python syntax", syntax_ok > 0 and syntax_fail == 0,
           f"{syntax_ok} ok, {syntax_fail} failed")

    # 3. Check for Flask app
    app_file = PROJECT_DIR / "app.py"
    has_flask = False
    has_routes = False
    has_model = False
    if app_file.exists():
        content = app_file.read_text()
        has_flask = "Flask" in content
        has_routes = "@app.route" in content or "app.route" in content
        has_model = "Bookmark" in content or "class Bookmark" in content

    result("Flask app exists", app_file.exists())
    result("Flask imported", has_flask)
    result("Routes defined", has_routes)
    result("Bookmark model", has_model or any(
        "Bookmark" in f.read_text() for f in py_files if f.exists() and f.name != "challenge_build.py"
    ))

    # 4. Check for specific endpoints from spec
    all_code = ""
    for f in py_files:
        if f.name != "challenge_build.py" and f.exists():
            all_code += f.read_text()

    endpoints = ["/bookmarks", "/search", "/import", "/export", "/favorite"]
    for ep in endpoints:
        found = ep in all_code
        result(f"Endpoint {ep}", found)

    # 5. Check for tests
    test_files = [f for f in py_files if "test" in f.name.lower()]
    has_tests = len(test_files) > 0
    result(f"Test files exist", has_tests,
           f"{len(test_files)} files: {[f.name for f in test_files]}")

    if test_files:
        test_content = test_files[0].read_text()
        has_assertions = "assert" in test_content or "assertEqual" in test_content
        result("Tests have assertions", has_assertions)

    # 6. Task lifecycle
    tasks = store.list()
    completed = sum(1 for t in tasks if t.status == "completed")
    failed = sum(1 for t in tasks if t.status == "failed")
    result(f"Task lifecycle", completed > 0,
           f"{completed} completed, {failed} failed out of {len(tasks)}")

    # 7. Audit trail
    audit_file = project.audit_dir / "audit.jsonl"
    audit_count = 0
    if audit_file.exists():
        lines = [l for l in audit_file.read_text().strip().split("\n") if l.strip()]
        audit_count = len(lines)
    result("Audit trail", audit_count > 0, f"{audit_count} entries")

    # ── Summary ─────────────────────────────────────────────────────────
    section("CHALLENGE SUMMARY")
    print(f"  Model: {CODING_MODEL} (Nova Lite)")
    print(f"  Tasks: {len(wave_results)} attempted")
    print(f"  Passed: {sum(1 for s,_,_,_ in wave_results if s == 'PASS')}")
    print(f"  Failed: {sum(1 for s,_,_,_ in wave_results if s == 'FAIL')}")
    print(f"  Files written: {total_files_written}")
    print(f"  Tool calls: {total_tool_calls}")
    print(f"  Duration: {total_duration:.1f}s")
    print()

    for status, tid, subject, detail in wave_results:
        marker = "+" if status == "PASS" else "!"
        print(f"  [{marker}] Task {tid}: {subject} — {detail}")

    # Return success
    all_passed = all(s == "PASS" for s, _, _, _ in wave_results)
    print(f"\n  {'ALL TASKS COMPLETED' if all_passed else 'SOME TASKS FAILED'}")
    return all_passed


if __name__ == "__main__":
    success = asyncio.run(run_challenge())
    sys.exit(0 if success else 1)
