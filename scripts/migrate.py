#!/usr/bin/env python3
"""Apply/rollback migrations: python scripts/migrate.py up|down [NNN]. Down scripts are exercised in the L1 rollback drill."""
import asyncio, sys
from pathlib import Path
import asyncpg
from src.config import get_settings

async def main(direction: str) -> None:
    c = await asyncpg.connect(get_settings().pg_dsn)
    files = sorted(Path("migrations").glob(f"*.{direction}.sql"), reverse=(direction == "down"))
    for f in files:
        await c.execute(f.read_text()); print(direction, f.name)
    await c.close()

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "up"))
