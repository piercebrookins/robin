#!/bin/zsh
set -euo pipefail
SERVICE="com.robin.agent"
usage(){ print -u2 "Usage: $0 set|check|delete OPENAI_API_KEY|ROBIN_PANEL_TOKEN"; exit 2; }
[[ $# -eq 2 ]] || usage
ACTION="$1"; NAME="$2"
[[ "$NAME" == "OPENAI_API_KEY" || "$NAME" == "ROBIN_PANEL_TOKEN" ]] || usage
case "$ACTION" in
  set) read -s "VALUE?Enter $NAME: "; print; security add-generic-password -U -a "$USER" -s "$SERVICE.$NAME" -w "$VALUE" >/dev/null; unset VALUE; print "$NAME stored in Keychain." ;;
  check) security find-generic-password -a "$USER" -s "$SERVICE.$NAME" >/dev/null 2>&1 && print "$NAME is present." || { print "$NAME is missing."; exit 1; } ;;
  delete) security delete-generic-password -a "$USER" -s "$SERVICE.$NAME" >/dev/null; print "$NAME deleted." ;;
  *) usage ;;
esac
