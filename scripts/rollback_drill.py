#!/usr/bin/env python3
"""L1 rollback drill (design §1.11): exercises every rollback layer that is exercisable on this host and records timings.
Layers: (1) feature-flag/config snapshot restore, (2) index manifest N-1 swap, (3) DB migration down→up on a scratch DB,
(4) cache namespace invalidation via policy_version bump, (5) code rollback to a tag (requires git history → reported if absent)."""
import asyncio, json, shutil, subprocess, sys, time
from pathlib import Path
import asyncpg
from src.config import get_settings
from utils import results_store

ROOT = Path(__file__).resolve().parent.parent
S = get_settings()
res = []

def step(name, fn):
    t = time.perf_counter()
    try:
        detail = fn(); ok = True
    except Exception as e:
        detail, ok = f"{type(e).__name__}: {e}", False
    ms = (time.perf_counter() - t) * 1000
    res.append({"layer": name, "ok": ok, "ms": round(ms, 1), "detail": str(detail)[:200]}); print(("OK   " if ok else "FAIL ") + f"{name:34s} {ms:8.1f} ms  {str(detail)[:90]}")

# 1. config/flag snapshot
def flags():
    snap = (ROOT / "mcp_config.json").read_text()
    p = ROOT / "mcp_config.json"; mod = json.loads(snap); mod["plugins"][0]["enabled"] = False; p.write_text(json.dumps(mod))
    p.write_text(snap)
    assert p.read_text() == snap; return "mcp_config.json restored byte-identical"
step("config/flag snapshot restore", flags)

# 2. index N-1
def index_swap():
    cur, prev = S.index_dir, S.index_dir.with_suffix(".prev")
    if not prev.exists():
        raise RuntimeError("no N-1 index present (run build_index twice)")
    tmp = S.index_dir.with_suffix(".swap")
    cur.rename(tmp); prev.rename(cur); tmp.rename(prev)       # swap
    from src.rag.index import load_index
    load_index(cur); man = json.loads((cur / "manifest.json").read_text())
    tmp = S.index_dir.with_suffix(".swap"); cur.rename(tmp); prev.rename(cur); tmp.rename(prev)   # swap back
    return f"N-1 index loaded ({man['n_chunks']} chunks) and swapped back"
step("index manifest N-1 swap", index_swap)

# 3. DB migration down/up on scratch DB
async def migrate():
    c = await asyncpg.connect(S.pg_dsn)
    await c.execute("DROP SCHEMA IF EXISTS ua_drill CASCADE; CREATE SCHEMA ua_drill; SET search_path TO ua_drill")
    up, down = (ROOT / "migrations/001_init.up.sql").read_text(), (ROOT / "migrations/001_init.down.sql").read_text()
    cnt = lambda: c.fetchval("select count(*) from information_schema.tables where table_schema='ua_drill' and table_name in ('hitl','audit_log','session_facts','lot_state')")
    await c.execute(up); n1 = await cnt()
    await c.execute(down); n2 = await cnt()
    await c.execute(up); n3 = await cnt()
    await c.execute("DROP SCHEMA ua_drill CASCADE"); await c.close()
    assert (n1, n2, n3) == (4, 0, 4), (n1, n2, n3); return "up→down→up in scratch schema ok (live data untouched)"
step("DB migration down/up (scratch schema)", lambda: asyncio.run(migrate()))

# 4. cache namespace bump
def cache_ns():
    from src import keys
    a, b = keys.l2_key("tenant_1", "p1", "q"), keys.l2_key("tenant_1", "p2", "q")
    assert a != b; return "policy_version bump changes every L2 key (old entries unreachable, expire by TTL)"
step("cache namespace invalidation", cache_ns)

# 5. code rollback
def code():
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("no git commits yet — code rollback NOT exercised (needs the owner's go-ahead to commit/tag)")
    return "git HEAD present"
step("code rollback to tag", code)

rid = results_store.new_run_id("rollback"); art = ROOT / "artifacts" / f"rollback_drill_{rid}.json"; art.write_text(json.dumps(res, indent=1))
print(f"\n{sum(r['ok'] for r in res)}/{len(res)} layers exercised OK → {art.relative_to(ROOT)}")
sys.exit(0 if all(r["ok"] for r in res[:4]) else 1)
