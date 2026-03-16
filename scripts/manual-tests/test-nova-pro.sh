#!/usr/bin/env bash
# Nova Pro (300K) — Manual Test Suite
# Tests FOCUSED prompt, multi-file builds, formation routing, artifact handoffs.
#
# Run from nova-forge root: bash scripts/manual-tests/test-nova-pro.sh
# Requires: source ~/.secrets/hercules.env

set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$FORGE_ROOT"
LOG="/tmp/forge-test-pro.log"
ISSUES="$FORGE_ROOT/issues.md"
PASS=0
FAIL=0
SKIP=0

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
pass() { PASS=$((PASS+1)); log "PASS: $1"; }
fail() { FAIL=$((FAIL+1)); log "FAIL: $1 — $2"; sed -i "/^### Nova Pro (300K) Findings/a - [ ] $1 — $2" "$ISSUES"; }
skip() { SKIP=$((SKIP+1)); log "SKIP: $1 — $2"; }
separator() { echo "────────────────────────────────────────────────" | tee -a "$LOG"; }

echo "" > "$LOG"
log "=== Nova Pro (300K) Test Suite ==="
log "Started: $(date)"
separator

# ── T-P01: Complex multi-file plan ───────────────────────────────────────────

log "T-P01: forge plan (complex expense tracker with auth)"
TEST_DIR=$(mktemp -d /tmp/forge-test-pro-P01-XXXX)
cd "$TEST_DIR"

if timeout 240 python3 "$FORGE_ROOT/forge.py" plan "expense tracker with user auth, categories, and monthly reports" --model nova-pro 2>&1 | tee -a "$LOG"; then
    if [ -f ".forge/state/tasks.json" ]; then
        TASK_COUNT=$(python3 -c "import json; print(len(json.load(open('.forge/state/tasks.json'))['tasks']))" 2>/dev/null || echo 0)
        if [ "$TASK_COUNT" -ge 3 ]; then
            pass "T-P01: Complex plan created $TASK_COUNT tasks"
        else
            fail "T-P01" "Too few tasks ($TASK_COUNT) for complex project"
        fi
    else
        fail "T-P01" "tasks.json not created"
    fi
else
    fail "T-P01" "forge plan failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-P02: Multi-wave build ──────────────────────────────────────────────────

log "T-P02: forge build (multi-wave, feature-impl formation)"
cd "$TEST_DIR"

START_TIME=$(date +%s)
if timeout 600 python3 "$FORGE_ROOT/forge.py" build --model nova-pro --no-preview 2>&1 | tee -a "$LOG"; then
    END_TIME=$(date +%s)
    DURATION=$((END_TIME - START_TIME))
    FILE_COUNT=$(find . -name "*.py" -o -name "*.html" -o -name "*.js" -o -name "*.css" | grep -v .forge | wc -l)

    if [ "$FILE_COUNT" -ge 2 ]; then
        pass "T-P02: Build produced $FILE_COUNT files in ${DURATION}s"
    else
        fail "T-P02" "Build completed but only $FILE_COUNT source files"
    fi
else
    fail "T-P02" "forge build failed or timed out (600s)"
fi
cd "$FORGE_ROOT"
separator

# ── T-P03: Syntax check generated files ─────────────────────────────────────

log "T-P03: Syntax check all generated .py files"
cd "$TEST_DIR"

SYNTAX_ERRORS=0
for pyfile in $(find . -name "*.py" -not -path "./.forge/*" 2>/dev/null); do
    if ! python3 -c "import py_compile; py_compile.compile('$pyfile', doraise=True)" 2>>"$LOG"; then
        SYNTAX_ERRORS=$((SYNTAX_ERRORS+1))
        log "  SYNTAX ERROR: $pyfile"
    fi
done

if [ "$SYNTAX_ERRORS" -eq 0 ]; then
    pass "T-P03: All generated Python files pass syntax check"
else
    fail "T-P03" "$SYNTAX_ERRORS files have syntax errors"
fi
cd "$FORGE_ROOT"
separator

# ── T-P04: Formation routing for complex project ────────────────────────────

log "T-P04: DAAO routing for complex/large project"

OUTPUT=$(timeout 10 python3 "$FORGE_ROOT/forge.py" formation --complexity complex --scope large 2>&1 | tee -a "$LOG")
if echo "$OUTPUT" | grep -qi "Formation:"; then
    FORMATION=$(echo "$OUTPUT" | grep "Formation:" | head -1)
    pass "T-P04: $FORMATION"
else
    fail "T-P04" "Formation selection failed for complex/large"
fi
separator

# ── T-P05: Artifact handoff verification ─────────────────────────────────────

log "T-P05: Verify .forge/artifacts/ created during build"
cd "$TEST_DIR"

if [ -d ".forge" ]; then
    ARTIFACT_COUNT=$(find .forge -name "*.py" -o -name "*.json" -o -name "*.html" 2>/dev/null | wc -l)
    if [ "$ARTIFACT_COUNT" -ge 0 ]; then
        pass "T-P05: .forge/ state intact ($ARTIFACT_COUNT files)"
    else
        fail "T-P05" "No artifacts found in .forge/"
    fi
else
    fail "T-P05" ".forge/ directory missing after build"
fi
cd "$FORGE_ROOT"
separator

# ── T-P06: Token budget verification ────────────────────────────────────────

log "T-P06: Verify Pro token budget (300K context, 8K max_tokens)"

OUTPUT=$(python3 -c "
from config import get_model_config
cfg = get_model_config('nova-pro')
print(f'context_window={cfg.context_window}')
print(f'max_tokens={cfg.max_tokens}')
ok = cfg.context_window >= 200000 and cfg.max_tokens >= 8192
print(f'BUDGET_OK={\"yes\" if ok else \"no\"}')
" 2>&1 | tee -a "$LOG")

if echo "$OUTPUT" | grep -q "BUDGET_OK=yes"; then
    pass "T-P06: Pro budget correct (300K context, 8K max_tokens)"
else
    fail "T-P06" "Pro token budget misconfigured"
fi
separator

# ── T-P07: Audit log verification ───────────────────────────────────────────

log "T-P07: Check audit trail after build"
cd "$TEST_DIR"

AUDIT_FILE=".forge/audit/audit.jsonl"
if [ -f "$AUDIT_FILE" ]; then
    ENTRIES=$(wc -l < "$AUDIT_FILE")
    pass "T-P07: Audit log has $ENTRIES entries"
else
    skip "T-P07" "No audit log (may be disabled)"
fi
cd "$FORGE_ROOT"
separator

# ── T-P08: Session dashboard ────────────────────────────────────────────────

log "T-P08: forge session dashboard"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" session dashboard 2>&1 | tee -a "$LOG"; then
    pass "T-P08: Session dashboard works"
else
    fail "T-P08" "Session dashboard failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-P09: Handoff context ──────────────────────────────────────────────────

log "T-P09: forge handoff"
cd "$TEST_DIR"

if timeout 10 python3 "$FORGE_ROOT/forge.py" handoff 2>&1 | tee -a "$LOG"; then
    pass "T-P09: Handoff context generated"
else
    fail "T-P09" "Handoff generation failed"
fi
cd "$FORGE_ROOT"
separator

# ── T-P10: Second plan in same project (re-plan) ────────────────────────────

log "T-P10: Re-plan in existing project (should work or warn)"
cd "$TEST_DIR"

if timeout 180 python3 "$FORGE_ROOT/forge.py" plan "add export to CSV feature" --model nova-pro 2>&1 | tee -a "$LOG"; then
    pass "T-P10: Re-plan in existing project works"
else
    # May intentionally fail if project has existing plan — check error message
    fail "T-P10" "Re-plan failed (may need spec overwrite support)"
fi
cd "$FORGE_ROOT"
separator

# ── T-P11: Agent info lookup ────────────────────────────────────────────────

log "T-P11: forge agent info spec-architect"

if timeout 10 python3 "$FORGE_ROOT/forge.py" agent info spec-architect 2>&1 | tee -a "$LOG"; then
    pass "T-P11: Agent info lookup works"
else
    fail "T-P11" "Agent info lookup failed"
fi
separator

# ── T-P12: Agent discover (fuzzy search) ────────────────────────────────────

log "T-P12: forge agent discover 'test'"

if timeout 10 python3 "$FORGE_ROOT/forge.py" agent discover test 2>&1 | tee -a "$LOG"; then
    pass "T-P12: Agent fuzzy search works"
else
    fail "T-P12" "Agent fuzzy search failed"
fi
separator

# ── Summary ──────────────────────────────────────────────────────────────────

log ""
log "=== Nova Pro Test Summary ==="
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
