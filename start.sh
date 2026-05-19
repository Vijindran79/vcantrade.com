#!/usr/bin/env bash
# ============================================================================
# VcanTrade AI - Daily Launcher (macOS / Linux)
# ============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "============================================"
echo "VcanTrade AI - Starting..."
echo "============================================"

# Make sure Ollama is running (don't fail if user runs it elsewhere)
if ! pgrep -x ollama >/dev/null 2>&1; then
    echo "Starting Ollama..."
    nohup ollama serve >/dev/null 2>&1 &
    sleep 3
fi

VENV_PY="$SCRIPT_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
    echo ""
    echo "ERROR: Virtual environment not found."
    echo "Re-run install.sh to fix this."
    echo ""
    exit 1
fi

echo "Launching VcanTrade AI..."
"$VENV_PY" main.py
