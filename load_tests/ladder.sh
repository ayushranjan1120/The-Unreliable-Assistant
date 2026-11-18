#!/usr/bin/env bash
# Run one rung of the ladder (design §1.11): load_tests/ladder.sh <SMOKE|L2|L3|L4|L5> [--record <version>]
# Env: SCALE (divide VU targets; REQUIRED >1 on hosts that cannot generate the load), THINK_S (5 = certification, 1 = overload),
#      PROFILE (dev-cpu|target), BASE_URL. Preconditions: injector health (CPU<70% idle check), baseline snapshot, rollback target noted.
set -euo pipefail
cd "$(dirname "$0")/.."
RUNG="${1:?rung}"; SCALE="${SCALE:-1}"; THINK_S="${THINK_S:-5}"; PROFILE="${PROFILE:-dev-cpu}"; BASE_URL="${BASE_URL:-http://localhost:8000}"
REC=""; [ "${2:-}" = "--record" ] && REC="--record ${3:-v0}"
K6="${K6:-$HOME/.local/bin/k6}"
idle=$(vmstat 1 2 | tail -1 | awk '{print $15}')
echo "[ladder] pre-run injector/host idle CPU: ${idle}%"
[ "${idle:-0}" -lt 30 ] && { echo "[ladder] ABORT: host CPU too busy to produce a valid run (SC-14)"; exit 3; }
curl -sf "$BASE_URL/healthz" >/dev/null || { echo "[ladder] ABORT: backend not healthy"; exit 4; }
mkdir -p artifacts/load; cp -f artifacts/kpi_runs.jsonl "artifacts/load/baseline_$(date +%s).jsonl" 2>/dev/null || true
if [ "$PROFILE" != "target" ] || [ "$SCALE" != "1" ]; then echo "[ladder] NOTE: profile=$PROFILE scale=1/$SCALE → result is NOT CERTIFIABLE (design §1.16)"; fi
set +e
RUNG=$RUNG SCALE=$SCALE THINK_S=$THINK_S BASE_URL=$BASE_URL "$K6" run --quiet --out json=artifacts/load/points_$RUNG.json --summary-export=artifacts/load/summary_$RUNG.json load_tests/k6_50k_load.js
rc=$?
set -e
[ $rc -eq 99 ] && echo "[ladder] k6 thresholds FAILED (exit 99) — see summary; write a failure note"
[ $rc -eq 108 ] && echo "[ladder] ABORTED by abort-condition threshold (exit 108) — ROLLBACK then failure note"
.venv/bin/python -m load_tests.parse_k6 "$RUNG" artifacts/load/points_$RUNG.json artifacts/load/summary_$RUNG.json "$(.venv/bin/python -c "print(max(1,round({'SMOKE':100,'L2':5000,'L3':10000,'L4':25000,'L5':50000}['$RUNG']/$SCALE)))")" "$THINK_S" "$SCALE" "$PROFILE" $REC
exit $rc
