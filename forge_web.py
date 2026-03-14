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
Nova Forge is an open-source AI agent orchestration framework. It takes a natural language
description of what you want to build and orchestrates multiple AI agents to plan, build,
test, and deploy it. Built for the Amazon Nova AI Hackathon 2026.

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
- `/interview` — Start guided project setup (scope, stack, risk, formation, model)
- `/plan` — Generate task plan from spec.md
- `/build` — Execute all pending tasks with AI agents
- `/build --no-review` — Skip the gate review step
- `/build --no-verify` — Skip runtime verification
- `/status` — View current build progress and task states
- `/tasks` — List all tasks with status, wave, and dependencies
- `/preview` — Launch dev server with shareable Cloudflare Tunnel URL
- `/preview stop` — Stop the preview server
- `/deploy` — Production deployment (Docker + nginx + SSL)
- `/model <alias>` — Switch the active model (e.g., nova-lite, gemini-flash)
- `/models` — Compare all models — cost, context window, strengths
- `/formation <name>` — View or set agent formation
- `/autonomy <0-5>` — Set agent autonomy level (A0-A5)
- `/login` — Set up or change API provider credentials
- `/config` — View or modify all settings
- `/guide` — Interactive project wizard
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

## Models (7 supported)
- **nova-lite** (32K context, Bedrock) — Fast, cheap. Best for focused tasks. $0.00006/1K input.
- **nova-pro** (300K context, Bedrock) — All-rounder. Complex coding. $0.0008/1K input.
- **nova-premier** (1M context, Bedrock) — Most powerful Nova. Deep reasoning. $0.002/1K input.
- **gemini-flash** (1M context, OpenRouter) — Lightning fast, huge context. Best value for code. $0.0001/1K input.
- **gemini-pro** (1M context, OpenRouter) — Top-tier reasoning. Complex architectures. $0.00125/1K input.
- **claude-sonnet** (200K context, Anthropic) — Excellent instruction-following. Premium. $0.003/1K input.
- **claude-haiku** (200K context, Anthropic) — Fast, affordable Claude. $0.0008/1K input.

Switch with `/model <alias>`, e.g. `/model gemini-flash`.

## Formations (8 pre-configured team layouts)
- **single-file** (1 role) — Small, focused file edits. 1-3 tasks.
- **lightweight-feature** (2 roles) — Single-layer features. Implementer + tester in parallel.
- **feature-impl** (4 roles, RECOMMENDED) — Adding features. Backend + frontend parallel, then integrator, then tester.
- **new-project** (3 roles) — Greenfield setup. Architect → implementers.
- **bug-investigation** (3 roles) — Unknown root cause. Three parallel investigators.
- **security-review** (3 roles) — Security audit. Scanner + modeler parallel, then fixer.
- **perf-optimization** (2 roles) — Profiling + optimization. Sequential deep work.
- **code-review** (3 roles) — PR review. Three parallel reviewers (security, performance, coverage). Read-only.

Set with `/formation <name>`, e.g. `/formation feature-impl`.

## Autonomy System (A0-A5)
- **A0 Manual** — Ask for everything. Full human control.
- **A1 Guided** — Read files freely. Ask before writes.
- **A2 Supervised** (DEFAULT) — Read/write freely. Ask before risky commands.
- **A3 Trusted** — Handle most things independently. Block high-risk only.
- **A4 Autonomous** — Full autopilot. Requires explicit `/autonomy 4`.
- **A5 Unattended** — CI/background. Full audit logging.

Set with `/autonomy <0-5>`.

## How the Pipeline Works
1. **Interview** — Interactive 5-step: scope, stack, risk, formation, model
2. **Plan & Decompose** — AI generates spec.md → tasks.json with dependencies
3. **Parallel Build** — Independent tasks run concurrently (asyncio.gather + semaphores)
4. **Gate Review** — Adversarial read-only reviewer produces PASS/FAIL/CONDITIONAL
5. **Preview & Deploy** — Cloudflare Tunnel URL or Docker + nginx + SSL

## Key Architecture
- `forge_cli.py` (2,894 LOC) — Interactive shell with 24 commands
- `forge_agent.py` (1,603 LOC) — Tool-use loop with 12 tools
- `model_router.py` (902 LOC) — Bedrock, OpenAI, Anthropic adapters
- `forge_pipeline.py` (848 LOC) — WaveExecutor, ArtifactManager, GateReviewer
- `forge_guards.py` (1,029 LOC) — RiskClassifier, PathSandbox, AutonomyManager
- `forge_verify.py` (564 LOC) — BuildVerifier (L1 static, L2 server, L3 browser)
- 908 tests across 38 test files, all passing

## Programmatic Usage
```python
from forge_agent import ForgeAgent
from model_router import ModelRouter

router = ModelRouter("bedrock/us.amazon.nova-2-lite-v1:0")
agent = ForgeAgent(project_root="./my-project", router=router, max_turns=30)
result = await agent.run(prompt="Create a Flask API", system="Write working code.")
print(result.artifacts)  # {"/path/to/app.py": "# code..."}
```

## Agent Tools (12 built-in)
read_file, write_file, append_file, edit_file, bash, glob_files, grep,
list_directory, search_replace_all, think, claim_file, check_context

## Providers
- **Amazon Bedrock** — AWS credentials (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY)
- **OpenRouter** — API key (OPENROUTER_API_KEY) — access to Gemini models
- **Anthropic** — API key (ANTHROPIC_API_KEY) — access to Claude models

## Project Structure
nova-forge/
├── forge.py               # Click CLI (14 commands)
├── forge_cli.py           # Interactive shell (main file)
├── forge_agent.py         # Core agent loop + 12 tools
├── forge_pipeline.py      # WaveExecutor + ArtifactManager
├── model_router.py        # LLM provider adapters
├── forge_guards.py        # Security (risk, sandbox, autonomy)
├── formations.py          # 10 formations + DAAO routing
├── prompt_builder.py      # System prompt construction
├── forge_verify.py        # BuildVerifier (L1-L3)
├── forge_preview.py       # PreviewManager + Cloudflare Tunnel
├── forge_deployer.py      # Docker + nginx + SSL deployment
├── config.py              # Configuration + context windows
├── agents/                # 20 YAML agent definitions
├── schemas/               # 8 JSON schemas
├── templates/             # 4 app templates
├── tests/unit/            # 1047 tests (48 test files)
└── web/                   # Web documentation guide
"""


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(str(_WEB_DIR), "index.html")


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
