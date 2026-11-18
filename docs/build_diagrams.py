#!/usr/bin/env python3
"""Generate the Gate-2 workflow diagram and the release-ladder diagram (300 DPI).

Usage:  python3 docs/build_diagrams.py            # writes PNGs next to this file
Needs:  `pip install graphviz` and the Graphviz `dot` binary.
Source of truth for the content is design.md (sections 1.3, 1.8, 1.11).
"""
from pathlib import Path
import graphviz

OUT = Path(__file__).resolve().parent
DPI = "300"

FONT = "Helvetica"
C = dict(  # one muted palette; layers are distinguished by cluster tint + label, not colour alone
    ingress="#E8F0FE", triage="#FFF4E0", mcp="#E6F4EA", control="#F3E8FD",
    egress="#FDE8E8", store="#EEEEEE", edge="#444444", warn="#B3261E", ok="#137333",
)


def base(name: str, rankdir="LR") -> graphviz.Digraph:
    g = graphviz.Digraph(name, format="png")
    g.attr(rankdir=rankdir, dpi=DPI, fontname=FONT, fontsize="20", labelloc="t",
           pad="0.4", nodesep="0.35", ranksep="0.7", compound="true", newrank="true",
           bgcolor="white")
    g.attr("node", shape="box", style="rounded,filled", fillcolor="white",
           fontname=FONT, fontsize="12", color=C["edge"], margin="0.14,0.08")
    g.attr("edge", fontname=FONT, fontsize="10", color=C["edge"], arrowsize="0.8")
    return g


def cluster(parent, name, label, fill):
    sub = graphviz.Digraph(name=f"cluster_{name}")
    sub.attr(label=label, style="rounded,filled", fillcolor=fill, color="#9AA0A6",
             fontname=FONT, fontsize="15", fontcolor="#202124", labeljust="l", margin="16")
    return sub


def workflow() -> graphviz.Digraph:
    g = base("workflow_orchestration", rankdir="TB")
    g.attr(ranksep="0.9", nodesep="0.5")
    g.attr(label="Workflow Orchestration - MCP-enabled agentic pipeline "
                 "(design.md D1.0, section 1.3 / 1.8)\\n"
                 "End-to-end budget: p95 <= 450 ms, p99 <= 590 ms @ 50K VUs", fontsize="22")

    g.node("client", "Client\\n(k6 / JMeter / UI)", shape="oval", fillcolor="#FFFFFF")

    # ---- 1. Ingress & security -------------------------------------------------
    s = cluster(g, "ingress", "1  INGRESS & SECURITY LAYER", C["ingress"])
    s.node("edge", "Envoy / NGINX\\nadmission control, keep-alive TLS\\n~5 ms")
    s.node("pii", "Pre-Hook 1: PII Redactor\\n(fail-closed, prio 10)")
    s.node("inj", "Pre-Hook 2: Injection Classifier\\n(fail-closed, prio 20)")
    s.node("cache", "Redis Semantic Cache Check\\nL1 per-user exact | L2 tenant-scoped\\nkey t:{tenant}:..., TTL 300s +/- 45s\\ncache.lookup <= 6 ms",
           fillcolor="#FFFFFF")
    s.node("hookbox", "hook.pre total <= 15 ms (KPI-01)", shape="note", fillcolor="#FFFFFF", fontsize="10")
    s.node("redis", "Redis Cluster\\n(tenant hash-tag)", shape="cylinder", fillcolor="#EEEEEE")
    s.edge("edge", "pii")
    s.edge("pii", "inj", label="redacted text")
    s.edge("inj", "cache", label="clean query")
    g.subgraph(s)

    g.node("block", "REJECT -32008\\nGUARDRAIL_BLOCK", fillcolor="#FDE8E8", color=C["warn"])
    g.node("hit", "Cache HIT\\nreturn cached answer\\n<= 60 ms p95", fillcolor="#E6F4EA", color=C["ok"])

    # ---- 2. Triage & routing -----------------------------------------------------
    t = cluster(g, "triage", "2  TRIAGE & SUBAGENT ROUTING", C["triage"])
    t.node("triage", "Triage Agent\\ntemperature = 0.0\\n(intent, domain, risk_tier, complexity)\\np95 <= 25 ms | hard timeout 30 ms",
           shape="box", fillcolor="#FFFFFF")
    t.node("direct", "DIRECT_ANSWER\\nDirect Response")
    t.node("retr", "HYBRID_RAG\\nRetrieval Subagent\\n(read-only)")
    t.node("exec", "TRANSACTION_EXEC\\nExecution Subagent\\n(no RAG / web)")
    t.node("oos", "REJECT_OOS\\ndeterministic refusal")
    t.node("audit", "Audit / Verification Subagent\\nindependent context, NLI veto", fillcolor="#FFFFFF")
    for n in ("direct", "retr", "exec", "oos"):
        t.edge("triage", n)
    g.subgraph(t)

    # ---- 3. MCP & 3-stage RAG ----------------------------------------------------
    m = cluster(g, "mcp", "3  MCP (JSON-RPC 2.0) & 3-STAGE HYBRID RAG", C["mcp"])
    h = cluster(m, "rag", "mcp-hybrid-rag  (stdio, timeout 150 ms)", "#FFFFFF")
    h.node("bm25", "Stage 1: BM25Okapi\\nk1=1.25 b=0.75 top_k=50\\n< 12 ms")
    h.node("bi", "Stage 2: Bi-Encoder + FAISS HNSW\\nbge-large ONNX, efSearch=64, top_k=50\\n< 25 ms")
    h.node("rrf", "RRF Fusion\\nk=60, dedup chunk_hash, pool=40\\n< 3 ms")
    h.node("ce", "Stage 3: Cross-Encoder\\nbge-reranker-v2-m3 ONNX FP16\\nbatch 32, rerank_k=4, sigmoid > 0.42\\n< 45 ms")
    h.edge("bm25", "rrf", label="50 + scores")
    h.edge("bi", "rrf", label="50 + cosine")
    h.edge("rrf", "ce", label="top 40")
    m.subgraph(h)
    m.node("idx", "BM25 postings + FAISS HNSW\\n(read-only, mmap, versioned)", shape="cylinder", fillcolor="#EEEEEE")
    m.node("vault", "knowledge_vault/ + graph.json", shape="cylinder", fillcolor="#EEEEEE")
    m.node("pg", "PostgreSQL + pgvector\\n(+ read replica, RLS)", shape="cylinder", fillcolor="#EEEEEE")
    m.node("gv", "mcp-graphify-vault  (stdio)\\nanchor -> depth <= 2 subgraph\\n~320 vs ~1,400 tokens")
    m.node("ss", "mcp-state-store  (Streamable HTTP, pool 200)\\nsession_facts | audit chain | HITL")
    g.subgraph(m)

    # ---- 4. Control plane & egress ---------------------------------------------------
    k = cluster(g, "control", "4  CONTROL PLANE & EGRESS", C["control"])
    k.node("loop", "Bounded ReAct Loop  (max_steps = 5)\\nINIT>TRIAGE>REASONING>TOOL_CALL>OBSERVATION>VERIFY>DONE|HALTED\\nstep <= 120 ms | total <= 450 ms | GoalAnchorBlock @ index 0\\nobservations <= 180 tokens | last 3 turns hot",
           shape="box")
    k.node("harness", "ToolExecutionHarness\\n1 Pydantic strict  2 regex auto-repair\\n3 RBAC tier  4 UUIDv4 idempotency")
    k.node("cb", "Circuit Breaker\\n(tool, args_hash) fails >= 2\\n-> HALTED_BREAKER", color=C["warn"])
    k.node("hitl", "Tier 3 HITL Gate\\nPENDING_APPROVAL -> Postgres\\nrequester != approver", color=C["warn"])
    k.node("abst", "Abstention short-circuit\\ncross-encoder top < 0.42\\nno LLM, <= 90 ms from triage", fillcolor="#FFFFFF")
    k.node("llm", "llm.generate\\nvLLM / TRT-LLM, prefix cache\\nTTFT <= 180 ms")
    k.edge("loop", "harness", label="every tool call")
    k.edge("harness", "cb")
    g.subgraph(k)

    e = cluster(g, "egress", "EGRESS (post-execution plugins)", C["egress"])
    e.node("nli", "In-line claim->chunk NLI veto\\n<= 30 ms")
    e.node("resp", "Response to client\\n(streamed, tenant echoed)", shape="oval", fillcolor="#FFFFFF")
    e.node("ragas", "Post-Execution RAGAS Auditor (async)\\nfaithfulness >= 0.92")
    e.node("otel", "OTel Export (async)\\ntraceparent -> Collector -> Prometheus + Tempo + Grafana")
    e.edge("ragas", "otel")
    g.subgraph(e)

    # ---- edges: ingress ---------------------------------------------------------------
    g.edge("client", "edge")
    g.edge("pii", "block", style="dashed", color=C["warn"], label="block")
    g.edge("inj", "block", style="dashed", color=C["warn"], label="block")
    g.edge("cache", "hit", label="HIT", color=C["ok"])
    g.edge("hit", "resp", color=C["ok"], style="dashed")
    g.edge("cache", "triage", label="MISS")
    g.edge("block", "resp", style="dashed", color=C["warn"])

    # ---- routing ----------------------------------------------------------------------
    g.edge("direct", "llm", label="short answer")
    g.edge("oos", "resp", style="dashed")
    g.edge("retr", "loop")
    g.edge("exec", "loop")

    # ---- MCP calls from the loop (via harness) -----------------------------------------
    g.edge("harness", "bm25", label="MCP JSON-RPC\\nhybrid_retrieve", lhead="cluster_rag")
    g.edge("harness", "bi", style="invis")
    g.edge("harness", "gv", label="resolve_subgraph")
    g.edge("harness", "ss", label="Tier 1/2 state\\nops")
    g.edge("cb", "hitl", label="Tier 3 only")
    g.edge("hitl", "ss", label="write PENDING_APPROVAL", style="dashed", color=C["warn"])
    g.edge("hitl", "resp", label="202 PENDING", style="dashed", color=C["warn"])

    g.edge("ce", "abst", label="top score")
    g.edge("abst", "resp", label="below 0.42: abstain", style="dashed")
    g.edge("ce", "loop", label="top 4 chunks\\nOBSERVATION", constraint="false")
    g.edge("gv", "loop", label="subgraph", constraint="false")
    g.edge("loop", "llm", label="above 0.42: synthesize")
    g.edge("llm", "nli")
    g.edge("nli", "resp", label="grounded")
    g.edge("nli", "loop", label="veto -> re-ground", style="dashed", constraint="false")
    g.edge("resp", "ragas", style="dashed", label="async")
    g.edge("audit", "ss", style="dotted", label="audit log only", constraint="false")

    # ---- data-store edges (short, local to each cluster) -----------------------------
    g.edge("cache", "redis", dir="both", style="dashed", arrowhead="none", arrowtail="none")
    g.edge("bm25", "idx", style="dashed", arrowhead="none")
    g.edge("gv", "vault", style="dashed", arrowhead="none")
    g.edge("ss", "pg", style="dashed", arrowhead="none")
    return g


def ladder() -> graphviz.Digraph:
    g = base("release_ladder", rankdir="TB")
    g.attr(label="Build -> Test -> Failure Note -> Re-version -> Rollback   (design.md section 1.11)",
           fontsize="22")

    g.node("plan", "Design D1.x locked\\n(DESIGN.lock sha256)", shape="oval")
    g.node("build", "BUILD vX.Y\\n(step order in section 1.14)")
    g.node("L0", "L0  Component bench + golden set\\nper-stage p95, RAGAS, IR metrics", fillcolor="#E6F4EA")
    g.node("L1", "L1  100 VU smoke + chaos + red team\\n+ ROLLBACK DRILL", fillcolor="#E6F4EA")
    g.node("L2", "L2  5K VUs  (~940 RPS)", fillcolor="#E8F0FE")
    g.node("L3", "L3  10K VUs  (~1.9K RPS)\\ncache steady state, cost", fillcolor="#E8F0FE")
    g.node("L4", "L4  25K VUs  (~4.7K RPS)\\n+ FMEA chaos injection", fillcolor="#E8F0FE")
    g.node("L5", "L5  50K VUs  (~9.4K RPS)\\nk6 + JMeter, KPI-31..35", fillcolor="#FFF4E0")
    g.node("L6", "L6  1-hr soak @ 50K\\nKPI-36 (RSS <= 3%)", fillcolor="#FFF4E0")
    g.node("cert", "CERTIFIED\\nall 36 KPIs PASS", shape="oval", fillcolor="#E6F4EA", color=C["ok"])

    g.node("dec", "KPI decision\\n(evaluated after EVERY rung)", shape="diamond", fillcolor="#FFFFFF")
    g.node("soft", "Soft miss\\n(<=10% over, latency only)\\ntune levers / scale\\nre-run SAME rung", fillcolor="#FFF4E0")
    g.node("hard", "Hard fail\\n(accuracy, isolation, HITL,\\nloop-escape, 5xx, crash)", fillcolor="#FDE8E8", color=C["warn"])
    g.node("rb", "ROLLBACK\\ntraffic weights | flag snapshot |\\nindex manifest N-1 | migration down", fillcolor="#FDE8E8", color=C["warn"])
    g.node("fn", "FAILURE NOTE\\nfailure_notes/FN-vX.Y-NNN.md\\nroot cause, fix, regression test", fillcolor="#FFFFFF")
    g.node("audit", "Post-hardening AUDIT report\\naudit/AUDIT-<date>.md\\ntriage -> repro test -> fix", fillcolor="#F3E8FD")
    g.node("ver", "NEW VERSION vX.Y+1\\nVERSIONS.md entry + tag", fillcolor="#FFFFFF")

    chain = ["plan", "build", "L0", "L1", "L2", "L3", "L4", "L5", "L6", "cert"]
    for a, b in zip(chain, chain[1:]):
        g.edge(a, b, label="PASS" if a not in ("plan", "build") else "")
    g.edge("dec", "soft", label="soft")
    g.edge("dec", "hard", label="hard / abort")
    g.edge("soft", "dec", label="re-run", style="dashed", constraint="false")
    g.edge("hard", "rb")
    g.edge("rb", "fn")
    g.edge("audit", "fn", label="findings")
    g.edge("fn", "ver")
    g.edge("ver", "L1", label="re-run from lowest\\naffected rung", style="dashed", constraint="false")
    return g


def topology() -> graphviz.Digraph:
    """docs/design.png — deployment/topology view (design §1.3): who talks to whom, and where state lives."""
    g = base("design", rankdir="LR")
    g.attr(label="System design - distributed topology & data flow (design.md D1.0 section 1.3)", fontsize="22", ranksep="0.9")
    g.node("inj", "Load injectors\nk6 (distributed) | JMeter 1 controller + 10 workers\n50K VUs, think time 5 s", shape="box3d")
    g.node("ui", "Streamlit workbench (dev-only)\n6 tabs, reads admin API\napp.py / views / components / utils", shape="box3d", fillcolor="#E8F0FE")
    g.node("env", "Envoy / NGINX\nadmission control (-32009 shed)\nkeep-alive TLS")
    w = cluster(g, "w", "FastAPI worker x N (stateless, uvicorn async) — bulkheads rag=64 db=128 llm=256", C["control"])
    w.node("pre", "Pre-hooks (Pluggy)\nPII -> injection (regex + classifier)\nfail-closed")
    w.node("cache", "Two-tier cache\nL1 per-user | L2 tenant 'shareable'")
    w.node("agent", "Triage + bounded ReAct runtime\nsubagents: Retrieval | Execution | Verification\nToolExecutionHarness + circuit breaker + HITL gate")
    w.node("host", "MCP host (ClientSession)\nsigned manifests, timeouts, JSON-RPC log")
    w.edge("pre", "cache"); w.edge("cache", "agent"); w.edge("agent", "host")
    g.subgraph(w)
    m = cluster(g, "m", "MCP servers (JSON-RPC 2.0)", C["mcp"])
    m.node("rag", "mcp-hybrid-rag (stdio, per worker)\nBM25 || bi-encoder+FAISS HNSW -> RRF -> cross-encoder\nONNX, micro-batch 32 / 8 ms")
    m.node("gv", "mcp-graphify-vault (stdio, per worker)\ndepth<=2 subgraph, ~320 tok")
    m.node("ss", "mcp-state-store (Streamable HTTP :8081)\nfacts | audit chain | HITL | lots")
    g.subgraph(m)
    d = cluster(g, "d", "State & models", C["store"])
    d.node("redis", "Redis (Cluster in target)\nt:{tenant}:... keys, jittered TTL", shape="cylinder")
    d.node("pg", "PostgreSQL + pgvector\nRLS, append-only hash-chained audit\n(+ read replica in target)", shape="cylinder")
    d.node("idx", "Index + vault + graph.json\nread-only, versioned (N-1 kept)", shape="cylinder")
    g.subgraph(d)
    g.node("llm", "LLM generation (small model, ADR-7)\nRouter: Groq -> OpenRouter -> extractive\n(target: vLLM / TRT-LLM)", fillcolor="#FFF4E0")
    o = cluster(g, "o", "Observability", C["egress"])
    o.node("otel", "OTel Collector\nW3C traceparent")
    o.node("prom", "Prometheus + Tempo + Grafana\nalerts -> runbooks / rollback")
    o.edge("otel", "prom")
    g.subgraph(o)
    g.edge("inj", "env"); g.edge("env", "pre"); g.edge("ui", "env", label="admin API", style="dashed")
    g.edge("host", "rag", label="stdio"); g.edge("host", "gv", label="stdio"); g.edge("host", "ss", label="HTTP")
    g.edge("cache", "redis", dir="both", style="dashed"); g.edge("agent", "llm", label="generate"); g.edge("ss", "pg"); g.edge("ss", "redis", style="dashed")
    g.edge("rag", "idx"); g.edge("gv", "idx")
    g.edge("agent", "otel", style="dotted", label="spans (async)")
    return g


if __name__ == "__main__":
    for fn in (workflow, ladder, topology):
        g = fn()
        path = g.render(filename=g.name, directory=str(OUT), cleanup=True)
        print("wrote", path)
