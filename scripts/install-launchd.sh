#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h}"
DOMAIN="gui/$(id -u)"
HELPER="$ROOT/apps/mac-helper/.build/release/RobinMacHelper"
[[ -x "$HELPER" ]] || { print -u2 "Native helper missing. Run ./scripts/bootstrap-macos.sh first."; exit 1; }
codesign --verify --strict "$HELPER" >/dev/null 2>&1 || { print -u2 "Native helper signature is invalid. Run ./scripts/sign-helper.sh."; exit 1; }
for NAME in OPENAI_API_KEY ROBIN_PANEL_TOKEN; do
  security find-generic-password -a "$USER" -s "com.robin.agent.$NAME" >/dev/null 2>&1 || { print -u2 "$NAME is missing from Keychain. Run ./scripts/keychain-secret.sh set $NAME."; exit 1; }
done
for DEVICE in "Robin Speaker" "Robin Microphone"; do
  system_profiler SPAudioDataType 2>/dev/null | grep -q "$DEVICE" || { print -u2 "$DEVICE is missing. Rerun bootstrap after BlackHole is loaded."; exit 1; }
done
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs/Robin" "$HOME/Library/Application Support/Robin/traces"
chmod 700 "$HOME/Library/Logs/Robin" "$HOME/Library/Application Support/Robin" "$HOME/Library/Application Support/Robin/traces"
for SERVICE in com.robin.helper com.robin.agent; do
  sed "s|__ROOT__|$ROOT|g; s|__HOME__|$HOME|g" "$ROOT/infra/launchd/$SERVICE.plist.template" > "$HOME/Library/LaunchAgents/$SERVICE.plist"
  launchctl bootout "$DOMAIN/$SERVICE" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$HOME/Library/LaunchAgents/$SERVICE.plist"
  launchctl enable "$DOMAIN/$SERVICE"
done
print "Installed Robin helper and agent launch services."
