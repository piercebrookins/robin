#!/bin/zsh
set -euo pipefail
ROOT="${0:A:h:h}"
BINARY="$ROOT/apps/mac-helper/.build/release/RobinMacHelper"
[[ -x "$BINARY" ]] || { print -u2 "Build the helper first: npm run mac-helper:build"; exit 1; }
IDENTITY="${ROBIN_CODESIGN_IDENTITY:--}"
codesign --force --options runtime --timestamp=none --sign "$IDENTITY" "$BINARY"
codesign --verify --strict --verbose=2 "$BINARY"
print "Signed RobinMacHelper with identity: $IDENTITY"
