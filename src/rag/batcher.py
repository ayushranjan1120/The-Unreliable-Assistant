"""Dynamic micro-batching: collect items from concurrent requests for up to ``max_wait_ms`` or ``max_batch`` items."""
from __future__ import annotations

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Sequence


class MicroBatcher:
    """Async batcher around a blocking ``fn(list[item]) -> list[result]`` (runs in a thread pool)."""

    def __init__(self, fn: Callable[[list[Any]], Sequence[Any]], max_batch: int = 32, max_wait_ms: float = 8.0, workers: int = 2) -> None:
        self.fn, self.max_batch, self.max_wait = fn, max_batch, max_wait_ms / 1000
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self._q: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._last_end = 0.0
        self.stats = {"batches": 0, "items": 0, "max_seen": 0}

    def _ensure(self) -> asyncio.Queue:
        if self._q is None:
            self._q = asyncio.Queue()
            self._task = asyncio.get_running_loop().create_task(self._loop())
        return self._q

    async def submit(self, items: list[Any]) -> list[Any]:
        """Submit items; resolves to results in the same order."""
        q, loop = self._ensure(), asyncio.get_running_loop()
        futs = [loop.create_future() for _ in items]
        for it, f in zip(items, futs):
            q.put_nowait((it, f))
        return list(await asyncio.gather(*futs))

    async def _loop(self) -> None:
        loop = asyncio.get_running_loop()
        assert self._q is not None
        while True:
            batch = [await self._q.get()]
            deadline = time.monotonic() + self.max_wait
            await asyncio.sleep(0)                      # let same-tick submitters enqueue
            idle = time.monotonic() - self._last_end > self.max_wait
            while len(batch) < self.max_batch:
                try:
                    batch.append(self._q.get_nowait()); continue
                except asyncio.QueueEmpty:
                    pass
                rem = deadline - time.monotonic()
                if rem <= 0 or (idle and len(batch) == 1):   # idle & alone → dispatch now (no latency tax at low load)
                    break
                try:
                    batch.append(await asyncio.wait_for(self._q.get(), rem))
                except asyncio.TimeoutError:
                    break
            items = [b[0] for b in batch]
            self.stats["batches"] += 1; self.stats["items"] += len(items); self.stats["max_seen"] = max(self.stats["max_seen"], len(items))
            try:
                res = await loop.run_in_executor(self.pool, self.fn, items)
                for (_, f), r in zip(batch, res):
                    if not f.done():
                        f.set_result(r)
                self._last_end = time.monotonic()
            except Exception as e:  # propagate to every waiter
                for _, f in batch:
                    if not f.done():
                        f.set_exception(e)
