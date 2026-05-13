#!/usr/bin/env bash
# =============================================================================
# VcaniTrade AI - Vast.ai Auto-Setup Script (Ubuntu 24.04)
# =============================================================================
# This script prepares a fresh Vast.ai instance for running the Lion Bot.
# It installs system dependencies, Python packages, starts Xvfb + Chrome CDP,
# and launches the bot inside a persistent tmux session.
#
# Usage:
#   chmod +x deploy.sh
#   ./deploy.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
PROJECT_DIR="/root/vcantrade"
LOG_DIR="/root/vcantrade/logs"
VENV_DIR="/root/vcantrade/venv"
TMUX_SESSION="vcantrade"
CHROME_DEBUG_PORT=9222
DASHBOARD_PORT=8765

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# ---------------------------------------------------------------------------
# 1. System Update & Base Packages
# ---------------------------------------------------------------------------
log_info "Updating package lists..."
apt-get update -qq

log_info "Installing system dependencies (xvfb, tmux, curl, wget, gnupg)..."
apt-get install -y -qq \
    xvfb \
    tmux \
    curl \
    wget \
    gnupg \
    ca-certificates \
    fonts-liberation \
    libappindicator3-1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdbus-1-3 \
    libgdk-pixbuf2.0-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    xdg-utils \
    libgbm1 \
    libgtk-3-0 \
    libxss1 \
    libu2f-udev \
    libvulkan1 \
    unzip \
    > /dev/null 2>&1 || true

# ---------------------------------------------------------------------------
# 2. Install Google Chrome (if missing)
# ---------------------------------------------------------------------------
if ! command -v google-chrome &> /dev/null; then
    log_info "Google Chrome not found. Installing..."
    wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb -O /tmp/chrome.deb
    dpkg -i /tmp/chrome.deb > /dev/null 2>&1 || apt-get install -f -y -qq > /dev/null 2>&1
    rm -f /tmp/chrome.deb
else
    log_info "Google Chrome already installed."
fi

# ---------------------------------------------------------------------------
# 3. Python 3.10+ & Virtual Environment
# ---------------------------------------------------------------------------
PYTHON_CMD="python3"
if ! command -v python3 &> /dev/null; then
    log_error "python3 is not installed. Aborting."
    exit 1
fi

PY_VERSION=$($PYTHON_CMD --version 2>&1 | awk '{print $2}')
log_info "Python version: $PY_VERSION"

log_info "Creating virtual environment at $VENV_DIR ..."
$PYTHON_CMD -m venv "$VENV_DIR" --clear || true
source "$VENV_DIR/bin/activate"

log_info "Upgrading pip..."
pip install --quiet --upgrade pip

# ---------------------------------------------------------------------------
# 4. Install Python Requirements
# ---------------------------------------------------------------------------
if [ -f "$PROJECT_DIR/requirements.txt" ]; then
    log_info "Installing Python requirements..."
    pip install --quiet -r "$PROJECT_DIR/requirements.txt"
else
    log_warn "requirements.txt not found in $PROJECT_DIR — skipping pip install."
fi

# ---------------------------------------------------------------------------
# 5. Install Playwright Browsers
# ---------------------------------------------------------------------------
log_info "Installing Playwright browsers..."
playwright install chromium || log_warn "Playwright install failed — may need manual fix."

# ---------------------------------------------------------------------------
# 6. Create Log Directory
# ---------------------------------------------------------------------------
mkdir -p "$LOG_DIR"
log_info "Log directory ready: $LOG_DIR"

# ---------------------------------------------------------------------------
# 7. Start Virtual Display (:99)
# ---------------------------------------------------------------------------
if ! pgrep -x "Xvfb" > /dev/null; then
    log_info "Starting Xvfb on DISPLAY :99 ..."
    Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
    sleep 2
else
    log_info "Xvfb already running."
fi

export DISPLAY=:99
log_info "DISPLAY set to :99"

# ---------------------------------------------------------------------------
# 8. Start Chrome with Remote Debugging
# ---------------------------------------------------------------------------
CHROME_PID=$(pgrep -f "remote-debugging-port=$CHROME_DEBUG_PORT" || true)
if [ -z "$CHROME_PID" ]; then
    log_info "Starting Google Chrome on debug port $CHROME_DEBUG_PORT ..."
    google-chrome \
        --no-sandbox \
        --disable-dev-shm-usage \
        --disable-gpu \
        --disable-software-rasterizer \
        --disable-extensions \
        --disable-background-networking \
        --disable-background-timer-throttling \
        --disable-backgrounding-occluded-windows \
        --disable-renderer-backgrounding \
        --disable-features=TranslateUI \
        --remote-debugging-port=$CHROME_DEBUG_PORT \
        --window-size=1920,1080 \
        --start-maximized \
        --user-data-dir=/root/chrome-profile \
        "about:blank" &
    sleep 4
else
    log_info "Chrome already running with remote-debugging-port=$CHROME_DEBUG_PORT (PID: $CHROME_PID)"
fi

# ---------------------------------------------------------------------------
# 9. Verify Chrome Debug Endpoint
# ---------------------------------------------------------------------------
log_info "Verifying Chrome CDP endpoint..."
for i in {1..10}; do
    if curl -s http://127.0.0.1:$CHROME_DEBUG_PORT/json/version > /dev/null 2>&1; then
        log_info "Chrome CDP is reachable on port $CHROME_DEBUG_PORT"
        break
    fi
    sleep 1
    if [ "$i" -eq 10 ]; then
        log_warn "Chrome CDP not responding after 10 seconds. Check logs."
    fi
done

# ---------------------------------------------------------------------------
# 10. Kill Existing Bot Session
# ---------------------------------------------------------------------------
if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    log_warn "Existing tmux session '$TMUX_SESSION' found. Killing it..."
    tmux kill-session -t "$TMUX_SESSION"
    sleep 1
fi

# ---------------------------------------------------------------------------
# 11. Start Bot in tmux
# ---------------------------------------------------------------------------
log_info "Starting VcaniTrade bot in tmux session '$TMUX_SESSION' ..."
tmux new-session -d -s "$TMUX_SESSION" \
    "export DISPLAY=:99; cd $PROJECT_DIR && source $VENV_DIR/bin/activate && python main.py 2>&1 | tee $LOG_DIR/bot_console.log"

sleep 2

if tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
    log_info "Bot is running inside tmux. Attach with: tmux attach -t $TMUX_SESSION"
    log_info "Dashboard bridge should be live on port $DASHBOARD_PORT"
    log_info "View logs: tail -f $LOG_DIR/vcani_trade.log"
else
    log_error "Failed to start tmux session. Check $LOG_DIR/bot_console.log"
    exit 1
fi

# ---------------------------------------------------------------------------
# 12. Quick Health Summary
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  VCANITRADE CLOUD DEPLOYMENT READY    ${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  Chrome CDP:    http://127.0.0.1:$CHROME_DEBUG_PORT"
echo -e "  Dashboard API: http://0.0.0.0:$DASHBOARD_PORT"
echo -e "  tmux session:  $TMUX_SESSION"
echo -e "  Project dir:   $PROJECT_DIR"
echo -e "  Logs:          $LOG_DIR"
echo ""
echo -e "  ${YELLOW}Commands:${NC}"
echo -e "    Attach bot:      tmux attach -t $TMUX_SESSION"
echo -e "    Detach bot:      Ctrl+B then D"
echo -e "    Kill bot:        tmux kill-session -t $TMUX_SESSION"
echo -e "    Tail logs:       tail -f $LOG_DIR/vcani_trade.log"
echo -e "    Tail HEARTBEAT:  tail -f $LOG_DIR/vcani_trade.log | grep HEARTBEAT"
echo -e "    Tail BRIDGE:     tail -f $LOG_DIR/vcani_trade.log | grep BRIDGE"
echo ""
