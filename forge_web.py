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

_VERSION = "0.5.0"

# ── Documentation context for chat ──────────────────────────────────────────

_DOCS_CONTEXT = """\
# Nova Forge Documentation

## What is Nova Forge?
Nova Forge is an open-source AI agent orchestration framework powered by Amazon Nova.
It takes a natural language description of what you want to build and orchestrates
multiple AI agents to plan, build, test, and deploy it. Built for the Amazon Nova AI
Hackathon 2026. 19 sprints, ~30,000 lines of code, 35 Python modules, 1,670 tests.

All 3 Amazon Nova models score S tier (100%) on the benchmark: Nova Lite (32K), Nova Pro (300K),
and Nova Premier (1M). Each builds a complete 5-file Flask + SQLite expense tracker from a
one-line description in under 3 minutes (Lite 144s, Pro 167s, Premier 1110s).

## Quick Start
1. Clone: `git clone https://github.com/herakles-dev/nova-forge.git && cd nova-forge`
2. Setup venv: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
3. Configure AWS credentials: `export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... AWS_DEFAULT_REGION=us-east-1`
4. Launch: `python3 forge_cli.py`
5. Type what you want to build — Nova handles the rest.

## Interactive Shell Commands
- `/interview` — Start 3-phase deep planning interview (scope, stack, risk, formation, model)
- `/plan` — Generate task plan from spec.md
- `/build` — Execute all pending tasks with AI agents
- `/status` — View current build progress and task states
- `/tasks` — List all tasks with status, wave, and dependencies
- `/preview` — Launch dev server with shareable Cloudflare Tunnel URL
- `/deploy` — Production deployment (Docker + nginx + SSL)
- `/model <alias>` — Switch model (nova-lite, nova-pro, nova-premier, gemini-flash, claude-sonnet)
- `/models` — Compare all models — cost, context window, strengths
- `/formation <name>` — View or set agent formation
- `/autonomy <0-5>` — Set agent autonomy level (A0=manual to A5=unattended)
- `/health` — System health dashboard
- `/competition` — Hackathon submission readiness validator
- `/config` — View or modify settings
- `/help` — Show all commands

## Amazon Nova Models (primary — via AWS Bedrock)
- **nova-lite** (32K context) — Fast, cheap. S 100% benchmark. 40 turns, 144s. $0.00006/1K input.
  Uses SLIM prompts (8 tools, ~325 tokens system prompt) for optimal 32K performance.
- **nova-pro** (300K context) — All-rounder. S 100% benchmark. 39 turns, 167s. $0.0008/1K input.
  FOCUSED prompts (13 tools, ~716 tokens), 8K max_tokens.
- **nova-premier** (1M context) — Most powerful. S 100% benchmark. 33 turns, 1110s. $0.002/1K input.
  FOCUSED prompts, 16K max_tokens, 300s Bedrock timeout, stop_reason truncation detection.

All 3 Nova models score S tier (100%) across all 5 dimensions: Task Completion, Code Quality,
Interface Fidelity, Runtime Viability, and Efficiency. Grade progression over 19 sprints:
Lite: C→S→S, Pro: C→S→S, Premier: C→A→S.

## Additional Models (4 more via OpenRouter/Anthropic)
- **gemini-flash** (1M context, OpenRouter) — Lightning fast. $0.0001/1K input.
- **gemini-pro** (1M context, OpenRouter) — Top-tier reasoning. $0.00125/1K input.
- **claude-sonnet** (200K context, Anthropic) — Excellent instruction-following. $0.003/1K input.
- **claude-haiku** (200K context, Anthropic) — Fast, affordable. $0.0008/1K input.

## How the Pipeline Works
1. **Interview** — 3-phase deep planning: scope/context, technical decisions, risk/formation
2. **Plan & Decompose** — AI generates spec.md → tasks.json with dependencies (topological sort)
3. **Parallel Build** — Independent tasks run concurrently by wave (asyncio.gather + semaphores)
4. **Gate Review** — Adversarial read-only reviewer produces PASS/FAIL/CONDITIONAL
5. **Preview** — 14-stack auto-detection, Cloudflare Tunnel with 3x retry and health monitor
6. **Deploy** — Docker + nginx + SSL, one command

## Key Architecture & Innovations
- **3-Tier Prompts**: Slim (32K, ~325 tokens), Focused (300K+, ~716 tokens) — model-appropriate
- **Adaptive Turn Budgets**: `compute_turn_budget()` scales by file count (1-file: 15 soft/19 hard)
- **Convergence Detector**: `ConvergenceTracker` disables write tools after 5 idle turns
- **Verify Phase Budget**: Capped at soft//4 turns, prevents endless read-back loops
- **Pre-Seeded Context**: Dependent tasks get upstream file content injected (4KB inline threshold)
- **Circuit Breaker**: Per-tool failure tracking, auto-disable after 3 failures
- **Self-Correction**: Agents verify own output — syntax check (repr()-based py_compile), read-back
- **JSON Recovery**: `_recover_json()` handles malformed LLM output (trailing commas, truncation)
- **Output Truncation Detection**: `stop_reason="max_tokens"` rejects partial tool calls
- **Context Compaction**: Budget-based (60% for 32K, 65% for 300K+), preserves tool pairs
- **File Ownership**: BuildContext claims prevent parallel agents from overwriting each other
- **14-stack Preview**: Auto-detects Flask, FastAPI, Node, React, Django, etc. (binds 127.0.0.1)
- **Bedrock Timeout**: 300s read_timeout via botocore.Config (Premier needs ~100s/inference)

## Formations (10 pre-configured team layouts)
- **single-file** — Small edits. 1-3 tasks.
- **lightweight-feature** — Single-layer. Implementer + tester.
- **feature-impl** (RECOMMENDED) — Full-stack. Backend + frontend + integrator + tester.
- **new-project** — Greenfield. Architect → implementers.
- **bug-investigation** — Unknown root cause. Three parallel investigators.
- **security-review** — Audit. Scanner + modeler + fixer.
- **perf-optimization** — Profiling + optimization.
- **code-review** — PR review. Three read-only reviewers.
- **recovery** — Post-failure. Investigator → fixer → validator.
- **all-hands-planning** — Spec review. 4 reviewers + synthesizer.

## Key Modules (35 files, ~30,000 LOC)
- `forge_cli.py` (4,689 LOC) — Interactive shell, deep planning interview, all /commands
- `forge_agent.py` (2,004 LOC) — Tool-use loop, 12 tools, ConvergenceTracker, verify phase
- `forge_orchestrator.py` (999 LOC) — Plan/build/deploy orchestration + JSON recovery
- `forge_preview.py` (996 LOC) — PreviewManager — 14-stack detection + Cloudflare Tunnel
- `prompt_builder.py` (940 LOC) — 3-tier prompt system + autonomy-aware
- `model_router.py` (900 LOC) — 3 provider adapters (Bedrock, OpenAI, Anthropic)
- `forge_pipeline.py` (870 LOC) — WaveExecutor + ArtifactManager + GateReviewer
- `forge_verify.py` (1,072 LOC) — BuildVerifier — L1 static, L2 server, L3 browser
- 1,670 tests across 50 test files, all passing

## Benchmark Scores (Sprint 19, 2026-03-16)
All 3 Nova models: S tier, 100% across every dimension.
- Nova Lite:    S 100% — 40 turns, 144s, 5/5 tasks
- Nova Pro:     S 100% — 39 turns, 167s, 5/5 tasks
- Nova Premier: S 100% — 33 turns, 1110s, 5/5 tasks

## Development Timeline (19 sprints)
Sprint 5: 12 tools, parallel waves, gate review, autonomy
Sprint 9: Assistant layer, A0-A5 autonomy
Sprint 13: JSON recovery, 3-tier prompts, Pro C→S
Sprint 14: Premier S tier, pre-seeded context
Sprint 17: Agent loop convergence, adaptive turn budgets, benchmark aligned to CLI
Sprint 18: 5-agent architecture review (78 issues), _auto_verify fix, prompt contradictions resolved
Sprint 19: 8-agent test swarm (1670 tests), Premier max_tokens 16384, all 3 models S 100%
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
            "tests": 1670,
            "test_files": 50,
            "modules": 35,
            "loc": 30000,
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
