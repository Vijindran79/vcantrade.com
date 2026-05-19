#!/usr/bin/env bash
# ============================================================================
# VcanTrade AI — Teacher-Only Installer (macOS / Linux)
#
# Same as install.sh but locks the bot into Teacher Mode.
#
# Usage (Terminal, copy this whole line):
#   curl -fsSL https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install-teacher.sh | bash
# ============================================================================

set -e

REPO_URL="https://github.com/Vijindran79/vcantrade.com.git"
INSTALL_ROOT="$HOME/VcanTrade"

# Run the standard installer.
curl -fsSL "https://raw.githubusercontent.com/Vijindran79/vcantrade.com/main/install.sh" | bash

# Force Teacher-only lock in .env.
ENV_PATH="$INSTALL_ROOT/.env"
if [ -f "$ENV_PATH" ]; then
    if grep -qE "^TEACHER_ONLY_LOCK=" "$ENV_PATH"; then
        sed -i.bak -E "s/^TEACHER_ONLY_LOCK=.*/TEACHER_ONLY_LOCK=True/" "$ENV_PATH"
        rm -f "${ENV_PATH}.bak"
    else
        printf "\nTEACHER_ONLY_LOCK=True\n" >> "$ENV_PATH"
    fi
    echo ""
    echo "============================================================"
    echo "  TEACHER MODE LOCKED"
    echo "============================================================"
    echo "  The bot will analyze and suggest only."
    echo "  Autonomous mode is disabled — you click the broker yourself."
    echo ""
else
    echo "WARNING: .env not found at $ENV_PATH. Open it later and add: TEACHER_ONLY_LOCK=True"
fi
