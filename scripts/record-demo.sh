#!/usr/bin/env bash
# Record a real Nova Forge demo session.
# Prerequisites: asciinema, pexpect (pip install pexpect)
# Usage: ./scripts/record-demo.sh [--model nova-lite]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Load secrets if available
if [ -f ~/.secrets/hercules.env ]; then
    source ~/.secrets/hercules.env
fi

# Check prerequisites
if ! command -v asciinema &> /dev/null; then
    echo "ERROR: asciinema not found. Install: pip install asciinema"
    exit 1
fi

python3 -c "import pexpect" 2>/dev/null || {
    echo "ERROR: pexpect not found. Install: pip install pexpect"
    exit 1
}

MODEL="${1:-nova-lite}"
OUTPUT="${PROJECT_DIR}/web/demo-real.cast"

echo "═══════════════════════════════════════════════════"
echo "  Nova Forge Demo Recording"
echo "  Model: ${MODEL}"
echo "  Output: ${OUTPUT}"
echo "═══════════════════════════════════════════════════"
echo ""
echo "This will run a real build session and record it."
echo "Press Ctrl+C to abort."
echo ""

python3 "${SCRIPT_DIR}/record_demo.py" --output "${OUTPUT}" --model "${MODEL}"

if [ -f "${OUTPUT}" ]; then
    SIZE=$(wc -c < "${OUTPUT}")
    echo ""
    echo "✓ Recording saved: ${OUTPUT} (${SIZE} bytes)"
    echo "  Update web/app.js to point to demo-real.cast"
fi
