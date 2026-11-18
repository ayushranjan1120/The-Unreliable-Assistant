#!/usr/bin/env bash
# Collect a SAFE diagnostics bundle to paste when something is stuck. Secrets are masked; env var NAMES only, never values.
# Usage: scripts/diagnose.sh > diag.txt     (review diag.txt, then share it)
cd "$(dirname "$0")/.."
mask() { sed -E 's#(postgres(ql)?://[^:/@ ]+):[^@ ]+@#\1:<masked>@#g; s#(gsk_|sk-or-v1-|npg_|ghp_|github_pat_)[A-Za-z0-9_-]+#\1<masked>#g; s#(Bearer )[A-Za-z0-9._-]+#\1<masked>#g'; }
echo "## when: $(date -u +%FT%TZ)  host: $(uname -sr)  cpus: $(nproc)  mem: $(free -g | awk '/Mem/{print $2"G"}')"
echo "## python: $(.venv/bin/python --version 2>&1)   git: $(git rev-parse --short HEAD 2>&1) $(git describe --tags --always 2>&1)"
echo "## design lock: $(cut -c1-12 DESIGN.lock 2>&1)"
echo; echo "## .env present (NAMES only, '<set>'/'<empty>'):"
[ -f .env ] && sed -E 's/^([A-Za-z_]+)=(.*)$/\1=\2/' .env | grep -v '^#' | grep -v '^$' | awk -F= '{print $1"="(length($2)>0?"<set>":"<empty>")}' || echo "  .env MISSING (copy .env.example)"
echo; echo "## services:"
scripts/dev_stack.sh status 2>&1
for p in 8000 8081 6379 8501; do printf "  port %s: " $p; (ss -ltn | grep -q ":$p " && echo listening || echo closed); done
redis-cli ping 2>&1 | sed 's/^/  redis: /'
echo; echo "## health:"; curl -s -m 5 localhost:8000/healthz 2>&1 | sed 's/^/  api: /'; echo
echo "## admin status (servers/plugins/levers):"; curl -s -m 8 localhost:8000/api/v1/admin/status 2>&1 | .venv/bin/python -c "import sys,json
try:
    d=json.load(sys.stdin); print(' mcp:',[(s['server'],s['state'],s['error'][:60]) for s in d['mcp_servers']]); print(' llm:',d.get('llm')); print(' cache:',d['cache']); print(' levers:',d['levers'])
except Exception as e: print(' (api not reachable)',type(e).__name__)" 2>&1
echo; echo "## database reachability (masked):"; .venv/bin/python - <<'PY' 2>&1 | mask
import asyncio, time, asyncpg
from src.config import get_settings
from src.repo import pg_connect_args
async def main():
    d, kw = pg_connect_args(get_settings().pg_dsn); t = time.perf_counter()
    try:
        c = await asyncpg.connect(d, timeout=15, **kw); await c.fetchval("select 1"); print(f"  connect+query ok in {(time.perf_counter()-t)*1000:.0f} ms; tables:", await c.fetchval("select count(*) from information_schema.tables where table_schema='public'")); await c.close()
    except Exception as e: print("  DB ERROR:", type(e).__name__, str(e)[:150])
asyncio.run(main())
PY
echo; echo "## KPI evidence summary:"; .venv/bin/python -m scripts.audit_check 2>&1 | grep -E "FAIL|BUILD-TIME|KPI EVIDENCE" | mask
echo; echo "## last log lines (masked):"; for f in /tmp/ua_api.log /tmp/ua_state.log; do echo "--- $f"; tail -n 15 $f 2>&1 | grep -viE "Processing request" | mask; done
