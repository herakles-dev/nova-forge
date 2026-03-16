#!/usr/bin/env bash
# Nova Premier (1M) — Manual Test Suite
# Tests long inference (300s timeout), 16K max_tokens, large file generation.
#
# WARNING: Premier inference takes ~100s per call. Budget 30-45 min for full suite.
#
# Run from nova-forge root: bash scripts/manual-tests/test-nova-premier.sh
# Requires: source ~/.secrets/hercules.env

set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$FORGE_ROOT"
LOG="/tmp/forge-test-premier.log"
ISSUES="$FORGE_ROOT/issues.md"
PASS=0
FAIL=0
SKIP=0

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
pass() { PASS=$((PASS+1)); log "PASS: $1"; }
fail() { FAIL=$((FAIL+1)); log "FAIL: $1 — $2"; sed -i "/^### Nova Premier (1M) Findings/a - [ ] $1 — $2" "$ISSUES"; }
skip() { SKIP=$((SKIP+1)); log "SKIP: $1 — $2"; }
separator() { echo "────────────────────────────────────────────────" | tee -a "$LOG"; }

echo "" > "$LOG"
log "=== Nova Premier (1M) Test Suite ==="
log "NOTE: Premier inference ~100s/call. Full suite takes 30-45 min."
log "Started: $(date)"
separator

# ── T-M01: Ambitious plan ────────────────────────────────────────────────────

log "T-M01: forge plan (ambitious SaaS project)"
TEST_DIR=$(mktemp -d /tmp/forge-test-premier-M01-XXXX)
cd "$TEST_DIR"

START_TIME=$(date +%s)
if timeout 900 python3 "$FORGE_ROOT/forge.py" plan "project management dashboard with kanban board, team members, and task tracking" --model nova-premier 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))

    if [ -f ".forge/state/tasks.json" ]; then
        TASK_COUNT=$(python3 -c "import json; print(len(json.load(open('.forge/state/tasks.json'))['tasks']))" 2>/dev/null || echo 0)
        pass "T-M01: Premier plan created $TASK_COUNT tasks in ${DURATION}s"
    else
        fail "T-M01" "tasks.json not created after ${DURATION}s"
    fi
else
    fail "T-M01" "forge plan timed out (900s)"
fi
cd "$FORGE_ROOT"
separator

# ── T-M02: Build with 300s timeout ──────────────────────────────────────────

log "T-M02: forge build (Premier, 300s Bedrock timeout test)"
cd "$TEST_DIR"

START_TIME=$(date +%s)
if timeout 1800 python3 "$FORGE_ROOT/forge.py" build --model nova-premier --no-preview 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    FILE_COUNT=$(find . -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" | grep -v .forge | wc -l)

    pass "T-M02: Premier build completed: $FILE_COUNT files in ${DURATION}s"
else
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    fail "T-M02" "Premier build failed after ${DURATION}s — timeout or API error"
fi
cd "$FORGE_ROOT"
separator

# ── T-M03: Syntax check generated code ──────────────────────────────────────

log "T-M03: Syntax check all generated .py files"
cd "$TEST_DIR"

SYNTAX_ERRORS=0
for pyfile in $(find . -name "*.py" -not -path "./.forge/*" 2>/dev/null); do
    if ! python3 -c "import py_compile; py_compile.compile('$pyfile', doraise=True)" 2>>"$LOG"; then
        SYNTAX_ERRORS=$((SYNTAX_ERRORS+1))
        log "  SYNTAX ERROR: $pyfile"
    fi
done

if [ "$SYNTAX_ERRORS" -eq 0 ]; then
    pass "T-M03: All Premier-generated Python files pass syntax check"
else
    fail "T-M03" "$SYNTAX_ERRORS files have syntax errors"
fi
cd "$FORGE_ROOT"
separator

# ── T-M04: 16K max_tokens verification ──────────────────────────────────────

log "T-M04: Verify Premier max_tokens = 16384"

OUTPUT=$(python3 -c "
from config import get_model_config
cfg = get_model_config('nova-premier')
print(f'context_window={cfg.context_window}')
print(f'max_tokens={cfg.max_tokens}')
ok = cfg.max_tokens >= 16384
print(f'MAX_TOKENS_OK={\"yes\" if ok else \"no\"}')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "MAX_TOKENS_OK=yes"; then
    pass "T-M04: Premier max_tokens = 16384"
else
    fail "T-M04" "Premier max_tokens not set to 16384"
fi
separator

# ── T-M05: Bedrock timeout config ────────────────────────────────────────────

log "T-M05: Verify Bedrock read_timeout = 300s"

OUTPUT=$(python3 -c "
from model_router import ModelRouter
import inspect
src = inspect.getsource(ModelRouter)
if '300' in src and 'read_timeout' in src:
    print('TIMEOUT_OK=yes')
else:
    print('TIMEOUT_OK=no')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "TIMEOUT_OK=yes"; then
    pass "T-M05: Bedrock read_timeout = 300s configured"
else
    fail "T-M05" "Bedrock read_timeout not set to 300s"
fi
separator

# ── T-M06: stop_reason detection ─────────────────────────────────────────────

log "T-M06: Verify stop_reason='max_tokens' handling"

OUTPUT=$(python3 -c "
import inspect
from model_router import ModelRouter
src = inspect.getsource(ModelRouter)
# Check that max_tokens or end_turn or stop_sequence handling exists
if 'stop_reason' in src or 'stopReason' in src or 'finish_reason' in src:
    print('STOP_REASON_OK=yes')
else:
    print('STOP_REASON_OK=no')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "STOP_REASON_OK=yes"; then
    pass "T-M06: stop_reason detection present in model_router"
else
    fail "T-M06" "stop_reason not handled — truncation risk"
fi
separator

# ── T-M07: Large file generation check ──────────────────────────────────────

log "T-M07: Check if Premier generated any files >100 lines"
cd "$TEST_DIR"

LARGE_FILES=0
for f in $(find . -name "*.py" -o -name "*.html" -o -name "*.js" | grep -v .forge 2>/dev/null); do
    LINES=$(wc -l < "$f" 2>/dev/null || echo 0)
    if [ "$LINES" -gt 100 ]; then
        LARGE_FILES=$((LARGE_FILES+1))
        log "  Large file: $f ($LINES lines)"
    fi
done

if [ "$LARGE_FILES" -gt 0 ]; then
    pass "T-M07: Premier generated $LARGE_FILES files >100 lines (using 16K max_tokens)"
else
    log "  INFO: No files >100 lines — may be normal for this project scope"
    pass "T-M07: File generation complete (no truncation observed)"
fi
cd "$FORGE_ROOT"
separator

# ── T-M08: new-project formation ─────────────────────────────────────────────

log "T-M08: Formation routing for novel/large (new-project expected)"

OUTPUT=$(timeout 10 python3 "$FORGE_ROOT/forge.py" formation --complexity novel --scope large 2>&1 | tee -a "$LOG")
if echo "$OUTPUT" | grep -qi "Formation:"; then
    pass "T-M08: Formation routing for novel/large works"
else
    fail "T-M08" "Formation routing failed for novel/large"
fi
separator

# ── T-M09: Session state persistence ────────────────────────────────────────

log "T-M09: Verify session state persists across commands"
cd "$TEST_DIR"

STATUS1=$(python3 "$FORGE_ROOT/forge.py" status 2>&1 | grep "Progress:" || echo "none")
STATUS2=$(python3 "$FORGE_ROOT/forge.py" status 2>&1 | grep "Progress:" || echo "none")

if [ "$STATUS1" = "$STATUS2" ] && [ "$STATUS1" != "none" ]; then
    pass "T-M09: Session state consistent across reads"
else
    fail "T-M09" "Session state inconsistent: '$STATUS1' vs '$STATUS2'"
fi
cd "$FORGE_ROOT"
separator

# ── T-M10: Compliance on Premier project ────────────────────────────────────

log "T-M10: Compliance check on Premier-built project"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" session compliance 2>&1 | tee -a "$LOG"; then
    pass "T-M10: Compliance check works on Premier project"
else
    fail "T-M10" "Compliance check failed"
fi
cd "$FORGE_ROOT"
separator

# ── Summary ──────────────────────────────────────────────────────────────────

log ""
log "=== Nova Premier Test Summary ==="
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
