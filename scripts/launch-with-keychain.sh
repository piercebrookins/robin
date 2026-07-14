#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h}"
SERVICE="com.robin.agent"
export OPENAI_API_KEY="$(security find-generic-password -a "$USER" -s "$SERVICE.OPENAI_API_KEY" -w)"
export ROBIN_PANEL_TOKEN="$(security find-generic-password -a "$USER" -s "$SERVICE.ROBIN_PANEL_TOKEN" -w)"
export ROBIN_MODE=production
cd "$ROOT"
exec /opt/homebrew/opt/node@24/bin/node "$ROOT/dist/apps/daemon/src/main.js"
