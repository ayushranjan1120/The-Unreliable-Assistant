#!/usr/bin/env bash
# Lock design.md (Gate 1). Records its sha256 in DESIGN.lock. Requires explicit review confirmation.
set -euo pipefail
cd "$(dirname "$0")/.."
[[ "${1:-}" == "--i-reviewed" ]] || { echo "Refusing: pass --i-reviewed after ticking the Review Checklist at the end of design.md." >&2; exit 1; }
if grep -qE '^- \[ \]' design.md; then
  echo "Refusing: unticked items remain in design.md Review Checklist:" >&2
  grep -nE '^- \[ \]' design.md >&2
  exit 1
fi
hash=$(sha256sum design.md | cut -d' ' -f1)
ver=$(grep -m1 -oE 'D[0-9]+\.[0-9]+(-DRAFT)?' design.md)
printf '%s  design.md  %s  locked %s\n' "$hash" "$ver" "$(date -u +%FT%TZ)" > DESIGN.lock
printf '| %s | design %s locked | sha256 %s |\n' "$(date -u +%F)" "$ver" "${hash:0:12}" >> VERSIONS.md
echo "Locked $ver (${hash:0:12}). Application code is now permitted."
