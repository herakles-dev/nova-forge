#!/usr/bin/env bash
# Nova Forge — Pre-Submission Test Harness
# Launches 6 parallel tmux panes for comprehensive manual testing.
#
# Usage: ./scripts/test-harness.sh
#
# Pane layout (2x3 grid):
#   ┌─────────────────┬─────────────────┬──────────────────┐
#   │ Nova Lite (32K)  │ Nova Pro (300K)  │ Nova Premier (1M)│
#   ├─────────────────┼─────────────────┼──────────────────┤
#   │ Preview/Deploy   │ Error Injection  │ Monitor/Regress  │
#   └─────────────────┴─────────────────┴──────────────────┘

set -euo pipefail

FORGE_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SESSION="forge-test"
SCRIPTS="$FORGE_ROOT/scripts/manual-tests"
ISSUES="$FORGE_ROOT/issues.md"

# ── Pre-flight ───────────────────────────────────────────────────────────────

if ! command -v tmux &>/dev/null; then
    echo "ERROR: tmux is required. Install with: apt install tmux"
    exit 1
fi

if [ ! -f "$HOME/.secrets/hercules.env" ]; then
    echo "ERROR: ~/.secrets/hercules.env not found. AWS credentials required."
    exit 1
fi

# Kill existing session if present
tmux kill-session -t "$SESSION" 2>/dev/null || true

# ── Create session ───────────────────────────────────────────────────────────

echo "=== Nova Forge Test Harness ==="
echo "Project: $FORGE_ROOT"
echo "Issues:  $ISSUES"
echo ""
echo "Launching 6-pane tmux session..."

# Create session with first pane (Nova Lite)
tmux new-session -d -s "$SESSION" -c "$FORGE_ROOT" \
    -x 220 -y 60

# Split into 6 panes (2 rows x 3 columns)
tmux split-window -h -t "$SESSION" -c "$FORGE_ROOT"
tmux split-window -h -t "$SESSION" -c "$FORGE_ROOT"
tmux select-layout -t "$SESSION" even-horizontal

# Split each column vertically
tmux select-pane -t "$SESSION:0.0"
tmux split-window -v -t "$SESSION:0.0" -c "$FORGE_ROOT"
tmux select-pane -t "$SESSION:0.2"
tmux split-window -v -t "$SESSION:0.2" -c "$FORGE_ROOT"
tmux select-pane -t "$SESSION:0.4"
tmux split-window -v -t "$SESSION:0.4" -c "$FORGE_ROOT"

# ── Load secrets in all panes ────────────────────────────────────────────────

for pane in 0 1 2 3 4 5; do
    tmux send-keys -t "$SESSION:0.$pane" \
        "source ~/.secrets/hercules.env && cd $FORGE_ROOT" C-m
done

sleep 1

# ── Set pane titles ──────────────────────────────────────────────────────────

tmux select-pane -t "$SESSION:0.0" -T "Nova Lite (32K)"
tmux select-pane -t "$SESSION:0.1" -T "Preview/Deploy"
tmux select-pane -t "$SESSION:0.2" -T "Nova Pro (300K)"
tmux select-pane -t "$SESSION:0.3" -T "Error Injection"
tmux select-pane -t "$SESSION:0.4" -T "Nova Premier (1M)"
tmux select-pane -t "$SESSION:0.5" -T "Monitor/Regress"

# ── Send test scripts to each pane ───────────────────────────────────────────

# Pane 0: Nova Lite tests
tmux send-keys -t "$SESSION:0.0" \
    "echo '=== PANE 0: Nova Lite (32K) Manual Tests ===' && echo 'Run: bash $SCRIPTS/test-nova-lite.sh'" C-m

# Pane 1: Preview/Deploy tests
tmux send-keys -t "$SESSION:0.1" \
    "echo '=== PANE 1: Preview & Deploy Tests ===' && echo 'Run: bash $SCRIPTS/test-preview-deploy.sh'" C-m

# Pane 2: Nova Pro tests
tmux send-keys -t "$SESSION:0.2" \
    "echo '=== PANE 2: Nova Pro (300K) Manual Tests ===' && echo 'Run: bash $SCRIPTS/test-nova-pro.sh'" C-m

# Pane 3: Error injection tests
tmux send-keys -t "$SESSION:0.3" \
    "echo '=== PANE 3: Error Injection & Edge Cases ===' && echo 'Run: bash $SCRIPTS/test-error-injection.sh'" C-m

# Pane 4: Nova Premier tests
tmux send-keys -t "$SESSION:0.4" \
    "echo '=== PANE 4: Nova Premier (1M) Manual Tests ===' && echo 'Run: bash $SCRIPTS/test-nova-premier.sh'" C-m

# Pane 5: Monitor (watch tests + issues)
tmux send-keys -t "$SESSION:0.5" \
    "echo '=== PANE 5: Monitor ===' && echo 'Watching: pytest + issues.md' && echo '---' && echo 'Quick commands:' && echo '  pytest tests/ -x -q                    # Full suite' && echo '  python3 benchmark_nova_models.py --all  # Regression' && echo '  cat issues.md                           # Current issues' && echo '  tail -f /tmp/forge-test-*.log           # Live logs'" C-m

# ── Attach ───────────────────────────────────────────────────────────────────

echo ""
echo "Session '$SESSION' ready with 6 panes."
echo ""
echo "Attach with:  tmux attach -t $SESSION"
echo ""
echo "Execution order (recommended):"
echo "  1. Pane 5 (Monitor): Run pytest first to confirm baseline"
echo "  2. Pane 0 (Lite):    Fastest model, catches obvious issues early"
echo "  3. Pane 2 (Pro):     Medium model, multi-file builds"
echo "  4. Pane 1 (Preview): Stack detection + tunnel while models run"
echo "  5. Pane 3 (Errors):  Edge cases after happy paths verified"
echo "  6. Pane 4 (Premier): Longest running, start last"
echo ""
echo "Token efficiency strategy:"
echo "  - Lite:    SLIM_TOOLS (5 tools), ~600c prompt, 4K max_tokens"
echo "  - Pro:     FOCUSED prompt (~1500c), 8K max_tokens"
echo "  - Premier: FOCUSED prompt, 16K max_tokens, 300s timeout"
echo ""

tmux attach -t "$SESSION"
