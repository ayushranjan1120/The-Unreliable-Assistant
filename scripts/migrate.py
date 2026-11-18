#!/usr/bin/env python3
"""Apply/rollback migrations: python -m scripts.migrate up|down. Uses PG_ADMIN_DSN (schema owner) when set, else PG_DSN."""
import asyncio, os, sys
from pathlib import Path
import asyncpg
from src.config import get_settings
from src.repo import pg_connect_args

async def main(direction: str) -> None:
    dsn, kw = pg_connect_args(os.environ.get("PG_ADMIN_DSN") or get_settings().pg_dsn)
    c = await asyncpg.connect(dsn, **kw)
    for f in sorted(Path("migrations").glob(f"*.{direction}.sql"), reverse=(direction == "down")):
        await c.execute(f.read_text()); print(direction, f.name)
    await c.close()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "up"))
