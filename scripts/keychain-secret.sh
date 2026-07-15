#!/bin/zsh
set -euo pipefail
SERVICE="com.robin.agent"
usage(){ print -u2 "Usage: $0 set|generate|copy|check|delete OPENAI_API_KEY|ROBIN_PANEL_TOKEN"; exit 2; }
[[ $# -eq 2 ]] || usage
ACTION="$1"; NAME="$2"
[[ "$NAME" == "OPENAI_API_KEY" || "$NAME" == "ROBIN_PANEL_TOKEN" ]] || usage
case "$ACTION" in
  set) read -s "VALUE?Enter $NAME: "; print; [[ ${#VALUE} -ge 20 ]] || { unset VALUE; print -u2 "$NAME must be at least 20 characters."; exit 1; }; security add-generic-password -U -a "$USER" -s "$SERVICE.$NAME" -w "$VALUE" >/dev/null; unset VALUE; print "$NAME stored in Keychain." ;;
  generate) [[ "$NAME" == "ROBIN_PANEL_TOKEN" ]] || usage; VALUE="$(openssl rand -base64 32)"; security add-generic-password -U -a "$USER" -s "$SERVICE.$NAME" -w "$VALUE" >/dev/null; unset VALUE; print "$NAME generated and stored in Keychain without displaying it." ;;
  copy) [[ "$NAME" == "ROBIN_PANEL_TOKEN" ]] || usage; security find-generic-password -a "$USER" -s "$SERVICE.$NAME" -w | pbcopy; ( sleep 60; print -n "" | pbcopy ) >/dev/null 2>&1 &!; print "$NAME copied to the clipboard for initial panel sign-in; the clipboard will clear in 60 seconds." ;;
  check) security find-generic-password -a "$USER" -s "$SERVICE.$NAME" >/dev/null 2>&1 && print "$NAME is present." || { print "$NAME is missing."; exit 1; } ;;
  delete) security delete-generic-password -a "$USER" -s "$SERVICE.$NAME" >/dev/null; print "$NAME deleted." ;;
  *) usage ;;
esac
