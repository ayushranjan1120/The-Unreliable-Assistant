#!/usr/bin/env python3
"""One-time/idempotent setup for a managed Postgres (Neon): migrate with the OWNER role, create a NOBYPASSRLS app role, write
PG_DSN into .env (never printed), and PROVE tenant row-level security is enforced for the app role.
Needs PG_ADMIN_DSN in .env. Re-running keeps the existing app password unless PG_DSN is missing from .env."""
import asyncio, os, re, secrets, sys, uuid
from pathlib import Path
from urllib.parse import quote
import asyncpg
from src.config import ROOT
from src.repo import pg_connect_args

ENV = ROOT / ".env"
APP_ROLE = "ua_app"

async def main() -> int:
    admin_raw = os.environ.get("PG_ADMIN_DSN")
    if not admin_raw:
        print("PG_ADMIN_DSN missing in .env"); return 2
    dsn, kw = pg_connect_args(admin_raw)
    a = await asyncpg.connect(dsn, **kw)
    print("owner connected; applying migrations…")
    for f in sorted(Path("migrations").glob("*.up.sql")):
        await a.execute(f.read_text()); print("  up", f.name)
    have_dsn = bool(re.search(r"^PG_DSN=.+", ENV.read_text(), re.M))
    exists = await a.fetchval("select 1 from pg_roles where rolname=$1", APP_ROLE)
    pw = None
    if not exists or not have_dsn:
        pw = secrets.token_urlsafe(24)
        await a.execute(f"{'ALTER' if exists else 'CREATE'} ROLE {APP_ROLE} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS")
    await a.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    await a.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    await a.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}")
    flags = dict(await a.fetchrow("select rolbypassrls, rolsuper from pg_roles where rolname=$1", APP_ROLE))
    print("app role flags:", flags)
    host = re.search(r"@([^/?]+)/([^?]+)", admin_raw)
    if pw:
        with ENV.open("a") as f:
            f.write(f"\n# written by scripts/neon_setup.py (app role, RLS enforced)\nPG_DSN=postgresql://{APP_ROLE}:{quote(pw)}@{host.group(1)}/{host.group(2)}?sslmode=require\nSTATE_TIMEOUT_MS=15000\nPG_COMMAND_TIMEOUT=20\n")
        print("PG_DSN (app role) written to .env")
    await a.close()
    # ---- prove RLS with the APP role ----
    env = dict(l.split("=", 1) for l in ENV.read_text().splitlines() if "=" in l and not l.startswith("#"))
    d2, kw2 = pg_connect_args(env["PG_DSN"])
    c = await asyncpg.connect(d2, **kw2)
    t1, t2 = f"tenant_rls_{uuid.uuid4().hex[:6]}", f"tenant_rls_{uuid.uuid4().hex[:6]}"
    async with c.transaction():
        await c.execute("select set_config('app.tenant_id',$1,true)", t1)
        await c.execute("insert into lot_state (tenant_id, lot, status) values ($1,'LOT-00001','X')", t1)
        own = await c.fetchval("select count(*) from lot_state")
    async with c.transaction():
        await c.execute("select set_config('app.tenant_id',$1,true)", t2)
        other = await c.fetchval("select count(*) from lot_state")
        try:
            async with c.transaction():
                await c.execute("insert into lot_state (tenant_id, lot, status) values ($1,'LOT-00002','X')", t1)
            cross_write = "ALLOWED (BAD)"
        except asyncpg.PostgresError:
            cross_write = "blocked"
    async with c.transaction():
        await c.execute("select set_config('app.tenant_id',$1,true)", t1); await c.execute("delete from lot_state where tenant_id=$1", t1)
    await c.close()
    ok = own == 1 and other == 0 and cross_write == "blocked" and not flags["rolbypassrls"]
    print(f"RLS proof as {APP_ROLE}: own-tenant rows={own} · other-tenant rows={other} · cross-tenant write {cross_write} → {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1

sys.exit(asyncio.run(main()))
