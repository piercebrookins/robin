#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h}"
[[ "$(uname -m)" == "arm64" ]] || { print -u2 "Robin supports Apple-silicon Macs only."; exit 1; }
command -v brew >/dev/null || { print -u2 "Install Homebrew from https://brew.sh, then rerun."; exit 1; }
brew bundle --file "$ROOT/Brewfile"
cd "$ROOT"
npm ci
npm run build
npm run mac-helper:build
"$ROOT/scripts/sign-helper.sh"
"$ROOT/apps/mac-helper/.build/release/RobinMacHelper" configure-audio
"$ROOT/scripts/configure-desktop.sh"
"$ROOT/scripts/install-launchd.sh"
print "Bootstrap complete. Follow docs/DEPLOY.md to grant permissions and sign in to Zoom, then run npm run doctor."
