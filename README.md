# Nova Forge

> Open-source agent orchestration framework. V11's proven patterns, any LLM, pure Python.

**Live demo**: [forge.herakles.dev](https://forge.herakles.dev)

## What is Nova Forge?

Nova Forge replaces closed-source agent runtimes (Claude Code, Cursor, etc.) with a **~300-line Python tool-use loop** that works with **any LLM** supporting function calling.

It ports battle-tested orchestration patterns from V11 (which built 89+ production services) to a standalone framework that runs with Amazon Nova, Google Gemini, Anthropic Claude, or any OpenAI-compatible model.

```bash
# Plan a project with Amazon Nova
forge plan "weather dashboard API" --model nova-lite

# Build it with parallel agents (Gemini for speed)
forge build --model gemini-flash

# Deploy to a live URL
forge deploy --domain weather.herakles.dev
```

## Architecture

```
User Goal → ForgeAgent (Planning) → spec.md
         → ForgeAgent (Decomposition) → tasks.json
         → WaveExecutor (Parallel Agents) → Built Project
         → GateReviewer (Quality Check) → PASS/FAIL
         → ForgeDeployer (Docker + nginx) → Live URL
```

### Key Components

| Component | Purpose |
|-----------|---------|
| **ForgeAgent** | Tool-use loop: prompt → LLM → tool calls → execute → loop |
| **ModelRouter** | 3 provider adapters: AWS Bedrock, OpenAI/OpenRouter, Anthropic |
| **TaskStore** | CRUD + JSON persistence + topological sort for wave computation |
| **WaveExecutor** | `asyncio.gather()` with semaphore throttling for parallel agents |
| **8 Formations** | Pre-built team patterns (feature-impl, code-review, security-review, etc.) |
| **GateReviewer** | LLM-backed quality gate producing PASS/FAIL/CONDITIONAL verdicts |
| **HookSystem** | Pre/post tool hooks compatible with V11's shell hook protocol |
| **PathSandbox** | Defense-in-depth file access control |
| **RiskClassifier** | 29 regex patterns for command risk classification |

## Supported Models

| Alias | Provider | Model |
|-------|----------|-------|
| `nova-lite` | AWS Bedrock | Amazon Nova 2 Lite |
| `nova-pro` | AWS Bedrock | Amazon Nova Pro |
| `nova-premier` | AWS Bedrock | Amazon Nova Premier |
| `gemini-flash` | OpenRouter | Google Gemini 2.0 Flash |
| `gemini-pro` | OpenRouter | Google Gemini 2.5 Pro |
| `claude-sonnet` | Anthropic | Claude Sonnet 4.6 |
| `claude-haiku` | Anthropic | Claude Haiku 4.5 |

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

### Create and build a project

```bash
# Initialize a new project
forge new my-app --template flask-api

# Plan it
cd my-app
forge plan "REST API with user authentication and CRUD endpoints"

# Build it
forge build

# Deploy it
forge deploy --domain my-app.example.com
```

## Templates

| Template | Stack | Use Case |
|----------|-------|----------|
| `flask-api` | Flask + Gunicorn | REST API backend |
| `streamlit-dash` | Streamlit + Plotly | Data dashboard |
| `static-site` | nginx + HTML/CSS | Landing page |
| `nova-chat` | Flask + Bedrock | AI chat app powered by Nova |

## 8 Formations

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

## Testing

```bash
# Run all 110 tests
pytest tests/ -v

# Run specific test module
pytest tests/unit/test_pipeline.py -v
```

## Project Structure

```
nova-forge/
├── forge.py              # CLI entry point (Click)
├── forge_agent.py        # Tool-use loop (~300 lines)
├── model_router.py       # 3 provider adapters
├── forge_tasks.py        # TaskStore + topological sort
├── forge_guards.py       # Risk classifier + PathSandbox
├── forge_hooks.py        # Hook system (V11 compatible)
├── formations.py         # 8 formation definitions
├── prompt_builder.py     # 7-section prompt construction
├── forge_pipeline.py     # WaveExecutor + ArtifactManager + GateReviewer
├── forge_orchestrator.py # CLI → pipeline wiring
├── forge_deployer.py     # Docker + nginx deployment
├── forge_web.py          # Web dashboard
├── config.py             # Model configs + .forge/ init
├── templates/            # 4 app skeleton templates
├── tests/                # 110 unit tests
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## The "Swappable Brain" Concept

Nova Forge proves that agent orchestration patterns are **model-portable**. The same `forge plan` + `forge build` pipeline works identically whether the brain is:

- Amazon Nova 2 Lite (AWS Bedrock)
- Google Gemini 2.0 Flash (OpenRouter)
- Anthropic Claude (direct API)

The brain is just a `--model` flag. The orchestration patterns (waves, formations, gates, artifacts) are the real innovation.

## Built for the Amazon Nova AI Hackathon

Nova Forge was built in 7 days for the [Amazon Nova AI Hackathon](https://devpost.com) to demonstrate that V11's orchestration patterns — battle-tested across 89 production services — work with any LLM, including Amazon Nova.

**Stats**: 6,053 lines of core code, 1,381 lines of tests, 110 passing tests, 13 Python modules, 8 formations, 7 model aliases, 3 provider adapters.

## License

MIT
