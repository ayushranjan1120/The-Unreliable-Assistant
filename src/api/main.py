"""FastAPI app: POST /api/v1/agent/query + health + dev-only admin endpoints for the Streamlit workbench.

Stateless per request (all state in Redis/Postgres); one MCP host per worker process.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import orjson
import redis.asyncio as aioredis
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import ORJSONResponse
from pydantic import ValidationError

from src import telemetry
from src.agent import llm as llmmod
from src.agent.runtime import AgentRuntime
from src.agent.verify import NliVerifier
from src.cache import TwoTierCache
from src.config import ROOT, get_settings
from src.contracts.models import AgentQueryIn, HybridRetrieveIn
from src.errors import HTTP_STATUS, Code, ToolError
from src.graphify import GraphIndex
from src.mcp_host import McpHost
from src.plugins.hooks import PluginMeta, PluginRegistry, load_config
from src.plugins.injection import InjectionShield
from src.plugins.injection_clf import InjectionClassifier
from src.plugins.pii import PiiRedactor
from utils import results_store

S = get_settings()
RT: AgentRuntime | None = None
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global RT
    telemetry.init_otel()
    redis = aioredis.from_url(S.redis_url, decode_responses=True, max_connections=200)
    try:
        await redis.ping()
    except Exception:
        redis = None                                              # FMEA #1: Redis down → per-worker LRU, still serving
    host = McpHost(S)
    await host.start()
    clf = InjectionClassifier() if os.environ.get("INJECTION_CLF", "1") == "1" else None
    reg = PluginRegistry()
    classes = {"pii_redactor": PiiRedactor(), "injection_shield": InjectionShield(clf)}
    for meta in load_config(ROOT / "mcp_config.json"):
        if meta.name in classes:
            reg.register(classes[meta.name], meta)
        else:
            reg.meta[meta.name] = meta                               # declarative-only plugins (circuit_breaker/ragas_auditor/otel_exporter) are toggled via flags
    graph = GraphIndex(S.vault_dir, S.graph_path)
    llm = llmmod.make_llm(S.manifest.llm_backend, S.manifest.llm_model)
    RT = AgentRuntime(S, host, TwoTierCache(redis, "p1", S.cache_ttl_s, S.cache_jitter_s), reg, llm, NliVerifier(), graph, redis, clf)
    RT.audit.start()
    yield
    await host.stop()
    if redis:
        await redis.aclose()


app = FastAPI(title="Unreliable Assistant → Enterprise Grade", lifespan=lifespan, default_response_class=ORJSONResponse)


def _rt() -> AgentRuntime:
    assert RT is not None
    return RT


def _admin(tok: str | None) -> None:
    if ADMIN_TOKEN and tok != ADMIN_TOKEN:
        raise HTTPException(401, "admin token required")


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness (separate loop task group; never touches the pools)."""
    return {"ok": True, "version": S.version, "profile": S.profile}


@app.post("/api/v1/agent/query")
async def query(request: Request, x_tenant_id: str | None = Header(default=None)):
    try:
        body = AgentQueryIn.model_validate_json(await request.body())
    except ValidationError as e:
        return ORJSONResponse({"error": {"code": int(Code.VALIDATION_FAILED), "message": "invalid request", "data": {"fields": [x["loc"] for x in e.errors()][:5]}}}, 422)
    if x_tenant_id != body.tenant_id:                                         # -32007: header must equal body tenant
        return ORJSONResponse({"error": ToolError(Code.TENANT_VIOLATION, "X-Tenant-ID must equal tenant_id").to_jsonrpc()}, 403)
    o, _ = await _rt().handle(body, request.headers.get("traceparent"))
    return ORJSONResponse(o.model_dump(), headers={"traceparent": telemetry.make_traceparent(o.trace_id)})


@app.post("/api/v1/admin/query_debug")
async def query_debug(request: Request, x_admin_token: str | None = Header(default=None)):
    """Dev-only: same as /query plus ReAct steps, spans, triage and token accounting (Streamlit Tab 1/6)."""
    _admin(x_admin_token)
    body = AgentQueryIn.model_validate_json(await request.body())
    o, extras = await _rt().handle(body, request.headers.get("traceparent"))
    return ORJSONResponse({**o.model_dump(), **{k: v for k, v in extras.items() if k in ("react_steps", "spans", "triage", "retrieval", "tokens", "verify", "cache", "pii", "draft", "chunk_heads", "context")}})


@app.get("/api/v1/admin/status")
async def status(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    rt = _rt()
    return {"mcp_servers": rt.host.status(), "plugins": rt.plugins.describe(), "levers": rt.levers, "cache": {**rt.cache.stats, "hit_ratio": rt.cache.hit_ratio(), "l2_enabled": rt.cache.l2_enabled},
            "metrics": telemetry.METRICS.snapshot(), "runtime": {k: v for k, v in rt.metrics.items() if k != "drift"}, "audit": rt.audit.stats, "llm": {"backend": rt.llm.__class__.__name__, "router": getattr(rt.llm, "stats", None)}}


@app.post("/api/v1/admin/plugins/toggle")
async def plugin_toggle(body: dict, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    _rt().plugins.set_enabled(body["name"], bool(body["enabled"]))
    return {"ok": True}


@app.get("/api/v1/admin/mcp/rpc_log")
async def rpc_log(tenant_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return {"items": [e for e in list(_rt().host.log) if e.get("tenant_id") in (tenant_id, None)][-50:]}


@app.post("/api/v1/admin/rag_lab")
async def rag_lab(body: dict, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    rt = _rt()
    req = {"tenant_id": body["tenant_id"], "query": body["query"], "k1": body.get("k1", 1.25), "b": body.get("b", 0.75), "rrf_k": int(body.get("rrf_k", 60)),
           "threshold": body.get("threshold", 0.42), "idempotency_key": str(uuid.uuid4()), "trace_id": uuid.uuid4().hex}
    r = await rt.host.call("hybrid-rag", "hybrid_retrieve_debug", req, body["tenant_id"])
    return {"stage_ms": r["stage_ms"], "top_score": r["top_score"], "abstain": r["top_score"] < req["threshold"], "candidates": r["candidates"]}


@app.get("/api/v1/admin/hitl")
async def hitl_pending(tenant_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return await _rt().host.call("state-store", "hitl_list", {"tenant_id": tenant_id}, tenant_id)


@app.post("/api/v1/admin/hitl/decide")
async def hitl_decide(body: dict, x_tenant_id: str = Header(...), x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    try:
        return await _rt().host.call("state-store", "hitl_decide", {"tenant_id": x_tenant_id, **body}, x_tenant_id)
    except ToolError as e:
        return ORJSONResponse({"error": e.to_jsonrpc()}, HTTP_STATUS.get(e.code, 400))


@app.get("/api/v1/admin/graphify/subgraph")
async def subgraph(tenant_id: str, anchor: str, depth: int = 2, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    r = _rt().graph.subgraph(anchor, depth, 320)
    if r["anchor"] is None:
        raise HTTPException(404, "anchor not found")
    return r


@app.get("/api/v1/admin/vault/notes")
async def notes(tenant_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return {"notes": [{"id": i, "domain": n.domain} for i, n in _rt().graph.notes.items()]}


@app.get("/api/v1/admin/vault/note/{note_id}")
async def note(note_id: str, tenant_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    n = _rt().graph.notes.get(note_id)
    if n is None:
        raise HTTPException(404)
    return {"frontmatter": {"id": n.id, "domain": n.domain, "risk_tier": n.risk_tier, "version": n.version}, "body": n.body + "\n\n" + " ".join(f"[[{l}]]" for l in n.links)}


@app.get("/api/v1/admin/session")
async def session(tenant_id: str, session_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    rt = _rt()
    hot = []
    if rt.redis is not None:
        from src import keys
        hot = [json.loads(x) for x in await rt.redis.lrange(keys.hot_key(tenant_id, session_id), 0, 2)]
    try:
        facts = (await rt.host.call("state-store", "facts_get", {"tenant_id": tenant_id, "session_id": session_id, "idempotency_key": str(uuid.uuid4()), "trace_id": uuid.uuid4().hex}, tenant_id))["facts"]
    except ToolError:
        facts = []
    return {"hot": hot, "facts": facts}


@app.get("/api/v1/admin/tokens")
async def tokens(tenant_id: str, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    m = _rt().metrics
    if not m["completed"]:
        return {"n": 0}
    return {"n": m["completed"], "reduction_pct": 100 * (1 - m["tokens_prompt"] / max(m["tokens_baseline"], 1)), "cost_per_1k": 1000 * m["cost"] / m["completed"]}


@app.get("/api/v1/admin/breaker")
async def breaker(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return {"entries": _rt().breaker.entries()}


@app.post("/api/v1/admin/breaker/reset")
async def breaker_reset(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    await _rt().breaker.reset()
    return {"ok": True}


@app.post("/api/v1/admin/breaker/trip")
async def breaker_trip(x_tenant_id: str = Header("tenant_0"), x_admin_token: str | None = Header(default=None)):
    """Drive the infinite-SKU-retry scenario: repeated malformed SKU lookups until the breaker opens."""
    _admin(x_admin_token)
    from src.harness.harness import ReqCtx
    rc = ReqCtx(x_tenant_id, "user_rt", "operator", str(uuid.uuid4()), uuid.uuid4().hex)
    codes = []
    for _ in range(4):
        try:
            await _rt().h_exec.call("sku_lookup", {"sku": "SKU99999"}, rc)
        except ToolError as e:
            codes.append(e.code.name)
    return {"codes": codes}


@app.post("/api/v1/admin/redteam/run")
async def redteam_run(body: dict, x_tenant_id: str = Header("tenant_0"), x_admin_token: str | None = Header(default=None)):
    """End-to-end adversarial run through the live runtime (direct/indirect/pii/fuzz/sku loop/tenant probe)."""
    _admin(x_admin_token)
    from eval import redteam_live
    return await redteam_live.run(_rt(), body.get("scenarios", []), int(body.get("mutations", 3)), x_tenant_id)


@app.get("/api/v1/admin/load/latest")
async def load_latest(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    p = ROOT / "artifacts" / "load_latest.json"
    return json.loads(p.read_text()) if p.exists() else {}
