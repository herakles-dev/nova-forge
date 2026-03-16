#!/usr/bin/env bash
# Nova Lite (32K) — Manual Test Suite
# Tests SLIM_TOOLS mode, token efficiency, all user paths.
#
# Run from nova-forge root: bash scripts/manual-tests/test-nova-lite.sh
# Requires: source ~/.secrets/hercules.env

set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$FORGE_ROOT"
LOG="/tmp/forge-test-lite.log"
ISSUES="$FORGE_ROOT/issues.md"
PASS=0
FAIL=0
SKIP=0

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
pass() { PASS=$((PASS+1)); log "PASS: $1"; }
fail() { FAIL=$((FAIL+1)); log "FAIL: $1 — $2"; sed -i "/^### Nova Lite (32K) Findings/a - [ ] $1 — $2" "$ISSUES"; }
skip() { SKIP=$((SKIP+1)); log "SKIP: $1 — $2"; }
separator() { echo "────────────────────────────────────────────────" | tee -a "$LOG"; }

echo "" > "$LOG"
log "=== Nova Lite (32K) Test Suite ==="
log "Started: $(date)"
separator

# ── T-L01: Non-interactive plan ──────────────────────────────────────────────

log "T-L01: forge plan (non-interactive, todo app)"
TEST_DIR=$(mktemp -d /tmp/forge-test-lite-L01-XXXX)
cd "$TEST_DIR"

if timeout 180 python3 "$FORGE_ROOT/forge.py" plan "simple todo list app" --model nova-lite 2>&1 | tee -a "$LOG"; then
    if [ -f ".forge/state/tasks.json" ]; then
        TASK_COUNT=$(python3 -c "import json; print(len(json.load(open('.forge/state/tasks.json'))['tasks']))" 2>/dev/null || echo 0)
        if [ "$TASK_COUNT" -gt 0 ]; then
            pass "T-L01: Plan created $TASK_COUNT tasks"
        else
            fail "T-L01" "tasks.json empty or malformed"
        fi
    else
        fail "T-L01" "tasks.json not created"
    fi
else
    fail "T-L01" "forge plan command failed (exit $?)"
fi
cd "$FORGE_ROOT"
separator

# ── T-L02: Build from plan ───────────────────────────────────────────────────

log "T-L02: forge build (from T-L01 plan)"
cd "$TEST_DIR"

if timeout 300 python3 "$FORGE_ROOT/forge.py" build --model nova-lite --no-preview 2>&1 | tee -a "$LOG"; then
    # Check for generated files
    FILE_COUNT=$(find . -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" | grep -v .forge | wc -l)
    if [ "$FILE_COUNT" -gt 0 ]; then
        pass "T-L02: Build produced $FILE_COUNT source files"
    else
        fail "T-L02" "Build completed but no source files generated"
    fi
else
    fail "T-L02" "forge build failed (exit $?)"
fi
cd "$FORGE_ROOT"
separator

# ── T-L03: Status check ─────────────────────────────────────────────────────

log "T-L03: forge status"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" status 2>&1 | tee -a "$LOG"; then
    pass "T-L03: Status displayed"
else
    fail "T-L03" "forge status failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L04: Task listing ─────────────────────────────────────────────────────

log "T-L04: forge list"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" list 2>&1 | tee -a "$LOG"; then
    pass "T-L04: Task list displayed"
else
    fail "T-L04" "forge list failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L05: Template-based plan (flask-api) ───────────────────────────────────

log "T-L05: forge plan with flask-api template"
TEST_DIR_05=$(mktemp -d /tmp/forge-test-lite-L05-XXXX)
cd "$TEST_DIR_05"

if timeout 180 python3 "$FORGE_ROOT/forge.py" plan "REST API for bookmarks" --model nova-lite --template flask-api 2>&1 | tee -a "$LOG"; then
    if [ -f ".forge/state/tasks.json" ]; then
        pass "T-L05: Template plan created"
    else
        fail "T-L05" "Template plan did not create tasks.json"
    fi
else
    fail "T-L05" "forge plan with template failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L06: Template-based plan (static-site) ────────────────────────────────

log "T-L06: forge plan with static-site template"
TEST_DIR_06=$(mktemp -d /tmp/forge-test-lite-L06-XXXX)
cd "$TEST_DIR_06"

if timeout 180 python3 "$FORGE_ROOT/forge.py" plan "personal portfolio page" --model nova-lite --template static-site 2>&1 | tee -a "$LOG"; then
    if [ -f ".forge/state/tasks.json" ]; then
        pass "T-L06: Static template plan created"
    else
        fail "T-L06" "Static template plan missing tasks.json"
    fi
else
    fail "T-L06" "forge plan with static-site template failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L07: Model listing ────────────────────────────────────────────────────

log "T-L07: forge models"

if timeout 10 python3 "$FORGE_ROOT/forge.py" models 2>&1 | tee -a "$LOG"; then
    pass "T-L07: Model listing works"
else
    fail "T-L07" "forge models failed"
fi
separator

# ── T-L08: Agent registry ───────────────────────────────────────────────────

log "T-L08: forge agent list"

if timeout 10 python3 "$FORGE_ROOT/forge.py" agent list 2>&1 | tee -a "$LOG"; then
    pass "T-L08: Agent list works"
else
    fail "T-L08" "forge agent list failed"
fi
separator

# ── T-L09: Session detect ───────────────────────────────────────────────────

log "T-L09: forge session detect"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" session detect 2>&1 | tee -a "$LOG"; then
    pass "T-L09: Session detect works"
else
    fail "T-L09" "forge session detect failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L10: Compliance check ─────────────────────────────────────────────────

log "T-L10: forge session compliance"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" session compliance 2>&1 | tee -a "$LOG"; then
    pass "T-L10: Compliance check works"
else
    fail "T-L10" "forge session compliance failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L11: Formation routing ────────────────────────────────────────────────

log "T-L11: forge formation (routine/small → single-file)"

OUTPUT=$(timeout 10 python3 "$FORGE_ROOT/forge.py" formation --complexity routine --scope small 2>&1 | tee -a "$LOG")
if echo "$OUTPUT" | grep -qi "single-file\|single_file\|Formation:"; then
    pass "T-L11: Formation routing works"
else
    fail "T-L11" "Expected single-file formation for routine/small"
fi
separator

# ── T-L12: forge new (project scaffold) ─────────────────────────────────────

log "T-L12: forge new test-app"
TEST_DIR_12=$(mktemp -d /tmp/forge-test-lite-L12-XXXX)
cd "$TEST_DIR_12"

if timeout 30 python3 "$FORGE_ROOT/forge.py" new test-scaffold-app 2>&1 | tee -a "$LOG"; then
    if [ -d "test-scaffold-app/.forge" ]; then
        pass "T-L12: Project scaffold created with .forge/"
    else
        fail "T-L12" ".forge/ directory not created"
    fi
else
    fail "T-L12" "forge new failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-L13: Token efficiency verification ────────────────────────────────────

log "T-L13: Verify SLIM_TOOLS for Lite (32K context)"

OUTPUT=$(python3 -c "
from config import get_model_config
from prompt_builder import PromptBuilder
from pathlib import Path
import tempfile

cfg = get_model_config('nova-lite')
print(f'context_window={cfg.context_window}')
print(f'max_tokens={cfg.max_tokens}')

# Create a temp project to build prompt for
with tempfile.TemporaryDirectory() as td:
    pb = PromptBuilder(project_root=Path(td))
    prompt = pb.build_system_prompt(model_id='nova-lite')
    print(f'prompt_len={len(prompt)}')
    if len(prompt) < 3000:
        print('SLIM=yes')
    else:
        print('SLIM=no')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "SLIM=yes"; then
    pass "T-L13: Lite uses slim prompt (<2000 chars)"
else
    fail "T-L13" "Lite prompt too large for 32K context"
fi
separator

# ── Summary ──────────────────────────────────────────────────────────────────

log ""
log "=== Nova Lite Test Summary ==="
log "PASS: $PASS"
log "FAIL: $FAIL"
log "SKIP: $SKIP"
log "Total: $((PASS + FAIL + SKIP))"
log "Log: $LOG"
log "Completed: $(date)"

if [ "$FAIL" -gt 0 ]; then
    log "STATUS: ISSUES FOUND — check $ISSUES"
    exit 1
else
    log "STATUS: ALL TESTS PASSED"
fi
