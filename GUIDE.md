# Nova Forge — User Guide

> The complete guide to building software with AI agents. From first build to advanced orchestration.

## Table of Contents

- [Getting Started](#getting-started)
- [Your First Build](#your-first-build)
- [The Build Pipeline](#the-build-pipeline)
- [Models](#models)
- [Formations](#formations)
- [Autonomy System](#autonomy-system)
- [Interview vs Quick Build](#interview-vs-quick-build)
- [Preview & Deploy](#preview--deploy)
- [Commands Reference](#commands-reference)
- [Configuration](#configuration)
- [Benchmarking](#benchmarking)
- [Troubleshooting](#troubleshooting)

---

## Getting Started

### Prerequisites

- Python 3.11+
- AWS account with [Bedrock model access](https://docs.aws.amazon.com/bedrock/latest/userguide/model-access.html) for Amazon Nova models

### Installation

```bash
git clone https://github.com/herakles-dev/nova-forge.git
cd nova-forge
./setup.sh                    # Creates venv + installs deps
source .venv/bin/activate
```

If `setup.sh` doesn't work: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`

### Credentials

Nova Forge needs API keys for at least one provider:

**Amazon Bedrock (recommended)**
```bash
export AWS_ACCESS_KEY_ID="your-key"
export AWS_SECRET_ACCESS_KEY="your-secret"
export AWS_DEFAULT_REGION="us-east-1"
```

**OpenRouter** (for Gemini models)
```bash
export OPENROUTER_API_KEY="your-key"
```

**Anthropic** (for Claude models)
```bash
export ANTHROPIC_API_KEY="your-key"
```

Or run `/login` inside the shell for an interactive setup wizard.

### Launch

```bash
python3 forge_cli.py
```

On first run, Nova Forge will:
1. Check for credentials
2. Ask your experience level (Beginner / Intermediate / Expert)
3. Set an appropriate autonomy level
4. Show a welcome message

---

## Your First Build

Just describe what you want:

```
> Build me an expense tracker with categories and charts
```

Nova Forge detects your intent and runs the full pipeline automatically:

1. **Smart Planning** — Analyzes your goal, detects components (frontend, API, database, auth), proposes a stack
2. **Quick Scope** — Asks 2-6 targeted questions (features, design, data model)
3. **Plan** — Generates spec.md + tasks.json with dependency ordering
4. **Build** — Executes tasks in parallel waves using AI agents
5. **Preview** — Offers to launch a live shareable URL

No slash commands needed. Just type what you want.

### Build intent triggers

These phrases automatically start the full pipeline:

> "Build me...", "Create a...", "Make a...", "I want...", "I need...", "Generate...", "Scaffold...", "Set up...", "Start a..."

---

## The Build Pipeline

### Stage 1: Plan

The planner agent reads your goal and scope context, then generates:

- **spec.md** — Project specification with requirements, architecture, file list
- **tasks.json** — Dependency-ordered task list for build agents

Tasks are organized into **waves** using topological sort (Kahn's algorithm). Independent tasks run in parallel; dependent tasks wait for their prerequisites.

**Fallback chain**: If the decomposer fails to produce tasks, Nova Forge retries, attempts JSON recovery from raw output, and ultimately creates a single fallback task. You'll never get stuck with zero tasks.

### Stage 2: Build

Each task is assigned to a **ForgeAgent** — an autonomous tool-use loop that calls Amazon Nova via AWS Bedrock.

**13 Agent Tools:**

| Tool | Purpose |
|------|---------|
| `read_file` | Read any file in the project |
| `write_file` | Create or overwrite a file |
| `append_file` | Add content to end of file |
| `edit_file` | Search-and-replace within a file |
| `bash` | Run shell commands |
| `glob_files` | Find files by pattern |
| `grep` | Search file contents |
| `list_directory` | List directory contents |
| `search_replace_all` | Bulk find-and-replace |
| `think` | Internal reasoning (no side effects) |
| `claim_file` | Claim file ownership (multi-agent) |
| `remember` | Store a note in persistent memory |
| `check_context` | Check remaining context budget |

**Safety features during build:**

- **Circuit breaker** — Tools auto-disable after 3 consecutive failures
- **Convergence tracker** — Disables writes after 5 idle turns (prevents loops)
- **Verify phase** — Agent reads back created files to check syntax and completeness
- **Adaptive turn budgets** — Scales by file count (1 file: 15 turns, 5 files: 30 turns)
- **Context compaction** — Automatically summarizes old messages when approaching limit

### Stage 3: Gate Review

After each wave, an adversarial **GateReviewer** (read-only agent) inspects the output:

- **PASS** — All quality checks satisfied, proceed to next wave
- **CONDITIONAL** — Minor issues found, proceed with warnings
- **FAIL** — Significant problems, build pauses

### Stage 4: Preview

After a successful build, Nova Forge offers a live preview:

- Auto-detects your stack (Flask, FastAPI, Node, React, static HTML, and 10 more)
- Starts a local dev server
- Creates a Cloudflare Tunnel for a shareable public URL (no account needed)
- 3x retry with exponential backoff if tunnel fails
- Falls back to `localhost` if Cloudflare unavailable

### Stage 5: Deploy

Production deployment with Docker + nginx:

```
/deploy myapp.herakles.dev
```

- Auto-generates Dockerfile for your detected stack
- Builds and runs Docker container (127.0.0.1 binding)
- Writes nginx reverse-proxy config with SSL
- Health-checks the deployment
- Registers in PORT_REGISTRY for collision prevention

---

## Models

### Amazon Nova (primary — via AWS Bedrock)

| Model | Alias | Context | Cost/1K input | Best For |
|-------|-------|---------|---------------|----------|
| Nova Lite | `nova-lite` | 32K | $0.00006 | Fast prototypes, simple apps, gate reviews |
| Nova Pro | `nova-pro` | 300K | $0.0008 | Complex features, multi-file coordination |
| Nova Premier | `nova-premier` | 1M | $0.002 | Deep reasoning, large codebases, architecture |

### Additional Models (via OpenRouter / Anthropic)

| Model | Alias | Context | Cost/1K input | Best For |
|-------|-------|---------|---------------|----------|
| Gemini Flash | `gemini-flash` | 1M | $0.0001 | Large context, fast iteration |
| Gemini Pro | `gemini-pro` | 1M | $0.00125 | Top-tier reasoning |
| Claude Sonnet | `claude-sonnet` | 200K | $0.003 | Excellent instruction-following |
| Claude Haiku | `claude-haiku` | 200K | $0.0008 | Fast, affordable |

### Switching Models

```
/model nova-pro          # Switch directly
/model                   # Interactive selector with credential status
/models                  # Compare all models side-by-side
```

### Model Presets

```
/config model_preset nova      # AWS-only (all 3 Nova models)
/config model_preset mixed     # Best-per-task across all providers
/config model_preset premium   # Always use Nova Pro as default
```

### When to Use Which Model

| Project Type | Recommended | Why |
|-------------|-------------|-----|
| Quick prototype (1-3 files) | `nova-lite` | Fast, cheap, S-tier on benchmarks |
| Multi-file feature (4-8 files) | `nova-pro` | Larger context, better coordination |
| Complex architecture (10+ files) | `nova-premier` | 1M context, deep reasoning |
| Tight budget | `nova-lite` | 33x cheaper than Pro |
| Speed priority | `gemini-flash` | Fastest inference |

### 3-Tier Prompt System

Nova Forge automatically adapts prompts based on context window:

- **Slim** (32K models like Lite): ~600 chars, 8 essential tools, output coaching
- **Focused** (300K+ models): ~1,500 chars, full 13 tools
- **Full** (1M+ models): ~5,000 chars with pre-seeded upstream context

This is why Nova Lite scores S-tier despite having 1/31 the context of Premier.

---

## Formations

Formations are pre-configured multi-agent team layouts. Nova Forge auto-selects the right one during build using DAAO (Difficulty-Aware Agentic Orchestration), but you can override.

### 11 Available Formations

| Formation | Agents | Pattern | Best For |
|-----------|--------|---------|----------|
| `single-file` | 1 | Solo implementer | Config changes, small edits |
| `lightweight-feature` | 2 | Implementer → Tester | Simple features (frontend-only or backend-only) |
| `feature-impl` | 4 | Backend + Frontend → Integrator → Tester | Full-stack features (most common) |
| `new-project` | 3 | Architect → 2 parallel implementers | Greenfield projects |
| `bug-investigation` | 3 | 3 parallel investigators | Unknown root cause bugs |
| `security-review` | 3 | Threat modeler + Scanner → Fixer | Security audits |
| `perf-optimization` | 2 | Optimizer → Tester | Performance bottlenecks |
| `code-review` | 3 | 3 parallel reviewers (read-only) | Pre-merge quality checks |
| `recovery` | 3 | Investigator → Fixer → Validator | Broken deployments, test regressions |
| `all-hands-planning` | 5 | 4 reviewers → Synthesizer | Complex projects needing architecture review |
| `integration-check` | 3 | Auditor → Fixer → Verifier | Post-build cross-file verification |

### Auto-Selection (DAAO Routing)

Nova Forge infers complexity and scope from your tasks, then selects:

| | Small (1-3 tasks) | Medium (4-8 tasks) | Large (9+ tasks) |
|---|---|---|---|
| **Routine** | single-file | lightweight-feature | lightweight-feature |
| **Medium** | lightweight-feature | lightweight-feature | feature-impl |
| **Complex** | lightweight-feature | feature-impl | all-hands-planning |

### Manual Override

```
/formation                    # Interactive selector
/formation feature-impl       # Set directly
```

### Tool Policies

Each formation role has a tool policy controlling what the agent can do:

| Policy | Can Read | Can Write | Can Bash | Purpose |
|--------|----------|-----------|----------|---------|
| **coding** | Yes | Yes | Yes | Implementation work |
| **testing** | Yes | No | Yes | Test execution, validation |
| **readonly** | Yes | No | No | Review, analysis |
| **full** | Yes | Yes | Yes | Unrestricted (optimization) |

---

## Autonomy System

Six trust levels control how much agents can do without asking.

### Levels at a Glance

| Level | Name | Read | Write | Bash | Deploy | Delete |
|-------|------|:----:|:-----:|:----:|:------:|:------:|
| **A0** | Manual | block | block | block | block | block |
| **A1** | Guided | auto | block | block | block | block |
| **A2** | Supervised | auto | auto | safe only | block | block |
| **A3** | Trusted | auto | auto | auto | block | block |
| **A4** | Autonomous | auto | auto | auto | auto | auto |
| **A5** | Unattended | auto | auto | auto | auto | auto + audit |

### Defaults by Skill Level

- **Beginner** → A1 (Guided) — see every file before it's written
- **Intermediate** → A2 (Supervised) — write freely, ask before risky commands
- **Expert** → A3 (Trusted) — minimal interruptions

### Auto-Escalation

Trust grows with successful builds:
- A0 → A1: after 5 clean builds
- A1 → A2: after 10 clean builds
- A2 → A3: after 25 clean builds
- **A3 → A4: never automatic** (must use `/autonomy 4`)
- De-escalation: drops level after failures within 10-minute window

### Usage

```
/autonomy           # Show current level and explanation
/autonomy ?         # Explain all levels in detail
/autonomy 3         # Set to A3 (Trusted)
```

---

## Interview vs Quick Build

### Quick Build (default)

Type a natural language description. Nova Forge runs smart planning with 2-6 targeted questions, then builds.

**Best for:** Prototypes, simple apps, when you know what you want.

```
> Build me a todo app with SQLite
```

### Full Interview (`/interview`)

5-step deep planning session covering scope, stack, risk, formation, and model — plus a deep dive with up to 8 question categories:

1. **Features** — What can users do? Checkboxes + free text
2. **Data** — Database choice, data entities
3. **Auth** — Session vs JWT, user roles
4. **Visual Design** — Color scheme, layout, CSS framework, dark mode, animations
5. **API Design** — REST vs GraphQL, auth method
6. **Real-time** — WebSockets vs SSE
7. **Deployment** — Target environment
8. **Testing** — Test approach

Only relevant categories are shown (auth questions only if auth detected, etc.).

**Best for:** Complex projects, when you want precise control, when the first build wasn't quite right.

```
/interview
```

### The Guide Wizard (`/guide`)

A conversational middle ground — walks you through setup step by step with recommendations at each point.

```
/guide
```

---

## Preview & Deploy

### Preview

```
/preview              # Auto-detect stack, start server + tunnel
/preview stop         # Stop the preview
/preview status       # Check if preview is running
```

**14 supported stacks:** Flask, FastAPI, Django, Streamlit, Next.js, Vite, Node.js, Go, Rust, Rails, PHP, generic Python, Docker, static HTML.

Preview binds to `127.0.0.1` (never `0.0.0.0`) and creates a Cloudflare Tunnel for a shareable URL.

**If preview breaks:**
- Auto-restarts dead server processes
- Re-establishes dropped tunnels
- Falls back to `localhost` if Cloudflare unavailable

### Deploy

```
/deploy                           # Interactive (asks for domain)
/deploy myapp.herakles.dev        # Direct deployment
```

**What happens:**
1. Assigns a port from 8161-8199 range (prevents collisions)
2. Auto-generates Dockerfile for your stack
3. Builds Docker image
4. Runs container with `127.0.0.1` binding
5. Writes nginx reverse-proxy config
6. Provisions SSL for `*.herakles.dev` domains
7. Health-checks the deployment
8. Updates PORT_REGISTRY

### Non-Interactive CLI

```bash
forge plan "expense tracker with charts" --model nova-lite
forge build --formation feature-impl
forge preview
forge deploy --domain myapp.herakles.dev
```

---

## Commands Reference

### Getting Started

| Command | Description |
|---------|-------------|
| `/guide` | Smart setup wizard with recommendations |
| `/interview` | 5-step deep planning (advanced) |
| `/autonomy [0-5]` | View or set autonomy level |
| `/autonomy ?` | Explain all levels |

### Build

| Command | Description |
|---------|-------------|
| `/plan <goal>` | Plan a project from description |
| `/build` | Execute all tasks with AI agents |
| `/preview` | Launch live preview (Cloudflare Tunnel) |
| `/deploy [domain]` | Ship to production (Docker + nginx) |
| `/status` | Progress bar and overview |
| `/tasks` | All tasks with status and dependencies |

### Configuration

| Command | Description |
|---------|-------------|
| `/model [alias]` | Switch model (or interactive selector) |
| `/models` | Compare all models + credential status |
| `/config [key] [value]` | View or edit settings |
| `/login [provider]` | Set up API credentials |

### Project

| Command | Description |
|---------|-------------|
| `/resume [n]` | Resume a recent project |
| `/new <name>` | Start fresh project directory |
| `/cd <path>` | Switch project directory |
| `/pwd` | Show current project |
| `/formation [name]` | View or set agent formation |
| `/audit` | View build audit log |
| `/builds [n]` | Build history (detail with `/builds 1`) |
| `/health` | System health dashboard |
| `/competition` | Hackathon readiness check |

### General

| Command | Description |
|---------|-------------|
| `/clear` | Clear screen |
| `/help` | Show all commands |
| `/quit` | Exit |

---

## Configuration

### Settings

View all: `/config`
Change one: `/config key value`

| Setting | Default | Description |
|---------|---------|-------------|
| `default_model` | `nova-lite` | Model for new builds |
| `model_preset` | `nova` | `nova` / `mixed` / `premium` |
| `project_dir` | `~/projects` | Where new projects are created |
| `max_turns` | `50` | Maximum turns per agent task |
| `temperature` | `0.3` | LLM sampling temperature |
| `auto_build` | `true` | Auto-confirm in guided flow |
| `show_tips` | `true` | Display contextual hints |

### File Locations

| Path | Purpose |
|------|---------|
| `~/.forge/config.json` | User config |
| `~/.forge/cli_state.json` | CLI state (skill level, build count) |
| `~/.forge/credentials.env` | Saved credentials |
| `~/.forge_history` | Command history |
| `.forge/` | Project-level state |
| `.forge/state/tasks.json` | Task definitions and status |
| `.forge/state/autonomy.json` | Autonomy level and trust score |
| `.forge/audit/audit.jsonl` | Full audit trail |

---

## Benchmarking

Run benchmarks to verify model performance:

```bash
source ~/.secrets/hercules.env                            # Load AWS credentials

python3 benchmark_nova_models.py --model nova-lite -v     # Single model
python3 benchmark_nova_models.py --all                    # All 3 Nova models
python3 benchmark_nova_models.py --history                # Run history trend
python3 benchmark_nova_models.py --scenario todo-app      # Alternative scenario
```

### Benchmark Scenarios

| Scenario | Difficulty | Stack | Tasks |
|----------|-----------|-------|-------|
| `expense-tracker` | Easy | Flask + SQLite | 5 |
| `todo-app` | Easy | FastAPI + SQLite | 5 |
| `kanban-board` | Hard | Flask + SQLite + Auth | 7 |
| `realtime-kanban` | Nightmare | Flask + SSE + Uploads | 10+ |

### Grade Scale

| Grade | Score | Meaning |
|-------|-------|---------|
| **S** | 95-100% | Exceptional — production-ready output |
| **A** | 85-94% | Strong — minor issues only |
| **B** | 75-84% | Good — some gaps |
| **C** | 60-74% | Functional but incomplete |
| **D** | 40-59% | Significant issues |
| **F** | <40% | Failed |

### Current Scores (Sprint 19)

| Model | Expense Tracker | Time | Turns |
|-------|:-:|:-:|:-:|
| Nova Lite (32K) | **S** 100% | 144s | 40 |
| Nova Pro (300K) | **S** 100% | 167s | 39 |
| Nova Premier (1M) | **S** 100% | 1110s | 33 |

---

## Troubleshooting

### "Need Amazon Bedrock credentials first"

Set AWS environment variables or run `/login`. You can also switch to a non-AWS model:
```
/model gemini-flash
```

### Build produces 0 tasks

This shouldn't happen (3-stage fallback prevents it). If it does:
1. Try a more specific description: "Build a Flask REST API for recipes with SQLite" instead of "build something"
2. Use `/interview` for structured planning
3. Check `/health` for system issues

### Preview won't start

- Check if another process is using the port: `lsof -i :5000`
- Try `/preview stop` then `/preview` again
- If Cloudflare tunnel fails, preview falls back to localhost

### Agent keeps retrying the same tool

Circuit breaker auto-disables tools after 3 failures. If the agent is stuck:
1. Check `/tasks` for the failing task
2. Re-run `/build` to retry with a fresh agent
3. Lower complexity: split into smaller tasks

### Context window exhaustion

For long builds (30+ turns), context compaction kicks in automatically. If you see truncated output:
- Use a larger model: `/model nova-pro` or `/model nova-premier`
- Reduce task complexity by breaking into smaller pieces

### Build partially completes

Some tasks pass, some fail. Options:
- `/build` to retry only failed tasks
- Describe the problem: "The API endpoint returns 500 — fix the database connection"
- `/tasks` to see which tasks failed and why

### Permission denied on deploy

`/deploy` needs Docker access and nginx write permissions. For quick sharing, use `/preview` instead — it needs no special permissions.

---

## Tips for Power Users

1. **Start with Lite, upgrade if needed.** Nova Lite is 33x cheaper and scores S-tier. Only switch to Pro/Premier for complex multi-file projects.

2. **Use `/interview` for important projects.** The deep planning produces dramatically better specs than quick build.

3. **Watch the gate review.** CONDITIONAL results point to real issues. Fix them before deploying.

4. **Leverage formations.** For security work, `/formation security-review` deploys a threat modeler + scanner + fixer team. For bugs, `bug-investigation` sends 3 investigators with different strategies.

5. **Tune autonomy.** Start at A2, move to A3 after you trust the output. Never use A5 on projects you care about.

6. **Pre-seed context.** For dependent tasks, Nova Forge automatically injects upstream file content. This saves 2-3 turns per task.

7. **Run benchmarks after model changes.** `python3 benchmark_nova_models.py --all -v` catches regressions.

8. **Use the audit log.** `/audit` shows every tool call, every file write, every decision. Essential for debugging agent behavior.
