# Nova Forge

> Open-source agent orchestration framework powered by Amazon Nova. Describe what you want — Nova plans, builds, reviews, and deploys it.

[![Amazon Nova AI Hackathon 2026](https://img.shields.io/badge/Amazon%20Nova-AI%20Hackathon%202026-ff9900?style=flat-square)](https://amazon-nova.devpost.com/)
[![Tests](https://img.shields.io/badge/tests-1670%20passing-4ade80?style=flat-square)]()
[![License: MIT](https://img.shields.io/badge/license-MIT-a78bfa?style=flat-square)](LICENSE)

**Live demo**: [forge.herakles.dev](https://forge.herakles.dev) | **Game demos**: [forge.herakles.dev/demos/](https://forge.herakles.dev/demos/)

## What is Nova Forge?

Nova Forge is an AI agent orchestration framework that turns a natural language description into a working, deployed application. You describe what you want to build. Nova Forge orchestrates multiple AI agents to plan it, build it in parallel waves, review it with an adversarial quality gate, and deploy it — all from a single command.

Built on **Amazon Nova** models via AWS Bedrock:

- **Nova Lite** (32K) — Fast, affordable builds. S-tier 100% on benchmarks.
- **Nova Pro** (300K) — Complex multi-system projects. S-tier 100% on benchmarks.
- **Nova Premier** (1M) — Deep reasoning for ambitious builds. S-tier 100% on benchmarks.

```bash
python3 forge_cli.py

> Build a tower defense game with 6 tower types and chain lightning
  Nova interviews you → plans 7 tasks → builds in parallel → reviews → deploys
  Result: 802-line game in 341 seconds. Playable in browser.
```

## 9-Game Demo Grid

Every game below was planned, coded, and deployed by Nova agents — zero human code. Each has an expandable proof-of-work card showing the exact prompt, model, and build stats.

### Lite (32K) — "Fast. Simple. Fun."

| Game | LOC | Time | What it demonstrates |
|------|-----|------|---------------------|
| [Nova Invaders](https://forge.herakles.dev/demos/nova-invaders/) | 449 | 167s | 3 enemy types, combos, particles, touch controls |
| [Breakout](https://forge.herakles.dev/demos/breakout/) | 238 | 90s | Paddle physics, colored bricks, glow effects |
| [Neon Drift](https://forge.herakles.dev/demos/neon-drift/) | 280 | 152s | Trail effects, close-call bonuses, speed ramp |

### Pro (300K) — "Complex. Strategic."

| Game | LOC | Time | What it demonstrates |
|------|-----|------|---------------------|
| [Asteroid Forge](https://forge.herakles.dev/demos/asteroid-forge/) | 558 | 289s | Resource mining, ship upgrades, 5 wave progression |
| [Hex Conquest](https://forge.herakles.dev/demos/hex-conquest/) | 528 | 312s | Hex grid math, AI opponent, turn-based strategy |
| [Forge Defense](https://forge.herakles.dev/demos/forge-defense/) | 802 | 341s | 6 tower types, chain lightning, splash damage, economy, 20 waves |

### Premier (1M) — "Ambitious. Stunning."

| Game | LOC | Time | What it demonstrates |
|------|-----|------|---------------------|
| [Gravity Wells](https://forge.herakles.dev/demos/gravity-wells/) | 632 | 987s | N-body gravity physics, 5 puzzle levels, particle trails |
| [Synth Swarm](https://forge.herakles.dev/demos/synth-swarm/) | 688 | 1156s | 200+ boid flocking AI, predators, procedural landscape, minimap |
| [Void Racer](https://forge.herakles.dev/demos/void-racer/) | 720 | 1243s | Pseudo-3D scanline rendering, 3 AI opponents, boost pads |

### App Demos (Full-Stack Benchmarks)

| App | Stack | Grade |
|-----|-------|-------|
| [Expense Tracker](https://forge.herakles.dev/demos/expense-tracker/) | Flask + SQLite + Chart.js | S 100% |
| [Kanban Board](https://forge.herakles.dev/demos/kanban-board/) | Flask + Auth + 3 tables | A 90% |
| [Todo App](https://forge.herakles.dev/demos/todo-app/) | FastAPI + SQLite | S 100% |

## Benchmark Results

All 3 Amazon Nova models score S-tier (100%) on the primary benchmark:

| Model | Grade | Score | Duration | Turns | Cost |
|-------|:-----:|:-----:|:--------:|:-----:|:----:|
| Nova Lite (32K) | **S** | 100% | 144s | 40 | $0.002 |
| Nova Pro (300K) | **S** | 100% | 167s | 39 | $0.004 |
| Nova Premier (1M) | **S** | 100% | 1110s | 33 | $0.030 |

**Grade progression over 20 sprints**: Lite S→S→S, Pro C→S→S, Premier C→A→S.

## Architecture

```
User Goal → Deep Interview (3-phase: scope, technical, risk)
         → ForgeAgent (Planning) → spec.md + tasks.json
         → WaveExecutor (Parallel Agents per wave)
           ├── Spec injection (every agent sees the full project spec)
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
| **3-Tier Prompts** | Slim (32K), Focused (300K+), Full (1M+) system prompts | Nova Lite C → S tier |
| **replace_lines Tool** | Line-number-based editing for structural code changes | Enabled autonomous debug of wrapping/refactoring |
| **Spec Injection** | Full spec.md injected into every agent's context | Agents get complete requirements, not just task summaries |
| **Pre-Seeded Context** | Dependent tasks get upstream file content injected | Saves 2-3 LLM turns per task |
| **Convergence Detector** | Deferred past 40% turn budget, prevents premature write-disable | Multi-file builds complete correctly |
| **Circuit Breaker** | Auto-disables tools after 3 consecutive failures | Prevents infinite retry loops |
| **JSON Recovery** | Handles malformed LLM output (trailing commas, truncation) | Zero crash rate on bad JSON |
| **Bedrock 300s Timeout** | Custom botocore.Config for Premier's ~100s inference | Premier C → S tier |

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

## 14 Agent Tools

| Tool | Purpose |
|------|---------|
| `read_file` | Read file contents with optional line range |
| `write_file` | Create or overwrite files |
| `append_file` | Append to existing files (for large file creation) |
| `edit_file` | Precise string replacement in files |
| `replace_lines` | Line-number-based editing for structural changes |
| `search_replace_all` | Bulk rename across a file |
| `bash` | Execute shell commands (secrets scrubbed from env) |
| `glob_files` | Find files by pattern |
| `grep` | Search file contents by regex |
| `list_directory` | List directory contents |
| `think` | Private reasoning scratchpad |
| `remember` | Save notes to project memory |
| `claim_file` | Exclusive write access in multi-agent builds |
| `check_context` | View other agents' announcements and claims |

## 11 Agent Formations

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
| Python modules | 35 |
| Tests | 1,670 (50 test files) |
| Agent tools | 14 |
| Formations | 11 |
| Game demos | 9 |
| App demos | 4 |
| Sprints completed | 20 |

## Development Timeline

| Sprint | Key Deliverable |
|--------|-----------------|
| 1-4 | Core framework, agent loop, Bedrock adapter, interactive CLI |
| 5-6 | 12 tools, parallel waves, artifact handoffs, gate review, preview |
| 7-8 | Light model optimization (SLIM_TOOLS, slim prompts, read-before-edit) |
| 9-10 | Assistant layer, A0-A5 autonomy, agent fine-tuning (1000 tests) |
| 11-12 | 14-stack preview, benchmark infra, deep planning interview |
| 13-14 | JSON recovery, 3-tier prompts, pre-seeded context — all models S-tier |
| 15-16 | Preview resilience, circuit breaker, self-correction, 2 new formations |
| 17-18 | Convergence tracking, 5-agent architecture review, agent loop hardening |
| 19 | 8-agent test swarm (1670 tests), Premier max_tokens 16384 |
| 20 | 9-game demo grid, 6 pipeline bug fixes, replace_lines tool, submission |

## Tech Stack

- **Language**: Python 3.11+ (pure Python, no JS/TS dependencies)
- **LLM Providers**: AWS Bedrock (Nova Lite/Pro/Premier), OpenRouter (Gemini), Anthropic (Claude)
- **CLI**: Click + custom interactive shell with Rich live UI
- **Testing**: pytest (1,670 tests, 50 test files)
- **Deployment**: Docker + nginx + SSL + Cloudflare Tunnels
- **Website**: Static HTML/CSS/JS at [forge.herakles.dev](https://forge.herakles.dev)

## Security

- Agent subprocess environment scrubs secrets (`*KEY*`, `*SECRET*`, `*TOKEN*`, `*PASSWORD*`)
- File sandbox prevents writes outside project root
- CORS restricted to production domain
- Docker container runs as non-root user
- Chat API has input length validation
- Credentials stored with 0600 permissions
- Full `.gitignore` coverage for sensitive files

## License

MIT
