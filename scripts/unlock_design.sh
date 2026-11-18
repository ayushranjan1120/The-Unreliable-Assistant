#!/usr/bin/env bash
# Unlock design.md for an amendment. A reason is mandatory and is recorded in VERSIONS.md.
set -euo pipefail
cd "$(dirname "$0")/.."
reason="${1:-}"
[[ -n "$reason" ]] || { echo "Usage: $0 \"<reason for design change>\"" >&2; exit 1; }
[[ -f DESIGN.lock ]] || { echo "Already unlocked." >&2; exit 0; }
rm DESIGN.lock
printf '| %s | design UNLOCKED | %s |\n' "$(date -u +%F)" "$reason" >> VERSIONS.md
echo "Unlocked. Edit design.md, bump its version, re-tick the checklist, then re-run scripts/lock_design.sh --i-reviewed."
