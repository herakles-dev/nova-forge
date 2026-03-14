"""Nova Forge Web Dashboard — serves forge.herakles.dev.

Provides:
  /              — Documentation guide (static HTML/CSS/JS from web/)
  /health        — Health check endpoint
  /api/info      — JSON with version, model support, formation count
  /api/docs/chat — Nova-powered Q&A about Nova Forge documentation
"""

from __future__ import annotations

import json
import os
import logging
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from config import MODEL_ALIASES, DEFAULT_MODELS

logger = logging.getLogger("forge.web")

_WEB_DIR = Path(__file__).parent / "web"
app = Flask(__name__, static_folder=str(_WEB_DIR), static_url_path="")
CORS(app)

_VERSION = "0.3.0"

# ── Documentation context for chat ──────────────────────────────────────────

_DOCS_CONTEXT = """\
# Nova Forge Documentation

## What is Nova Forge?
Nova Forge is an open-source AI agent orchestration framework powered by Amazon Nova.
It takes a natural language description of what you want to build and orchestrates
multiple AI agents to plan, build, test, and deploy it. Built for the Amazon Nova AI
Hackathon 2026. 16 sprints, ~25,600 lines of code, 39 Python modules, 1051 tests.

## Quick Start
1. Clone: `git clone https://github.com/Herakles-AI/nova-forge.git && cd nova-forge`
2. Install: `pip install -r requirements.txt`
3. Configure credentials (at least one provider):
   - AWS Bedrock: `export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1`
   - OpenRouter: `export OPENROUTER_API_KEY=...`
   - Anthropic: `export ANTHROPIC_API_KEY=...`
4. Launch: `python3 forge_cli.py`
5. Type what you want to build — Nova handles the rest.

## Interactive Shell Commands
- `/interview` — Start 3-phase deep planning interview (scope, stack, risk, formation, model)
- `/plan` — Generate task plan from spec.md
- `/build` — Execute all pending tasks with AI agents
- `/build --no-review` — Skip the gate review step
- `/build --no-verify` — Skip runtime verification
- `/status` — View current build progress and task states
- `/tasks` — List all tasks with status, wave, and dependencies
- `/preview` — Launch dev server with shareable Cloudflare Tunnel URL (3x retry with backoff)
- `/preview stop` — Stop the preview server
- `/deploy` — Production deployment (Docker + nginx + SSL)
- `/model <alias>` — Switch the active model (e.g., nova-lite, gemini-flash)
- `/models` — Compare all models — cost, context window, strengths
- `/formation <name>` — View or set agent formation
- `/autonomy <0-5>` — Set agent autonomy level (A0-A5)
- `/health` — System health dashboard (preview, model, project, disk)
- `/competition` — Hackathon submission readiness validator (8 checks)
- `/login` — Set up or change API provider credentials
- `/config` — View or modify all settings
- `/guide` — Interactive project wizard (skill-level adaptive)
- `/resume` — Resume a previous session
- `/new <name>` — Create a new project directory
- `/cd <path>` — Change project directory
- `/audit` — View JSONL audit trail
- `/clear` — Clear screen
- `/help` — Show help
- `/quit` — Exit

## CLI Commands (non-interactive)
- `forge plan "description" --model nova-lite` — Plan a project
- `forge build --model gemini-flash` — Execute the plan
- `forge deploy --domain app.example.com` — Deploy to production
- `forge preview` — Launch preview
- `forge status` — View status
- `forge models` — List models
- `forge chat` — Launch interactive shell

## Amazon Nova Models (primary — via AWS Bedrock)
- **nova-lite** (32K context) — Fast, cheap. Best for focused tasks. $0.00006/1K input.
  Benchmark: S tier (100%) on Expense Tracker, A tier (90%) on Kanban Board.
  Uses slim prompts and SLIM_TOOLS for optimal 32K performance.
- **nova-pro** (300K context) — All-rounder. Complex coding. $0.0008/1K input.
  Benchmark: S tier (99%) on Expense Tracker, A tier (90%) on Kanban Board.
  Focused prompts, 8K max_tokens.
- **nova-premier** (1M context) — Most powerful Nova. Deep reasoning. $0.002/1K input.
  Benchmark: S tier (100%) on Expense Tracker, A tier (89%) on Kanban Board.
  Focused prompts, 300s Bedrock timeout, pre-seeded context injection.

All 3 Nova models achieved S tier on the Expense Tracker benchmark (5 tasks, 5 files,
Flask + SQLite). On the harder Kanban Board scenario (7 tasks, 7 files, auth + 3 tables),
all 3 achieved A tier. Grade progression: C → S over sprints 13-14.

## Additional Models (4 more via OpenRouter/Anthropic)
- **gemini-flash** (1M context, OpenRouter) — Lightning fast, huge context. $0.0001/1K input.
- **gemini-pro** (1M context, OpenRouter) — Top-tier reasoning. $0.00125/1K input.
- **claude-sonnet** (200K context, Anthropic) — Excellent instruction-following. $0.003/1K input.
- **claude-haiku** (200K context, Anthropic) — Fast, affordable. $0.0008/1K input.

Switch with `/model <alias>`, e.g. `/model nova-lite`.

## 3-Tier Prompt System
Nova Forge uses model-appropriate system prompts:
- **Slim** (<=32K, ~600 chars) — For Nova Lite. Minimal tools, output coaching.
- **Focused** (<=1M, ~1,500 chars) — For Pro/Premier/Gemini. Standard tools.
- **Full** (>1M, ~5K chars) — Maximum detail with all context.

## Formations (10 pre-configured team layouts)
- **single-file** (1 role) — Small, focused file edits. 1-3 tasks.
- **lightweight-feature** (2 roles) — Single-layer features. Implementer + tester in parallel.
- **feature-impl** (4 roles, RECOMMENDED) — Adding features. Backend + frontend parallel, then integrator, then tester.
- **new-project** (3 roles) — Greenfield setup. Architect → implementers.
- **bug-investigation** (3 roles) — Unknown root cause. Three parallel investigators.
- **security-review** (3 roles) — Security audit. Scanner + modeler parallel, then fixer.
- **perf-optimization** (2 roles) — Profiling + optimization. Sequential deep work.
- **code-review** (3 roles) — PR review. Three parallel reviewers (security, performance, coverage). Read-only.
- **recovery** (3 roles) — Post-failure diagnosis. Investigator → fixer → validator.
- **all-hands-planning** (5 roles) — Spec review with 4 parallel reviewers then synthesizer.

Set with `/formation <name>`, e.g. `/formation feature-impl`.

## Autonomy System (A0-A5)
- **A0 Manual** — Ask for everything. Full human control.
- **A1 Guided** — Read files freely. Ask before writes.
- **A2 Supervised** (DEFAULT) — Read/write freely. Ask before risky commands.
- **A3 Trusted** — Handle most things independently. Block high-risk only.
- **A4 Autonomous** — Full autopilot. Requires explicit `/autonomy 4`.
- **A5 Unattended** — CI/background. Full audit logging.

Set with `/autonomy <0-5>`. Auto-escalation capped at A3.

## How the Pipeline Works
1. **Interview** — 3-phase deep planning: scope/context, technical decisions, risk/formation
2. **Plan & Decompose** — AI generates spec.md → tasks.json with dependencies (topological sort)
3. **Parallel Build** — Independent tasks run concurrently by wave (asyncio.gather + semaphores)
4. **Gate Review** — Adversarial read-only reviewer produces PASS/FAIL/CONDITIONAL
5. **Preview** — 14-stack auto-detection, Cloudflare Tunnel with 3x retry and health monitor
6. **Deploy** — Docker + nginx + SSL, one command

## Key Architecture & Innovations
- **3-Tier Prompts**: Slim (32K), Focused (1M), Full (>1M) — model-appropriate system prompts
- **Pre-Seeded Context**: Dependent tasks get upstream file content injected (saves 2-3 turns)
- **Circuit Breaker**: Per-tool failure tracking, auto-disable after 3 failures
- **Self-Correction**: Agents verify their own output after task completion (read-back check)
- **JSON Recovery**: `_recover_json()` handles malformed LLM output (trailing commas, truncation)
- **Context Compaction**: Budget-based (60% for 32K, 65% for 200K+), preserves tool pairs
- **30 turns/task**: Universal limit, no model-specific caps
- **14-stack Preview**: Auto-detects Flask, FastAPI, Node, React, etc.
- **Bedrock Timeout**: 300s read_timeout via botocore.Config (Premier needs ~100s/inference)

## Key Modules (39 files, ~25,600 LOC)
- `forge_cli.py` (3,604 LOC) — Interactive shell, deep planning interview, all /commands
- `forge_agent.py` (1,638 LOC) — Tool-use loop, 12 tools, AgentEvent, auto-verify
- `forge_hooks_impl.py` (1,057 LOC) — 12 hook implementations
- `forge_guards.py` (1,030 LOC) — RiskClassifier + PathSandbox + AutonomyManager (A0-A5)
- `forge_assistant.py` (1,014 LOC) — Smart assistant — skill detection, recommendations
- `prompt_builder.py` (917 LOC) — 3-tier prompt system + autonomy-aware
- `model_router.py` (902 LOC) — 3 provider adapters (Bedrock 300s timeout, OpenAI, Anthropic)
- `forge_pipeline.py` (848 LOC) — WaveExecutor + ArtifactManager + GateReviewer
- `forge_preview.py` (800 LOC) — PreviewManager — 14-stack detection + Cloudflare Tunnel
- `forge_orchestrator.py` (751 LOC) — Plan/build/deploy orchestration + JSON recovery
- 1051 tests across 48 test files, all passing

## Agent Tools (12 built-in)
read_file, write_file, append_file, edit_file, bash, glob_files, grep,
list_directory, search_replace_all, think, claim_file, check_context

## Benchmark Scenarios (4 difficulty levels)
- **expense-tracker** — Easy. 5 tasks, 5 files, Flask + SQLite. All Nova models score S.
- **todo-app** — Easy. 4 tasks, 1 table, FastAPI + SQLite.
- **kanban-board** — Hard. 7 tasks, 7 files, auth + 3 tables + status machine. All Nova models score A.
- **realtime-kanban** — Nightmare. 8 tasks, 5 tables, SSE, file uploads. Stress test.

## Live Demos
6 interactive demo apps at forge.herakles.dev/demos/:
- Expense Tracker (Nova Lite, S 100%) — add/delete expenses, bar charts
- Kanban Board (Nova Lite, A 90%) — click to advance tasks between columns
- Kanban Board (Nova Pro, A 90%) — same spec, different architecture
- Kanban Board (Nova Premier, A 89%) — 4 columns, subtask counts
- Todo App — check/uncheck, add tasks, filter by status
- Realtime Kanban — NIGHTMARE scenario mockup with SSE, activity log, 5 tables

## Providers
- **Amazon Bedrock** — AWS credentials — access to Nova Lite, Pro, Premier
- **OpenRouter** — API key — access to Gemini Flash, Pro
- **Anthropic** — API key — access to Claude Sonnet, Haiku

## Development Timeline (16 sprints in 5 days)
Sprint 5: 12 tools, parallel waves, gate review, autonomy
Sprint 7-8: Light model optimization, agent intelligence
Sprint 9: Assistant layer, A0-A5 autonomy, adaptive UX
Sprint 12: Deep planning interview (3-phase, 8 categories)
Sprint 13: JSON recovery, 3-tier prompts, Pro C→S, Premier C→A
Sprint 14: Premier S tier, pre-seeded context, Bedrock 300s timeout
Sprint 15: Preview resilience, circuit breaker, /health + /competition
Sprint 16: Self-correction, 2 new formations, demo recording
"""


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(_WEB_DIR), "index.html")


@app.route("/demos/")
def demos_index():
    """Serve the demos showcase index."""
    return send_from_directory(str(_WEB_DIR / "demos"), "index.html")


@app.route("/demos/<path:subpath>")
def demos(subpath):
    """Serve demo pages — static HTML showcases of Nova-built projects."""
    if subpath.endswith("/") or "." not in subpath.split("/")[-1]:
        subpath = subpath.rstrip("/") + "/index.html"
    return send_from_directory(str(_WEB_DIR / "demos"), subpath)


@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": _VERSION})


@app.route("/api/info")
def api_info():
    from formations import FORMATIONS
    return jsonify({
        "name": "Nova Forge",
        "version": _VERSION,
        "description": "Open-source agent orchestration framework",
        "models": {
            "aliases": MODEL_ALIASES,
            "defaults": DEFAULT_MODELS,
        },
        "formations": {
            name: {
                "roles": len(f.roles),
                "waves": len(f.wave_order),
                "gate_criteria_count": len(f.gate_criteria),
                "description": f.description,
            }
            for name, f in FORMATIONS.items()
        },
        "providers": ["bedrock", "openai", "anthropic"],
        "tools": [
            "read_file", "write_file", "append_file", "edit_file",
            "bash", "glob_files", "grep", "list_directory",
            "search_replace_all", "think", "claim_file", "check_context",
        ],
        "stats": {
            "tests": 1047,
            "test_files": 48,
            "modules": 39,
            "loc": 25600,
        },
    })


@app.route("/api/docs/chat", methods=["POST"])
def docs_chat():
    """Answer questions about Nova Forge using Amazon Nova."""
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    # Try Bedrock Nova first, fall back to OpenRouter, then Anthropic
    response_text = _call_llm(message)
    return jsonify({"response": response_text})


def _call_llm(user_message: str) -> str:
    """Call an LLM with docs context to answer the user's question."""
    system_prompt = (
        "You are Nova, the AI assistant behind Nova Forge — an open-source agent "
        "orchestration framework. Answer questions about Nova Forge based on the "
        "documentation provided. Be helpful, concise, and accurate. "
        "Use code blocks for commands. If you don't know something, say so honestly.\n\n"
        + _DOCS_CONTEXT
    )

    # Try Bedrock (Nova)
    if os.environ.get("AWS_ACCESS_KEY_ID"):
        try:
            return _call_bedrock(system_prompt, user_message)
        except Exception as e:
            logger.warning("Bedrock chat failed: %s", e)

    # Try OpenRouter (Gemini)
    if os.environ.get("OPENROUTER_API_KEY"):
        try:
            return _call_openrouter(system_prompt, user_message)
        except Exception as e:
            logger.warning("OpenRouter chat failed: %s", e)

    # Try Anthropic (Claude)
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            return _call_anthropic(system_prompt, user_message)
        except Exception as e:
            logger.warning("Anthropic chat failed: %s", e)

    return (
        "The chat service requires API credentials to be configured. "
        "Please set up at least one provider (AWS Bedrock, OpenRouter, or Anthropic) "
        "and restart the web server. In the meantime, try the CLI: `python3 forge_cli.py`"
    )


def _call_bedrock(system: str, message: str) -> str:
    """Call Amazon Nova via Bedrock Converse API."""
    import boto3

    client = boto3.client("bedrock-runtime", region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    response = client.converse(
        modelId="us.amazon.nova-lite-v1:0",
        system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": message}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.3},
    )
    return response["output"]["message"]["content"][0]["text"]


def _call_openrouter(system: str, message: str) -> str:
    """Call Gemini via OpenRouter."""
    import openai

    client = openai.OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
    )
    response = client.chat.completions.create(
        model="google/gemini-2.0-flash-001",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": message},
        ],
        max_tokens=1024,
        temperature=0.3,
    )
    return response.choices[0].message.content


def _call_anthropic(system: str, message: str) -> str:
    """Call Claude via Anthropic API."""
    import anthropic

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        system=system,
        messages=[{"role": "user", "content": message}],
        max_tokens=1024,
        temperature=0.3,
    )
    return response.content[0].text


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8162, debug=True)
