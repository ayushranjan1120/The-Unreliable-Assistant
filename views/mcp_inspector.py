"""Tab 3 — MCP & Plugin Inspector: server status, JSON-RPC payloads, plugin hot-toggles."""
from __future__ import annotations

import json

import streamlit as st

import cached
import state
from components import cards, tables


def render(ctx: state.Ctx) -> None:
    """Render Tab 3."""
    st.subheader("MCP & Plugin Inspector")
    res = cached.backend_get(ctx.backend_url, "/api/v1/admin/status")
    if not res.ok:
        cards.no_data()
        return
    d = res.data
    st.markdown("**MCP servers**")
    tables.simple_table(d.get("mcp_servers", []), "no MCP servers reported")
    st.divider()
    st.markdown("**Lifecycle plugins** (hot-toggle; fail-closed plugins show a warning)")
    for p in d.get("plugins", []):
        c1, c2, c3 = st.columns([3, 1, 4])
        c1.write(f"`{p['name']}` · {p['stage']} · prio {p['priority']}")
        on = c2.toggle("on", value=p["enabled"], key=f"plug_{p['name']}")
        if on != p["enabled"]:
            r = cached.backend(ctx.backend_url).post("/api/v1/admin/plugins/toggle", {"name": p["name"], "enabled": on}, ctx.tenant_id)
            (st.toast if r.ok else st.error)(f"{p['name']} → {'on' if on else 'off'}" if r.ok else r.error)
        if p.get("fail_closed") and not on:
            c3.warning("fail-closed plugin disabled — requests will be rejected")
    st.divider()
    st.markdown("**Recent JSON-RPC payloads**")
    rpc = cached.backend_get(ctx.backend_url, "/api/v1/admin/mcp/rpc_log", tenant_id=ctx.tenant_id)
    items = rpc.data.get("items", []) if rpc.ok else []
    if not items:
        cards.no_data("No JSON-RPC calls captured yet.")
        return
    for it in items[-10:][::-1]:
        with st.expander(f"{it['server']}.{it['method']} · {it['ms']:.1f} ms · {'ok' if it['ok'] else 'ERROR'}"):
            st.code(json.dumps(it["request"], indent=2), language="json")
            st.code(json.dumps(it["response"], indent=2), language="json")
