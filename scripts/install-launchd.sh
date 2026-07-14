#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h}"
DOMAIN="gui/$(id -u)"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/Robin" "$HOME/Library/Application Support/Robin/traces"
chmod 700 "$HOME/Library/Logs/Robin" "$HOME/Library/Application Support/Robin" "$HOME/Library/Application Support/Robin/traces"
for SERVICE in com.robin.helper com.robin.agent; do
  sed "s|__ROOT__|$ROOT|g; s|__HOME__|$HOME|g" "$ROOT/infra/launchd/$SERVICE.plist.template" > "$HOME/Library/LaunchAgents/$SERVICE.plist"
  launchctl bootout "$DOMAIN/$SERVICE" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$HOME/Library/LaunchAgents/$SERVICE.plist"
  launchctl enable "$DOMAIN/$SERVICE"
done
print "Installed Robin helper and agent launch services."
