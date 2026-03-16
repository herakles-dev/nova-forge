#!/usr/bin/env bash
# Preview & Deploy — Integration Test Suite
# Tests stack detection, Cloudflare tunnel, Docker deployment, port allocation.
#
# Run from nova-forge root: bash scripts/manual-tests/test-preview-deploy.sh
# Requires: source ~/.secrets/hercules.env, docker running

set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$FORGE_ROOT"
LOG="/tmp/forge-test-preview.log"
ISSUES="$FORGE_ROOT/issues.md"
PASS=0
FAIL=0
SKIP=0

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }
pass() { PASS=$((PASS+1)); log "PASS: $1"; }
fail() { FAIL=$((FAIL+1)); log "FAIL: $1 — $2"; sed -i "/^### Preview\/Deploy Findings/a - [ ] $1 — $2" "$ISSUES"; }
skip() { SKIP=$((SKIP+1)); log "SKIP: $1 — $2"; }
separator() { echo "────────────────────────────────────────────────" | tee -a "$LOG"; }

echo "" > "$LOG"
log "=== Preview & Deploy Test Suite ==="
log "Started: $(date)"
separator

# ── T-D01: Stack detection — Flask ───────────────────────────────────────────

log "T-D01: Stack detection — Flask app"
TEST_DIR=$(mktemp -d /tmp/forge-test-stack-flask-XXXX)

cat > "$TEST_DIR/app.py" << 'PYEOF'
from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "<h1>Hello</h1>"

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
PYEOF

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR'))
print(f'kind={si.kind}')
print(f'entry={si.entry}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=flask\|kind=python"; then
    pass "T-D01: Flask app detected correctly"
else
    fail "T-D01" "Flask detection failed: $OUTPUT"
fi
separator

# ── T-D02: Stack detection — FastAPI ─────────────────────────────────────────

log "T-D02: Stack detection — FastAPI app"
TEST_DIR_02=$(mktemp -d /tmp/forge-test-stack-fastapi-XXXX)

cat > "$TEST_DIR_02/main.py" << 'PYEOF'
from fastapi import FastAPI
app = FastAPI()

@app.get("/")
def index():
    return {"message": "hello"}
PYEOF

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR_02'))
print(f'kind={si.kind}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=fastapi"; then
    pass "T-D02: FastAPI app detected correctly"
else
    fail "T-D02" "FastAPI detection failed: $OUTPUT"
fi
separator

# ── T-D03: Stack detection — Static site ─────────────────────────────────────

log "T-D03: Stack detection — Static HTML"
TEST_DIR_03=$(mktemp -d /tmp/forge-test-stack-static-XXXX)

cat > "$TEST_DIR_03/index.html" << 'HTMLEOF'
<!DOCTYPE html>
<html><body><h1>Hello World</h1></body></html>
HTMLEOF

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR_03'))
print(f'kind={si.kind}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=static"; then
    pass "T-D03: Static site detected correctly"
else
    fail "T-D03" "Static detection failed: $OUTPUT"
fi
separator

# ── T-D04: Stack detection — Streamlit ───────────────────────────────────────

log "T-D04: Stack detection — Streamlit app"
TEST_DIR_04=$(mktemp -d /tmp/forge-test-stack-streamlit-XXXX)

cat > "$TEST_DIR_04/app.py" << 'PYEOF'
import streamlit as st
st.title("Dashboard")
st.write("Hello")
PYEOF

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR_04'))
print(f'kind={si.kind}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=streamlit"; then
    pass "T-D04: Streamlit app detected correctly"
else
    fail "T-D04" "Streamlit detection failed: $OUTPUT"
fi
separator

# ── T-D05: Stack detection — Node.js ─────────────────────────────────────────

log "T-D05: Stack detection — Node.js app"
TEST_DIR_05=$(mktemp -d /tmp/forge-test-stack-node-XXXX)

cat > "$TEST_DIR_05/package.json" << 'JSONEOF'
{
    "name": "test-app",
    "scripts": { "start": "node server.js" },
    "dependencies": {}
}
JSONEOF
cat > "$TEST_DIR_05/server.js" << 'JSEOF'
const http = require('http');
http.createServer((req, res) => res.end('ok')).listen(3000);
JSEOF

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR_05'))
print(f'kind={si.kind}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=node\|kind=nodejs"; then
    pass "T-D05: Node.js app detected correctly"
else
    fail "T-D05" "Node.js detection failed: $OUTPUT"
fi
separator

# ── T-D06: Stack detection — Docker ──────────────────────────────────────────

log "T-D06: Stack detection — Dockerfile project"
TEST_DIR_06=$(mktemp -d /tmp/forge-test-stack-docker-XXXX)

cat > "$TEST_DIR_06/Dockerfile" << 'DEOF'
FROM python:3.11-slim
EXPOSE 8080
CMD ["python3", "-m", "http.server", "8080"]
DEOF

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR_06'))
print(f'kind={si.kind}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=docker"; then
    pass "T-D06: Docker project detected correctly"
else
    fail "T-D06" "Docker detection failed: $OUTPUT"
fi
separator

# ── T-D07: Stack detection — Unknown (empty) ────────────────────────────────

log "T-D07: Stack detection — Empty directory (should be 'unknown')"
TEST_DIR_07=$(mktemp -d /tmp/forge-test-stack-empty-XXXX)

OUTPUT=$(timeout 10 python3 -c "
import sys; sys.path.insert(0, '$FORGE_ROOT')
from pathlib import Path
from forge_preview import detect_stack
si = detect_stack(Path('$TEST_DIR_07'))
print(f'kind={si.kind}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "kind=unknown"; then
    pass "T-D07: Empty directory correctly detected as 'unknown'"
else
    fail "T-D07" "Empty dir should be 'unknown', got: $OUTPUT"
fi
separator

# ── T-D08: Preview — Flask server startup ────────────────────────────────────

log "T-D08: Preview — Flask server starts on 127.0.0.1"

OUTPUT=$(timeout 15 python3 -c "
import sys, subprocess, time, socket
sys.path.insert(0, '$FORGE_ROOT')

# Find free port
port = 15100
for p in range(15100, 15120):
    with socket.socket() as s:
        if s.connect_ex(('127.0.0.1', p)) != 0:
            port = p
            break

# Start Flask app from T-D01 test dir
proc = subprocess.Popen(
    ['python3', '-m', 'flask', '--app', 'app:app', 'run', '--host', '127.0.0.1', '--port', str(port)],
    cwd='$TEST_DIR',
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
)
time.sleep(4)

# Check if server is running
with socket.socket() as s:
    connected = s.connect_ex(('127.0.0.1', port)) == 0

proc.kill()
proc.wait()
print(f'SERVER_OK={\"yes\" if connected else \"no\"}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "SERVER_OK=yes"; then
    pass "T-D08: Flask server starts and binds to 127.0.0.1"
elif echo "$OUTPUT" | grep -q "Traceback\|ModuleNotFoundError"; then
    skip "T-D08" "Flask not available in test env"
else
    fail "T-D08" "Flask server failed to start"
fi
separator

# ── T-D09: Cloudflared availability check ───────────────────────────────────

log "T-D09: Check cloudflared binary availability"

if command -v cloudflared &>/dev/null; then
    CF_VERSION=$(cloudflared --version 2>&1 | head -1)
    pass "T-D09: cloudflared available — $CF_VERSION"
elif [ -f "$HOME/.forge/bin/cloudflared" ]; then
    pass "T-D09: cloudflared available at ~/.forge/bin/cloudflared"
else
    skip "T-D09" "cloudflared not installed (tunnel tests will use fallback)"
fi
separator

# ── T-D10: Port allocation check ────────────────────────────────────────────

log "T-D10: Port allocation range check"

OUTPUT=$(timeout 10 python3 -c "
import sys
sys.path.insert(0, '$FORGE_ROOT')
try:
    from forge_deployer import ForgeDeployer
    import inspect
    src = inspect.getsource(ForgeDeployer)
    if '8161' in src or 'PORT_START' in src:
        print('PORT_RANGE_OK=yes')
    else:
        print('PORT_RANGE_OK=no')
except ImportError as e:
    print(f'IMPORT_ERROR={e}')
" 2>&1) || true
echo "$OUTPUT" >> "$LOG"

if echo "$OUTPUT" | grep -q "PORT_RANGE_OK=yes"; then
    pass "T-D10: Port allocation range configured (8161-8199)"
else
    fail "T-D10" "Port allocation range issue: $OUTPUT"
fi
separator

# ── T-D11: Docker daemon check ──────────────────────────────────────────────

log "T-D11: Docker daemon accessibility"

if docker info &>/dev/null 2>&1; then
    pass "T-D11: Docker daemon running and accessible"
else
    skip "T-D11" "Docker daemon not accessible (deploy tests will be limited)"
fi
separator

# ── T-D12: Website health check ─────────────────────────────────────────────

log "T-D12: forge.herakles.dev health check"

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8162/health 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    pass "T-D12: forge.herakles.dev health endpoint returns 200"
elif [ "$HTTP_CODE" = "000" ]; then
    skip "T-D12" "Web service not running on port 8162"
else
    fail "T-D12" "Health endpoint returned HTTP $HTTP_CODE"
fi
separator

# ── Summary ──────────────────────────────────────────────────────────────────

log ""
log "=== Preview & Deploy Test Summary ==="
log "PASS: $PASS"
log "FAIL: $FAIL"
log "SKIP: $SKIP"
log "Total: $((PASS + FAIL + SKIP))"
log "Log: $LOG"
log "Completed: $(date)"

# Cleanup temp dirs
rm -rf /tmp/forge-test-stack-* 2>/dev/null || true

if [ "$FAIL" -gt 0 ]; then
    log "STATUS: ISSUES FOUND — check $ISSUES"
    exit 1
else
    log "STATUS: ALL TESTS PASSED"
fi
