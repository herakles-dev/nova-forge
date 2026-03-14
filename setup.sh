#!/usr/bin/env bash
# Nova Forge — Quick Setup
# Usage: ./setup.sh

set -e

echo "Setting up Nova Forge..."

# Create venv if needed
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

# Activate
source .venv/bin/activate

# Install
echo "Installing dependencies..."
pip install -q -r requirements.txt

echo ""
echo "Done! To get started:"
echo ""
echo "  1. Set your AWS credentials:"
echo "     export AWS_ACCESS_KEY_ID=\"your-key\""
echo "     export AWS_SECRET_ACCESS_KEY=\"your-secret\""
echo "     export AWS_DEFAULT_REGION=\"us-east-1\""
echo ""
echo "  2. Launch Nova Forge:"
echo "     source .venv/bin/activate"
echo "     python3 forge_cli.py"
echo ""
