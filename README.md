# Nova Forge

> Open-source agent orchestration framework powered by Amazon Nova. Describe what you want — Nova plans, builds, reviews, and deploys it.

[![Amazon Nova AI Hackathon 2026](https://img.shields.io/badge/Amazon%20Nova-AI%20Hackathon%202026-ff9900?style=flat-square)](https://amazon-nova.devpost.com/)
[![Tests](https://img.shields.io/badge/tests-1670%20passing-4ade80?style=flat-square)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-a78bfa?style=flat-square)](LICENSE)

**Live demo**: [forge.herakles.dev](https://forge.herakles.dev) | **Interactive demos**: [forge.herakles.dev/demos/](https://forge.herakles.dev/demos/)

## What is Nova Forge?

Nova Forge is an AI agent orchestration framework that turns a natural language description into a working, deployed application. You describe what you want to build. Nova Forge orchestrates multiple AI agents to plan it, build it in parallel waves, review it with an adversarial quality gate, and deploy it — all from a single command.

Built entirely on **Amazon Nova** models via AWS Bedrock:

- **Nova Lite** (32K) — Fast, affordable builds. S-tier on benchmarks.
- **Nova Pro** (300K) — Complex multi-file projects. S-tier on benchmarks.
- **Nova Premier** (1M) — Deep reasoning for the hardest problems. S-tier on benchmarks.

```bash
python3 forge_cli.py

> Build an expense tracker with categories, charts, and CSV export
  Nova interviews you → plans 5 tasks → builds in parallel → reviews → deploys
  Result: Working Flask app in 170 seconds. Grade: S (100%).
```

## Benchmark Results

All 3 Amazon Nova models benchmarked across 4 difficulty scenarios with 25+ automated verification checks per run (HTTP testing, interface fidelity, code quality):

| Scenario | Nova Lite (32K) | Nova Pro (300K) | Nova Premier (1M) |
|----------|:-:|:-:|:-:|
| **Expense Tracker** (Easy) | **S** 100% · 144s | **S** 100% · 167s | **S** 100% · 1110s |
| **Kanban Board** (Hard) | **A** 90% · 285s | **A** 90% · 240s | **A** 89% · 1336s |
| Todo App (Easy) | Scenario defined | Scenario defined | Scenario defined |
| Realtime Kanban (Nightmare) | Stress test | Stress test | Stress test |

**Grade progression over 19 sprints**: C → A → S. Key breakthroughs: 3-tier prompt system, pre-seeded upstream context, Bedrock 300s timeout, convergence tracking, stop_reason detection.

## Architecture

```
User Goal → Deep Interview (3-phase: scope, technical, risk)
         → ForgeAgent (Planning) → spec.md + tasks.json
         → WaveExecutor (Parallel Agents per wave)
           ├── Pre-seeded upstream context injection
           ├── Per-task retry with error self-correction
           ├── Circuit breaker (auto-disable after 3 tool failures)
           └── Artifact handoff between dependent tasks
         → GateReviewer (Adversarial quality check) → PASS/FAIL
         → PreviewManager (14-stack detection + Cloudflare Tunnel)
         → ForgeDeployer (Docker + nginx + SSL) → Live URL
```

### Key Innovations

| Innovation | What it does | Impact |
|------------|-------------|--------|
| **3-Tier Prompts** | Slim (32K), Focused (300K+), Full (1M+) system prompts per model | Nova Lite went from C to S tier |
| **Pre-Seeded Context** | Dependent tasks get upstream file content injected | Saves 2-3 LLM turns per task |
| **Circuit Breaker** | Auto-disables tools after 3 consecutive failures | Prevents infinite retry loops |
| **Agent Self-Correction** | Agents verify their own output with read-back checks | Catches incomplete writes |
| **JSON Recovery** | Handles malformed LLM output (trailing commas, truncation, fences) | Zero crash rate on bad JSON |
| **Context Compaction** | Budget-based (60% for 32K, 65% for 200K+), preserves tool pairs | Enables 30-turn conversations |
| **Bedrock 300s Timeout** | Custom botocore.Config for Premier's ~100s inference time | Premier went from C to S tier |

## Quick Start

**Prerequisites**: Python 3.11+ and AWS credentials with [Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) for Nova models.

```bash
git clone https://github.com/herakles-dev/nova-forge.git
cd nova-forge
./setup.sh                    # Creates venv + installs deps

source .venv/bin/activate     # Activate the virtual environment

# Set your AWS Bedrock credentials
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"

python3 forge_cli.py          # Launch Nova Forge
```

> **Note**: If `./setup.sh` doesn't work on your system, you can install manually:
> `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

### Interactive Commands

| Command | Description |
|---------|-------------|
| `/interview` | 3-phase deep planning interview (scope, stack, risk, formation, model) |
| `/plan` | Generate task plan from spec |
| `/build` | Execute all tasks with parallel AI agents |
| `/preview` | Live preview via Cloudflare Tunnel (3x retry with backoff) |
| `/deploy` | Production deployment (Docker + nginx + SSL) |
| `/model <name>` | Switch model (`nova-lite`, `nova-pro`, `nova-premier`) |
| `/formation <name>` | Set agent team layout (11 formations) |
| `/autonomy <0-5>` | Set agent trust level (A0 manual → A5 unattended) |
| `/health` | System health dashboard |
| `/competition` | Hackathon submission readiness check (8 gates) |

### Non-Interactive CLI

```bash
forge plan "expense tracker with categories and charts" --model nova-lite
forge build
forge preview
forge deploy --domain app.example.com
```

## 11 Agent Formations

Pre-configured multi-agent team layouts with automatic DAAO routing:

| Formation | Agents | Pattern |
|-----------|--------|---------|
| `single-file` | 1 | Quick edits, config changes |
| `lightweight-feature` | 2 | Implementer + tester in parallel |
| `feature-impl` | 4 | Backend + frontend parallel → integrator → tester |
| `new-project` | 3 | Architect → 2 implementers |
| `bug-investigation` | 3 | Three parallel investigators |
| `security-review` | 3 | Scanner + modeler → fixer |
| `perf-optimization` | 2 | Profiler → optimizer |
| `code-review` | 3 | Three parallel reviewers (read-only) |
| `recovery` | 3 | Investigator → fixer → validator |
| `all-hands-planning` | 5 | 4 parallel reviewers → synthesizer |
| `integration-check` | 2 | Cross-file integration validation |

## Autonomy System (A0-A5)

Six trust levels control what agents can do without asking:

| Level | Read | Write | Bash | Deploy | Delete |
|-------|:----:|:-----:|:----:|:------:|:------:|
| A0 Manual | ask | ask | ask | ask | ask |
| A1 Guided | auto | ask | ask | ask | ask |
| **A2 Supervised** (default) | auto | auto | conditional | ask | ask |
| A3 Trusted | auto | auto | auto | conditional | ask |
| A4 Autonomous | auto | auto | auto | auto | conditional |
| A5 Unattended | auto | auto | auto | auto | auto |

Auto-escalation capped at A3. Full audit logging at all levels.

## Project Stats

| Metric | Value |
|--------|-------|
| Lines of code | ~30,000 |
| Python modules | 38 |
| Tests | 1,670 (50 test files) |
| Agent definitions | 20 |
| Formations | 11 |
| Agent tools | 12 |
| JSON schemas | 8 |
| App templates | 4 |
| Hook implementations | 12 |
| Sprints completed | 19 |

## Development Timeline

| Sprint | Key Deliverable |
|--------|-----------------|
| 5 | 12 tools, parallel waves, artifact handoffs, gate review |
| 7-8 | Light model optimization (SLIM_TOOLS, slim prompts) |
| 9 | Assistant layer, A0-A5 autonomy, adaptive UX |
| 12 | Deep planning interview (3-phase, 8 categories) |
| 13 | JSON recovery, 3-tier prompts — Pro C→S, Premier C→A |
| 14 | Pre-seeded context, Bedrock 300s timeout — Premier A→S |
| 15 | Preview resilience, circuit breaker, /health + /competition |
| 16 | Agent self-correction, recovery + all-hands-planning formations |
| 17 | Convergence tracking, adaptive turn budgets, verify phase budget |
| 18 | 5-agent architecture review (78 issues), agent loop hardening |
| 19 | 8-agent test swarm (1108→1670 tests), all 3 models S 100% |

## Tech Stack

- **Language**: Python 3.11+ (pure Python, no JS/TS dependencies)
- **LLM Provider**: AWS Bedrock (Amazon Nova Lite, Pro, Premier)
- **CLI**: Click + custom interactive shell with Rich live UI
- **Testing**: pytest (1,670 tests)
- **Deployment**: Docker + nginx + SSL + Cloudflare Tunnels
- **Website**: Static HTML/CSS/JS at [forge.herakles.dev](https://forge.herakles.dev)

## Live Demos

7 interactive demo apps at [forge.herakles.dev/demos/](https://forge.herakles.dev/demos/):

- **Breakout Game** — Nova Lite, S 100%, 90s (playable arcade game)
- **Expense Tracker** — Nova Lite, S 100%, 144s
- **Kanban Board** — Nova Lite, A 90%, 285s
- **Kanban Board** — Nova Pro, A 90%, 240s
- **Kanban Board** — Nova Premier, A 89%, 1336s
- **Todo App** — Easy scenario preview
- **Realtime Kanban** — Nightmare scenario preview (SSE, 5 tables, file uploads)

## License

MIT
