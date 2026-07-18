#!/usr/bin/env bash
set -euo pipefail

uv run uvicorn robin_core.main:app --app-dir apps/core --host 127.0.0.1 --port 8787 &
CORE_PID=$!
pnpm --dir apps/web dev &
WEB_PID=$!

cleanup() {
  kill "$CORE_PID" "$WEB_PID" 2>/dev/null || true
}
trap cleanup EXIT

wait
