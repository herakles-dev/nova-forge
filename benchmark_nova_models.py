#!/usr/bin/env python3
"""Nova Model Benchmark Suite — standardized testing protocol for Nova Lite/Pro/Premier.

Runs the same 5-task Expense Tracker build across each Nova model, measures
25 verification checks, and produces a comparative scorecard with letter grades.

Rating System:
  S  (95-100%) — Production-grade. Ship it.
  A  (85-94%)  — Strong. Minor polish needed.
  B  (75-84%)  — Functional. Some gaps to address.
  C  (60-74%)  — Partial. Significant issues.
  D  (40-59%)  — Broken. Major failures.
  F  (<40%)    — Non-functional.

Dimensions scored (each 0-100, weighted):
  1. Task Completion   (30%) — Did it create the expected files?
  2. Code Quality      (25%) — Syntax-clean, no stubs, right patterns?
  3. Interface Fidelity (20%) — Do imports match exports? No hallucinations?
  4. Runtime Viability  (15%) — Does the server start? Do endpoints work?
  5. Efficiency         (10%) — Turns, retries, token usage, cost

Usage:
    source ~/.secrets/hercules.env

    # Run all 3 Nova models
    python3 benchmark_nova_models.py

    # Run a specific model
    python3 benchmark_nova_models.py --model nova-lite

    # Run all and save comparison report
    python3 benchmark_nova_models.py --all --report

    # Compare against a previous run
    python3 benchmark_nova_models.py --all --compare benchmarks/run_20260313_1400.json

    # Show results from a previous run
    python3 benchmark_nova_models.py --show benchmarks/run_20260313_1400.json
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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from benchmarks.benchmark_store import (
    BenchmarkStore, collect_metadata, detect_regressions,
    diff_checks, generate_optimization_hints, append_changelog,
    format_history, RegressionAlert, CheckDiff, OptimizationHint,
)

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("benchmark")

from config import get_model_config, ForgeProject, resolve_model, init_forge_dir, get_context_window, compute_turn_budget
from forge_agent import ForgeAgent, BUILT_IN_TOOLS, AgentEvent, get_tools_for_model
from forge_comms import BuildContext
from forge_guards import PathSandbox
from forge_hooks import HookSystem
from forge_tasks import TaskStore
from forge_models import estimate_cost, format_cost, get_capability


# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_BASE = Path("/tmp/forge-benchmark")

NOVA_MODELS = ["nova-lite", "nova-pro", "nova-premier"]

BENCHMARKS_DIR = Path(__file__).parent / "benchmarks"

# Letter grade thresholds
GRADE_THRESHOLDS = [
    (95, "S"), (85, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F"),
]

# Dimension weights (must sum to 1.0)
DIMENSION_WEIGHTS = {
    "task_completion":    0.30,
    "code_quality":       0.25,
    "interface_fidelity": 0.20,
    "runtime_viability":  0.15,
    "efficiency":         0.10,
}


# ── Spec & Tasks (same as benchmark_expense_tracker.py for consistency) ──────

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

EXPECTED_FILES = ["models.py", "api.py", "static/index.html", "static/app.js", "static/style.css"]


# ── Scenario 2: Todo App (FastAPI) ──────────────────────────────────────────

TODO_SPEC_MD = """\
# Todo App

A task management app with FastAPI backend and vanilla JS frontend.

## Tech Stack
- Backend: Python 3 + FastAPI + Uvicorn
- Database: SQLite3 (raw sqlite3 module, NOT SQLAlchemy)
- Frontend: Vanilla HTML/CSS/JS (no frameworks)

## Data Models

### Todo
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- title (TEXT NOT NULL)
- completed (INTEGER DEFAULT 0)
- priority (TEXT DEFAULT 'medium', one of 'low','medium','high')
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

## API Endpoints

All endpoints return JSON.

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/todos | List all todos (optional ?completed=true/false&priority=high) |
| POST | /api/todos | Create todo {title, priority} |
| PUT | /api/todos/{id} | Update todo {title, completed, priority} |
| DELETE | /api/todos/{id} | Delete todo |

## Frontend

Single page at / (index.html):
- Add todo form: title, priority dropdown
- Todo list: checkbox to toggle completed, delete button
- Filter bar: all / active / completed
- Priority badges: colored labels

## File Structure
```
models.py          - Database helpers (init_db, CRUD functions)
main.py            - FastAPI app with routes (imports from models)
static/index.html  - Main page
static/app.js      - Frontend logic
```

## IMPORTANT
- models.py uses raw sqlite3, NOT SQLAlchemy
- main.py uses FastAPI, NOT Flask
- The app variable must be named 'app': app = FastAPI()
"""

TODO_TASKS_JSON = [
    {
        "subject": "Create todo database models",
        "description": (
            "Create models.py with raw sqlite3 helpers. "
            "Functions: init_db(), create_todo(title, priority='medium'), get_todos(completed=None, priority=None), "
            "update_todo(todo_id, **kwargs), delete_todo(todo_id). "
            "Use DB_PATH='todos.db'. Call init_db() at import time."
        ),
        "files": ["models.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [],
    },
    {
        "subject": "Create FastAPI routes",
        "description": (
            "Create main.py with FastAPI app and routes. "
            "Import from models: from models import init_db, create_todo, get_todos, update_todo, delete_todo. "
            "Routes: GET/POST /api/todos, PUT/DELETE /api/todos/{id}. "
            "Serve static files via StaticFiles mount. "
            "For request validation, use simple Pydantic BaseModel classes with plain type hints "
            "(str, Optional[str], bool). Do NOT use Field(regex=...) or Field(pattern=...) — "
            "just use plain str type for priority and validate manually if needed."
        ),
        "files": ["main.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create frontend HTML page",
        "description": (
            "Create static/index.html — single page todo app. "
            "Include: a <form> element for adding todos with title input and priority dropdown, "
            "a todo list section with checkboxes for toggling completion, "
            "filter buttons (all/active/completed), and priority badges. Link to app.js. "
            "Use semantic HTML with proper form elements."
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
            "Functions: loadTodos(), addTodo(), toggleTodo(id), deleteTodo(id), filterTodos(status). "
            "Use fetch() for all API calls to /api/todos endpoints."
        ),
        "files": ["static/app.js"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [1, 2],
    },
]

TODO_EXPECTED_FILES = ["models.py", "main.py", "static/index.html", "static/app.js"]


# ── Scenario 3: Kanban Board (Hard) ─────────────────────────────────────────

KANBAN_SPEC_MD = """\
# Project Kanban Board

A project management app with authentication, kanban task board, and team collaboration.

## Tech Stack
- Backend: Python 3 + Flask
- Database: SQLite3 (raw sqlite3 module, NOT SQLAlchemy)
- Frontend: Vanilla HTML/CSS/JS (no frameworks)
- Auth: hashlib for password hashing, hmac for tokens

## Data Models

### User
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- username (TEXT NOT NULL UNIQUE)
- password_hash (TEXT NOT NULL)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

### Project
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- name (TEXT NOT NULL)
- description (TEXT DEFAULT '')
- owner_id (INTEGER NOT NULL REFERENCES users)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

### Task
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- title (TEXT NOT NULL)
- description (TEXT DEFAULT '')
- status (TEXT NOT NULL DEFAULT 'todo', one of 'todo', 'in_progress', 'done')
- priority (TEXT NOT NULL DEFAULT 'medium', one of 'low', 'medium', 'high', 'critical')
- project_id (INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE)
- assignee_id (INTEGER REFERENCES users)
- due_date (TEXT, ISO format YYYY-MM-DD, nullable)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

## API Endpoints

All endpoints return JSON. Prefix: none (root-level).

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/auth/register | Register user {username, password} → {id, username} |
| POST | /api/auth/login | Login {username, password} → {token, user_id, username} |
| GET | /api/projects | List all projects |
| POST | /api/projects | Create project {name, description, owner_id} |
| GET | /api/projects/<id>/tasks | List tasks for project (optional ?status=&priority=) |
| POST | /api/tasks | Create task {title, description, status, priority, project_id, assignee_id, due_date} |
| PUT | /api/tasks/<id> | Update task (any subset of fields) |
| DELETE | /api/tasks/<id> | Delete task |
| GET | /api/projects/<id>/stats | Project stats {total, by_status: {todo, in_progress, done}, overdue_count} |

## Frontend Pages

Single page app at / (index.html):
- Login/register form (toggle between modes)
- Project selector dropdown (after login)
- Kanban board: 3 columns (Todo, In Progress, Done)
- Task cards showing: title, priority badge (colored), assignee, due date
- Click task to edit in a modal/form, buttons to move between columns
- New task form: title, description, priority dropdown, assignee, due date
- Project stats bar: total tasks, progress percentage, overdue count

## File Structure
```
config.py         - Configuration constants (SECRET_KEY, DB_PATH)
auth.py           - Password hashing (hashlib) and token helpers (hmac)
models.py         - Database helpers (init_db, CRUD for users/projects/tasks)
api.py            - Flask app with auth + project + task routes
static/index.html - Main page with login and kanban board
static/app.js     - Frontend logic (auth state, fetch API, kanban interaction)
static/style.css  - Kanban column layout and card styling
```

## IMPORTANT
- config.py exports SECRET_KEY (str) and DB_PATH (str)
- auth.py exports: hash_password(password) → str, verify_password(password, hashed) → bool, generate_token(user_id, secret) → str, validate_token(token, secret) → int|None
- auth.py uses hashlib.sha256 for hashing and hmac for tokens — NOT bcrypt, NOT jwt
- models.py uses raw sqlite3, NOT SQLAlchemy
- models.py exports FUNCTIONS (not classes): init_db(), create_user(), get_user_by_username(), create_project(), get_projects(), get_project_tasks(), create_task(), update_task(), delete_task(), get_project_stats()
- api.py imports from BOTH auth and models
- Do NOT create ORM model classes
"""

KANBAN_TASKS_JSON = [
    {
        "subject": "Create configuration module",
        "description": (
            "Create config.py with application constants. "
            "Exports: SECRET_KEY = 'kanban-secret-key-2026' (str), DB_PATH = 'kanban.db' (str). "
            "Keep it simple — just these two constants, no classes or functions."
        ),
        "files": ["config.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [],
    },
    {
        "subject": "Create authentication module",
        "description": (
            "Create auth.py with password hashing and token helpers. "
            "Import hashlib and hmac (stdlib only, no external deps). "
            "Functions: hash_password(password: str) → str using hashlib.sha256 hexdigest, "
            "verify_password(password: str, hashed: str) → bool, "
            "generate_token(user_id: int, secret: str) → str using hmac.new with sha256, "
            "validate_token(token: str, secret: str) → int or None (returns user_id if valid). "
            "Token format: '{user_id}:{hmac_signature}' so it can be split and verified."
        ),
        "files": ["auth.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create database models with 3 tables",
        "description": (
            "Create models.py with raw sqlite3 database helpers for 3 related tables. "
            "Import DB_PATH from config: from config import DB_PATH. "
            "Tables: users (id, username, password_hash, created_at), "
            "projects (id, name, description, owner_id FK→users, created_at), "
            "tasks (id, title, description, status, priority, project_id FK→projects, assignee_id FK→users, due_date, created_at). "
            "Enable foreign keys: PRAGMA foreign_keys = ON. "
            "Functions: init_db(), create_user(username, password_hash) → dict, "
            "get_user_by_username(username) → dict|None, "
            "create_project(name, description, owner_id) → dict, "
            "get_projects() → list[dict], "
            "get_project_tasks(project_id, status=None, priority=None) → list[dict], "
            "create_task(title, description, status, priority, project_id, assignee_id=None, due_date=None) → dict, "
            "update_task(task_id, **kwargs) → dict, "
            "delete_task(task_id) → bool, "
            "get_project_stats(project_id) → dict with keys: total, by_status (dict), overdue_count (int). "
            "Call init_db() at import time."
        ),
        "files": ["models.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create Flask API with auth and CRUD routes",
        "description": (
            "Create api.py with Flask app and all REST routes. "
            "Import from auth: from auth import hash_password, verify_password, generate_token, validate_token. "
            "Import from models: from models import init_db, create_user, get_user_by_username, "
            "create_project, get_projects, get_project_tasks, create_task, update_task, delete_task, get_project_stats. "
            "Import from config: from config import SECRET_KEY. "
            "Auth routes: POST /api/auth/register (hash password, create user), POST /api/auth/login (verify, return token). "
            "Project routes: GET /api/projects, POST /api/projects. "
            "Task routes: GET /api/projects/<id>/tasks (with ?status=&priority= filters), "
            "POST /api/tasks, PUT /api/tasks/<id>, DELETE /api/tasks/<id>. "
            "Stats: GET /api/projects/<id>/stats. "
            "Serve static files from ./static/. Return JSON with appropriate status codes."
        ),
        "files": ["api.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [1, 2],
    },
    {
        "subject": "Create kanban board HTML page",
        "description": (
            "Create static/index.html — single page kanban board UI. "
            "Include: auth section (login/register forms, toggleable), "
            "project selector dropdown, "
            "kanban board with 3 columns: div.column#todo-column, div.column#in-progress-column, div.column#done-column, "
            "each column has a header and a task list area. "
            "Task card template: title, priority badge, assignee name, due date. "
            "New task form (modal or inline): title, description, priority dropdown (low/medium/high/critical), "
            "assignee, due date input. "
            "Stats bar showing total tasks and progress. "
            "Link to app.js and style.css."
        ),
        "files": ["static/index.html"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [2],
    },
    {
        "subject": "Create kanban CSS styling",
        "description": (
            "Create static/style.css — kanban board styling. "
            "Layout: flexbox kanban columns (3 equal columns side by side). "
            "Cards: rounded corners, subtle shadow, priority color indicators "
            "(low=green, medium=blue, high=orange, critical=red). "
            "Auth forms: centered, clean. Responsive: stack columns on mobile. "
            "Use a consistent color scheme with a dark header."
        ),
        "files": ["static/style.css"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [4],
    },
    {
        "subject": "Create kanban frontend JavaScript",
        "description": (
            "Create static/app.js — frontend logic for the kanban board. "
            "Auth state: store token in localStorage, show/hide auth vs kanban sections. "
            "Functions: register(), login(), logout(), loadProjects(), selectProject(id), "
            "loadTasks(projectId), renderKanban(tasks), addTask(), moveTask(taskId, newStatus), "
            "editTask(taskId), deleteTask(taskId), loadStats(projectId). "
            "Use fetch() with Authorization header for all API calls. "
            "DOM manipulation: populate dropdowns, render task cards in correct columns, "
            "handle form submissions, update stats bar."
        ),
        "files": ["static/app.js"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [3, 4],
    },
]

KANBAN_EXPECTED_FILES = [
    "config.py", "auth.py", "models.py", "api.py",
    "static/index.html", "static/app.js", "static/style.css",
]


# ── Scenario 4: Realtime Kanban (Nightmare) ─────────────────────────────────

REALTIME_SPEC_MD = """\
# Realtime Project Kanban Board

A full-featured project management app with authentication, real-time updates via
Server-Sent Events (SSE), file attachments, and an activity log.

## Tech Stack
- Backend: Python 3 + Flask
- Database: SQLite3 (raw sqlite3 module, NOT SQLAlchemy)
- Frontend: Vanilla HTML/CSS/JS (no frameworks)
- Auth: hashlib for password hashing, hmac for tokens
- Real-time: Server-Sent Events (SSE) via Flask streaming response
- Uploads: Flask request.files + os.makedirs for storage

## Data Models

### User
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- username (TEXT NOT NULL UNIQUE)
- password_hash (TEXT NOT NULL)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

### Project
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- name (TEXT NOT NULL)
- description (TEXT DEFAULT '')
- owner_id (INTEGER NOT NULL REFERENCES users)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

### Task
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- title (TEXT NOT NULL)
- description (TEXT DEFAULT '')
- status (TEXT NOT NULL DEFAULT 'todo', one of 'todo', 'in_progress', 'done')
- priority (TEXT NOT NULL DEFAULT 'medium', one of 'low', 'medium', 'high', 'critical')
- project_id (INTEGER NOT NULL REFERENCES projects ON DELETE CASCADE)
- assignee_id (INTEGER REFERENCES users)
- due_date (TEXT, ISO format YYYY-MM-DD, nullable)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

### Attachment
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- filename (TEXT NOT NULL)
- filepath (TEXT NOT NULL)
- task_id (INTEGER NOT NULL REFERENCES tasks ON DELETE CASCADE)
- uploaded_by (INTEGER NOT NULL REFERENCES users)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

### ActivityLog
- id (INTEGER PRIMARY KEY AUTOINCREMENT)
- action (TEXT NOT NULL, e.g. 'task_created', 'task_moved', 'file_uploaded')
- entity_type (TEXT NOT NULL, e.g. 'task', 'project', 'attachment')
- entity_id (INTEGER NOT NULL)
- user_id (INTEGER REFERENCES users)
- details (TEXT, JSON string with extra info)
- created_at (TEXT DEFAULT CURRENT_TIMESTAMP)

## API Endpoints

All endpoints return JSON. Prefix: none (root-level).

| Method | Path | Description |
|--------|------|-------------|
| POST | /api/auth/register | Register user {username, password} → {id, username} |
| POST | /api/auth/login | Login {username, password} → {token, user_id, username} |
| GET | /api/projects | List all projects |
| POST | /api/projects | Create project {name, description, owner_id} |
| GET | /api/projects/<id>/tasks | List tasks (optional ?status=&priority=) |
| POST | /api/tasks | Create task {title, desc, status, priority, project_id, assignee_id, due_date} |
| PUT | /api/tasks/<id> | Update task |
| DELETE | /api/tasks/<id> | Delete task |
| GET | /api/projects/<id>/stats | Project stats {total, by_status, overdue_count, attachment_count} |
| GET | /api/tasks/<id>/attachments | List attachments for task |
| POST | /api/tasks/<id>/attachments | Upload file (multipart/form-data, field name: 'file') |
| DELETE | /api/attachments/<id> | Delete attachment |
| GET | /api/activity | Recent activity log (optional ?project_id=&limit=50) |
| GET | /api/events | SSE stream — emits task_updated, task_created, task_deleted events as JSON |

## Server-Sent Events (SSE)

The /api/events endpoint returns a streaming response with Content-Type: text/event-stream.
Each event has format:
```
event: task_updated
data: {"task_id": 1, "status": "done", "timestamp": "..."}

```
Events are broadcast when tasks are created, updated, or deleted.
Use a module-level list of queues (one per connected client) for fan-out.

## File Uploads

POST /api/tasks/<id>/attachments accepts multipart/form-data with a 'file' field.
Files are saved to uploads/<task_id>/<filename>.
Max file size: 10MB.
Return {id, filename, task_id} on success.

## Frontend Pages

Single page at / (index.html):
- Login/register form (toggle between modes)
- Project selector dropdown
- Kanban board: 3 columns (Todo, In Progress, Done)
- Task cards: title, priority badge, assignee, due date, attachment count badge
- Task detail modal: edit fields, file upload area, attachment list
- Activity feed sidebar: scrolling list of recent actions
- Auto-refresh via EventSource (SSE) — kanban updates without page reload

## File Structure
```
config.py         - Configuration (SECRET_KEY, DB_PATH, UPLOAD_DIR, MAX_FILE_SIZE)
auth.py           - Password hashing (hashlib) and token helpers (hmac)
models.py         - Database helpers (init_db, CRUD for all 5 tables)
events.py         - SSE event broadcaster (EventBroadcaster class with subscribe/publish)
api.py            - Flask app with all routes (auth, projects, tasks, attachments, activity, SSE)
static/index.html - Main page with kanban board, activity feed, file upload
static/app.js     - Frontend logic (auth, kanban, SSE client, file upload)
static/style.css  - Full styling (kanban, modals, activity feed, file badges)
```

## IMPORTANT
- config.py exports: SECRET_KEY (str), DB_PATH (str), UPLOAD_DIR (str, default 'uploads'), MAX_FILE_SIZE (int, 10485760)
- auth.py exports: hash_password(password) → str, verify_password(password, hashed) → bool, generate_token(user_id, secret) → str, validate_token(token, secret) → int|None
- auth.py uses hashlib.sha256 for hashing and hmac for tokens — NOT bcrypt, NOT jwt
- events.py exports: EventBroadcaster class with methods subscribe() → queue, unsubscribe(queue), publish(event_type, data_dict)
- models.py uses raw sqlite3, NOT SQLAlchemy
- models.py exports FUNCTIONS (not classes): init_db(), plus CRUD for all 5 tables
- api.py imports from auth, models, events, AND config
- File uploads use Flask request.files and os module — NOT any upload library
- Do NOT create ORM model classes
"""

REALTIME_TASKS_JSON = [
    {
        "subject": "Create configuration module",
        "description": (
            "Create config.py with application constants. "
            "Exports: SECRET_KEY = 'realtime-kanban-secret-2026' (str), "
            "DB_PATH = 'realtime_kanban.db' (str), "
            "UPLOAD_DIR = 'uploads' (str), "
            "MAX_FILE_SIZE = 10485760 (int, 10MB in bytes). "
            "Keep it simple — just these four constants."
        ),
        "files": ["config.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [],
    },
    {
        "subject": "Create authentication module",
        "description": (
            "Create auth.py with password hashing and token helpers. "
            "Import hashlib and hmac (stdlib only). "
            "Functions: hash_password(password: str) → str using hashlib.sha256 hexdigest, "
            "verify_password(password: str, hashed: str) → bool, "
            "generate_token(user_id: int, secret: str) → str using hmac.new with sha256, "
            "validate_token(token: str, secret: str) → int or None (returns user_id if valid). "
            "Token format: '{user_id}:{hmac_signature}'."
        ),
        "files": ["auth.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create database models with 5 tables",
        "description": (
            "Create models.py with raw sqlite3 database helpers for 5 related tables. "
            "Import DB_PATH from config: from config import DB_PATH. "
            "Tables: users (id, username, password_hash, created_at), "
            "projects (id, name, description, owner_id→users, created_at), "
            "tasks (id, title, description, status, priority, project_id→projects, assignee_id→users, due_date, created_at), "
            "attachments (id, filename, filepath, task_id→tasks, uploaded_by→users, created_at), "
            "activity_log (id, action, entity_type, entity_id, user_id→users, details, created_at). "
            "Enable PRAGMA foreign_keys = ON. "
            "Functions: init_db(), "
            "create_user(username, password_hash), get_user_by_username(username), "
            "create_project(name, description, owner_id), get_projects(), "
            "get_project_tasks(project_id, status=None, priority=None), "
            "create_task(title, description, status, priority, project_id, assignee_id=None, due_date=None), "
            "update_task(task_id, **kwargs), delete_task(task_id), get_project_stats(project_id), "
            "create_attachment(filename, filepath, task_id, uploaded_by), get_task_attachments(task_id), delete_attachment(attachment_id), "
            "log_activity(action, entity_type, entity_id, user_id=None, details=None), get_activity(project_id=None, limit=50). "
            "Call init_db() at import time. "
            "get_project_stats returns: {total, by_status: {todo, in_progress, done}, overdue_count, attachment_count}."
        ),
        "files": ["models.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create SSE event broadcaster",
        "description": (
            "Create events.py with a Server-Sent Events broadcasting system. "
            "Class EventBroadcaster with: "
            "__init__(self) — initialize empty list of subscriber queues, "
            "subscribe(self) → queue.Queue — create and register a new queue, return it, "
            "unsubscribe(self, q) — remove queue from subscribers, "
            "publish(self, event_type: str, data: dict) — put formatted SSE message on all subscriber queues. "
            "SSE format: 'event: {type}\\ndata: {json}\\n\\n'. "
            "Use queue.Queue from stdlib for thread-safe fan-out. "
            "Also export a module-level instance: broadcaster = EventBroadcaster()."
        ),
        "files": ["events.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [0],
    },
    {
        "subject": "Create Flask API with all routes",
        "description": (
            "Create api.py with Flask app and all REST routes. "
            "Import from auth: from auth import hash_password, verify_password, generate_token, validate_token. "
            "Import from models: from models import (init_db, create_user, get_user_by_username, "
            "create_project, get_projects, get_project_tasks, create_task, update_task, delete_task, "
            "get_project_stats, create_attachment, get_task_attachments, delete_attachment, "
            "log_activity, get_activity). "
            "Import from events: from events import broadcaster. "
            "Import from config: from config import SECRET_KEY, UPLOAD_DIR, MAX_FILE_SIZE. "
            "Auth routes: POST /api/auth/register, POST /api/auth/login. "
            "Project routes: GET/POST /api/projects. "
            "Task routes: GET /api/projects/<id>/tasks, POST /api/tasks, PUT /api/tasks/<id>, DELETE /api/tasks/<id>. "
            "Stats: GET /api/projects/<id>/stats. "
            "Attachments: GET /api/tasks/<id>/attachments, POST /api/tasks/<id>/attachments (multipart), DELETE /api/attachments/<id>. "
            "Activity: GET /api/activity (?project_id=&limit=50). "
            "SSE: GET /api/events — streaming response with text/event-stream content type. "
            "On task mutations, call broadcaster.publish() and log_activity(). "
            "Serve static files from ./static/."
        ),
        "files": ["api.py"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [1, 2, 3],
    },
    {
        "subject": "Create realtime kanban HTML page",
        "description": (
            "Create static/index.html — single page realtime kanban board. "
            "Auth section: login/register forms (toggleable). "
            "Project selector dropdown. "
            "Kanban board: 3 columns (div.column#todo-column, div.column#in-progress-column, div.column#done-column). "
            "Task cards: title, priority badge, assignee, due date, attachment count indicator. "
            "Task detail modal: edit form, file upload input (type=file), attachment list with delete buttons. "
            "Activity feed sidebar: scrollable list of recent actions. "
            "New task form: title, description, priority, assignee, due date. "
            "Link to app.js and style.css. "
            "Include a hidden div#sse-status for connection status indicator."
        ),
        "files": ["static/index.html"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [2],
    },
    {
        "subject": "Create realtime kanban CSS styling",
        "description": (
            "Create static/style.css — full styling for realtime kanban. "
            "Kanban layout: flexbox 3-column board. "
            "Cards: rounded corners, shadow, priority colors (low=green, medium=blue, high=orange, critical=red). "
            "Modal: overlay with centered panel for task detail. "
            "Activity feed sidebar: fixed right panel, scrollable. "
            "File upload area: dashed border dropzone style. "
            "Attachment list: small file icons with delete button. "
            "Auth forms: centered. Responsive: stack columns on mobile, hide sidebar. "
            "SSE status indicator: small dot (green=connected, red=disconnected)."
        ),
        "files": ["static/style.css"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [5],
    },
    {
        "subject": "Create realtime kanban frontend JavaScript",
        "description": (
            "Create static/app.js — full frontend logic with SSE and file uploads. "
            "Auth: register(), login(), logout(), store token in localStorage. "
            "Projects: loadProjects(), selectProject(id). "
            "Kanban: loadTasks(projectId), renderKanban(tasks), addTask(), "
            "moveTask(taskId, newStatus), editTask(taskId), deleteTask(taskId). "
            "SSE: connectSSE() using new EventSource('/api/events'), "
            "handle 'task_updated'/'task_created'/'task_deleted' events to auto-refresh kanban. "
            "File upload: uploadFile(taskId, fileInput) using FormData and fetch with multipart, "
            "loadAttachments(taskId), deleteAttachment(attachmentId). "
            "Activity: loadActivity(), renderActivityFeed(items). "
            "Stats: loadStats(projectId). "
            "Use fetch() with Authorization header. DOM manipulation for all UI updates."
        ),
        "files": ["static/app.js"],
        "sprint": "sprint-01",
        "risk": "low",
        "blocked_by": [4, 5],
    },
]

REALTIME_EXPECTED_FILES = [
    "config.py", "auth.py", "models.py", "events.py", "api.py",
    "static/index.html", "static/app.js", "static/style.css",
]


# ── Scenario registry ──────────────────────────────────────────────────────

BENCHMARK_SCENARIOS = {
    "expense-tracker": {
        "spec": SPEC_MD,
        "tasks": TASKS_JSON,
        "expected_files": EXPECTED_FILES,
        "name": "Expense Tracker",
        "description": "Flask + SQLite + vanilla JS (5 tasks, 3 waves)",
    },
    "todo-app": {
        "spec": TODO_SPEC_MD,
        "tasks": TODO_TASKS_JSON,
        "expected_files": TODO_EXPECTED_FILES,
        "name": "Todo App",
        "description": "FastAPI + SQLite + vanilla JS (4 tasks, 3 waves)",
    },
    "kanban-board": {
        "spec": KANBAN_SPEC_MD,
        "tasks": KANBAN_TASKS_JSON,
        "expected_files": KANBAN_EXPECTED_FILES,
        "name": "Kanban Board",
        "description": "Flask + SQLite + auth + 3 tables (7 tasks, 4 waves) — HARD",
    },
    "realtime-kanban": {
        "spec": REALTIME_SPEC_MD,
        "tasks": REALTIME_TASKS_JSON,
        "expected_files": REALTIME_EXPECTED_FILES,
        "name": "Realtime Kanban",
        "description": "Flask + SQLite + auth + SSE + uploads + 5 tables (8 tasks, 4 waves) — NIGHTMARE",
    },
}

DEFAULT_SCENARIO = "expense-tracker"


# ── Display helpers ──────────────────────────────────────────────────────────

def _c(code: str, text: str) -> str:
    """ANSI color wrapper."""
    codes = {"r": "91", "g": "92", "y": "93", "c": "96", "m": "95", "b": "1", "d": "2", "0": "0"}
    return f"\033[{codes.get(code, '0')}m{text}\033[0m"

def section(title: str) -> None:
    print(f"\n{_c('c', '─' * 70)}")
    print(f"  {_c('b', title)}")
    print(f"{_c('c', '─' * 70)}")

def grade_color(grade: str) -> str:
    """Return ANSI-colored grade."""
    if grade == "S":
        return _c("m", "S")
    if grade == "A":
        return _c("g", "A")
    if grade == "B":
        return _c("c", "B")
    if grade == "C":
        return _c("y", "C")
    return _c("r", grade)

def score_to_grade(score: float) -> str:
    for threshold, grade in GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"

def score_bar(score: float, width: int = 20) -> str:
    """Visual bar: ████████░░░░░░░░░░░░ 75%"""
    filled = int(score / 100 * width)
    empty = width - filled
    if score >= 85:
        color = "g"
    elif score >= 60:
        color = "y"
    else:
        color = "r"
    return f"{_c(color, '█' * filled)}{_c('d', '░' * empty)} {score:.0f}%"


# ── Interface analysis (from benchmark_expense_tracker.py) ───────────────────

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
    return imports


# ── Scoring engine ───────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    """Single verification check."""
    name: str
    dimension: str  # task_completion | code_quality | interface_fidelity | runtime_viability | efficiency
    passed: bool
    detail: str = ""
    weight: float = 1.0  # relative weight within dimension


@dataclass
class ModelBenchmark:
    """Complete benchmark result for one model."""
    model_alias: str
    model_id: str
    timestamp: str
    duration_secs: float
    tasks_passed: int
    tasks_total: int
    retries: int
    total_turns: int
    total_tool_calls: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost: float
    checks: list[CheckResult] = field(default_factory=list)
    task_results: list[dict] = field(default_factory=list)
    interface_issues: list[str] = field(default_factory=list)
    server_ok: bool = False

    @property
    def dimension_scores(self) -> dict[str, float]:
        """Calculate per-dimension score (0-100)."""
        scores = {}
        for dim in DIMENSION_WEIGHTS:
            dim_checks = [c for c in self.checks if c.dimension == dim]
            if not dim_checks:
                scores[dim] = 0.0
                continue
            total_weight = sum(c.weight for c in dim_checks)
            passed_weight = sum(c.weight for c in dim_checks if c.passed)
            scores[dim] = (passed_weight / total_weight * 100) if total_weight > 0 else 0.0
        return scores

    @property
    def overall_score(self) -> float:
        """Weighted overall score (0-100)."""
        dims = self.dimension_scores
        return sum(dims.get(d, 0) * w for d, w in DIMENSION_WEIGHTS.items())

    @property
    def grade(self) -> str:
        return score_to_grade(self.overall_score)

    def to_dict(self) -> dict:
        return {
            "model_alias": self.model_alias,
            "model_id": self.model_id,
            "timestamp": self.timestamp,
            "duration_secs": round(self.duration_secs, 1),
            "tasks_passed": self.tasks_passed,
            "tasks_total": self.tasks_total,
            "retries": self.retries,
            "total_turns": self.total_turns,
            "total_tool_calls": self.total_tool_calls,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_cost": round(self.total_cost, 6),
            "overall_score": round(self.overall_score, 1),
            "grade": self.grade,
            "dimension_scores": {k: round(v, 1) for k, v in self.dimension_scores.items()},
            "checks": [
                {"name": c.name, "dimension": c.dimension, "passed": c.passed,
                 "detail": c.detail, "weight": c.weight}
                for c in self.checks
            ],
            "task_results": self.task_results,
            "interface_issues": self.interface_issues,
            "server_ok": self.server_ok,
        }


# ── Resilience checks (bonus, non-destructive) ───────────────────────────────

def run_resilience_checks(project_dir: Path) -> list[dict]:
    """Inject faults and verify the checker catches them. Restore after each test.

    Returns list of {name, passed, detail} dicts. Non-destructive:
    original files are always restored.
    """
    results = []

    # Check 1: Inject syntax error → verify checker catches it
    target = project_dir / "models.py"
    if target.exists():
        original = target.read_text()
        try:
            target.write_text(original + "\n!!!SYNTAX ERROR!!!\n")
            try:
                import ast
                ast.parse(target.read_text())
                results.append({"name": "Syntax error detection", "passed": False,
                                "detail": "Injected syntax error was not caught by AST parse"})
            except SyntaxError:
                results.append({"name": "Syntax error detection", "passed": True,
                                "detail": "AST correctly caught injected syntax error"})
        finally:
            target.write_text(original)

    # Check 2: Delete expected file → verify checker flags it
    target = project_dir / "api.py"
    if target.exists():
        original = target.read_text()
        try:
            target.unlink()
            missing = not target.exists()
            results.append({"name": "Missing file detection", "passed": missing,
                            "detail": "Checker correctly detects missing api.py" if missing
                            else "File still exists after deletion"})
        finally:
            target.write_text(original)

    # Check 3: Truncate a file to stub → verify size check catches it
    target = project_dir / "static" / "app.js"
    if target.exists():
        original = target.read_text()
        try:
            target.write_text("// stub")
            is_stub = target.stat().st_size < 400
            results.append({"name": "Stub file detection", "passed": is_stub,
                            "detail": f"Truncated file size {target.stat().st_size}B detected as stub"
                            if is_stub else "Stub not detected"})
        finally:
            target.write_text(original)

    return results


# ── Build runner ─────────────────────────────────────────────────────────────

async def run_single_model(model_alias: str, verbose: bool = False, scenario_key: str = DEFAULT_SCENARIO) -> ModelBenchmark:
    """Run the full benchmark for one model. Returns structured result."""

    scenario = BENCHMARK_SCENARIOS[scenario_key]
    spec_text = scenario["spec"]
    tasks_data = scenario["tasks"]
    scenario_name = scenario["name"]

    model_id = resolve_model(model_alias)
    project_dir = PROJECT_BASE / f"bench-{model_alias}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── Setup ────────────────────────────────────────────────────────────
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True)
    (project_dir / "static").mkdir()
    (project_dir / "spec.md").write_text(spec_text)

    project = init_forge_dir(project_dir)
    store = TaskStore(project.tasks_file)

    # Create tasks
    subject_to_id: dict[int, str] = {}
    for i, t in enumerate(tasks_data):
        blocked = [subject_to_id[b] for b in t.get("blocked_by", []) if b in subject_to_id]
        task = store.create(
            subject=t["subject"],
            description=t["description"],
            metadata={
                "project": scenario_key,
                "sprint": t["sprint"],
                "risk": t["risk"],
                "files": t["files"],
            },
            blocked_by=blocked or None,
        )
        subject_to_id[i] = task.id

    tasks = store.list()
    waves = store.compute_waves()
    build_context = BuildContext(project_root=project_dir)

    # Pre-claim files
    for t in tasks:
        t_files = (t.metadata or {}).get("files", [])
        agent_id = f"task-{t.id}"
        for tf in t_files:
            build_context.claim_file(tf, agent_id)

    # ── Build ────────────────────────────────────────────────────────────
    total_start = time.time()
    total_turns = 0
    total_tc = 0
    total_tokens_in = 0
    total_tokens_out = 0
    total_cost = 0.0
    total_retries = 0
    task_results = []

    for wave_idx, wave_tasks in enumerate(waves):
        for task in wave_tasks:
            fresh = store.get(task.id)
            if fresh and fresh.status in ("blocked", "completed"):
                continue

            store.update(task.id, status="in_progress")
            if verbose:
                task_num = sum(1 for t in task_results) + 1
                print(f"    [{model_alias}] Task {task_num}/{len(tasks)}: {task.subject}", flush=True)

            ctx_window = get_context_window(model_id)
            # Let config.py's scaling logic determine the right max_tokens
            mc = get_model_config(model_id)
            hooks = HookSystem(project.settings_file)
            sandbox = PathSandbox(project_dir)

            # Gather upstream context
            from forge_cli import ForgeShell
            shell = ForgeShell.__new__(ForgeShell)
            shell.project_path = project_dir

            upstream_context = shell._gather_upstream_artifacts(task, store, store.list())
            context_sections = list(upstream_context.values())
            context_hint = "\n\n" + "\n\n".join(context_sections) if context_sections else ""

            # Mandatory reads
            mandatory_reads = []
            for dep_id in (task.blocked_by or []):
                dep = store.get(dep_id)
                if dep and dep.artifacts:
                    for fpath in dep.artifacts.keys():
                        short = shell._shorten_path(fpath)
                        if short.endswith(('.py', '.js', '.ts')):
                            mandatory_reads.append(short)
            mandatory_reads = list(dict.fromkeys(mandatory_reads))[:8]

            # Pre-seed upstream file content to save read turns
            read_instruction = ""
            if mandatory_reads:
                pre_seeded = []
                still_need_read = []
                for fpath in mandatory_reads:
                    full_path = project_dir / fpath
                    if full_path.exists() and full_path.stat().st_size < 10_000:
                        try:
                            content = full_path.read_text()
                            _lang = {"py": "python", "js": "javascript", "html": "html", "css": "css"}.get(
                                fpath.rsplit(".", 1)[-1] if "." in fpath else "", ""
                            )
                            pre_seeded.append(f"### {fpath}\n```{_lang}\n{content}\n```")
                        except Exception:
                            still_need_read.append(fpath)
                    else:
                        still_need_read.append(fpath)

                if pre_seeded:
                    read_instruction = (
                        "\n\n## UPSTREAM FILES (already read for you)\n"
                        "These files were created by earlier tasks. Use their ACTUAL interfaces:\n\n"
                        + "\n\n".join(pre_seeded) + "\n\n"
                        "Do NOT call read_file on these — you already have their content above.\n"
                    )
                if still_need_read:
                    read_instruction += (
                        f"\n\n## MANDATORY: Read Before Writing\n"
                        f"You MUST call read_file on these files BEFORE writing code that uses them:\n"
                        + ", ".join(still_need_read) + "\n"
                    )

            spec_text = (project_dir / "spec.md").read_text()
            expected_files = (task.metadata or {}).get("files", [])
            files_hint = ", ".join(expected_files) if expected_files else "as specified"

            # Chunk hint: only for 32K models — matches CLI behavior
            chunk_hint = ""
            if ctx_window <= 32_000:
                chunk_hint = (
                    "\n\n## OUTPUT LIMIT\n"
                    "You have a ~4K token output limit (~80 lines of code per tool call).\n"
                    "NEVER write more than 80 lines in a single write_file call.\n"
                    "Strategy: write_file (first 80 lines) → append_file (next 80) → repeat.\n"
                )

            # SQLite threading hint for Flask projects
            sqlite_hint = (
                "\n\n## IMPORTANT: SQLite + Flask Threading\n"
                "Do NOT create a module-level sqlite3 connection. Flask is multi-threaded — "
                "module-level connections cause 'SQLite objects created in a thread can only be used in that same thread'.\n"
                "Instead, create a NEW connection inside each function:\n"
                "  def get_categories():\n"
                "      conn = sqlite3.connect(DB_PATH)\n"
                "      # ... use conn ...\n"
                "      conn.close()\n"
            )

            # Language hints per file
            _lang_lines = []
            for _f in expected_files:
                if _f.endswith(".py"):
                    _lang_lines.append(f"- {_f}: **Python** (use # comments, def/class, from X import Y)")
                elif _f.endswith(".js"):
                    _lang_lines.append(f"- {_f}: **JavaScript** (use // comments, function/const)")
                elif _f.endswith(".html"):
                    _lang_lines.append(f"- {_f}: HTML")
                elif _f.endswith(".css"):
                    _lang_lines.append(f"- {_f}: CSS")
            lang_hint = "\n".join(_lang_lines)

            prompt = (
                f"## Project Spec\n{spec_text}\n\n"
                f"## Your Task\n{task.subject}: {task.description}\n\n"
                f"## Your Files\n"
                f"You MUST create these files: {files_hint}\n"
                f"Only write YOUR files. Do NOT create files that belong to other tasks.\n\n"
                f"## File Languages\n{lang_hint}\n\n"
                f"## Instructions\n"
                f"Implement this task COMPLETELY. Use write_file to create EVERY file listed above. "
                f"CRITICAL: Include ALL functions listed in the task description in your FIRST write_file call. "
                f"Do NOT write a partial file (e.g. only init_db) and edit it later — write the COMPLETE file "
                f"with every function in one shot. If it's too long, write the first half with write_file then "
                f"IMMEDIATELY call append_file with the remaining functions. "
                f"ALWAYS read existing files first with read_file before writing code that depends on them. "
                f"Use their ACTUAL interface — do not assume or hallucinate function names or APIs. "
                f"Write complete, working code — not stubs or placeholders. "
                f"Do NOT describe file contents in text — use write_file/append_file tools with the full content."
                f"{chunk_hint}"
                f"{sqlite_hint}"
                f"{read_instruction}"
                f"{context_hint}"
            )

            # System prompt: use PromptBuilder — same path as CLI
            from prompt_builder import PromptBuilder
            pb = PromptBuilder(project_dir)
            system = pb.build_system_prompt(
                role="builder",
                project_context=spec_text[:2000] if spec_text else "",
                model_id=mc.model_id,
            )

            # Tool selection: same model-aware path as CLI
            task_tools = get_tools_for_model(ctx_window, has_build_context=True)

            # Adaptive turn budget based on task complexity
            _task_meta = dict(task.metadata or {})
            if task.blocked_by:
                _task_meta["blocked_by"] = task.blocked_by
            _budget = compute_turn_budget(_task_meta, max_turns_ceiling=30)
            per_task_turns = _budget["soft_limit"]

            agent = ForgeAgent(
                model_config=mc,
                project_root=project_dir,
                hooks=hooks,
                sandbox=sandbox,
                tools=task_tools,
                max_turns=per_task_turns,
                agent_id=f"task-{task.id}",
                build_context=build_context,
            )
            agent._verify_budget = _budget["verify_budget"]
            agent._escalation_turns = _budget["escalation_turns"]

            task_start = time.time()
            retries = 0
            try:
                result = await agent.run(prompt=prompt, system=system)
                duration = time.time() - task_start
                fc = len(result.artifacts) if result.artifacts else 0
                tc = result.tool_calls_made

                # No-write retry
                if expected_files and fc == 0 and not result.error:
                    retries += 1
                    retry_prompt = (
                        f"You completed the task description but did NOT use the write_file tool to create any files.\n"
                        f"You MUST create the following files using the write_file tool: {', '.join(expected_files)}\n"
                        f"Do NOT describe what to write — actually call write_file with the full file content.\n\n"
                        f"Original task:\n{prompt}"
                    )
                    result = await agent.run(prompt=retry_prompt, system=system)
                    duration = time.time() - task_start
                    tc += result.tool_calls_made
                    fc = len(result.artifacts) if result.artifacts else 0

                # Stub retry
                if expected_files and not result.error:
                    stub_files = []
                    min_size = {"py": 100, "js": 200, "html": 200, "css": 100}
                    for fpath in expected_files:
                        full = project_dir / fpath
                        if full.exists():
                            size = full.stat().st_size
                            ext = fpath.rsplit(".", 1)[-1] if "." in fpath else ""
                            threshold = min_size.get(ext, 100)
                            if size < threshold:
                                stub_files.append(f"{fpath} ({size} bytes)")
                    if stub_files:
                        retries += 1
                        retry_prompt = (
                            f"You wrote these files but they are STUBS or PLACEHOLDERS with almost no content:\n"
                            f"{', '.join(stub_files)}\n\n"
                            f"You MUST rewrite them with COMPLETE, FULLY FUNCTIONAL code.\n\n"
                            f"Original task:\n{prompt}"
                        )
                        result = await agent.run(prompt=retry_prompt, system=system)
                        duration = time.time() - task_start
                        tc += result.tool_calls_made
                        fc = max(fc, len(result.artifacts) if result.artifacts else 0)

                task_cost = estimate_cost(mc.model_id, result.tokens_in, result.tokens_out)
                total_cost += task_cost
                total_turns += result.turns
                total_tc += tc
                total_tokens_in += result.tokens_in
                total_tokens_out += result.tokens_out
                total_retries += retries

                status = "fail" if result.error else "pass"
                if result.error:
                    store.update(task.id, status="failed", artifacts=result.artifacts)
                else:
                    store.update(task.id, status="completed", artifacts=result.artifacts)

                task_results.append({
                    "task_id": str(task.id),
                    "subject": task.subject,
                    "status": status,
                    "turns": result.turns,
                    "tool_calls": tc,
                    "files_created": fc,
                    "retries": retries,
                    "duration": round(duration, 1),
                    "cost": round(task_cost, 6),
                })

                if verbose:
                    icon = _c("g", "+") if status == "pass" else _c("r", "!")
                    files_detail = ", ".join(
                        f.rsplit("/", 1)[-1] for f in (result.artifacts or {}).keys()
                    ) or "none"
                    elapsed = time.time() - total_start
                    print(
                        f"      [{icon}] {duration:.0f}s  {result.turns}t  "
                        f"{tc}tc  files=[{files_detail}]  "
                        f"retries={retries}  elapsed={elapsed:.0f}s",
                        flush=True,
                    )

            except Exception as exc:
                duration = time.time() - task_start
                store.update(task.id, status="failed")
                task_results.append({
                    "task_id": str(task.id),
                    "subject": task.subject,
                    "status": "error",
                    "error": str(exc)[:200],
                    "duration": round(duration, 1),
                })
                if verbose:
                    elapsed = time.time() - total_start
                    print(
                        f"      [{_c('r', 'X')}] ERROR after {duration:.0f}s: "
                        f"{str(exc)[:100]}  elapsed={elapsed:.0f}s",
                        flush=True,
                    )

    total_duration = time.time() - total_start
    tasks_passed = sum(1 for t in task_results if t["status"] == "pass")

    # ── Verification checks ──────────────────────────────────────────────
    checks: list[CheckResult] = []

    # --- Dimension 1: Task Completion (30%) ---
    expected_files = scenario["expected_files"]

    # 1a. File existence (5 checks, weight 2 each)
    for ef in expected_files:
        p = project_dir / ef
        exists = p.exists()
        size = p.stat().st_size if exists else 0
        checks.append(CheckResult(
            name=f"File exists: {ef}",
            dimension="task_completion",
            passed=exists,
            detail=f"{size} bytes" if exists else "MISSING",
            weight=2.0,
        ))

    # 1b. File size thresholds (not stubs)
    _all_min_sizes = {"models.py": 300, "api.py": 300, "main.py": 300,
                      "static/index.html": 500, "static/app.js": 400, "static/style.css": 150,
                      "config.py": 30, "auth.py": 200, "events.py": 150}
    min_sizes = {ef: _all_min_sizes.get(ef, 200) for ef in expected_files}
    for ef, min_sz in min_sizes.items():
        p = project_dir / ef
        size = p.stat().st_size if p.exists() else 0
        checks.append(CheckResult(
            name=f"Not stub: {ef} >= {min_sz}B",
            dimension="task_completion",
            passed=size >= min_sz,
            detail=f"{size} bytes",
            weight=1.0,
        ))

    # 1c. All tasks passed
    total_tasks = len(tasks_data)
    checks.append(CheckResult(
        name=f"All {total_tasks} tasks passed",
        dimension="task_completion",
        passed=tasks_passed == total_tasks,
        detail=f"{tasks_passed}/{total_tasks}",
        weight=3.0,
    ))

    # --- Dimension 2: Code Quality (25%) ---

    # 2a. Python syntax check
    import py_compile
    py_files = [f for f in expected_files if f.endswith(".py")]
    for pyf in py_files:
        p = project_dir / pyf
        if p.exists():
            try:
                py_compile.compile(str(p), doraise=True)
                checks.append(CheckResult(
                    name=f"Syntax OK: {pyf}", dimension="code_quality",
                    passed=True, weight=2.0,
                ))
            except py_compile.PyCompileError as e:
                checks.append(CheckResult(
                    name=f"Syntax OK: {pyf}", dimension="code_quality",
                    passed=False, detail=str(e)[:100], weight=2.0,
                ))
        else:
            checks.append(CheckResult(
                name=f"Syntax OK: {pyf}", dimension="code_quality",
                passed=False, detail="file missing", weight=2.0,
            ))

    # 2b. models.py uses sqlite3 (not SQLAlchemy)
    models_path = project_dir / "models.py"
    if models_path.exists():
        models_src = models_path.read_text(encoding="utf-8", errors="replace")
        uses_sqlite3 = "sqlite3" in models_src
        uses_sqlalchemy = bool(re.search(r'^\s*(?:from|import)\s+.*sqlalchemy', models_src, re.MULTILINE | re.IGNORECASE))
        checks.append(CheckResult(
            name="models.py uses sqlite3", dimension="code_quality",
            passed=uses_sqlite3, weight=2.0,
        ))
        checks.append(CheckResult(
            name="models.py avoids SQLAlchemy", dimension="code_quality",
            passed=not uses_sqlalchemy, detail="imports SQLAlchemy" if uses_sqlalchemy else "",
            weight=2.0,
        ))
        # Functions (not classes) pattern
        iface = extract_module_interface(models_path)
        has_classes = bool(iface.get("classes"))
        has_functions = bool(iface.get("functions"))
        checks.append(CheckResult(
            name="models.py exports functions (not classes)", dimension="code_quality",
            passed=has_functions and not has_classes,
            detail=f"{len(iface.get('functions', {}))} funcs, {len(iface.get('classes', {}))} classes",
            weight=1.5,
        ))
    else:
        for name in ["models.py uses sqlite3", "models.py avoids SQLAlchemy", "models.py exports functions"]:
            checks.append(CheckResult(name=name, dimension="code_quality", passed=False, detail="missing", weight=2.0))

    # 2c. HTML structure
    html_path = project_dir / "static" / "index.html"
    if html_path.exists():
        html_raw = html_path.read_text(encoding="utf-8", errors="replace")
        html = html_raw.lower()
        # Core checks (all scenarios)
        checks.append(CheckResult(name="HTML has <form>", dimension="code_quality",
                                  passed="<form" in html, weight=1.0))
        checks.append(CheckResult(name="HTML links app.js", dimension="code_quality",
                                  passed="app.js" in html_raw, weight=1.0))
        # Scenario-specific checks
        if scenario_key == "expense-tracker":
            checks.append(CheckResult(name="HTML has <table>", dimension="code_quality",
                                      passed="<table" in html, weight=1.0))
            checks.append(CheckResult(name="HTML links Chart.js", dimension="code_quality",
                                      passed="chart.js" in html or "chartjs" in html, weight=1.0))
            checks.append(CheckResult(name="HTML links style.css", dimension="code_quality",
                                      passed="style.css" in html_raw, weight=1.0))
        elif scenario_key in ("kanban-board", "realtime-kanban"):
            checks.append(CheckResult(name="HTML links style.css", dimension="code_quality",
                                      passed="style.css" in html_raw, weight=1.0))
            # Kanban columns
            has_columns = ("todo" in html and "in.progress" in re.sub(r'[-_]', '.', html)
                           and "done" in html)
            checks.append(CheckResult(name="HTML has kanban columns (todo/in_progress/done)",
                                      dimension="code_quality", passed=has_columns, weight=1.5))
            # Auth forms
            has_auth_ui = ("login" in html or "register" in html or "sign" in html)
            checks.append(CheckResult(name="HTML has auth UI (login/register)",
                                      dimension="code_quality", passed=has_auth_ui, weight=1.0))
            if scenario_key == "realtime-kanban":
                # SSE / EventSource reference
                has_sse_ref = ("eventsource" in html or "event-stream" in html
                               or "sse" in html or "/api/events" in html)
                checks.append(CheckResult(name="HTML references SSE/EventSource",
                                          dimension="code_quality", passed=has_sse_ref, weight=1.0))
                # File upload
                has_upload = ('type="file"' in html or "type='file'" in html
                              or "file" in html)
                checks.append(CheckResult(name="HTML has file upload element",
                                          dimension="code_quality", passed=has_upload, weight=1.0))
        else:
            # Todo-app and others: check for list/checkbox UI
            checks.append(CheckResult(name="HTML has list/checkbox UI", dimension="code_quality",
                                      passed="<li" in html or "<ul" in html or "checkbox" in html or "<input" in html,
                                      weight=1.0))
    else:
        for label in ["HTML has <form>", "HTML links app.js"]:
            checks.append(CheckResult(name=label, dimension="code_quality", passed=False, detail="missing", weight=1.0))

    # 2d. JS quality
    js_path = project_dir / "static" / "app.js"
    if js_path.exists():
        js_src = js_path.read_text(encoding="utf-8", errors="replace")
        checks.append(CheckResult(
            name="JS uses fetch()", dimension="code_quality",
            passed="fetch(" in js_src, weight=1.0,
        ))
        checks.append(CheckResult(
            name="JS calls /api/ endpoints", dimension="code_quality",
            passed="/api/" in js_src or "'/api'" in js_src or '"/api"' in js_src,
            weight=1.0,
        ))
        checks.append(CheckResult(
            name="JS has DOM manipulation", dimension="code_quality",
            passed="document." in js_src, weight=1.0,
        ))
    else:
        for label in ["JS uses fetch()", "JS calls /api/ endpoints", "JS has DOM manipulation"]:
            checks.append(CheckResult(name=label, dimension="code_quality", passed=False, detail="missing", weight=1.0))

    # 2e. Kanban-specific module quality checks
    if scenario_key in ("kanban-board", "realtime-kanban"):
        # config.py quality
        config_path = project_dir / "config.py"
        if config_path.exists():
            config_src = config_path.read_text(encoding="utf-8", errors="replace")
            has_secret = "SECRET_KEY" in config_src
            has_db_path = "DB_PATH" in config_src
            checks.append(CheckResult(
                name="config.py has SECRET_KEY and DB_PATH", dimension="code_quality",
                passed=has_secret and has_db_path,
                detail=f"SECRET_KEY={'yes' if has_secret else 'no'}, DB_PATH={'yes' if has_db_path else 'no'}",
                weight=1.5,
            ))
        else:
            checks.append(CheckResult(name="config.py exists", dimension="code_quality",
                                      passed=False, detail="missing", weight=1.5))

        # auth.py quality
        auth_path = project_dir / "auth.py"
        if auth_path.exists():
            auth_src = auth_path.read_text(encoding="utf-8", errors="replace")
            uses_hashlib = "hashlib" in auth_src
            uses_hmac = "hmac" in auth_src
            no_jwt = "jwt" not in auth_src.lower() or "hmac" in auth_src  # allow if hmac present
            checks.append(CheckResult(
                name="auth.py uses hashlib+hmac (not jwt/bcrypt)", dimension="code_quality",
                passed=uses_hashlib and uses_hmac,
                detail=f"hashlib={'yes' if uses_hashlib else 'no'}, hmac={'yes' if uses_hmac else 'no'}",
                weight=2.0,
            ))
        else:
            checks.append(CheckResult(name="auth.py exists", dimension="code_quality",
                                      passed=False, detail="missing", weight=2.0))

        # JS SSE + upload checks for realtime
        if scenario_key == "realtime-kanban":
            js_path_rt = project_dir / "static" / "app.js"
            if js_path_rt.exists():
                js_src_rt = js_path_rt.read_text(encoding="utf-8", errors="replace")
                checks.append(CheckResult(
                    name="JS uses EventSource for SSE", dimension="code_quality",
                    passed="EventSource" in js_src_rt or "event-stream" in js_src_rt,
                    weight=1.5,
                ))
                checks.append(CheckResult(
                    name="JS uses FormData for uploads", dimension="code_quality",
                    passed="FormData" in js_src_rt, weight=1.0,
                ))

            # events.py quality
            events_path = project_dir / "events.py"
            if events_path.exists():
                events_src = events_path.read_text(encoding="utf-8", errors="replace")
                events_iface = extract_module_interface(events_path)
                has_broadcaster = "EventBroadcaster" in events_iface.get("classes", {})
                has_queue = "queue" in events_src.lower() or "Queue" in events_src
                checks.append(CheckResult(
                    name="events.py has EventBroadcaster class", dimension="code_quality",
                    passed=has_broadcaster,
                    detail="class found" if has_broadcaster else "missing",
                    weight=2.0,
                ))
                checks.append(CheckResult(
                    name="events.py uses queue for fan-out", dimension="code_quality",
                    passed=has_queue, weight=1.0,
                ))
            else:
                checks.append(CheckResult(name="events.py exists", dimension="code_quality",
                                          passed=False, detail="missing", weight=2.0))

    # --- Dimension 3: Interface Fidelity (20%) ---

    # 3a. Import/export compatibility
    interface_issues = []
    # Determine the API/server file — api.py for expense-tracker, main.py for todo-app
    api_path = project_dir / "api.py"
    if not api_path.exists():
        api_path = project_dir / "main.py"
    if models_path.exists() and api_path.exists():
        models_iface = extract_module_interface(models_path)
        if "error" not in models_iface:
            models_exports = set()
            models_exports.update(models_iface["functions"].keys())
            models_exports.update(models_iface["classes"].keys())
            models_exports.update(models_iface["assignments"])

            api_imports = extract_imports(api_path)
            mismatches = []
            for imp in api_imports:
                if imp.get("from") == "models":
                    for name in imp["names"]:
                        if name not in models_exports:
                            mismatches.append(name)
                            interface_issues.append(
                                f"api.py imports '{name}' but models.py doesn't export it"
                            )

            checks.append(CheckResult(
                name="Import/export names match", dimension="interface_fidelity",
                passed=len(mismatches) == 0,
                detail=f"{len(mismatches)} mismatches" if mismatches else "all match",
                weight=4.0,
            ))

            # ORM hallucination check
            api_src = api_path.read_text(encoding="utf-8", errors="replace")
            orm_patterns = [
                (r'\.query\b', ".query()"),
                (r'\.to_dict\(\)', ".to_dict()"),
                (r'db\.session', "db.session"),
            ]
            for pattern, label in orm_patterns:
                found = bool(re.search(pattern, api_src))
                if found:
                    interface_issues.append(f"ORM hallucination: {label}")
                checks.append(CheckResult(
                    name=f"No ORM hallucination: {label}", dimension="interface_fidelity",
                    passed=not found, weight=2.0,
                ))
        else:
            checks.append(CheckResult(
                name="Interface analysis", dimension="interface_fidelity",
                passed=False, detail=models_iface.get("error", "unknown"), weight=4.0,
            ))
    else:
        checks.append(CheckResult(
            name="Import/export analysis", dimension="interface_fidelity",
            passed=False, detail="files missing", weight=4.0,
        ))

    # 3a-bis. auth→api interface check (kanban scenarios have double boundary)
    if scenario_key in ("kanban-board", "realtime-kanban"):
        auth_path = project_dir / "auth.py"
        if auth_path.exists() and api_path.exists():
            auth_iface = extract_module_interface(auth_path)
            if "error" not in auth_iface:
                auth_exports = set()
                auth_exports.update(auth_iface["functions"].keys())
                auth_exports.update(auth_iface["assignments"])

                api_imports = extract_imports(api_path)
                auth_mismatches = []
                for imp in api_imports:
                    if imp.get("from") == "auth":
                        for name in imp["names"]:
                            if name not in auth_exports:
                                auth_mismatches.append(name)
                                interface_issues.append(
                                    f"api.py imports '{name}' but auth.py doesn't export it"
                                )
                checks.append(CheckResult(
                    name="auth→api import/export match", dimension="interface_fidelity",
                    passed=len(auth_mismatches) == 0,
                    detail=f"{len(auth_mismatches)} mismatches" if auth_mismatches else "all match",
                    weight=3.0,
                ))

                # Auth function signatures
                required_auth = ["hash_password", "verify_password", "generate_token", "validate_token"]
                auth_present = set(auth_iface.get("functions", {}).keys())
                auth_missing = [f for f in required_auth if f not in auth_present]
                checks.append(CheckResult(
                    name="auth.py has required functions", dimension="interface_fidelity",
                    passed=len(auth_missing) == 0,
                    detail=f"missing: {auth_missing}" if auth_missing else f"all {len(required_auth)} present",
                    weight=2.5,
                ))
        else:
            checks.append(CheckResult(
                name="auth→api interface analysis", dimension="interface_fidelity",
                passed=False, detail="auth.py or api.py missing", weight=3.0,
            ))

    # 3a-ter. events→api interface check (realtime kanban has triple boundary)
    if scenario_key == "realtime-kanban":
        events_path = project_dir / "events.py"
        if events_path.exists() and api_path.exists():
            events_iface = extract_module_interface(events_path)
            if "error" not in events_iface:
                events_exports = set()
                events_exports.update(events_iface["classes"].keys())
                events_exports.update(events_iface["assignments"])

                api_imports = extract_imports(api_path)
                events_mismatches = []
                for imp in api_imports:
                    if imp.get("from") == "events":
                        for name in imp["names"]:
                            if name not in events_exports:
                                events_mismatches.append(name)
                                interface_issues.append(
                                    f"api.py imports '{name}' but events.py doesn't export it"
                                )
                checks.append(CheckResult(
                    name="events→api import/export match", dimension="interface_fidelity",
                    passed=len(events_mismatches) == 0,
                    detail=f"{len(events_mismatches)} mismatches" if events_mismatches else "all match",
                    weight=2.5,
                ))
        else:
            checks.append(CheckResult(
                name="events→api interface analysis", dimension="interface_fidelity",
                passed=False, detail="events.py or api.py missing", weight=2.5,
            ))

    # 3b. Required function signatures in models.py
    if models_path.exists():
        iface = extract_module_interface(models_path)
        if scenario_key == "todo-app":
            required_funcs = ["init_db", "create_todo", "get_todos", "update_todo", "delete_todo"]
        elif scenario_key == "kanban-board":
            required_funcs = [
                "init_db", "create_user", "get_user_by_username",
                "create_project", "get_projects", "get_project_tasks",
                "create_task", "update_task", "delete_task", "get_project_stats",
            ]
        elif scenario_key == "realtime-kanban":
            required_funcs = [
                "init_db", "create_user", "get_user_by_username",
                "create_project", "get_projects", "get_project_tasks",
                "create_task", "update_task", "delete_task", "get_project_stats",
                "create_attachment", "get_task_attachments", "delete_attachment",
                "log_activity", "get_activity",
            ]
        else:
            required_funcs = ["init_db", "create_category", "get_categories",
                              "create_expense", "get_expenses", "delete_expense", "get_monthly_summary"]
        present = set(iface.get("functions", {}).keys())
        missing = [f for f in required_funcs if f not in present]
        checks.append(CheckResult(
            name="Required functions present", dimension="interface_fidelity",
            passed=len(missing) == 0,
            detail=f"missing: {missing}" if missing else f"all {len(required_funcs)} present",
            weight=3.0,
        ))

    # --- Dimension 4: Runtime Viability (15%) ---

    # 4a. API route strings present
    if scenario_key == "todo-app":
        expected_routes = ["/api/todos"]
    elif scenario_key == "kanban-board":
        expected_routes = ["/api/auth", "/api/projects", "/api/tasks", "/api/projects/"]
    elif scenario_key == "realtime-kanban":
        expected_routes = ["/api/auth", "/api/projects", "/api/tasks",
                           "/api/events", "/api/activity", "/api/attachments"]
    else:
        expected_routes = ["/api/categories", "/api/expenses", "/api/summary"]

    if api_path.exists():
        api_src = api_path.read_text(encoding="utf-8", errors="replace")
        for route in expected_routes:
            checks.append(CheckResult(
                name=f"Route defined: {route}", dimension="runtime_viability",
                passed=route in api_src, weight=1.5,
            ))
    else:
        for route in expected_routes:
            checks.append(CheckResult(name=f"Route: {route}", dimension="runtime_viability",
                                      passed=False, detail="server file missing", weight=1.5))

    # 4b. Server start test
    server_ok = False
    if models_path.exists() and api_path.exists():
        try:
            from forge_preview import detect_stack
            si = detect_stack(project_dir)
            checks.append(CheckResult(
                name="Stack detected", dimension="runtime_viability",
                passed=si.kind != "unknown", detail=f"{si.kind}: {si.entry}", weight=1.0,
            ))

            if si.kind != "unknown":
                from forge_preview import PreviewManager
                preview_mgr = PreviewManager(project_dir)
                try:
                    port = preview_mgr._start_server_only(stack_info=si)
                    started = port > 0
                    checks.append(CheckResult(
                        name="Server starts", dimension="runtime_viability",
                        passed=started, detail=f"port={port}" if started else "failed", weight=2.0,
                    ))
                    server_ok = started

                    if started:
                        import urllib.request
                        # Give server a moment to fully initialize (DB, routes)
                        time.sleep(1.5)
                        # GET test (scenario-specific endpoint)
                        if scenario_key == "todo-app":
                            get_url = f"http://localhost:{port}/api/todos"
                            get_label = "GET /api/todos returns 200"
                        elif scenario_key in ("kanban-board", "realtime-kanban"):
                            get_url = f"http://localhost:{port}/api/projects"
                            get_label = "GET /api/projects returns 200"
                        else:
                            get_url = f"http://localhost:{port}/api/categories"
                            get_label = "GET /api/categories returns 200"
                        try:
                            resp = urllib.request.urlopen(get_url, timeout=5)
                            checks.append(CheckResult(
                                name=get_label, dimension="runtime_viability",
                                passed=resp.getcode() == 200, detail=f"status={resp.getcode()}", weight=2.0,
                            ))
                        except Exception as e:
                            checks.append(CheckResult(
                                name=get_label, dimension="runtime_viability",
                                passed=False, detail=str(e)[:80], weight=2.0,
                            ))
                        # POST test (scenario-specific endpoint + payload)
                        if scenario_key == "todo-app":
                            post_url = f"http://localhost:{port}/api/todos"
                            post_data = json.dumps({"title": "Test todo", "priority": "high"}).encode()
                            post_label = "POST /api/todos works"
                        elif scenario_key in ("kanban-board", "realtime-kanban"):
                            post_url = f"http://localhost:{port}/api/auth/register"
                            post_data = json.dumps({"username": "benchtest", "password": "test1234"}).encode()
                            post_label = "POST /api/auth/register works"
                        else:
                            post_url = f"http://localhost:{port}/api/expenses"
                            post_data = json.dumps({"amount": 42.50, "description": "Test", "category_id": 1, "date": "2026-03-11"}).encode()
                            post_label = "POST /api/expenses works"
                        try:
                            req = urllib.request.Request(
                                post_url, data=post_data,
                                headers={"Content-Type": "application/json"}, method="POST",
                            )
                            resp = urllib.request.urlopen(req, timeout=5)
                            checks.append(CheckResult(
                                name=post_label, dimension="runtime_viability",
                                passed=resp.getcode() in (200, 201), detail=f"status={resp.getcode()}", weight=2.0,
                            ))
                        except Exception as e:
                            checks.append(CheckResult(
                                name=post_label, dimension="runtime_viability",
                                passed=False, detail=str(e)[:80], weight=2.0,
                            ))
                        # SSE endpoint test (realtime-kanban only)
                        if scenario_key == "realtime-kanban":
                            try:
                                sse_url = f"http://localhost:{port}/api/events"
                                sse_req = urllib.request.Request(sse_url)
                                sse_resp = urllib.request.urlopen(sse_req, timeout=3)
                                content_type = sse_resp.headers.get("Content-Type", "")
                                is_sse = "text/event-stream" in content_type
                                checks.append(CheckResult(
                                    name="SSE endpoint returns event-stream", dimension="runtime_viability",
                                    passed=is_sse,
                                    detail=f"Content-Type: {content_type[:50]}",
                                    weight=2.0,
                                ))
                            except Exception as e:
                                checks.append(CheckResult(
                                    name="SSE endpoint returns event-stream", dimension="runtime_viability",
                                    passed=False, detail=str(e)[:80], weight=2.0,
                                ))
                finally:
                    preview_mgr.stop()
        except Exception as e:
            checks.append(CheckResult(
                name="Runtime test", dimension="runtime_viability",
                passed=False, detail=str(e)[:100], weight=2.0,
            ))

    # --- Dimension 5: Efficiency (10%) ---

    # 5a. Retries (0 is ideal)
    checks.append(CheckResult(
        name="Zero retries needed", dimension="efficiency",
        passed=total_retries == 0,
        detail=f"{total_retries} retries",
        weight=2.0,
    ))

    # 5b. Total turns (generous — includes self-correction verification turns)
    turn_limit = 45
    checks.append(CheckResult(
        name=f"Turns <= {turn_limit}", dimension="efficiency",
        passed=total_turns <= turn_limit,
        detail=f"{total_turns} turns",
        weight=1.5,
    ))

    # 5c. Duration (scale by model — Premier has high per-call latency)
    dur_limit = 1200 if "premier" in model_alias else 360
    checks.append(CheckResult(
        name=f"Duration <= {dur_limit}s", dimension="efficiency",
        passed=total_duration <= dur_limit,
        detail=f"{total_duration:.0f}s",
        weight=1.0,
    ))

    # 5d. Cost efficiency
    cap = get_capability(model_alias)
    cost_threshold = 0.05 if cap and cap.cost_per_1k_input < 0.001 else 0.50
    checks.append(CheckResult(
        name=f"Cost <= {format_cost(cost_threshold)}", dimension="efficiency",
        passed=total_cost <= cost_threshold,
        detail=format_cost(total_cost),
        weight=1.5,
    ))

    return ModelBenchmark(
        model_alias=model_alias,
        model_id=model_id,
        timestamp=timestamp,
        duration_secs=total_duration,
        tasks_passed=tasks_passed,
        tasks_total=total_tasks,
        retries=total_retries,
        total_turns=total_turns,
        total_tool_calls=total_tc,
        total_tokens_in=total_tokens_in,
        total_tokens_out=total_tokens_out,
        total_cost=total_cost,
        checks=checks,
        task_results=task_results,
        interface_issues=interface_issues,
        server_ok=server_ok,
    )


# ── Report rendering ─────────────────────────────────────────────────────────

def print_model_scorecard(bm: ModelBenchmark) -> None:
    """Print detailed scorecard for one model."""
    dims = bm.dimension_scores
    overall = bm.overall_score

    print(f"\n  {_c('b', bm.model_alias.upper())}  {_c('d', bm.model_id)}")
    print(f"  Grade: {grade_color(bm.grade)}  Overall: {score_bar(overall)}")
    print()

    # Dimension breakdown
    dim_labels = {
        "task_completion":    "Task Completion",
        "code_quality":       "Code Quality",
        "interface_fidelity": "Interface Fidelity",
        "runtime_viability":  "Runtime Viability",
        "efficiency":         "Efficiency",
    }
    for dim, label in dim_labels.items():
        score = dims.get(dim, 0)
        weight = DIMENSION_WEIGHTS[dim]
        g = score_to_grade(score)
        print(f"    {label:22s} {score_bar(score, 15)} {grade_color(g)} ({weight:.0%} weight)")

    # Tasks
    print(f"\n  {_c('b', 'Tasks')}: {bm.tasks_passed}/{bm.tasks_total}  "
          f"Duration: {bm.duration_secs:.0f}s  Cost: {format_cost(bm.total_cost)}  "
          f"Turns: {bm.total_turns}  Retries: {bm.retries}")
    for tr in bm.task_results:
        icon = _c("g", "+") if tr["status"] == "pass" else _c("r", "!")
        dur = tr.get("duration", 0)
        turns = tr.get("turns", "?")
        print(f"    [{icon}] {tr['subject'][:40]:40s} {dur:5.1f}s  {turns}t")

    # Failed checks
    failed = [c for c in bm.checks if not c.passed]
    if failed:
        print(f"\n  {_c('r', f'{len(failed)} failed checks')}:")
        for c in failed:
            detail = f" — {c.detail}" if c.detail else ""
            print(f"    {_c('r', '✗')} {c.name}{_c('d', detail)}")

    # Interface issues
    if bm.interface_issues:
        print(f"\n  {_c('r', 'Interface issues')}:")
        for issue in bm.interface_issues:
            print(f"    {_c('r', '✗')} {issue}")


def print_comparison_table(results: list[ModelBenchmark]) -> None:
    """Print side-by-side comparison of all models."""

    section("COMPARATIVE SCORECARD")

    # Header
    cols = ["Metric"] + [r.model_alias for r in results]
    widths = [24] + [16] * len(results)

    def row(label: str, values: list[str]) -> str:
        parts = [f"  {label:{widths[0]}s}"]
        for i, v in enumerate(values):
            parts.append(f"{v:>{widths[i + 1]}s}")
        return "".join(parts)

    print(row("", [_c("b", r.model_alias) for r in results]))
    print(f"  {'─' * (widths[0] + sum(widths[1:]))}")

    # Overall
    print(row("Grade", [grade_color(r.grade) for r in results]))
    print(row("Overall Score", [f"{r.overall_score:.0f}%" for r in results]))
    print()

    # Dimensions
    dim_labels = {
        "task_completion": "Task Completion",
        "code_quality": "Code Quality",
        "interface_fidelity": "Interface Fidelity",
        "runtime_viability": "Runtime",
        "efficiency": "Efficiency",
    }
    for dim, label in dim_labels.items():
        vals = []
        for r in results:
            s = r.dimension_scores.get(dim, 0)
            g = score_to_grade(s)
            vals.append(f"{s:.0f}% ({grade_color(g)})")
        print(row(label, vals))

    print()
    print(row("Tasks", [f"{r.tasks_passed}/{r.tasks_total}" for r in results]))
    print(row("Duration", [f"{r.duration_secs:.0f}s" for r in results]))
    print(row("Cost", [format_cost(r.total_cost) for r in results]))
    print(row("Turns", [str(r.total_turns) for r in results]))
    print(row("Retries", [str(r.retries) for r in results]))
    print(row("Tool Calls", [str(r.total_tool_calls) for r in results]))
    print(row("Tokens In", [f"{r.total_tokens_in:,}" for r in results]))
    print(row("Tokens Out", [f"{r.total_tokens_out:,}" for r in results]))
    print(row("Server OK", [_c("g", "YES") if r.server_ok else _c("r", "NO") for r in results]))
    print(row("Interface Issues", [
        _c("g", "0") if not r.interface_issues else _c("r", str(len(r.interface_issues)))
        for r in results
    ]))

    # Winner
    print()
    best = max(results, key=lambda r: r.overall_score)
    print(f"  {_c('b', 'Best overall')}: {_c('m', best.model_alias)} "
          f"({best.overall_score:.0f}% — {grade_color(best.grade)})")

    # Value pick (best score per dollar)
    for r in results:
        if r.total_cost > 0:
            r._score_per_dollar = r.overall_score / r.total_cost
        else:
            r._score_per_dollar = r.overall_score * 1000  # free = infinite value
    value_pick = max(results, key=lambda r: r._score_per_dollar)
    print(f"  {_c('b', 'Best value')}:   {_c('c', value_pick.model_alias)} "
          f"({value_pick.overall_score:.0f}% for {format_cost(value_pick.total_cost)})")


def print_comparison_with_previous(current: list[ModelBenchmark], previous: dict) -> None:
    """Show delta from a previous run."""
    prev_by_model = {r["model_alias"]: r for r in previous.get("results", [])}

    section("DELTA FROM PREVIOUS RUN")
    print(f"  Previous: {previous.get('timestamp', '?')}")
    print()

    for r in current:
        prev = prev_by_model.get(r.model_alias)
        if not prev:
            print(f"  {r.model_alias}: (no previous data)")
            continue

        delta_score = r.overall_score - prev.get("overall_score", 0)
        delta_dur = r.duration_secs - prev.get("duration_secs", 0)
        arrow = _c("g", "▲") if delta_score > 0 else (_c("r", "▼") if delta_score < 0 else "=")
        prev_grade = prev.get("grade", "?")
        print(
            f"  {r.model_alias:16s}  "
            f"{prev_grade} → {grade_color(r.grade)}  "
            f"{arrow} {abs(delta_score):+.1f}%  "
            f"duration: {delta_dur:+.0f}s"
        )


def show_saved_results(path: Path) -> None:
    """Display results from a saved JSON file."""
    data = json.loads(path.read_text())
    results = data.get("results", [])
    print(f"\n  Loaded {len(results)} results from {path.name}")
    print(f"  Timestamp: {data.get('timestamp', '?')}")

    for r in results:
        grade = r.get("grade", "?")
        score = r.get("overall_score", 0)
        alias = r.get("model_alias", "?")
        dur = r.get("duration_secs", 0)
        cost = r.get("total_cost", 0)
        print(f"\n  {_c('b', alias):20s}  {grade_color(grade)}  {score:.0f}%  "
              f"{dur:.0f}s  {format_cost(cost)}")

        dims = r.get("dimension_scores", {})
        for dim, score in dims.items():
            label = dim.replace("_", " ").title()
            print(f"    {label:22s} {score:.0f}%")


def print_regressions(alerts: list[RegressionAlert]) -> None:
    """Print regression alerts."""
    if not alerts:
        print(f"\n  {_c('g', 'No regressions detected.')}")
        return
    section("REGRESSION ALERTS")
    for a in alerts:
        icon = _c("r", "!!!") if a.severity == "critical" else _c("y", " ! ")
        print(f"  {icon} {a.model:16s} {a.dimension:20s} {a.old_value} -> {a.new_value}")


def print_check_diffs(diffs: list[CheckDiff]) -> None:
    """Print per-check state changes."""
    if not diffs:
        print(f"\n  {_c('d', 'No check-level changes.')}")
        return
    section("CHECK-LEVEL CHANGES")
    regressions = [d for d in diffs if d.old_state and not d.new_state]
    improvements = [d for d in diffs if not d.old_state and d.new_state]
    if regressions:
        print(f"  {_c('r', 'Regressions')}:")
        for d in regressions:
            print(f"    {_c('r', 'PASS->FAIL')} [{d.model}] {d.check_name}")
    if improvements:
        print(f"  {_c('g', 'Improvements')}:")
        for d in improvements:
            print(f"    {_c('g', 'FAIL->PASS')} [{d.model}] {d.check_name}")


def print_optimization_hints(hints: list[OptimizationHint]) -> None:
    """Print actionable optimization suggestions."""
    if not hints:
        return
    section("OPTIMIZATION HINTS")
    for h in hints:
        dim_label = h.dimension.replace("_", " ").title()
        print(f"  {_c('y', dim_label)} ({h.score:.0f}%)")
        print(f"    {h.suggestion}")
        if h.files:
            print(f"    Files: {_c('c', ', '.join(h.files))}")
        print()


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Nova Model Benchmark Suite")
    parser.add_argument("--model", type=str, help="Run single model (e.g. nova-lite)")
    parser.add_argument("--all", action="store_true", help="Run all 3 Nova models")
    parser.add_argument("--report", action="store_true", help="Save results to benchmarks/")
    parser.add_argument("--compare", type=str, help="Path to previous results JSON for delta comparison")
    parser.add_argument("--show", type=str, help="Display results from a saved JSON file")
    parser.add_argument("--history", action="store_true", help="Show grade/score history for all runs")
    parser.add_argument("--history-model", type=str, help="Filter history to one model")
    parser.add_argument("--history-limit", type=int, default=20, help="Limit history rows")
    parser.add_argument("--diff-checks", type=str, metavar="PATH", help="Show per-check changes vs a previous run")
    parser.add_argument("--no-save", action="store_true", help="Skip saving results")
    parser.add_argument("--trigger", type=str, default="", help="Why this run was triggered")
    parser.add_argument("--name", type=str, default="", help="Human-readable run name")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show per-task progress")
    parser.add_argument("--scenario", type=str, default=DEFAULT_SCENARIO,
                        choices=list(BENCHMARK_SCENARIOS.keys()),
                        help=f"Benchmark scenario (default: {DEFAULT_SCENARIO})")
    parser.add_argument("--all-scenarios", action="store_true",
                        help="Run all benchmark scenarios sequentially")
    args = parser.parse_args()

    if args.show:
        show_saved_results(Path(args.show))
        return

    if args.history:
        print(format_history(BENCHMARKS_DIR, model_filter=args.history_model, limit=args.history_limit))
        return

    if args.diff_checks:
        store = BenchmarkStore(BENCHMARKS_DIR)
        current = store.load_latest()
        prev = store.load_run(Path(args.diff_checks))
        if not current or not prev:
            print(f"  {_c('r', 'Could not load runs for diff')}")
            return
        diffs = diff_checks(current.get('results', []), prev)
        print_check_diffs(diffs)
        return

    models_to_run = []
    if args.model:
        models_to_run = [args.model]
    elif args.all:
        models_to_run = NOVA_MODELS
    else:
        # Default: run all
        models_to_run = NOVA_MODELS

    scenario = BENCHMARK_SCENARIOS[args.scenario]
    scenario_name = scenario["name"]
    task_count = len(scenario["tasks"])

    print(f"\n{_c('m', '═' * 70)}")
    print(f"  {_c('b', 'Nova Forge Model Benchmark Suite')}")
    print(f"  Models: {', '.join(models_to_run)}")
    print(f"  Scenario: {scenario_name} ({task_count} tasks)")
    print(f"{_c('m', '═' * 70)}")

    results: list[ModelBenchmark] = []

    for model in models_to_run:
        section(f"Running: {model}")
        try:
            bm = await run_single_model(model, verbose=args.verbose, scenario_key=args.scenario)
            results.append(bm)
            print_model_scorecard(bm)
        except Exception as exc:
            print(f"  {_c('r', f'ERROR: {model} failed — {exc}')}")

    if len(results) > 1:
        print_comparison_table(results)

    # Compare with previous run
    if args.compare:
        prev_path = Path(args.compare)
        if prev_path.exists():
            previous = json.loads(prev_path.read_text())
            print_comparison_with_previous(results, previous)
        else:
            print(f"  {_c('r', f'Previous results not found: {args.compare}')}")

    # ── Post-run pipeline ─────────────────────────────────────────────
    store = BenchmarkStore(BENCHMARKS_DIR)
    result_dicts = [r.to_dict() for r in results]

    if not args.no_save:
        # Load previous BEFORE saving (so symlink still points to old run)
        previous = store.load_latest()

        # Collect metadata and save
        metadata = collect_metadata(spec_text=scenario["spec"])
        metadata.trigger = args.trigger
        metadata.models_run = [r.model_alias for r in results]
        run_path = store.save_run(result_dicts, metadata, run_name=args.name)
        print(f"\n  {_c('c', f'Results saved: {run_path}')}")

        # Auto-compare vs previous
        regressions = []
        if previous:
            regressions = detect_regressions(result_dicts, previous)
            print_regressions(regressions)

            diffs = diff_checks(result_dicts, previous)
            print_check_diffs(diffs)

        # Optimization hints (always)
        hints = generate_optimization_hints(result_dicts)
        print_optimization_hints(hints)

        # Changelog
        run_data = store.load_run(run_path)
        if run_data:
            append_changelog(BENCHMARKS_DIR, run_data, previous, regressions)

    print()


if __name__ == "__main__":
    asyncio.run(main())
