#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUN_TESTS=1
START_APP=1
REAL_MEET=0

usage() {
  cat <<'EOF'
Usage: scripts/setup_partner.sh [options]

Options:
  --real-meet     Switch config to real Chrome + process audio bridge mode.
  --skip-tests    Install/build/start without running the full test suite.
  --no-start      Install/build/test without starting Robin.
  -h, --help      Show this help.

The default path sets up simulator mode, validates the app, starts Robin, and
prints the dashboard URL. Real Google Meet testing still requires Chrome login,
BlackHole 2ch, and macOS Screen Recording/Accessibility permissions.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --real-meet)
      REAL_MEET=1
      shift
      ;;
    --skip-tests)
      RUN_TESTS=0
      shift
      ;;
    --no-start)
      START_APP=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

log() {
  printf "\n\033[1;34m==>\033[0m %s\n" "$1"
}

warn() {
  printf "\033[1;33mwarning:\033[0m %s\n" "$1"
}

fail() {
  printf "\033[1;31merror:\033[0m %s\n" "$1" >&2
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}

ensure_macos_tools() {
  if [[ "$(uname -s)" != "Darwin" ]]; then
    warn "Robin's real Meet/audio path is Mac-specific. Simulator setup can still run on this machine if dependencies work."
    return
  fi
  if ! xcode-select -p >/dev/null 2>&1; then
    xcode-select --install || true
    fail "Install Xcode Command Line Tools, then rerun this script."
  fi
}

ensure_uv() {
  if has_cmd uv; then
    return
  fi
  log "Installing uv"
  if has_cmd brew; then
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
  fi
  has_cmd uv || fail "uv is still unavailable. Install uv and rerun this script."
}

ensure_node_and_pnpm() {
  if ! has_cmd node; then
    if has_cmd brew; then
      log "Installing Node.js"
      brew install node
    else
      fail "Node.js is missing. Install Node 22+ or Homebrew, then rerun this script."
    fi
  fi

  local major
  major="$(node -p 'Number(process.versions.node.split(".")[0])' 2>/dev/null || echo 0)"
  if [[ "$major" -lt 22 ]]; then
    fail "Node.js 22+ is required. Current version: $(node -v). Upgrade Node, then rerun this script."
  fi

  if has_cmd pnpm; then
    return
  fi
  log "Enabling pnpm"
  if has_cmd corepack; then
    corepack enable
    corepack prepare pnpm@10.30.0 --activate
  elif has_cmd brew; then
    brew install pnpm
  else
    fail "pnpm is missing and corepack is unavailable. Install pnpm, then rerun this script."
  fi
  has_cmd pnpm || fail "pnpm is still unavailable. Install pnpm and rerun this script."
}

write_env() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
  fi

  if ! grep -q '^ROBIN_CONFIG_PATH=' .env; then
    printf '\nROBIN_CONFIG_PATH=config/robin.example.yaml\n' >> .env
  fi

  if grep -Eq '^OPENAI_API_KEY=.+$' .env; then
    return
  fi

  local key
  if [[ -n "${OPENAI_API_KEY:-}" ]]; then
    key="$OPENAI_API_KEY"
  else
    printf "Paste OPENAI_API_KEY, then press Enter. Input is hidden: "
    IFS= read -r -s key
    printf "\n"
  fi

  if [[ -z "$key" ]]; then
    fail "OPENAI_API_KEY is required for OpenAI-backed smoke tests."
  fi

  python - "$key" <<'PY'
from pathlib import Path
import sys

path = Path(".env")
key = sys.argv[1]
lines = path.read_text().splitlines()
updated = False
for index, line in enumerate(lines):
    if line.startswith("OPENAI_API_KEY="):
        lines[index] = f"OPENAI_API_KEY={key}"
        updated = True
        break
if not updated:
    lines.append(f"OPENAI_API_KEY={key}")
path.write_text("\n".join(lines) + "\n")
PY
  chmod 600 .env
}

set_real_meet_config() {
  python - <<'PY'
from pathlib import Path

path = Path("config/robin.example.yaml")
text = path.read_text()
replacements = {
    'mode: "simulator"': 'mode: "openai"',
    'bridge_mode: "simulator"': 'bridge_mode: "process"',
    'bridge_executable: null': 'bridge_executable: "./apps/macos-bridge/.build/debug/robin-macos-bridge"',
    'automation_mode: "simulator"': 'automation_mode: "playwright"',
    'executable_path: null': 'executable_path: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"',
}
for old, new in replacements.items():
    text = text.replace(old, new, 1)
path.write_text(text)
PY
}

real_meet_prereq_notes() {
  if [[ "$REAL_MEET" -ne 1 ]]; then
    return
  fi
  log "Real Meet prerequisites"
  [[ -x "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" ]] || warn "Google Chrome was not found at /Applications/Google Chrome.app."
  if [[ ! -d "/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver" && ! -d "/Library/Audio/Plug-Ins/HAL/BlackHole16ch.driver" ]]; then
    warn "BlackHole was not found. Install BlackHole 2ch before real audio loopback testing."
  fi
  cat <<'EOF'
Before real Meet smoke:
1. Open Chrome and sign in with Robin's dedicated Google account.
2. Grant Screen Recording and Accessibility to the terminal/app running Robin.
3. In Google Meet settings, choose BlackHole 2ch as Robin's microphone.
4. Have a second participant join the same Meet to verify Robin can be heard.
EOF
}

log "Checking local toolchain"
ensure_macos_tools
ensure_uv
ensure_node_and_pnpm

log "Creating .env"
write_env

if [[ "$REAL_MEET" -eq 1 ]]; then
  log "Switching config to real Meet mode"
  set_real_meet_config
fi

log "Installing Python dependencies"
uv sync

log "Installing web dependencies"
pnpm install --frozen-lockfile

log "Building macOS bridge"
swift build --package-path apps/macos-bridge

log "Seeding demo workspace"
uv run python scripts/seed_demo_workspace.py

if [[ "$RUN_TESTS" -eq 1 ]]; then
  log "Running validation suite"
  uv run pytest
  pnpm --dir apps/web test
  pnpm --dir apps/web typecheck
  make smoke-bridge
fi

if [[ "$START_APP" -eq 1 ]]; then
  log "Starting Robin"
  make demo-reset
  make preflight
  make smoke-test
fi

real_meet_prereq_notes

cat <<'EOF'

Robin setup finished.

Dashboard: http://127.0.0.1:3000
Core API:   http://127.0.0.1:8787/docs

Useful next commands:
  make preflight
  make smoke-test
  ROBIN_REAL_MEET_URL=https://meet.google.com/... make smoke-real-meet

Logs are under:
  RobinWorkspace/sessions/logs/
  RobinWorkspace/sessions/traces/
EOF
