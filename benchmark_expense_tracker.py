#!/usr/bin/env python3
"""Nova Forge Benchmark: Expense Tracker with UI

Stress-tests the multi-agent interface mismatch fixes:
  - Wave 1: models.py (SQLite CRUD with specific function signatures)
  - Wave 2: api.py (Flask REST — MUST import from models correctly)
  - Wave 3: static/index.html + static/app.js (UI — forms, tables, charts)

Validates:
  RC1: Failed task artifacts visible to downstream
  RC2: Rich interface announcements (AST signatures)
  RC3: Full parameter lists in export extraction
  RC4: Mandatory read-before-write instruction
  RC5: Import name resolution catches mismatches

Usage:
    source ~/.secrets/hercules.env
    python3 benchmark_expense_tracker.py [--model MODEL]
"""

import asyncio
import ast
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

from config import get_model_config, ForgeProject, resolve_model, init_forge_dir
from forge_agent import ForgeAgent, BUILT_IN_TOOLS, AgentEvent
from forge_comms import BuildContext
from forge_guards import PathSandbox
from forge_hooks import HookSystem
from forge_tasks import TaskStore

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path("/tmp/forge-benchmark/expense-tracker")
DEFAULT_MODEL = "bedrock/us.amazon.nova-2-lite-v1:0"

SPEC_MD = """\
# Expense Tracker

A personal expense tracking app with Flask backend and vanilla JS frontend.

## Tech Stack
- Backend: Python 3 + Flask
- Database: SQLite3 (raw sqlite3 module, NOT SQLAlchemy)
- Frontend: Vanilla HTML/CSS/JS (no frameworks)
- Charts: Chart.js CDN

## Data Models

### Category
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- name (TEXT NOT NULL UNIQUE)
- color (TEXT DEFAULT '#6c757d')

### Expense
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- amount (REAL NOT NULL)
- description (TEXT)
- category_id (INTEGER REFERENCES categories)
- date (TEXT NOT NULL, ISO format YYYY-MM-DD)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

## API Endpoints

All endpoints return JSON. Prefix: none (root-level).

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/categories | List all categories |
| POST | /api/categories | Create category {name, color} |
| GET | /api/expenses | List expenses (optional ?category_id=&start=&end=) |
| POST | /api/expenses | Create expense {amount, description, category_id, date} |
| PUT | /api/expenses/<id> | Update expense |
| DELETE | /api/expenses/<id> | Delete expense |
| GET | /api/summary | Monthly summary {total, by_category: [{name, total, color}]} |

## Frontend Pages

Single page app at / (index.html):
- Expense form: amount, description, category dropdown, date picker
- Expense table: sortable, with edit/delete buttons
- Category manager: add/edit categories with color picker
- Monthly chart: pie chart of spending by category (Chart.js)
- Filter bar: date range + category filter

## File Structure
```
models.py          - Database helpers (init_db, CRUD functions)
api.py             - Flask app with routes (imports from models)
static/index.html  - Main page
static/app.js      - Frontend logic (fetch API calls, DOM manipulation)
static/style.css   - Styling
```

## IMPORTANT
- models.py uses raw sqlite3, NOT SQLAlchemy
- models.py exports FUNCTIONS (not classes): init_db(), create_category(), get_categories(), etc.
- api.py imports these functions: `from models import init_db, create_category, ...`
- Do NOT create ORM model classes like Category or Expense
"""

TASKS_JSON = [
    {
        "subject": "Create database models and helpers",
        "description": (
            "Create models.py with raw sqlite3 database helpers. "
            "Functions: init_db(), create_category(name, color), get_categories(), "
            "create_expense(amount, description, category_id, date), get_expenses(category_id=None, start=None, end=None), "
            "update_expense(expense_id, **kwargs), delete_expense(expense_id), get_monthly_summary(). "
            "Use a module-level DB_PATH='expenses.db'. Call init_db() at import time to ensure tables exist."
        ),
        "files": ["models.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [],
    },
    {
        "subject": "Create Flask API routes",
        "description": (
            "Create api.py with Flask app and REST routes. "
            "Import functions from models.py: from models import init_db, create_category, get_categories, "
            "create_expense, get_expenses, update_expense, delete_expense, get_monthly_summary. "
            "Routes: GET/POST /api/categories, GET/POST /api/expenses, PUT/DELETE /api/expenses/<id>, GET /api/summary. "
            "Serve static files from ./static/. Return JSON responses with appropriate status codes."
        ),
        "files": ["api.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create frontend HTML page",
        "description": (
            "Create static/index.html — single page expense tracker UI. "
            "Include: expense form (amount, description, category dropdown, date), expense table, "
            "category manager section, monthly pie chart placeholder (Chart.js CDN), "
            "filter bar (date range, category). Link to app.js and style.css."
        ),
        "files": ["static/index.html"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create frontend JavaScript",
        "description": (
            "Create static/app.js — frontend logic. "
            "Functions: loadCategories(), loadExpenses(), addExpense(), editExpense(id), deleteExpense(id), "
            "addCategory(), renderChart(), applyFilters(). "
            "Use fetch() for all API calls to /api/* endpoints. "
            "Populate category dropdowns, render expense table, initialize Chart.js pie chart."
        ),
        "files": ["static/app.js"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [1, 2],
    },
    {
        "subject": "Create CSS styling",
        "description": (
            "Create static/style.css — clean, modern styling. "
            "Style the expense form, table, category manager, chart container, and filter bar. "
            "Use a consistent color scheme. Make it responsive."
        ),
        "files": ["static/style.css"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [2],
    },
]

# ── Display helpers ──────────────────────────────────────────────────────────

def section(title: str) -> None:
    print(f"\n\033[1;36m{'─' * 70}")
    print(f"  {title}")
    print(f"{'─' * 70}\033[0m")

def check(label: str, passed: bool, detail: str = "") -> bool:
    marker = "\033[92mPASS\033[0m" if passed else "\033[91mFAIL\033[0m"
    suffix = f"  — {detail}" if detail else ""
    print(f"  [{marker}] {label}{suffix}")
    return passed

def warn(msg: str) -> None:
    print(f"  \033[93m[WARN]\033[0m {msg}")

# ── Interface Analysis ───────────────────────────────────────────────────────

def extract_module_interface(py_path: Path) -> dict:
    """Extract public interface from a Python file using AST."""
    if not py_path.exists():
        return {"error": "file not found"}
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        return {"error": f"syntax error: {e}"}

    interface = {"functions": {}, "classes": {}, "assignments": []}
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [a.arg for a in node.args.args if a.arg != "self"]
            interface["functions"][node.name] = params
        elif isinstance(node, ast.ClassDef):
            methods = {}
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    params = [a.arg for a in child.args.args if a.arg != "self"]
                    methods[child.name] = params
            interface["classes"][node.name] = methods
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    interface["assignments"].append(target.id)
    return interface


def extract_imports(py_path: Path) -> list[dict]:
    """Extract import statements from a Python file."""
    if not py_path.exists():
        return []
    try:
        tree = ast.parse(py_path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names = [alias.name for alias in node.names]
            imports.append({"from": node.module, "names": names})
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({"import": alias.name})
    return imports


def check_interface_compatibility(project: Path) -> list[str]:
    """The core test: do downstream files import names that actually exist upstream?"""
    issues = []

    models_path = project / "models.py"
    api_path = project / "api.py"

    if not models_path.exists():
        issues.append("models.py not created")
        return issues
    if not api_path.exists():
        issues.append("api.py not created")
        return issues

    # Get models.py interface
    models_iface = extract_module_interface(models_path)
    if "error" in models_iface:
        issues.append(f"models.py: {models_iface['error']}")
        return issues

    models_exports = set()
    models_exports.update(models_iface["functions"].keys())
    models_exports.update(models_iface["classes"].keys())
    models_exports.update(models_iface["assignments"])

    # Check api.py imports from models
    api_imports = extract_imports(api_path)
    for imp in api_imports:
        if imp.get("from") == "models":
            for name in imp["names"]:
                if name not in models_exports:
                    issues.append(
                        f"INTERFACE MISMATCH: api.py imports '{name}' from models.py, "
                        f"but models.py only exports: {sorted(models_exports)}"
                    )

    # Check if api.py uses ORM patterns (the exact bug we're fixing)
    api_src = api_path.read_text(encoding="utf-8", errors="replace")
    orm_patterns = [
        (r'\.query\b', "ORM .query() pattern"),
        (r'\.to_dict\(\)', "ORM .to_dict() pattern"),
        (r'db\.session', "SQLAlchemy db.session pattern"),
        (r'Base\.metadata', "SQLAlchemy Base.metadata pattern"),
    ]
    for pattern, label in orm_patterns:
        if re.search(pattern, api_src):
            issues.append(f"ORM HALLUCINATION: api.py uses {label} — should use functional helpers")

    # Check models.py doesn't use SQLAlchemy (ignore comments mentioning it)
    models_src = models_path.read_text(encoding="utf-8", errors="replace")
    import_sqlalchemy = re.search(r'^\s*(?:from|import)\s+.*sqlalchemy', models_src, re.MULTILINE | re.IGNORECASE)
    if import_sqlalchemy:
        issues.append("models.py imports SQLAlchemy — spec requires raw sqlite3")

    # Check that models.py uses sqlite3
    if "sqlite3" not in models_src:
        issues.append("models.py doesn't import sqlite3 — spec requires raw sqlite3")

    return issues


# ── Build Runner ─────────────────────────────────────────────────────────────

async def run_benchmark(coding_model: str):
    """Run the full benchmark: setup → build → verify → report."""

    # ── Phase 0: Clean setup ──────────────────────────────────────────────
    section("Phase 0: Project Setup")

    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR)
    PROJECT_DIR.mkdir(parents=True)
    (PROJECT_DIR / "static").mkdir()

    # Write spec
    (PROJECT_DIR / "spec.md").write_text(SPEC_MD)
    print(f"  Spec: {PROJECT_DIR / 'spec.md'}")

    # Initialize .forge/
    project = init_forge_dir(PROJECT_DIR)
    store = TaskStore(project.tasks_file)

    # Create tasks with proper dependencies
    subject_to_id: dict[int, str] = {}
    for i, t in enumerate(TASKS_JSON):
        blocked = []
        for b in t.get("blocked_by", []):
            if b in subject_to_id:
                blocked.append(subject_to_id[b])
        task = store.create(
            subject=t["subject"],
            description=t["description"],
            metadata={
                "project": "expense-tracker",
                "sprint": t["sprint"],
                "risk": t["risk"],
                "files": t["files"],
            },
            blocked_by=blocked or None,
        )
        subject_to_id[i] = task.id

    tasks = store.list()
    waves = store.compute_waves()
    print(f"  Tasks: {len(tasks)}")
    print(f"  Waves: {len(waves)}")
    for i, wave in enumerate(waves):
        print(f"    Wave {i}: {[t.subject[:40] for t in wave]}")

    # ── Phase 1: Build ────────────────────────────────────────────────────
    section("Phase 1: Agent Build")
    print(f"  Model: {coding_model}")

    build_context = BuildContext(project_root=PROJECT_DIR)

    # Pre-claim ALL task files upfront so agents can't write outside their scope
    for t in tasks:
        t_files = (t.metadata or {}).get("files", [])
        agent_id = f"task-{t.id}"
        for tf in t_files:
            build_context.claim_file(tf, agent_id)

    total_start = time.time()
    wave_results = []
    all_announcements = []

    for wave_idx, wave_tasks in enumerate(waves):
        section(f"Wave {wave_idx}/{len(waves) - 1}")

        for task in wave_tasks:
            fresh = store.get(task.id)
            if fresh and fresh.status in ("blocked", "completed"):
                print(f"  ⊘ Skipping {task.id} ({fresh.status})")
                continue

            store.update(task.id, status="in_progress")
            print(f"  ▸ [{task.id}] {task.subject}")

            # Build agent
            resolved = resolve_model(coding_model)
            # Use larger output for models with bigger context windows
            from config import get_context_window
            ctx = get_context_window(resolved)
            mt = 4096 if ctx <= 32_000 else 8192
            mc = get_model_config(resolved, max_tokens=mt)
            hooks = HookSystem(project.settings_file)
            sandbox = PathSandbox(PROJECT_DIR)

            # Gather upstream context (uses our fixed _gather method)
            from forge_cli import ForgeShell
            shell = ForgeShell.__new__(ForgeShell)
            shell.project_path = PROJECT_DIR

            upstream_context = shell._gather_upstream_artifacts(task, store, store.list())
            context_sections = list(upstream_context.values())
            context_hint = "\n\n" + "\n\n".join(context_sections) if context_sections else ""

            # Build mandatory read instruction (Fix 5)
            mandatory_reads = []
            for dep_id in (task.blocked_by or []):
                dep = store.get(dep_id)
                if dep and dep.artifacts:
                    for fpath in dep.artifacts.keys():
                        short = shell._shorten_path(fpath)
                        if short.endswith(('.py', '.js', '.ts')):
                            mandatory_reads.append(short)
            mandatory_reads = list(dict.fromkeys(mandatory_reads))[:8]

            read_instruction = ""
            if mandatory_reads:
                read_instruction = (
                    f"\n\n## MANDATORY: Read Before Writing\n"
                    f"Your task depends on these upstream files. You MUST call read_file on each one "
                    f"BEFORE writing any code that imports from or interacts with them:\n"
                    + ", ".join(mandatory_reads) + "\n"
                    f"Do NOT assume what functions, classes, or APIs these files contain. "
                    f"Read them and use their ACTUAL interface."
                )

            spec_text = (PROJECT_DIR / "spec.md").read_text()
            prompt = (
                f"## Project Spec\n{spec_text}\n\n"
                f"## Your Task\n{task.subject}: {task.description}\n\n"
                f"## Instructions\n"
                f"Implement this task COMPLETELY. Use write_file to create EVERY file listed in your task. "
                f"For large files (>100 lines), use write_file for the first section then append_file for remaining sections. "
                f"Read existing files first with read_file if you need context. "
                f"Write complete, working code — not stubs or placeholders. "
                f"Do NOT describe file contents in text — use the write_file/append_file tools with the full content."
                f"{read_instruction}"
                f"{context_hint}"
            )

            system = (
                "You are a Python/JS developer. Write complete, production-quality code. "
                "Always use write_file to create files. For large files, use write_file for the first "
                "~100 lines then append_file to add remaining sections — never leave files incomplete. "
                "Read existing files with read_file before writing code that depends on them. "
                "Use their ACTUAL interface — do not assume or hallucinate."
            )

            # Track events
            events = []
            def on_event(evt: AgentEvent, _events=events):
                _events.append(evt)
                if evt.kind == "file_claimed":
                    print(f"    📎 Claimed: {evt.file_path}")
                elif evt.kind == "file_conflict":
                    print(f"    ⚠ Conflict: {evt.file_path} — {evt.error}")

            agent = ForgeAgent(
                model_config=mc,
                project_root=PROJECT_DIR,
                hooks=hooks,
                sandbox=sandbox,
                tools=BUILT_IN_TOOLS,
                max_turns=30,
                agent_id=f"task-{task.id}",
                build_context=build_context,
                on_event=on_event,
            )

            expected_files = (task.metadata or {}).get("files", [])

            task_start = time.time()
            try:
                result = await agent.run(prompt=prompt, system=system)
                duration = time.time() - task_start
                fc = len(result.artifacts) if result.artifacts else 0
                tc = result.tool_calls_made

                # No-write retry: agent completed but didn't create expected files
                if expected_files and fc == 0 and not result.error:
                    warn(f"Task {task.id} wrote 0 files — retrying with explicit instruction")
                    retry_prompt = (
                        f"You completed the task description but did NOT use the write_file tool to create any files.\n"
                        f"You MUST create the following files using the write_file tool: {', '.join(expected_files)}\n"
                        f"For large files, use write_file for the first section then append_file for remaining sections.\n"
                        f"Do NOT describe what to write — actually call write_file/append_file with the full file content.\n\n"
                        f"Original task:\n{prompt}"
                    )
                    result = await agent.run(prompt=retry_prompt, system=system)
                    duration = time.time() - task_start
                    tc += result.tool_calls_made
                    fc = len(result.artifacts) if result.artifacts else 0

                # Stub retry: files exist but are too small
                if expected_files and not result.error:
                    stub_files = []
                    min_size = {"py": 100, "js": 200, "html": 200, "css": 100}
                    for fpath in expected_files:
                        full = PROJECT_DIR / fpath
                        if full.exists():
                            size = full.stat().st_size
                            ext = fpath.rsplit(".", 1)[-1] if "." in fpath else ""
                            threshold = min_size.get(ext, 100)
                            if size < threshold:
                                stub_files.append(f"{fpath} ({size} bytes)")
                    if stub_files:
                        warn(f"Task {task.id} wrote stubs: {stub_files} — retrying")
                        retry_prompt = (
                            f"You wrote these files but they are STUBS or PLACEHOLDERS with almost no content:\n"
                            f"{', '.join(stub_files)}\n\n"
                            f"You MUST rewrite them with COMPLETE, FULLY FUNCTIONAL code. "
                            f"For large files, use write_file for the initial section then append_file for remaining sections. "
                            f"Do NOT write comments like 'implement here' or 'placeholder'. "
                            f"Write the ACTUAL working implementation.\n\n"
                            f"Original task:\n{prompt}"
                        )
                        result = await agent.run(prompt=retry_prompt, system=system)
                        duration = time.time() - task_start
                        tc += result.tool_calls_made
                        fc = max(fc, len(result.artifacts) if result.artifacts else 0)

                if result.error:
                    store.update(task.id, status="failed", artifacts=result.artifacts)
                    wave_results.append(("FAIL", task.id, task.subject, result.error[:100], duration))
                    check(f"Task {task.id}: {task.subject}", False, f"{result.error[:80]}")
                else:
                    store.update(task.id, status="completed", artifacts=result.artifacts)
                    wave_results.append(("PASS", task.id, task.subject,
                                        f"{result.turns}t/{tc}tc/{fc}f", duration))
                    check(f"Task {task.id}: {task.subject}", True,
                          f"{result.turns} turns, {tc} calls, {fc} files, {duration:.1f}s")

            except Exception as exc:
                duration = time.time() - task_start
                store.update(task.id, status="failed")
                wave_results.append(("FAIL", task.id, task.subject, str(exc)[:100], duration))
                check(f"Task {task.id}: {task.subject}", False, f"exception: {exc}")

    total_duration = time.time() - total_start

    # ── Phase 2: Verification ─────────────────────────────────────────────
    section("Phase 2: Verification")

    checks_passed = 0
    checks_total = 0

    # 2a. File existence
    expected_files = ["models.py", "api.py", "static/index.html", "static/app.js", "static/style.css"]
    for ef in expected_files:
        p = PROJECT_DIR / ef
        checks_total += 1
        if check(f"File exists: {ef}", p.exists(),
                 f"{p.stat().st_size} bytes" if p.exists() else "MISSING"):
            checks_passed += 1

    # 2b. Syntax check
    import py_compile
    py_files = list(PROJECT_DIR.glob("*.py"))
    for f in py_files:
        checks_total += 1
        try:
            py_compile.compile(str(f), doraise=True)
            if check(f"Syntax: {f.name}", True):
                checks_passed += 1
        except py_compile.PyCompileError as e:
            check(f"Syntax: {f.name}", False, str(e)[:100])

    # 2c. Interface compatibility (THE MAIN TEST)
    section("Phase 2b: Interface Compatibility (Core Test)")
    issues = check_interface_compatibility(PROJECT_DIR)
    checks_total += 1
    if issues:
        check("Interface compatibility", False, f"{len(issues)} issues")
        for issue in issues:
            print(f"    \033[91m✗\033[0m {issue}")
    else:
        checks_passed += 1
        check("Interface compatibility", True, "api.py imports match models.py exports")

    # 2d. models.py interface analysis
    section("Phase 2c: Interface Analysis")
    models_iface = extract_module_interface(PROJECT_DIR / "models.py")
    if "error" not in models_iface:
        print(f"  models.py functions:")
        for fname, params in models_iface["functions"].items():
            print(f"    def {fname}({', '.join(params)})")
        if models_iface["classes"]:
            warn(f"models.py has classes: {list(models_iface['classes'].keys())} — spec wants functions only")
        print(f"  models.py assignments: {models_iface['assignments']}")

    api_iface = extract_module_interface(PROJECT_DIR / "api.py")
    if "error" not in api_iface:
        print(f"\n  api.py functions:")
        for fname, params in api_iface["functions"].items():
            print(f"    def {fname}({', '.join(params)})")
        api_imports = extract_imports(PROJECT_DIR / "api.py")
        models_imports = [i for i in api_imports if i.get("from") == "models"]
        if models_imports:
            print(f"\n  api.py imports from models: {models_imports[0]['names']}")

    # 2e. API endpoint coverage
    section("Phase 2d: API Endpoint Coverage")
    if (PROJECT_DIR / "api.py").exists():
        api_src = (PROJECT_DIR / "api.py").read_text()
        expected_routes = [
            "/api/categories",
            "/api/expenses",
            "/api/summary",
        ]
        for route in expected_routes:
            checks_total += 1
            found = route in api_src
            if check(f"Route: {route}", found):
                checks_passed += 1

    # 2f. Frontend verification
    section("Phase 2e: Frontend Verification")
    html_path = PROJECT_DIR / "static" / "index.html"
    if html_path.exists():
        html = html_path.read_text()
        ui_checks = [
            ("Form element", "<form" in html.lower()),
            ("Table element", "<table" in html.lower()),
            ("Chart.js CDN", "chart.js" in html.lower() or "chartjs" in html.lower()),
            ("app.js linked", "app.js" in html),
            ("style.css linked", "style.css" in html),
        ]
        for label, passed in ui_checks:
            checks_total += 1
            if check(f"HTML: {label}", passed):
                checks_passed += 1

    js_path = PROJECT_DIR / "static" / "app.js"
    if js_path.exists():
        js_src = js_path.read_text()
        js_checks = [
            ("fetch() calls", "fetch(" in js_src),
            ("/api/ endpoint usage", "/api/" in js_src),
            ("DOM manipulation", "document." in js_src),
        ]
        for label, passed in js_checks:
            checks_total += 1
            if check(f"JS: {label}", passed):
                checks_passed += 1

    # 2g. BuildContext stats
    section("Phase 2f: Multi-Agent Coordination Stats")
    ctx_stats = build_context.stats()
    print(f"  File claims: {ctx_stats.get('total_claims', 0)}")
    print(f"  Conflicts prevented: {ctx_stats.get('conflicts', 0)}")
    print(f"  Announcements: {ctx_stats.get('total_announcements', 0)}")
    announcements = build_context.get_announcements()
    for ann in announcements[-10:]:
        sig_preview = ann.detail[:80] if ann.detail else ""
        has_signature = any(c in sig_preview for c in "();,")
        marker = "✓" if has_signature else "○"
        print(f"    [{marker}] {ann.agent_id}: {ann.kind} — {sig_preview}")

    # Count announcements with interface signatures (Fix 2 validation)
    checks_total += 1
    rich_announcements = sum(
        1 for a in announcements
        if a.detail and any(c in a.detail for c in "();,")
    )
    if check(f"Rich announcements (with signatures)", rich_announcements > 0,
             f"{rich_announcements}/{len(announcements)} have interface info"):
        checks_passed += 1

    # ── Phase 3: Server + Preview Test ──────────────────────────────────
    section("Phase 3: Server + Preview")
    server_ok = False
    preview_url = None
    preview_mgr = None

    try:
        from forge_preview import PreviewManager, PreviewError, detect_stack

        si = detect_stack(PROJECT_DIR)
        checks_total += 1
        if check(f"Stack detected", si.kind != "unknown", f"{si.kind}: {si.entry}"):
            checks_passed += 1

        if si.kind != "unknown":
            preview_mgr = PreviewManager(PROJECT_DIR)

            # Start server only first for API testing
            port = preview_mgr._start_server_only(stack_info=si)
            checks_total += 1
            if check("Server starts", port > 0, f"port={port}"):
                checks_passed += 1
                server_ok = True

            if server_ok:
                import urllib.request
                # Test GET
                try:
                    resp = urllib.request.urlopen(f"http://localhost:{port}/api/categories", timeout=5)
                    status = resp.getcode()
                    body = resp.read().decode()
                    checks_total += 1
                    if check(f"GET /api/categories", status == 200, f"status={status}, body={body[:80]}"):
                        checks_passed += 1
                except Exception as e:
                    checks_total += 1
                    check("GET /api/categories", False, str(e)[:100])

                # Test POST
                try:
                    data = json.dumps({"amount": 42.50, "description": "Test", "category_id": 1, "date": "2026-03-11"}).encode()
                    req = urllib.request.Request(
                        f"http://localhost:{port}/api/expenses",
                        data=data,
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    resp = urllib.request.urlopen(req, timeout=5)
                    checks_total += 1
                    if check(f"POST /api/expenses", resp.getcode() in (200, 201), f"status={resp.getcode()}"):
                        checks_passed += 1
                except Exception as e:
                    checks_total += 1
                    check("POST /api/expenses", False, str(e)[:100])

            # Now start tunnel for preview URL
            preview_mgr.stop()  # Stop server-only mode
            try:
                preview_mgr = PreviewManager(PROJECT_DIR)
                preview_url = preview_mgr.start(stack_info=si)
                checks_total += 1
                if check("Preview tunnel", bool(preview_url), preview_url or "no URL"):
                    checks_passed += 1
            except PreviewError as e:
                checks_total += 1
                check("Preview tunnel", False, str(e)[:100])

    except Exception as e:
        checks_total += 1
        check("Server test", False, str(e)[:100])

    # ── Final Report ──────────────────────────────────────────────────────
    section("BENCHMARK RESULTS")

    tasks_passed = sum(1 for s, *_ in wave_results if s == "PASS")
    tasks_failed = sum(1 for s, *_ in wave_results if s == "FAIL")

    print(f"  Model:          {coding_model}")
    print(f"  Duration:       {total_duration:.1f}s")
    print(f"  Tasks:          {tasks_passed}/{len(wave_results)} passed")
    print(f"  Verifications:  {checks_passed}/{checks_total} passed")
    print(f"  Interface OK:   {'YES' if not issues else 'NO — ' + str(len(issues)) + ' issues'}")
    print(f"  Server OK:      {'YES' if server_ok else 'NO'}")
    if preview_url:
        print(f"  Preview:        {preview_url}")
    print()

    for status, tid, subject, detail, dur in wave_results:
        marker = "\033[92m+\033[0m" if status == "PASS" else "\033[91m!\033[0m"
        print(f"  [{marker}] Task {tid}: {subject} ({dur:.1f}s) — {detail}")

    print()
    if issues:
        print("  \033[91m✗ INTERFACE MISMATCH DETECTED\033[0m")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("  \033[92m✓ NO INTERFACE MISMATCHES\033[0m")

    overall = tasks_passed == len(wave_results) and not issues and checks_passed >= checks_total * 0.7
    print(f"\n  {'=' * 50}")
    if overall:
        print(f"  \033[1;92m  BENCHMARK PASSED  \033[0m")
    else:
        print(f"  \033[1;91m  BENCHMARK FAILED  \033[0m")
    print(f"  {'=' * 50}")

    # Save results
    results = {
        "model": coding_model,
        "duration": total_duration,
        "tasks_passed": tasks_passed,
        "tasks_total": len(wave_results),
        "checks_passed": checks_passed,
        "checks_total": checks_total,
        "interface_issues": issues,
        "server_ok": server_ok,
        "preview_url": preview_url,
        "overall": overall,
        "wave_results": [
            {"status": s, "task_id": tid, "subject": subj, "detail": det, "duration": dur}
            for s, tid, subj, det, dur in wave_results
        ],
    }
    results_path = PROJECT_DIR / ".forge" / "benchmark_results.json"
    results_path.write_text(json.dumps(results, indent=2))
    print(f"\n  Results saved: {results_path}")

    if preview_url and preview_mgr:
        print(f"\n  \033[1;36mPreview running at: {preview_url}\033[0m")
        print(f"  Press Ctrl+C to stop preview and exit.")
        try:
            preview_mgr._server_proc.wait()
        except KeyboardInterrupt:
            preview_mgr.stop()

    return overall


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    model = DEFAULT_MODEL
    if "--model" in sys.argv:
        idx = sys.argv.index("--model")
        if idx + 1 < len(sys.argv):
            model = resolve_model(sys.argv[idx + 1])

    print(f"\033[1;35m{'═' * 70}")
    print(f"  Nova Forge Benchmark: Expense Tracker")
    print(f"  Model: {model}")
    print(f"  Project: {PROJECT_DIR}")
    print(f"{'═' * 70}\033[0m")

    success = asyncio.run(run_benchmark(model))
    sys.exit(0 if success else 1)
