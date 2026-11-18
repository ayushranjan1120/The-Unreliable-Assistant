"""MCP host: persistent ClientSessions to the three servers with bulkheads, timeouts, a per-server circuit,
signed-manifest verification, and a JSON-RPC payload log (design Gate 0/9, FMEA #3/#5)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
from collections import deque
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from src import telemetry
from src.config import ROOT, Settings
from src.errors import Code, ToolError
from mcp_servers import manifest

CIRCUIT_OPEN_S = 5.0


class ServerConn:
    """One MCP server connection managed by a long-lived task (context managers must exit in the entering task)."""

    def __init__(self, name: str, cfg: dict, pool: int, timeout_ms: float, index_hash: str = "") -> None:
        self.name, self.cfg, self.timeout, self.index_hash = name, cfg, timeout_ms / 1000, index_hash
        self.sem = asyncio.Semaphore(pool)
        self.session: ClientSession | None = None
        self.state, self.err, self.open_until = "down", "", 0.0
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self.calls = self.errors = 0

    async def _runner(self) -> None:
        try:
            ok, why = manifest.verify(self.name, self.index_hash)
            if not ok:
                self.state, self.err = "refused", f"manifest verification failed: {why}"
                return
            async with AsyncExitStack() as st:
                if self.cfg["transport"] == "stdio":
                    py = str(ROOT / ".venv" / "bin" / "python")
                    params = StdioServerParameters(command=py, args=[str(ROOT / a) if a.endswith(".py") else a for a in self.cfg["args"] if a not in ("run",)], cwd=str(ROOT))
                    r, w = await st.enter_async_context(stdio_client(params))
                else:
                    r, w, _ = await st.enter_async_context(streamablehttp_client(self.cfg["url"]))
                self.session = await st.enter_async_context(ClientSession(r, w))
                await self.session.initialize()
                self.state, self.err = "up", ""
                self._ready.set()
                await self._stop.wait()
        except Exception as e:
            self.state, self.err = "down", f"{type(e).__name__}: {str(e)[:120]}"
        finally:
            self.session = None
            if self.state == "up":
                self.state = "down"
            self._ready.set()

    async def start(self, wait_s: float = 120.0) -> None:
        """Spawn/connect and wait for readiness."""
        self._ready.clear(); self._stop.clear()
        self._task = asyncio.get_running_loop().create_task(self._runner())
        try:
            await asyncio.wait_for(self._ready.wait(), wait_s)
        except asyncio.TimeoutError:
            self.state, self.err = "down", "startup timeout"

    async def stop(self) -> None:
        """Shut down the connection."""
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, 5)
            except Exception:
                self._task.cancel()

    async def restart(self) -> None:
        """Respawn (FMEA #3)."""
        await self.stop()
        await self.start()


class McpHost:
    """Facade used by the agent: ``await host.call('hybrid-rag', 'hybrid_retrieve', {...})``."""

    def __init__(self, s: Settings, config_path: Path | None = None) -> None:
        self.s = s
        cfg = json.loads((config_path or ROOT / "mcp_config.json").read_text())["mcpServers"]
        idx_manifest = ROOT / "artifacts" / "index" / "manifest.json"
        ih = hashlib.sha256(json.dumps(json.loads(idx_manifest.read_text()), sort_keys=True).encode()).hexdigest() if idx_manifest.exists() else ""
        pools = {"hybrid-rag": s.rag_pool, "state-store": s.db_pool, "graphify-vault": s.rag_pool}
        self.servers = {n: ServerConn(n, c, pools[n], (c.get("timeout_ms", 150) * s.time_scale) if n != "state-store" else float(os.environ.get("STATE_TIMEOUT_MS", 150 * s.time_scale)),
                                      ih if n == "hybrid-rag" else "") for n, c in cfg.items()}
        self.log: deque[dict] = deque(maxlen=300)
        self.m = telemetry.METRICS

    async def start(self) -> None:
        """Connect all servers concurrently (graceful if one fails: that server is 'down' and its circuit reports it)."""
        await asyncio.gather(*(c.start() for c in self.servers.values()))

    async def stop(self) -> None:
        await asyncio.gather(*(c.stop() for c in self.servers.values()), return_exceptions=True)

    def status(self) -> list[dict]:
        """Admin view of each server."""
        return [{"server": n, "transport": c.cfg["transport"], "state": c.state, "error": c.err, "calls": c.calls, "errors": c.errors,
                 "circuit_open": time.monotonic() < c.open_until, "timeout_ms": round(c.timeout * 1000)} for n, c in self.servers.items()]

    async def call(self, server: str, tool: str, args: dict, tenant_id: str | None = None) -> dict:
        """JSON-RPC tools/call with bulkhead + timeout + circuit. Raises ToolError(TOOL_TIMEOUT / DEPENDENCY_DEGRADED)."""
        c = self.servers[server]
        if time.monotonic() < c.open_until or c.session is None:
            raise ToolError(Code.DEPENDENCY_DEGRADED, f"{server} unavailable (circuit open)", {"server": server})
        t0 = time.perf_counter()
        entry = {"server": server, "method": tool, "tenant_id": tenant_id, "request": _redact_req(args), "response": None, "ok": False, "ms": 0.0}
        try:
            async with c.sem:
                with telemetry.span(f"mcp.rpc.{server}"):
                    res = await asyncio.wait_for(c.session.call_tool(tool, {"req": args}), c.timeout)
            rtt = (time.perf_counter() - t0) * 1000
            if res.isError:
                msg = res.content[0].text if res.content else "error"
                m = re.search(r"\[(-32\d{3})\]\s*(.*)", msg, re.S)      # re-raise the server's coded error faithfully
                if m and int(m.group(1)) in {c.value for c in Code}:
                    raise ToolError(Code(int(m.group(1))), m.group(2)[:200])
                raise ToolError(Code.TOOL_TIMEOUT, f"{server}.{tool}: {msg[:160]}")
            t1 = time.perf_counter()
            payload = json.loads(res.content[0].text) if res.content else {}
            serde = (time.perf_counter() - t1) * 1000
            srv_ms = payload.get("server_ms") or payload.get("ms")
            if srv_ms is not None:                                   # KPI-30: JSON-RPC overhead = RTT − server processing (+ client parse)
                self.m.observe("mcp.rpc.overhead", max(0.0, rtt - srv_ms) + serde)
            entry.update(ok=True, response=_trim(payload), ms=rtt)
            c.calls += 1
            return payload
        except asyncio.TimeoutError:
            c.errors += 1
            c.open_until = time.monotonic() + CIRCUIT_OPEN_S           # trip the server bulkhead circuit
            self.m.inc(f"mcp.{server}.timeout")
            entry.update(response={"error": "timeout"}, ms=(time.perf_counter() - t0) * 1000)
            raise ToolError(Code.TOOL_TIMEOUT, f"{server}.{tool} exceeded {c.timeout * 1000:.0f} ms")
        except ToolError as e:
            c.errors += 1
            entry.update(response={"error": e.message}, ms=(time.perf_counter() - t0) * 1000)
            raise
        except Exception as e:
            c.errors += 1
            c.open_until = time.monotonic() + CIRCUIT_OPEN_S
            entry.update(response={"error": type(e).__name__}, ms=(time.perf_counter() - t0) * 1000)
            asyncio.get_running_loop().create_task(c.restart())          # respawn in background
            raise ToolError(Code.DEPENDENCY_DEGRADED, f"{server} failed: {type(e).__name__}")
        finally:
            self.log.append(entry)


def _trim(o: Any, n: int = 600) -> Any:
    s = json.dumps(o, default=str)
    return o if len(s) <= n else {"_truncated": s[:n] + "…"}


def _redact_req(a: dict) -> dict:
    return {k: (v if k != "query" else v[:200]) for k, v in a.items()}
