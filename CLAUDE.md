# Nova Forge

> Open-source agent orchestration framework. V11's proven patterns, any LLM, pure Python.
> **Amazon Nova AI Hackathon** — deadline March 16, 2026. Live at forge.herakles.dev.

## Session Protocol

**Every session uses V11 spec-driven workflow.** No ad-hoc implementation.

1. **Start**: `/v11` — detect state, check tasks, show dashboard
2. **Plan**: Create tasks via `TaskCreate` with sprint metadata before writing code
3. **Execute**: `TaskUpdate(status="in_progress")` before work, `TaskUpdate(status="completed")` after
4. **Verify**: `pytest tests/ -x -q` after changes (1051 tests must pass), syntax-check modified files
5. **Track**: Update sprint_history.md and MEMORY.md after completing a sprint

**Never implement without tasks.** Even quick fixes get a task for traceability.

## Project Status

**Phase: Submission ready.** Core framework complete (19 sprints, ~30,000 LOC, 35 modules, 1,670 tests).

### Benchmark Scores (Sprint 19, 2026-03-16)
| Model | Grade | Score | Time | Turns |
|-------|-------|-------|------|-------|
| Nova Lite (32K) | S | 100% | 144s | 40 |
| Nova Pro (300K) | S | 100% | 167s | 39 |
| Nova Premier (1M) | S | 100% | 1110s | 33 |

## Commands

```bash
# Tests
pytest tests/ -x -q                                      # Quick (1670 tests)
pytest tests/unit/test_pipeline.py -v                    # Single module
python3 -c "import py_compile; py_compile.compile('FILE.py', doraise=True)"  # Syntax check

# Interactive CLI
python3 forge_cli.py

# Non-interactive
python3 forge.py plan "description" --model nova-lite
python3 forge.py build --model gemini-flash
python3 forge.py preview
python3 forge.py deploy --domain app.example.com

# Benchmarks
source ~/.secrets/hercules.env                           # REQUIRED for AWS
python3 benchmark_nova_models.py --model nova-lite -v    # Single model
python3 benchmark_nova_models.py --all                   # All 3 Nova models
python3 benchmark_nova_models.py --history               # Run history trend
python3 benchmark_nova_models.py --diff-checks benchmarks/runs/2026-03/run_PREV.json

# Website
cd web/ && python3 -m http.server 8160                   # Local preview
# Production: Docker container on port 8160, nginx at forge.herakles.dev
```

## Tech Stack

- **Language**: Python 3.11+ (pure Python, no JS/TS)
- **CLI**: Click (forge.py) + custom interactive shell (forge_cli.py)
- **LLM Providers**: AWS Bedrock (Nova Lite/Pro/Premier), OpenRouter (Gemini), Anthropic (Claude)
- **UI**: Rich (live progress, tables, panels, spinners)
- **Testing**: pytest (1,670 tests, 50 test files)
- **Deployment**: Docker + nginx + SSL + Cloudflare Tunnels
- **Website**: Static HTML/CSS/JS at web/ (forge.herakles.dev, port 8160)

## Architecture

```
User Goal -> Interview (3-phase deep planning)
          -> ForgeAgent (Planning) -> spec.md + tasks.json
          -> WaveExecutor (Parallel Agents per wave) -> Built Project
          -> GateReviewer (Adversarial quality check) -> PASS/FAIL
          -> Preview (Cloudflare Tunnel) -> Shareable URL
          -> ForgeDeployer (Docker + nginx) -> Live URL
```

### Key Architecture Decisions
- **3-tier prompts**: Slim (<=32K), Focused (<=1M), Full (>1M) — model-appropriate system prompts
- **Bedrock timeout**: 300s read_timeout via botocore.Config (Premier needs ~100s/inference)
- **Pre-seeded context**: Dependent tasks get upstream file content injected (saves 2-3 turns)
- **JSON recovery**: `_recover_json()` handles malformed LLM output (trailing commas, truncation, fences)
- **Autonomy A0-A5**: 6-level trust system with auto-escalation cap at A3
- **Context compaction**: Budget-based (60%/65%), preserves toolUse/toolResult pairs
- **Adaptive turn budgets**: `compute_turn_budget()` scales by file count (1-file: 15 soft/19 hard)
- **Convergence detector**: `ConvergenceTracker` disables writes after 5 idle turns
- **Verify phase budget**: Capped at soft//4 turns, prevents endless read-back loops

## Module Map (35 files, ~30,000 LOC)

| Module | LOC | Purpose |
|--------|-----|---------|
| forge_cli.py | 4689 | Interactive shell, deep planning interview, all /commands |
| forge_agent.py | 2004 | Tool-use loop, 12 tools, ConvergenceTracker, verify phase, auto-verify |
| forge_hooks_impl.py | 1057 | 12 hook implementations |
| forge_guards.py | 1030 | RiskClassifier + PathSandbox + AutonomyManager (A0-A5) |
| forge_assistant.py | 1014 | Smart assistant — skill detection, interview, scope summary |
| forge_orchestrator.py | 999 | Plan/build/deploy orchestration + JSON recovery |
| forge_preview.py | 996 | PreviewManager — 14-stack detection + Cloudflare Tunnel |
| prompt_builder.py | 940 | 3-tier prompt system + autonomy-aware + previewability |
| formations.py | 907 | 11 formations + DAAO routing + 5 tool policies |
| model_router.py | 900 | 3 provider adapters (Bedrock 300s timeout, OpenAI, Anthropic) |
| forge_pipeline.py | 870 | WaveExecutor + ArtifactManager + GateReviewer |
| forge.py | 740 | Click CLI commands (14 commands) |
| forge_display.py | 685 | Rich live UI, brand-themed spinners and progress |
| forge_tasks.py | 643 | TaskStore + topological sort (Kahn's algorithm) |
| forge_index.py | 634 | ProjectIndex, export/import scanning, dependency graph |
| forge_session.py | 625 | Session lifecycle + persistence |
| forge_verify.py | 1072 | BuildVerifier — L1 static, L2 server, L3 browser checks |
| forge_deployer.py | 468 | Docker + nginx + SSL deployment |
| forge_web.py | 372 | Web dashboard + docs chat API |
| config.py | 366 | Model configs, context windows, adaptive turn budgets |
| forge_teams.py | 318 | Multi-agent team spawning |
| forge_memory.py | 309 | Persistent memory system |
| forge_migrate.py | 295 | Legacy version migration (V5-V10 → Forge) |
| forge_models.py | 294 | Model definitions and capability profiles |
| forge_hooks.py | 293 | Hook system (V11 compatible) |
| forge_prompt.py | 282 | Selection menus, Escape-to-cancel, brand colors |
| forge_compliance.py | 280 | 10-gate compliance checker |
| forge_registry.py | 276 | Agent definition registry (20 agents) |
| forge_competition.py | 253 | Competition readiness validator (8 checks) |
| forge_livereload.py | 252 | LiveReloadServer for build previews |
| forge_comms.py | 233 | BuildContext, FileClaim, AgentAnnouncement |
| forge_audit.py | 224 | JSONL audit trail |
| forge_theme.py | 189 | Design tokens, brand palette, console, visual helpers |
| forge_schema.py | 134 | 8 JSON schema validators |

### Benchmark & Demo Scripts

| Module | LOC | Purpose |
|--------|-----|---------|
| benchmark_nova_models.py | 2404 | Model benchmark — auto-save, regressions, pre-seeded context, aligned to CLI |
| benchmarks/benchmark_store.py | 569 | BenchmarkStore, metadata, regressions, diffs, hints |
| benchmark_expense_tracker.py | 835 | E2E benchmark (legacy) |
| demo_nova_e2e.py | 564 | E2E demo script |
| challenge_build.py | 274 | Challenge build runner |

### Website (forge.herakles.dev)

| File | LOC | Purpose |
|------|-----|---------|
| web/index.html | 802 | Main page — hero, quickstart, ask-nova chat, try-it prompts, architecture |
| web/style.css | 1381 | Design system v4 — brand palette, responsive, dark theme |
| web/app.js | 343 | Interactive UI — chat, nav, asciinema player integration |
| web/demo.cast | — | Asciinema recording (NDJSON) for terminal demo |

## File Layout

```
nova-forge/
├── bin/forge, bin/herakles     # CLI entry points (symlinked)
├── forge.py                    # Click CLI (14 commands)
├── forge_cli.py                # Interactive shell (main file, 3604 LOC)
├── forge_agent.py              # Core agent loop + 12 tools
├── forge_assistant.py          # Smart assistant (skill, recommendations)
├── model_router.py             # LLM provider adapters (Bedrock/OpenAI/Anthropic)
├── prompt_builder.py           # 3-tier system prompt (slim/focused/full)
├── config.py                   # Configuration + context windows
├── forge_orchestrator.py       # Plan/build/deploy coordination
├── forge_pipeline.py           # WaveExecutor + ArtifactManager
├── forge_guards.py             # Security (risk, sandbox, autonomy)
├── forge_preview.py            # 14-stack preview + Cloudflare Tunnel
├── forge_deployer.py           # Docker + nginx deployment
├── forge_verify.py             # BuildVerifier (L1-L3 checks)
├── forge_display.py            # Rich live UI
├── forge_theme.py              # Brand design tokens
├── formations.py               # 11 formations + DAAO routing
├── forge_tasks.py              # TaskStore + topo sort
├── forge_*.py                  # (14 more modules — see Module Map)
├── benchmark_nova_models.py    # Model benchmark suite
├── benchmarks/                 # Benchmark infrastructure + run history
├── agents/                     # 20 YAML agent definitions
├── schemas/                    # 8 JSON schemas
├── templates/                  # 4 app skeletons
├── scripts/                    # Demo recording tools
├── tests/unit/                 # 1,670 tests (50 test files)
├── web/                        # Website (forge.herakles.dev)
│   ├── index.html, style.css, app.js, demo.cast
├── Dockerfile, docker-compose.yml, requirements.txt
└── CLAUDE.md                   # This file
```

## Conventions

- **V11 workflow**: Always use TaskCreate/TaskUpdate for tracking. No untracked work.
- **Read before edit**: Always read files before modifying them
- **Syntax check after edit**: `python3 -c "import py_compile; py_compile.compile('FILE.py', doraise=True)"`
- **Test after changes**: `pytest tests/ -x -q` (1,670 tests, all must pass)
- **No docs unless asked**: Don't create README/docs files unprompted
- **Secrets**: Never commit credentials; load via `source ~/.secrets/hercules.env`
- **Benchmark after model changes**: `python3 benchmark_nova_models.py --all -v` to verify no regressions

## Sprint History (Sprints 5-16)

| Sprint | Date | Key Deliverable |
|--------|------|-----------------|
| 5 | 03-10 | 12 tools, parallel waves, artifact handoffs, gate review, autonomy, streaming |
| 6 | 03-11 | Multi-agent comms, preview/verification, 8 interface bugs fixed |
| 7 | 03-11 | Light model optimization (SLIM_TOOLS, slim prompts, smart compaction) |
| 8 | 03-11 | Agent intelligence (multi-lang verify, read-before-edit, completeness) |
| 9 | 03-12 | Assistant layer, A0-A5 autonomy, adaptive UX |
| 10 | 03-12 | Agent fine-tuning (file ownership, write enforcement, 883→1000 tests) |
| 11 | 03-13 | 14-stack preview, benchmark infrastructure, 1000 tests |
| 12 | 03-13 | Deep planning interview (3-phase, 8 categories, scope summary) |
| 13 | 03-13 | CLI visual upgrade, JSON recovery, 3-tier prompts, Pro C→S, Premier C→A |
| 14 | 03-13 | Premier S tier (pre-seeded context, Bedrock timeout, removed turn caps) |
| 15 | 03-14 | Preview resilience (3x retry, health monitor), agent circuit breaker, /health + /competition commands, website stats fix, todo-app benchmark scenario |
| 16 | 03-14 | 2 new formations (recovery, all-hands-planning), agent self-correction, demo recording script, benchmark resilience checks |
| 17 | 03-15 | Agent loop convergence: adaptive turn budgets, ConvergenceTracker, verify phase budget, escalation budget reduction, hard limit tightening, benchmark aligned to CLI path, completeness directive |
| 18 | 03-15 | 5-agent architecture review (78 issues found), fix _auto_verify shlex bug, prompt contradictions resolved, agent loop hardening (6 fixes), preview 127.0.0.1, artifact 4KB threshold |
| 19 | 03-16 | 8-agent test swarm (1108→1670 tests), Premier max_tokens 16384 + stop_reason detection, all 3 models S 100%, submission prep |

Full details: memory/sprint_history.md
