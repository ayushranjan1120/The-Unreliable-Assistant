"""Tab 4 — Memory & Graphify Tokens: subgraph, vault notes, session state, token savings."""
from __future__ import annotations

import streamlit as st

import cached
import state
from components import cards, charts, tables


def render(ctx: state.Ctx) -> None:
    """Render Tab 4."""
    st.subheader("Memory & Graphify Tokens")
    a, b = st.columns(2)
    with a:
        with st.container(border=True):
            st.markdown("**Graphify subgraph (depth ≤ 2)**")
            anchor = st.text_input("Anchor node", "SKU-099", key="gv_anchor")
            r = cached.backend_get(ctx.backend_url, "/api/v1/admin/graphify/subgraph", tenant_id=ctx.tenant_id, anchor=anchor, depth=2)
            if not r.ok:
                cards.no_data(r.error)
            else:
                d = r.data
                cards.metric_row([("Subgraph tokens", d["token_count"], None), ("Raw dump tokens", d["raw_token_estimate"], None),
                                  ("Reduction", f"{100 * (1 - d['token_count'] / max(d['raw_token_estimate'], 1)):.1f}%", "KPI-26 input")])
                st.graphviz_chart(_dot(d["nodes"], d["edges"]))
    with b:
        with st.container(border=True):
            st.markdown("**Obsidian atomic note**")
            notes = cached.backend_get(ctx.backend_url, "/api/v1/admin/vault/notes", tenant_id=ctx.tenant_id)
            if not notes.ok:
                cards.no_data(notes.error)
            else:
                ids = [n["id"] for n in notes.data["notes"]]
                sel = st.selectbox("Note", ids)
                n = cached.backend_get(ctx.backend_url, f"/api/v1/admin/vault/note/{sel}", tenant_id=ctx.tenant_id)
                if n.ok:
                    st.json(n.data["frontmatter"])
                    st.markdown(n.data["body"])
    st.divider()
    st.markdown("**Session state browser (tenant-scoped)**")
    s = cached.backend_get(ctx.backend_url, "/api/v1/admin/session", tenant_id=ctx.tenant_id, session_id=ctx.session_id)
    if not s.ok:
        cards.no_data(s.error)
    else:
        c1, c2 = st.columns(2)
        c1.markdown("Hot memory (last 3 turns, Redis)")
        c1.json(s.data.get("hot", []))
        c2.markdown("Verified facts (Postgres `session_facts`)")
        with c2:
            tables.simple_table(s.data.get("facts", []), "no facts for this session")
    st.divider()
    t = cached.backend_get(ctx.backend_url, "/api/v1/admin/tokens", tenant_id=ctx.tenant_id)
    if t.ok and t.data.get("n"):
        charts.gauge(t.data["reduction_pct"], "Token reduction % (KPI-26 ≥ 45)", 45.0, 0, 100)
        cards.metric_row([("Cost / 1K queries", f"${t.data['cost_per_1k']:.3f}", "KPI-29 ≤ $0.45"), ("Samples", t.data["n"], None)])
    else:
        cards.no_data("No token-accounting samples yet.")


def _dot(nodes: list[dict], edges: list[list[str]]) -> str:
    """Build a Graphviz DOT string for the subgraph."""
    lines = ["digraph G { rankdir=LR; node [shape=box, style=rounded, fontsize=10];"]
    lines += [f'"{n["id"]}" [label="{n["id"]}\\n{n.get("kind", "")}"];' for n in nodes]
    lines += [f'"{a}" -> "{b}";' for a, b in edges]
    return "\n".join(lines) + "}"
