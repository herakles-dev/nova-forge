# Manual Interactive Test Protocol — Ctrl-C Pause & Benchmark Suite

## Pre-requisites

```bash
cd /home/hercules/nova-forge
source ~/.secrets/hercules.env
```

---

## Test 1: Ctrl-C Pause/Cancel During Build

### 1A. Basic Ctrl-C → Pause Menu → Cancel

1. Launch the interactive shell:
   ```bash
   python3 forge_cli.py
   ```
2. At the `forge>` prompt, type:
   ```
   /plan Build a todo app with Flask backend, SQLite database, and HTML frontend
   ```
3. Wait for planning to complete (creates spec.md + tasks.json).
4. Start the build:
   ```
   /build
   ```
5. **While the first task is running** (you'll see the spinner), press **Ctrl-C**.
6. **Expected**: The spinner changes to "Pausing after current operation..." and within a few seconds the pause menu appears:
   ```
   Build paused.  X/Y tasks completed, Z remaining.

   ? What would you like to do?
   > Resume build
     Cancel build
   ```
7. Select **Cancel build** (arrow down + Enter).
8. **Expected**: Build stops, summary shows paused count, hint says "Run /build again to resume."
9. Verify completed tasks kept their status:
   ```
   /status
   ```
   - Completed tasks should show "completed"
   - The interrupted task should show "pending" (not "failed" or "in_progress")

### 1B. Ctrl-C → Pause Menu → Resume

1. Start the build again:
   ```
   /build
   ```
2. Press **Ctrl-C** while a task is running.
3. In the pause menu, select **Resume build**.
4. **Expected**: Build continues from where it left off — already-completed tasks are skipped.
5. Let it finish. Verify all tasks complete.

### 1C. Ctrl-C During Wave Boundary

1. Plan a larger project with 5+ tasks that span multiple waves:
   ```
   /plan Build an expense tracker with Flask, SQLite, HTML/JS frontend, and Chart.js charts
   ```
2. `/build` and press Ctrl-C quickly after a wave completes (between waves).
3. **Expected**: Same pause menu appears at the wave boundary.

### 1D. Multiple Rapid Ctrl-C

1. During a build, press **Ctrl-C three times rapidly**.
2. **Expected**: No crash. Pause menu appears once (asyncio.Event.set() is idempotent).

### 1E. Ctrl-C in Pause Menu

1. During a build, press Ctrl-C to get the pause menu.
2. In the pause menu itself, press **Ctrl-C** again.
3. **Expected**: Treated as cancel — returns to `forge>` prompt cleanly.

### 1F. SIGINT Handler Restoration

1. After a build (whether completed, cancelled, or resumed), press **Ctrl-C** at the `forge>` prompt.
2. **Expected**: Normal prompt_toolkit Ctrl-C behavior (not the pause handler). The original handler was restored.

### 1G. Deferred Resume (/build again)

1. Start a build, Ctrl-C, cancel.
2. Run `/build` again.
3. **Expected**: Only pending/failed tasks are retried. Previously completed tasks are skipped. This is the existing retry path — verify it still works after the cancellation changes.

---

## Test 2: Benchmark Suite

### 2A. Single Model Run (nova-lite — fastest)

```bash
python3 benchmark_nova_models.py --model nova-lite -v
```

**Expected**:
- Per-task progress shown (because -v)
- Scorecard printed with letter grade and dimension breakdown
- Visual bars for each dimension
- Failed checks listed
- Results saved to benchmarks/

### 2B. Full Comparison (all 3 Nova models)

```bash
python3 benchmark_nova_models.py --all -v
```

**Expected**:
- Runs nova-lite, nova-pro, nova-premier sequentially
- Individual scorecards for each
- Comparison table at the end:
  - Grade, score, dimensions per model
  - Tasks, duration, cost, turns, retries
  - "Best overall" and "Best value" picks
- Results saved to `benchmarks/run_YYYYMMDD_HHMMSS.json` and `benchmarks/latest.json`

### 2C. Compare Against Previous Run

After running 2B at least once:

```bash
python3 benchmark_nova_models.py --all --compare benchmarks/latest.json -v
```

**Expected**: Delta section showing grade/score changes per model.

### 2D. Show Saved Results

```bash
python3 benchmark_nova_models.py --show benchmarks/latest.json
```

**Expected**: Displays stored results without re-running.

---

## Pass Criteria

| Test | Pass If |
|------|---------|
| 1A | Pause menu appears, cancel works, tasks revert to pending |
| 1B | Resume continues from pending, completed tasks skipped |
| 1C | Pause works between waves |
| 1D | No crash on multiple signals |
| 1E | Ctrl-C in menu = cancel |
| 1F | Normal Ctrl-C at prompt after build |
| 1G | /build retry skips completed tasks |
| 2A | Scorecard prints with grade for nova-lite |
| 2B | All 3 models scored, comparison table printed |
| 2C | Delta comparison works |
| 2D | Saved results display correctly |

## Logging Issues

If a test fails, note:
- Which model was active
- Exact error message or unexpected behavior
- Whether the `forge>` prompt was recoverable (or required restart)
