#!/usr/bin/env bash
# Certification rung runner (design §1.11). Usage: scripts/certify.sh <L2|L3|L4|L5> [version]
# REFUSES to run unless the environment is a valid TARGET deployment, so a dev-host result can never be recorded as certified.
set -euo pipefail
cd "$(dirname "$0")/.."
RUNG="${1:?rung L2|L3|L4|L5}"; VER="${2:-v0.2}"
fail() { echo "[certify] REFUSED: $1" >&2; exit 2; }
[ "${PROFILE:-}" = "target" ] || fail "PROFILE must be 'target' (got '${PROFILE:-unset}')"
[ "${TIME_SCALE:-1}" = "1" ] || fail "TIME_SCALE must be 1 (no time dilation) on the target"
[ "${SCALE:-1}" = "1" ] || fail "SCALE must be 1 (full VU counts)"
[ "${THINK_S:-5}" = "5" ] || echo "[certify] NOTE: THINK_S=${THINK_S} → this is the OVERLOAD scenario, not a latency certification"
[ "${LLM_BACKEND:-}" = "openai_compat" ] || fail "LLM_BACKEND must be openai_compat (self-hosted vLLM/TRT-LLM); hosted free-tier APIs cannot sustain this load"
command -v nvidia-smi >/dev/null && nvidia-smi -L | head -1 || fail "no GPU visible (nvidia-smi)"
[ -n "${BASE_URL:-}" ] || fail "BASE_URL (gateway URL, not localhost) is required"
echo "$BASE_URL" | grep -qE "localhost|127\.0\.0\.1" && fail "BASE_URL must be the gateway, injectors must NOT share the SUT host (SC-14)"
python -m scripts.audit_check || fail "build-time adherence check failed"
git diff --quiet || fail "working tree is dirty; certify a tagged, reproducible build"
echo "[certify] preconditions OK for $RUNG on $VER; running…"
THINK_S=5 load_tests/ladder.sh "$RUNG" --record "$VER"
python -m scripts.make_vr "$VER"
echo "[certify] done. Inspect failure_notes/reports/VR-$VER.md; on any miss write FN-$VER-NNN first (CLAUDE.md), then fix/rollback per design §1.11."
