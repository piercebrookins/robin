#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

CHROME="${ROBIN_CHROME_EXECUTABLE:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
PROFILE_DIR="${ROBIN_CHROME_PROFILE_DIR:-$HOME/Library/Application Support/Robin/Chrome}"
PORT="${ROBIN_CHROME_DEBUG_PORT:-9222}"
START_URL="${1:-https://meet.google.com}"

if [[ ! -x "$CHROME" ]]; then
  echo "Google Chrome executable not found: $CHROME" >&2
  echo "Set ROBIN_CHROME_EXECUTABLE=/path/to/Chrome if it is installed elsewhere." >&2
  exit 1
fi

mkdir -p "$PROFILE_DIR"

if curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "Robin Chrome debug endpoint is already available at http://127.0.0.1:$PORT"
  echo "Leave that Chrome window open, then run:"
  echo "  ROBIN_REAL_MEET_URL=https://meet.google.com/... make smoke-real-meet"
  exit 0
fi

echo "Launching Robin's dedicated Chrome profile with remote debugging enabled."
echo "Profile: $PROFILE_DIR"
echo "Debug:   http://127.0.0.1:$PORT"
echo
echo "Important:"
echo "- This is not your normal Chrome profile. Chrome requires a non-default profile for remote debugging."
echo "- Sign into Robin's Google account in the Chrome window that opens."
echo "- Leave the window open while running real Meet smoke tests."
echo

"$CHROME" \
  --remote-debugging-port="$PORT" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --autoplay-policy=no-user-gesture-required \
  "$START_URL" >/tmp/robin-chrome.log 2>&1 &

deadline=$((SECONDS + 20))
until curl -fsS "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    echo "Chrome opened, but the debug endpoint did not become available." >&2
    echo "Check /tmp/robin-chrome.log for Chrome startup errors." >&2
    exit 1
  fi
  sleep 0.5
done

echo "Robin Chrome is ready at http://127.0.0.1:$PORT"
echo "Next: sign in inside that Chrome window, then run:"
echo "  ROBIN_REAL_MEET_URL=https://meet.google.com/... make smoke-real-meet"
