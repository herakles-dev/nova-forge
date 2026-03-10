# Nova Forge

> Open-source agent orchestration framework. V11's proven patterns, any LLM, pure Python.

## Tech Stack

- **Language**: Python 3.11+ (pure Python, no JS/TS)
- **CLI**: Click (forge.py) + custom interactive shell (forge_cli.py)
- **LLM Providers**: AWS Bedrock (Nova), OpenRouter (Gemini), Anthropic (Claude)
- **UI**: Rich (live progress, tables, panels, spinners)
- **Testing**: pytest (303 tests in tests/unit/)
- **Deployment**: Docker + nginx + SSL + Cloudflare Tunnels
- **Dependencies**: boto3, openai, anthropic, flask, rich, click, pyyaml, jsonschema

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

## Module Map (25 files, 13,719 LOC)

| Module | LOC | Purpose |
|--------|-----|---------|
| forge_cli.py | ~2400 | Interactive shell, interview, all /commands |
| forge_agent.py | ~800 | Tool-use loop + AgentEvent system |
| forge_pipeline.py | ~700 | WaveExecutor + ArtifactManager + GateReviewer |
| forge_display.py | ~430 | Rich live UI (BuildDisplay) |
| forge_guards.py | ~650 | RiskClassifier + PathSandbox + AutonomyManager |
| forge_hooks.py | ~400 | Hook system (V11 compatible) |
| forge_hooks_impl.py | ~500 | 12 hook implementations |
| model_router.py | ~600 | 3 provider adapters (Bedrock, OpenAI, Anthropic) |
| forge_tasks.py | ~400 | TaskStore + topological sort (Kahn's algorithm) |
| formations.py | ~500 | 8 formations + DAAO routing |
| prompt_builder.py | ~350 | 7-section prompt construction |
| forge_orchestrator.py | ~600 | Plan/build/deploy orchestration |
| forge_deployer.py | ~500 | Docker + nginx + SSL deployment |
| forge_web.py | ~400 | Web dashboard (forge.herakles.dev) |
| forge_session.py | ~300 | Session lifecycle + persistence |
| forge_compliance.py | ~350 | 10-gate compliance checker |
| forge_registry.py | ~300 | Agent definition registry (20 agents) |
| forge_schema.py | ~250 | 8 JSON schema validators |
| forge_audit.py | ~200 | JSONL audit trail |
| forge_migrate.py | ~200 | Legacy version migration |
| forge_teams.py | ~300 | Multi-agent team spawning |
| config.py | ~235 | Model configs + .forge/ init |
| forge.py | ~500 | Click CLI commands |

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

## File Layout

```
nova-forge/
├── bin/forge              # Bash wrapper -> forge_cli.py
├── bin/herakles           # Alias
├── forge.py               # Click CLI
├── forge_cli.py           # Interactive shell (2400 LOC, main file)
├── forge_agent.py         # Core agent loop
├── forge_display.py       # Rich live UI
├── model_router.py        # LLM provider adapters
├── forge_tasks.py         # Task CRUD + topo sort
├── forge_pipeline.py      # WaveExecutor + ArtifactManager
├── forge_guards.py        # Security (risk, sandbox, autonomy)
├── forge_hooks.py         # Hook system
├── forge_hooks_impl.py    # 12 hook implementations
├── formations.py          # 8 formations + DAAO
├── prompt_builder.py      # Prompt construction
├── forge_orchestrator.py  # Plan/build/deploy coordination
├── forge_deployer.py      # Docker deployment
├── forge_web.py           # Web dashboard
├── forge_session.py       # Session persistence
├── forge_compliance.py    # Compliance checker
├── forge_registry.py      # Agent registry
├── forge_schema.py        # JSON schema validation
├── forge_audit.py         # Audit trail
├── forge_migrate.py       # Version migration
├── forge_teams.py         # Team spawning
├── config.py              # Configuration
├── agents/                # 20 YAML agent definitions
├── schemas/               # 8 JSON schemas
├── templates/             # 4 app skeleton templates
├── tests/unit/            # 303 tests (19 test files)
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
