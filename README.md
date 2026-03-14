# Nova Forge

> Open-source agent orchestration framework. V11's proven patterns, any LLM, pure Python.

**Live demo**: [forge.herakles.dev](https://forge.herakles.dev)

## What is Nova Forge?

Nova Forge replaces closed-source agent runtimes (Claude Code, Cursor, etc.) with a **pure Python tool-use loop** that works with **any LLM** supporting function calling.

It ports battle-tested orchestration patterns from V11 (which built 89+ production services) to a standalone framework that runs with Amazon Nova, Google Gemini, Anthropic Claude, or any OpenAI-compatible model.

```bash
# Launch the interactive CLI
forge

# Or use individual commands
forge plan "weather dashboard API" --model nova-lite
forge build --model gemini-flash
forge preview            # Cloudflare Tunnel — shareable URL, no account needed
forge deploy --domain weather.herakles.dev
```

## Architecture

```
User Goal → Interview (scope, stack, risk)
         → ForgeAgent (Planning) → spec.md + tasks.json
         → WaveExecutor (Parallel Agents) → Built Project
           ├── Per-task retry with error self-correction
           ├── Artifact handoff between dependent tasks
           └── Dependency-aware failure blocking
         → GateReviewer (Quality Check) → PASS/FAIL
         → Preview (Cloudflare Tunnel) → Shareable URL
         → ForgeDeployer (Docker + nginx) → Live URL
```

### Key Components

| Component | Purpose |
|-----------|---------|
| **ForgeAgent** | Tool-use loop: prompt → LLM → tool calls → execute → loop. Emits real-time events. |
| **ModelRouter** | 3 provider adapters: AWS Bedrock, OpenAI/OpenRouter, Anthropic |
| **TaskStore** | CRUD + JSON persistence + topological sort for wave computation |
| **WaveExecutor** | `asyncio.gather()` with semaphore throttling for parallel agents |
| **BuildDisplay** | Rich live UI: real-time tool calls, per-task results, build summary with tokens/timing |
| **10 Formations** | Pre-built team patterns (feature-impl, code-review, security-review, etc.) |
| **GateReviewer** | LLM-backed quality gate producing PASS/FAIL/CONDITIONAL verdicts |
| **HookSystem** | 12 pre/post/stop hooks compatible with V11's shell hook protocol |
| **PathSandbox** | Defense-in-depth file access control |
| **RiskClassifier** | 29 regex patterns for command risk classification |
| **ArtifactManager** | Per-agent isolation with post-gather merging and upstream injection |
| **ForgeDeployer** | Docker build → container → nginx reverse proxy → SSL → health check |

### Agent Event System

ForgeAgent emits real-time `AgentEvent` callbacks during execution:

| Event | Data |
|-------|------|
| `turn_start` | Turn number |
| `model_response` | Tokens in/out, duration |
| `tool_start` | Tool name, args, file path |
| `tool_end` | Result preview, duration, file action |
| `compact` | Before/after token counts |
| `error` | Error message |

The `BuildDisplay` class subscribes to these events to render a live progress bar showing exactly what the agent is doing (reading files, writing code, running commands).

## Supported Models

| Alias | Provider | Model | Context |
|-------|----------|-------|---------|
| `nova-lite` | AWS Bedrock | Amazon Nova 2 Lite | 32K |
| `nova-pro` | AWS Bedrock | Amazon Nova Pro | 300K |
| `nova-premier` | AWS Bedrock | Amazon Nova Premier | 1M |
| `gemini-flash` | OpenRouter | Google Gemini 2.0 Flash | 1M |
| `gemini-pro` | OpenRouter | Google Gemini 2.5 Pro | 1M |
| `claude-sonnet` | Anthropic | Claude Sonnet 4.6 | 200K |
| `claude-haiku` | Anthropic | Claude Haiku 4.5 | 200K |

## Quick Start

### Prerequisites

- Python 3.11+
- AWS credentials (for Bedrock models)
- OpenRouter API key (for Gemini models, optional)

### Install

```bash
git clone https://github.com/herakles-dev/nova-forge.git
cd nova-forge
pip install -r requirements.txt
```

### Set up credentials

```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"
export OPENROUTER_API_KEY="your-key"  # optional, for Gemini
```

### Interactive CLI (recommended)

```bash
forge                # Launch the interactive shell
```

Inside the shell:
```
> Build me a REST API for managing recipes
  (Nova interviews you, plans, and builds automatically)

/interview Build a weather dashboard    # Guided 5-step interview
/plan REST API with auth                # Direct planning (skip interview)
/build                                  # Execute the plan
/preview                                # Share via Cloudflare Tunnel
/deploy my-app.example.com             # Production deploy
```

### Non-interactive CLI

```bash
forge new my-app --template flask-api
cd my-app
forge plan "REST API with user authentication and CRUD endpoints"
forge build
forge preview              # Shareable URL via Cloudflare Tunnel
forge deploy --domain my-app.example.com
```

## Interactive Shell Commands

### Build
| Command | Description |
|---------|-------------|
| `/interview <goal>` | Guided build with V11-style interview (scope, stack, risk) |
| `/plan <goal>` | Plan a project directly (skip interview) |
| `/build` | Execute the plan with retry + artifact handoff |
| `/status` | Progress bar and project overview |
| `/tasks` | See all tasks with status and dependencies |

### Deploy & Preview
| Command | Description |
|---------|-------------|
| `/preview` | Launch a live preview via Cloudflare Tunnel (shareable URL) |
| `/preview stop` | Stop the tunnel and dev server |
| `/deploy <domain>` | Deploy to production (Docker + nginx + SSL) |

### Configuration
| Command | Description |
|---------|-------------|
| `/model <name>` | Switch AI model (e.g. `/model gemini-flash`) |
| `/models` | Show all available models + credential status |
| `/config` | View or change settings |
| `/login` | Set up API credentials for a provider |

### Project
| Command | Description |
|---------|-------------|
| `/resume <n>` | Resume a recent project |
| `/new <name>` | Start a fresh project directory |
| `/cd <path>` | Switch project directory |
| `/pwd` | Show current project location |
| `/formation` | Agent team configurations |
| `/audit` | View the build audit log |

## Build Features

### Retry with Self-Correction
Each task gets up to 2 retries. On failure, the error is injected into the retry prompt so the agent can self-correct:
```
Previous Attempt Failed: old_string not found in app.py
Please try a different approach. Common fixes:
- If a file wasn't found, use glob_files to discover the correct path
- If an edit failed, read the file first to get the exact string
```

### Artifact Handoff
Completed tasks pass their outputs to dependent tasks:
```
## Context from Prior Tasks
Task [1] "Setup project structure" completed.
Files produced:
  - app.py (2400 bytes)
  - models.py (1200 bytes)
```

### Dependency-Aware Failure Blocking
If a task fails, all tasks that depend on it are automatically blocked instead of running and failing predictably.

### Live Build Display
Real-time visibility into what the agent is doing:
- Spinner shows current tool call (`Reading app.py`, `Writing models.py`, `Running npm install`)
- Per-task result line with duration, tool calls, files touched, token usage
- Build summary table with total stats

## Templates

| Template | Stack | Use Case |
|----------|-------|----------|
| `flask-api` | Flask + Gunicorn | REST API backend |
| `streamlit-dash` | Streamlit + Plotly | Data dashboard |
| `static-site` | nginx + HTML/CSS | Landing page |
| `nova-chat` | Flask + Bedrock | AI chat app powered by Nova |

## 10 Formations

Formations are pre-built Agent Team patterns for common workflows:

| Formation | Roles | Pattern |
|-----------|-------|---------|
| `single-file` | 1 implementer | Quick edits |
| `lightweight-feature` | implementer + tester | Single-layer features |
| `feature-impl` | 2 impl + integrator + tester | Full-stack features |
| `new-project` | architect + 2 implementers | Greenfield setup |
| `bug-investigation` | 3 parallel investigators | Unknown root cause |
| `security-review` | modeler + scanner + fixer | Security audit |
| `perf-optimization` | optimizer + tester | Performance work |
| `code-review` | 3 parallel reviewers | PR review |

DAAO routing automatically selects a formation based on project scope and complexity.

## Testing

```bash
# Run all 1050+ tests
pytest tests/ -v

# Run specific test module
pytest tests/unit/test_pipeline.py -v
```

## Project Structure

```
nova-forge/
├── bin/
│   ├── forge                 # CLI entry point (bash wrapper)
│   └── herakles              # Alias for forge
├── forge.py                  # CLI commands (Click)
├── forge_cli.py              # Interactive shell (ForgeShell) + V11-style interview
├── forge_agent.py            # Tool-use loop + AgentEvent system
├── forge_display.py          # Rich live UI (BuildDisplay)
├── model_router.py           # 3 provider adapters (Bedrock, OpenAI, Anthropic)
├── forge_tasks.py            # TaskStore + topological sort
├── forge_guards.py           # Risk classifier + PathSandbox + AutonomyManager
├── forge_hooks.py            # Hook system (V11 compatible)
├── forge_hooks_impl.py       # 12 hook implementations
├── formations.py             # 8 formation definitions + DAAO routing
├── prompt_builder.py         # 7-section prompt construction
├── forge_pipeline.py         # WaveExecutor + ArtifactManager + GateReviewer
├── forge_orchestrator.py     # Plan/build/deploy orchestration
├── forge_deployer.py         # Docker + nginx + SSL deployment
├── forge_web.py              # Web dashboard (forge.herakles.dev)
├── forge_session.py          # Session lifecycle + persistence
├── forge_compliance.py       # 10-gate compliance checker
├── forge_registry.py         # Agent definition registry (20 agents)
├── forge_schema.py           # 8 JSON schema validators
├── forge_audit.py            # JSONL audit trail
├── forge_migrate.py          # Legacy version migration
├── forge_teams.py            # Multi-agent team spawning
├── config.py                 # Model configs + .forge/ init
├── agents/                   # 20 agent definitions (YAML)
├── schemas/                  # 8 JSON schemas
├── templates/                # 4 app skeleton templates
├── tests/                    # 303 unit tests
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## The "Swappable Brain" Concept

Nova Forge proves that agent orchestration patterns are **model-portable**. The same `forge plan` + `forge build` pipeline works identically whether the brain is:

- Amazon Nova 2 Lite (AWS Bedrock)
- Google Gemini 2.0 Flash (OpenRouter)
- Anthropic Claude (direct API)

The brain is just a `--model` flag. The orchestration patterns (waves, formations, gates, artifacts, retries) are the real innovation.

## Built for the Amazon Nova AI Hackathon

Nova Forge was built in 7 days for the [Amazon Nova AI Hackathon](https://devpost.com) to demonstrate that V11's orchestration patterns — battle-tested across 89 production services — work with any LLM, including Amazon Nova.

**Stats**: 13,719 lines of core code | 3,076 lines of tests | 303 passing tests | 25 Python modules | 20 agent definitions | 8 formations | 8 JSON schemas | 7 model aliases | 4 templates | 3 provider adapters

## License

MIT
