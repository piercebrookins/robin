#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

KEEP_STATE=0
if [[ "${1:-}" == "--keep-state" ]]; then
  KEEP_STATE=1
fi

if [[ ! -f .env ]]; then
  echo "Robin needs .env. Run scripts/setup_partner.sh --real-meet once." >&2
  exit 1
fi

audio_status="$(PYTHONPATH=apps/core uv run python -c 'from robin_core.config import load_settings; s=load_settings(); print(f"{s.audio.mode}|{s.audio.bridge_mode}|{bool(s.openai_api_key)}|{s.audio.output_device_name}")')"
IFS='|' read -r audio_mode bridge_mode api_key_ready output_device <<< "$audio_status"
if [[ "$audio_mode" != "openai" || "$bridge_mode" != "process" || "$api_key_ready" != "True" ]]; then
  echo "Robin real audio is not configured (mode=$audio_mode, bridge=$bridge_mode, OpenAI key=$api_key_ready)." >&2
  echo "Run scripts/setup_partner.sh --real-meet --no-start, then retry make robin." >&2
  exit 1
fi
echo "Audio ready: OpenAI voice → $output_device; Chrome audio → ScreenCaptureKit transcription."

if [[ ! -x apps/macos-bridge/.build/debug/robin-macos-bridge ]]; then
  echo "Building the macOS audio bridge once…"
  swift build --package-path apps/macos-bridge
fi

if [[ "$KEEP_STATE" -eq 0 ]]; then
  echo "Preparing a clean rehearsal…"
  uv run python scripts/demo_reset.py
else
  echo "Restarting Robin while preserving rehearsal state…"
  uv run python scripts/demo_reset.py --stop-only
fi

if [[ ! -f apps/web/.next/BUILD_ID ]] || \
   [[ apps/web/next.config.ts -nt apps/web/.next/BUILD_ID ]] || \
   [[ apps/web/package.json -nt apps/web/.next/BUILD_ID ]] || \
   [[ -n "$(find apps/web/app apps/web/lib -type f -newer apps/web/.next/BUILD_ID -print -quit 2>/dev/null)" ]]; then
  echo "Building the lightweight dashboard runtime…"
  pnpm --dir apps/web build
fi

echo "Preparing Robin's Chrome session…"
scripts/launch_robin_chrome.sh about:blank

(
  for _ in {1..60}; do
    if curl -fsS http://127.0.0.1:3000 >/dev/null 2>&1 && \
       curl -fsS http://127.0.0.1:8787/health >/dev/null 2>&1; then
      open http://127.0.0.1:3000
      echo "Robin is ready. Paste the Meet link in the dashboard."
      exit 0
    fi
    sleep 0.5
  done
  echo "Robin did not become ready. Check RobinWorkspace/sessions/logs/." >&2
) &

echo "Starting Robin. Keep this terminal open; press Control-C to stop."
export ROBIN_WEB_MODE=production
exec .venv/bin/python scripts/robin.py dev
