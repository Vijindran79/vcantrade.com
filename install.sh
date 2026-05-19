#!/usr/bin/env bash
# ============================================================================
# VcanTrade AI — One-Line Installer (macOS / Linux)
#
# Usage (Terminal, copy this whole line):
#   curl -fsSL https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.sh | bash
#
# Note for Mac users:
#   MetaTrader 5 has NO Mac/Linux Python connector. The MT5 execution mode
#   will NOT work on this machine. You can still run the bot in TradingView
#   mode (browser-based) on a Mac.
#
#   If you trade through MT5, install on the Windows machine that runs MT5
#   (or a Parallels Windows VM) using install.ps1 instead.
# ============================================================================

set -e

# Edit this line once you push the repo to GitHub:
REPO_URL="https://github.com/Vijindran79/vcantrade.com.git"
INSTALL_ROOT="$HOME/VcanTrade"

echo ""
echo "============================================================"
echo "  VcanTrade AI — macOS / Linux Installer"
echo "============================================================"
echo ""

OS="$(uname -s)"

# --- 1. Python 3.11 check ----------------------------------------------------
if command -v python3.11 >/dev/null 2>&1; then
    echo "[1/5] Python 3.11 found."
else
    echo "[1/5] Python 3.11 not found. Installing..."
    if [ "$OS" = "Darwin" ]; then
        if ! command -v brew >/dev/null 2>&1; then
            echo "Homebrew is required. Install it first from https://brew.sh"
            exit 1
        fi
        brew install python@3.11
    else
        echo "Please install Python 3.11 from https://www.python.org/downloads/ then re-run this installer."
        exit 1
    fi
fi

# --- 2. Git check ------------------------------------------------------------
if command -v git >/dev/null 2>&1; then
    echo "[2/5] Git found."
else
    echo "[2/5] Git not found. Installing..."
    if [ "$OS" = "Darwin" ]; then
        brew install git
    else
        echo "Please install git, then re-run this installer."
        exit 1
    fi
fi

# --- 3. Clone / update repo --------------------------------------------------
echo "[3/5] Downloading code into $INSTALL_ROOT ..."
if [ -d "$INSTALL_ROOT/.git" ]; then
    cd "$INSTALL_ROOT"
    git fetch --all --quiet
    git reset --hard origin/main --quiet
    cd - >/dev/null
    echo "    Updated existing copy."
else
    rm -rf "$INSTALL_ROOT"
    git clone --depth 1 "$REPO_URL" "$INSTALL_ROOT"
    echo "    Cloned fresh copy."
fi

# --- 4. Virtual env + dependencies -------------------------------------------
echo "[4/5] Installing Python packages (this can take a few minutes)..."
cd "$INSTALL_ROOT"

if [ ! -d ".venv" ]; then
    python3.11 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet

echo "    Installing Playwright browser (Chromium)..."
python -m playwright install chromium

if [ ! -f ".env" ]; then
    cp .env.example .env
    echo "    Created .env from template."
fi

deactivate

# --- 5. Make start.sh executable --------------------------------------------
echo "[5/5] Finalising..."
chmod +x "$INSTALL_ROOT/start.sh" 2>/dev/null || true

echo ""
echo "============================================================"
echo "  DONE!"
echo "============================================================"
echo ""
echo "Next steps:"
echo "  1. Open TradingView Desktop  (MT5 is not supported on Mac/Linux)"
echo "  2. In a separate terminal, run:  ollama serve"
echo "  3. Start the bot:  $INSTALL_ROOT/start.sh"
echo ""
echo "Installed at: $INSTALL_ROOT"
echo ""
