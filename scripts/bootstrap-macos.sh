#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h}"
[[ "$(uname -m)" == "arm64" ]] || { print -u2 "Robin supports Apple-silicon Macs only."; exit 1; }
command -v brew >/dev/null || { print -u2 "Install Homebrew from https://brew.sh, then rerun."; exit 1; }
brew bundle --file "$ROOT/Brewfile"
cd "$ROOT"
NPM="/opt/homebrew/opt/node@24/bin/npm"
[[ -x "$NPM" ]] || { print -u2 "Homebrew node@24 is unavailable."; exit 1; }
"$NPM" ci
"$NPM" run build
"$NPM" run mac-helper:build
"$ROOT/scripts/sign-helper.sh"
if ! system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole 2ch" || ! system_profiler SPAudioDataType 2>/dev/null | grep -q "BlackHole 16ch"; then
  print -u2 "BlackHole was installed but both drivers are not loaded yet. Reboot this Mac, then rerun ./scripts/bootstrap-macos.sh. No Robin services were installed."
  exit 75
fi
"$ROOT/apps/mac-helper/.build/release/RobinMacHelper" configure-audio
"$ROOT/scripts/configure-desktop.sh"
print "Bootstrap preparation complete. Store the two Keychain secrets, grant helper permissions, sign in to Zoom, then run ./scripts/install-launchd.sh. See docs/DEPLOY.md."
