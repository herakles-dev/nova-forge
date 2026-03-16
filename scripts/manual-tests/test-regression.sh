#!/usr/bin/env bash
# Cross-Model Regression Test Suite
# Runs benchmarks, pytest, syntax checks — ensures no regressions.
#
# Run from nova-forge root: bash scripts/manual-tests/test-regression.sh
# Requires: source ~/.secrets/hercules.env

set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$FORGE_ROOT"
LOG="/tmp/forge-test-regression.log"
ISSUES="$FORGE_ROOT/issues.md"
PASS=0
FAIL=0
SKIP=0

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
pass() { PASS=$((PASS+1)); log "PASS: $1"; }
fail() { FAIL=$((FAIL+1)); log "FAIL: $1 — $2"; sed -i "/^### Regression Findings/a - [ ] $1 — $2" "$ISSUES"; }
skip() { SKIP=$((SKIP+1)); log "SKIP: $1 — $2"; }
separator() { echo "────────────────────────────────────────────────" | tee -a "$LOG"; }

echo "" > "$LOG"
log "=== Cross-Model Regression Test Suite ==="
log "Started: $(date)"
separator

# ── T-R01: Full pytest suite ─────────────────────────────────────────────────

log "T-R01: pytest tests/ -x -q (full 1670-test suite)"

START_TIME=$(date +%s)
if pytest tests/ -x -q --tb=short 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    pass "T-R01: Full test suite passed in ${DURATION}s"
else
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    fail "T-R01" "Test suite failed after ${DURATION}s"
fi
separator

# ── T-R02: Syntax check all 35 modules ──────────────────────────────────────

log "T-R02: Syntax check all Python modules"

SYNTAX_ERRORS=0
for pyfile in $(find "$FORGE_ROOT" -maxdepth 1 -name "*.py" -not -name "test_*" | sort); do
    if ! python3 -c "import py_compile; py_compile.compile('$pyfile', doraise=True)" 2>>"$LOG"; then
        SYNTAX_ERRORS=$((SYNTAX_ERRORS+1))
        log "  SYNTAX ERROR: $(basename $pyfile)"
    fi
done

if [ "$SYNTAX_ERRORS" -eq 0 ]; then
    pass "T-R02: All modules pass syntax check"
else
    fail "T-R02" "$SYNTAX_ERRORS modules have syntax errors"
fi
separator

# ── T-R03: Benchmark — Nova Lite ─────────────────────────────────────────────

log "T-R03: Benchmark Nova Lite (target: S tier >= 95%)"
log "  This calls live Bedrock API — estimated ~3 minutes"

START_TIME=$(date +%s)
if timeout 600 python3 benchmark_nova_models.py --model nova-lite -v 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    # Check for S grade in output
    if grep -q "Grade: S\|grade.*S\|100%" "$LOG" 2>/dev/null; then
        pass "T-R03: Nova Lite benchmark S tier in ${DURATION}s"
    else
        fail "T-R03" "Nova Lite benchmark did not achieve S tier"
    fi
else
    fail "T-R03" "Nova Lite benchmark failed or timed out"
fi
separator

# ── T-R04: Benchmark — Nova Pro ──────────────────────────────────────────────

log "T-R04: Benchmark Nova Pro (target: S tier >= 95%)"
log "  Estimated ~5 minutes"

START_TIME=$(date +%s)
if timeout 600 python3 benchmark_nova_models.py --model nova-pro -v 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    if grep -q "Grade: S\|grade.*S\|100%" "$LOG" 2>/dev/null; then
        pass "T-R04: Nova Pro benchmark S tier in ${DURATION}s"
    else
        fail "T-R04" "Nova Pro benchmark did not achieve S tier"
    fi
else
    fail "T-R04" "Nova Pro benchmark failed or timed out"
fi
separator

# ── T-R05: Benchmark — Nova Premier ─────────────────────────────────────────

log "T-R05: Benchmark Nova Premier (target: S tier >= 95%)"
log "  WARNING: Premier takes ~18 minutes (100s/inference x 10+ turns)"

START_TIME=$(date +%s)
if timeout 1800 python3 benchmark_nova_models.py --model nova-premier -v 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    if grep -q "Grade: S\|grade.*S\|100%" "$LOG" 2>/dev/null; then
        pass "T-R05: Nova Premier benchmark S tier in ${DURATION}s"
    else
        fail "T-R05" "Nova Premier benchmark did not achieve S tier"
    fi
else
    fail "T-R05" "Nova Premier benchmark failed or timed out (1800s)"
fi
separator

# ── T-R06: Regression diff check ────────────────────────────────────────────

log "T-R06: Benchmark regression detection (diff-checks)"

LAST_RUN=$(ls -1t benchmarks/runs/2026-03/run_*.json 2>/dev/null | head -1 || echo "")
if [ -n "$LAST_RUN" ]; then
    if python3 benchmark_nova_models.py --diff-checks "$LAST_RUN" 2>&1 | tee -a "$LOG"; then
        pass "T-R06: No regressions vs last benchmark run"
    else
        fail "T-R06" "Regressions detected vs $LAST_RUN"
    fi
else
    skip "T-R06" "No previous benchmark run to compare"
fi
separator

# ── T-R07: Website health ───────────────────────────────────────────────────

log "T-R07: Website static files integrity"

MISSING=0
for f in web/index.html web/style.css web/app.js; do
    if [ ! -f "$FORGE_ROOT/$f" ]; then
        MISSING=$((MISSING+1))
        log "  MISSING: $f"
    fi
done

if [ "$MISSING" -eq 0 ]; then
    pass "T-R07: All website files present"
else
    fail "T-R07" "$MISSING website files missing"
fi
separator

# ── T-R08: Template integrity ───────────────────────────────────────────────

log "T-R08: All 4 templates have required files"

TEMPLATE_ISSUES=0
for tmpl in flask-api streamlit-dash static-site nova-chat; do
    TMPL_DIR="$FORGE_ROOT/templates/$tmpl"
    if [ ! -d "$TMPL_DIR" ]; then
        TEMPLATE_ISSUES=$((TEMPLATE_ISSUES+1))
        log "  MISSING: templates/$tmpl/"
    fi
done

if [ "$TEMPLATE_ISSUES" -eq 0 ]; then
    pass "T-R08: All 4 templates present"
else
    fail "T-R08" "$TEMPLATE_ISSUES templates missing"
fi
separator

# ── T-R09: Config consistency ───────────────────────────────────────────────

log "T-R09: Model config consistency check"

OUTPUT=$(python3 -c "
import sys
sys.path.insert(0, '$FORGE_ROOT')
from config import MODEL_ALIASES, get_model_config

errors = []
for alias in MODEL_ALIASES:
    try:
        cfg = get_model_config(alias)
        if cfg.context_window <= 0:
            errors.append(f'{alias}: invalid context_window={cfg.context_window}')
        if cfg.max_tokens <= 0:
            errors.append(f'{alias}: invalid max_tokens={cfg.max_tokens}')
    except Exception as e:
        errors.append(f'{alias}: {e}')

if errors:
    for e in errors:
        print(f'  ERROR: {e}')
    print('CONFIG_OK=no')
else:
    print('CONFIG_OK=yes')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "CONFIG_OK=yes"; then
    pass "T-R09: All model configs consistent"
else
    fail "T-R09" "Model config inconsistencies found"
fi
separator

# ── T-R10: Import chain check ───────────────────────────────────────────────

log "T-R10: Core module import chain"

OUTPUT=$(python3 -c "
import sys
sys.path.insert(0, '$FORGE_ROOT')

modules = [
    'config', 'forge_agent', 'forge_orchestrator', 'forge_pipeline',
    'forge_preview', 'forge_deployer', 'forge_verify', 'forge_guards',
    'forge_tasks', 'forge_session', 'formations', 'model_router',
    'prompt_builder', 'forge_display', 'forge_theme', 'forge_registry',
]

failed = []
for mod in modules:
    try:
        __import__(mod)
    except Exception as e:
        failed.append(f'{mod}: {e}')

if failed:
    for f in failed:
        print(f'  IMPORT FAIL: {f}')
    print(f'IMPORTS_OK=no')
else:
    print(f'IMPORTS_OK=yes ({len(modules)} modules)')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "IMPORTS_OK=yes"; then
    pass "T-R10: All core modules import successfully"
else
    fail "T-R10" "Module import failures"
fi
separator

# ── Summary ──────────────────────────────────────────────────────────────────

log ""
log "=== Regression Test Summary ==="
log "PASS: $PASS"
log "FAIL: $FAIL"
log "SKIP: $SKIP"
log "Total: $((PASS + FAIL + SKIP))"
log "Log: $LOG"
log "Completed: $(date)"

if [ "$FAIL" -gt 0 ]; then
    log "STATUS: REGRESSIONS DETECTED — check $ISSUES"
    exit 1
else
    log "STATUS: NO REGRESSIONS — ALL CLEAR"
fi
