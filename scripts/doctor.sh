#!/bin/zsh
set -euo pipefail
cd "${0:A:h:h}"
exec npm run doctor
