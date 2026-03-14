# Nova Model Benchmark Protocol

## Purpose

Standardized testing protocol for measuring Nova Lite, Pro, and Premier
as agentic coders inside Nova Forge. Run after every tuning change to
track regressions and improvements. Results are auto-saved with git metadata,
regression detection, and optimization hints.

## Quick Start

```bash
source ~/.secrets/hercules.env

# Run all 3 Nova models (takes ~5-8 min) — results auto-saved
python3 benchmark_nova_models.py --all

# Run a single model
python3 benchmark_nova_models.py --model nova-lite

# Run with context (who triggered it and why)
python3 benchmark_nova_models.py --model nova-lite --trigger "prompt_builder refactor" --name "post-sprint-11"

# Compare against a previous run
python3 benchmark_nova_models.py --all --compare benchmarks/latest.json

# Review saved results
python3 benchmark_nova_models.py --show benchmarks/latest.json

# View run history
python3 benchmark_nova_models.py --history
python3 benchmark_nova_models.py --history --history-model nova-lite --history-limit 10

# Check-level diff against a previous run
python3 benchmark_nova_models.py --diff-checks benchmarks/runs/2026-03/run_20260312_140000.json

# Run without saving (dry run)
python3 benchmark_nova_models.py --model nova-lite --no-save
```

## The Benchmark

**Task**: Build an Expense Tracker (Flask + SQLite + vanilla JS)

- 5 tasks, 3 waves, 5 files
- Wave 0: models.py (database CRUD)
- Wave 1: api.py + index.html (depend on models.py) — parallel
- Wave 2: app.js + style.css (depend on waves 0+1) — parallel

This tests the core agentic coding dimensions:
1. Can the model follow a spec and produce working files?
2. Can downstream agents correctly consume upstream outputs?
3. Does the model hallucinate APIs that don't exist?
4. Does the generated code actually run?
5. How efficient is the model (turns, tokens, retries)?

## Rating System

### Letter Grades

| Grade | Score | Meaning |
|-------|-------|---------|
| **S** | 95-100% | Production-grade. Ship it. |
| **A** | 85-94% | Strong. Minor polish needed. |
| **B** | 75-84% | Functional. Some gaps to address. |
| **C** | 60-74% | Partial. Significant issues. |
| **D** | 40-59% | Broken. Major failures. |
| **F** | <40% | Non-functional. |

### Dimensions (weighted)

| Dimension | Weight | What it measures |
|-----------|--------|------------------|
| **Task Completion** | 30% | Files created, not stubs, all tasks passed |
| **Code Quality** | 25% | Syntax, correct patterns (sqlite3 not ORM), HTML/JS structure |
| **Interface Fidelity** | 20% | Import/export compatibility, no hallucinated APIs |
| **Runtime Viability** | 15% | Server starts, endpoints respond, routes defined |
| **Efficiency** | 10% | Turns, retries, duration, cost |

### Overall Score

```
overall = Σ (dimension_score × dimension_weight)
grade   = score_to_grade(overall)
```

## When to Run

- After modifying `prompt_builder.py` (system prompts)
- After changing `forge_agent.py` (tool execution loop)
- After updating model configs or context window handling
- After any change to the build pipeline (`forge_cli.py _cmd_build`)
- Before and after hackathon submission

## CLI Flags

| Flag | Description |
|------|-------------|
| `--model MODEL` | Run a single model (e.g. `nova-lite`) |
| `--all` | Run all 3 Nova models |
| `--verbose`, `-v` | Show per-task progress |
| `--report` | Legacy flag (no-op — results always saved now) |
| `--compare PATH` | Show delta from a previous run JSON |
| `--show PATH` | Display results from a saved JSON file |
| `--history` | Show grade/score trend table for all saved runs |
| `--history-model MODEL` | Filter history to one model |
| `--history-limit N` | Limit history rows (default 20) |
| `--diff-checks PATH` | Show per-check state changes vs a previous run |
| `--no-save` | Skip saving results (dry run) |
| `--trigger TEXT` | Record why this run was triggered |
| `--name TEXT` | Human-readable run name |

## Post-Run Pipeline

Every run (unless `--no-save`) automatically executes:

```
1. Collect metadata (git commit, branch, dirty state, python version, hashes)
2. Load previous latest run (before symlink update)
3. Save current run to benchmarks/runs/YYYY-MM/run_TIMESTAMP.json
4. Update symlinks: latest.json + latest_{model}.json
5. Append to runs/runs.jsonl index
6. Auto-compare vs previous:
   a. Regression alerts (grade drops, score drops >5%, server flips)
   b. Check-level diffs (individual pass↔fail changes)
7. Generate optimization hints (dimension scores below threshold)
8. Append entry to CHANGELOG.md
```

## Directory Structure

```
benchmarks/
    __init__.py                     # Package init
    benchmark_store.py              # Storage, analysis, hints engine
    PROTOCOL.md                     # This file
    CHANGELOG.md                    # Auto-maintained run changelog
    latest.json -> runs/2026-03/... # Symlink to most recent run
    latest_nova-lite.json -> ...    # Per-model symlink
    latest_nova-pro.json -> ...
    runs/
        runs.jsonl                  # Append-only index (one JSON line per run)
        2026-03/
            run_20260313_143022.json
            run_20260313_160500.json
```

## JSON Schema v2

All new runs use schema v2 (backward compatible with v1):

```json
{
  "schema_version": 2,
  "timestamp": "2026-03-13T14:30:22.123456",
  "run_name": "post-sprint-11",
  "metadata": {
    "git_commit": "2e5b8c1",
    "git_branch": "main",
    "git_dirty": false,
    "git_changed_files": [],
    "python_version": "3.11.10",
    "platform": "Linux-6.1.0-39-amd64-x86_64-with-glibc2.36",
    "spec_hash": "a1b2c3d4e5f6",
    "system_prompt_hash": "f6e5d4c3b2a1",
    "models_run": ["nova-lite", "nova-pro"],
    "trigger": "prompt_builder refactor"
  },
  "summary": {
    "models": ["nova-lite", "nova-pro"],
    "grades": {"nova-lite": "A", "nova-pro": "S"},
    "scores": {"nova-lite": 88.5, "nova-pro": 95.2},
    "duration_total": 178.3,
    "cost_total": 0.0234
  },
  "results": [
    {
      "model_alias": "nova-lite",
      "model_id": "bedrock/us.amazon.nova-lite-v1:0",
      "overall_score": 88.5,
      "grade": "A",
      "dimension_scores": { ... },
      "checks": [ {"name": "...", "dimension": "...", "passed": true, "detail": "...", "weight": 1.0} ],
      "task_results": [ ... ],
      "server_ok": true,
      ...
    }
  ]
}
```

**v1 backward compatibility**: Old runs without `schema_version` are loaded as-is.
The store reads them correctly — they just lack `metadata` and `summary` fields.

## Regression Detection

Automatic comparison against the previous run detects:

| Condition | Severity | Example |
|-----------|----------|---------|
| Grade drops 1 level | warning | A → B |
| Grade drops 2+ levels | critical | A → D |
| Overall score drops > 5% | warning | 88% → 82% |
| Dimension score drops > 10% | warning | Interface Fidelity 80% → 65% |
| Server was OK, now fails | critical | server_ok: true → false |

Regressions are printed in red after each run and recorded in CHANGELOG.md.

## Optimization Hints

When a dimension score falls below its threshold, actionable suggestions are shown:

| Dimension | Threshold | Key Files |
|-----------|-----------|-----------|
| Task Completion | < 70% | `prompt_builder.py`, `forge_agent.py` |
| Code Quality | < 70% | `prompt_builder.py`, `forge_agent.py` |
| Interface Fidelity | < 60% | `prompt_builder.py`, `forge_cli.py` |
| Runtime Viability | < 60% | `forge_preview.py`, `forge_agent.py`, `forge_verify.py` |
| Efficiency | < 60% | `config.py`, `forge_agent.py`, `forge_models.py` |

Hints use the worst score across all models in the run to surface the most
impactful improvements.

## CHANGELOG Format

`CHANGELOG.md` is auto-maintained. Each entry includes:

```markdown
## 2026-03-13 — post-sprint-11

| **nova-lite**: A (88%) | **nova-pro**: S (95%) |

Git: `2e5b8c1` on `main`
Trigger: prompt_builder refactor
Duration: 178s | Cost: $0.0234

**Regressions:**
- ! nova-lite score: 92.0% -> 88.0%

---
```

Newest entries appear at the top. Never manually edit — the benchmark suite manages it.

## Interpreting Results

### Healthy baseline (as of Sprint 10)

| Model | Expected Grade | Expected Score | Notes |
|-------|---------------|----------------|-------|
| Nova Lite (32K) | B+ | 78-85% | Limited by context window; may need retries |
| Nova Pro (300K) | A | 85-92% | Strong all-rounder |
| Nova Premier (1M) | A+ | 90-95% | Best quality, higher cost |

### Red flags

- **Grade drops by 2+ levels** between runs → regression introduced
- **Interface Fidelity < 60%** → model is hallucinating APIs
- **Task Completion < 70%** → files aren't being created (prompt issue)
- **Efficiency F** → model is looping/retrying excessively
- **Runtime Viability 0%** → generated code doesn't even parse

### Tracking improvement

Use `--history` for the full trend table:

```bash
python3 benchmark_nova_models.py --history
```

```
  Date         Name                  nova-lite    nova-pro  nova-premier
  -----------------------------------------------------------------------
  2026-03-13   post-sprint-11         A   88%      S   95%      S   96%
  2026-03-12   baseline               B   78%      A   88%      A   92%
```

Or `--compare` for detailed delta against a specific run:

```bash
python3 benchmark_nova_models.py --all --compare benchmarks/runs/2026-03/run_20260312_140000.json
```

## Extending the Benchmark

To add a new check:

1. Add a `CheckResult` to the appropriate dimension in `run_single_model()`
2. Set `weight` to control its influence (1.0 = standard, 2.0 = important)
3. Run the benchmark and verify the new check appears in the scorecard

To add a new benchmark task (beyond Expense Tracker):

1. Create a new spec + tasks definition
2. Follow the same pattern: setup → build → verify → score
3. Results from different benchmarks can be compared by grade

To add a new optimization hint:

1. Add an entry to `_HINT_MAP` in `benchmarks/benchmark_store.py`
2. Include the threshold score, suggestion text, and relevant file paths
