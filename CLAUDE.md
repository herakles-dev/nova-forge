# Nova Forge

> Open-source agent orchestration framework. V11's proven patterns, any LLM, pure Python.

## Tech Stack

- **Language**: Python 3.11+ (pure Python, no JS/TS)
- **CLI**: Click (forge.py) + custom interactive shell (forge_cli.py)
- **LLM Providers**: AWS Bedrock (Nova), OpenRouter (Gemini), Anthropic (Claude)
- **UI**: Rich (live progress, tables, panels, spinners)
- **Testing**: pytest (883 tests in 37 test files)
- **Deployment**: Docker + nginx + SSL + Cloudflare Tunnels
- **Dependencies**: boto3, openai, anthropic, flask, rich, click, pyyaml, jsonschema, pydantic

## Commands

```bash
# Run tests
pytest tests/ -v

# Run specific module tests
pytest tests/unit/test_pipeline.py -v

# Syntax check a file
python3 -c "import py_compile; py_compile.compile('FILE.py', doraise=True)"

# Launch interactive CLI
python3 forge_cli.py

# Non-interactive commands
python3 forge.py plan "description" --model nova-lite
python3 forge.py build --model gemini-flash
python3 forge.py preview
python3 forge.py deploy --domain app.example.com

# Entry points (symlinked to /usr/local/bin/)
forge              # Primary CLI
herakles           # Alias
```

## Architecture

```
User Goal -> Interview (scope, stack, risk)
          -> ForgeAgent (Planning) -> spec.md + tasks.json
          -> WaveExecutor (Parallel Agents) -> Built Project
          -> GateReviewer (Quality Check) -> PASS/FAIL
          -> Preview (Cloudflare Tunnel) -> Shareable URL
          -> ForgeDeployer (Docker + nginx) -> Live URL
```

## Module Map (35 files, 21,082 LOC)

### Core Modules

| Module | LOC | Purpose |
|--------|-----|---------|
| forge_cli.py | 2894 | Interactive shell, interview, all /commands |
| forge_agent.py | 1603 | Tool-use loop, 12 tools, AgentEvent, auto-verify, read-before-write |
| forge_hooks_impl.py | 1057 | 12 hook implementations |
| model_router.py | 902 | 3 provider adapters (Bedrock, OpenAI, Anthropic) + JSON recovery |
| forge_pipeline.py | 848 | WaveExecutor + ArtifactManager + GateReviewer |
| forge_guards.py | 1029 | RiskClassifier + PathSandbox + AutonomyManager (A0-A5) |
| prompt_builder.py | 852 | 7-section prompt + slim variants + autonomy-aware prompts |
| forge.py | 740 | Click CLI commands (14 commands) |
| forge_orchestrator.py | 670 | Plan/build/deploy orchestration |
| formations.py | 656 | 8 formations + DAAO routing + 5 tool policies |
| forge_tasks.py | 643 | TaskStore + topological sort (Kahn's algorithm) |
| forge_index.py | 634 | ProjectIndex, export/import scanning, dependency graph |
| forge_verify.py | 564 | BuildVerifier — L1 static, L2 server, L3 browser checks |
| forge_display.py | 560 | Rich live UI (BuildDisplay, TaskTrace) |
| forge_session.py | 494 | Session lifecycle + persistence |
| forge_preview.py | 427 | PreviewManager — Cloudflare Tunnel + dev server |
| forge_deployer.py | 384 | Docker + nginx + SSL deployment |
| forge_teams.py | 318 | Multi-agent team spawning |
| forge_memory.py | 309 | Persistent memory system |
| forge_migrate.py | 295 | Legacy version migration (V5-V10 → Forge) |
| forge_hooks.py | 293 | Hook system (V11 compatible) |
| forge_models.py | 286 | Model definitions and capability profiles |
| forge_compliance.py | 280 | 10-gate compliance checker |
| forge_registry.py | 276 | Agent definition registry (20 agents) |
| config.py | 269 | Model configs, .forge/ init, context windows |
| forge_livereload.py | 252 | LiveReloadServer for build previews |
| forge_web.py | 238 | Web dashboard (forge.herakles.dev) |
| forge_audit.py | 224 | JSONL audit trail |
| forge_comms.py | 196 | BuildContext, FileClaim, AgentAnnouncement |
| forge_schema.py | 134 | 8 JSON schema validators |
| forge_prompt.py | 106 | Prompt utilities |
| forge_assistant.py | 513 | Smart session assistant — skill detection, recommendations |

### Benchmark & Demo Scripts

| Module | LOC | Purpose |
|--------|-----|---------|
| benchmark_expense_tracker.py | 832 | End-to-end benchmark (5 tasks, 3 waves, 25 checks) |
| demo_nova_e2e.py | 564 | E2E demo script |
| challenge_build.py | 274 | Challenge build runner |

## Key Patterns

### AgentEvent System
ForgeAgent emits events via `on_event` callback: turn_start, model_response, tool_start, tool_end, compact, error. BuildDisplay subscribes for real-time UI.

### Build Pipeline (CLI path)
`_cmd_build()` in forge_cli.py implements:
- **Wave execution**: Topological sort -> sequential waves, parallel tasks within each wave
- **Retry with self-correction**: MAX_RETRIES=2, error injected into retry prompt
- **Artifact handoff**: `_gather_upstream_artifacts()` passes file info from completed deps to dependent tasks
- **Dependency-aware failure blocking**: `failed_ids` set, dependent tasks auto-blocked

### Build Pipeline (library path)
forge_pipeline.py provides WaveExecutor, ArtifactManager, GateReviewer for programmatic use.

### Model Router
Provider detection from model ID prefix: `bedrock/` -> Bedrock Converse API, `openrouter/` or `openai/` -> OpenAI-compatible, `anthropic/` -> Anthropic API. Each adapter normalizes to common tool-use format.

### Bedrock Converse API
Requires exact 1:1 toolUse/toolResult pairing per turn. The router handles this constraint.

### Assistant Layer
ForgeAssistant provides skill-level-aware guidance: detects beginner/intermediate/expert from usage signals (builds completed, recent projects, model config), recommends autonomy levels, formations, and models. Contextual hints are shown once per context and gated by skill level (experts see fewer tips). Integrated into ForgeShell for adaptive UX.

### Autonomy System (A0-A5)
Six-level trust system controlling what Nova can do without asking:
- A0 Manual: ask for everything
- A1 Guided: read freely, ask before writing
- A2 Supervised: read/write freely, ask for risky commands (default)
- A3 Trusted: handle most things independently
- A4 Autonomous: full autopilot
- A5 Unattended: background/CI execution with audit logging

Auto-escalation stops at A3 (A4+ requires explicit `set_level()`). De-escalation: single error drops 1 level, 5+ errors in 10 minutes crashes to A0. 1-hour cooldown prevents rapid re-escalation.

## File Layout

```
nova-forge/
├── bin/forge              # Bash wrapper -> forge_cli.py
├── bin/herakles           # Alias
├── forge.py               # Click CLI (14 commands)
├── forge_cli.py           # Interactive shell (main file)
├── forge_agent.py         # Core agent loop + 12 tools
├── forge_assistant.py     # Smart session assistant (skill, recommendations)
├── forge_comms.py         # BuildContext, FileClaim, announcements
├── forge_compliance.py    # 10-gate compliance checker
├── forge_deployer.py      # Docker + nginx + SSL deployment
├── forge_display.py       # Rich live UI
├── forge_guards.py        # Security (risk, sandbox, autonomy A0-A5)
├── forge_hooks.py         # Hook system (V11 compatible)
├── forge_hooks_impl.py    # 12 hook implementations
├── forge_index.py         # ProjectIndex, exports, dependencies
├── forge_livereload.py    # Live reload server for previews
├── forge_memory.py        # Persistent memory system
├── forge_migrate.py       # Version migration (V5-V10)
├── forge_models.py        # Model definitions + capabilities
├── forge_orchestrator.py  # Plan/build/deploy coordination
├── forge_pipeline.py      # WaveExecutor + ArtifactManager
├── forge_preview.py       # PreviewManager + Cloudflare Tunnel
├── forge_prompt.py        # Prompt utilities
├── forge_registry.py      # Agent definition registry
├── forge_schema.py        # JSON schema validation
├── forge_session.py       # Session persistence
├── forge_tasks.py         # Task CRUD + topo sort
├── forge_teams.py         # Team spawning
├── forge_verify.py        # BuildVerifier (L1-L3 checks)
├── forge_web.py           # Web dashboard
├── forge_audit.py         # JSONL audit trail
├── formations.py          # 8 formations + DAAO routing
├── model_router.py        # LLM provider adapters
├── prompt_builder.py      # System prompt construction
├── config.py              # Configuration + context windows
├── benchmark_expense_tracker.py  # E2E benchmark
├── demo_nova_e2e.py       # E2E demo script
├── challenge_build.py     # Challenge build runner
├── agents/                # 20 YAML agent definitions
├── schemas/               # 8 JSON schemas
├── templates/             # 4 app skeletons (flask-api, streamlit-dash, static-site, nova-chat)
├── tests/unit/            # 883 tests (37 test files)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## Conventions

- **Read before edit**: Always read files before modifying them
- **Syntax check after edit**: `python3 -c "import py_compile; py_compile.compile('FILE.py', doraise=True)"`
- **No docs unless asked**: Don't create README/docs files unprompted
- **Test after changes**: `pytest tests/ -v` to verify no regressions
- **Secrets**: Never commit credentials; load via `source ~/.secrets/hercules.env`

## Competition Context

Built for the **Amazon Nova AI Hackathon** (deadline: March 16, 2026). Key differentiator: V11's orchestration patterns (waves, formations, gates, artifacts, retries) are model-portable — same pipeline works with Nova, Gemini, or Claude via `--model` flag.

**Live demo**: forge.herakles.dev
