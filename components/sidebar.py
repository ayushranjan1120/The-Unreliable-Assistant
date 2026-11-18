"""Global sidebar: tenant/user/session, backend, version, refresh, backend status."""
from __future__ import annotations

import streamlit as st

import cached
import state


def render() -> state.Ctx:
    """Draw the sidebar and return the current :class:`state.Ctx`."""
    with st.sidebar:
        st.title("🛠️ Workbench")
        st.caption("The Unreliable Assistant → Enterprise Grade")
        st.divider()
        st.subheader("Context")
        st.text_input("Tenant", key="tenant_id", help="Pattern tenant_[A-Za-z0-9_-]{1,64}")
        st.text_input("User", key="user_id")
        st.text_input("Session", key="session_id")
        st.divider()
        st.subheader("Backend")
        st.text_input("URL", key="backend_url")
        h = cached.backend_health(st.session_state["backend_url"])
        if h.ok:
            st.success(f"online · {h.latency_ms:.0f} ms")
        else:
            st.warning("offline — views show 'no data'")
        st.divider()
        st.subheader("Build")
        st.selectbox("Version", ["v0", "v0.1", "v0.2"], key="version")
        st.selectbox("Profile", ["dev-cpu", "target"], key="profile",
                     help="Only 'target' counts toward certification of GPU/scale-bound KPIs (design §1.16).")
        lock = cached.lock_status()
        st.caption(f"design {lock['design_version']} · {lock['state']} {lock['hash']}")
        st.divider()
        st.toggle("Auto-refresh", key="auto_refresh")
        st.slider("Refresh (s)", 2, 30, key="refresh_s", disabled=not st.session_state["auto_refresh"])
    return state.ctx()
