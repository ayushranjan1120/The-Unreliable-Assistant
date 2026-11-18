"""Central ``st.session_state`` management: typed keys, defaults, accessors (design §1.13.1 rule 3)."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any

import streamlit as st

DEFAULTS: dict[str, Any] = {
    "tenant_id": "tenant_0",
    "user_id": "user_1",
    "session_id": None,
    "backend_url": os.environ.get("BACKEND_URL", "http://localhost:8000"),
    "version": os.environ.get("BUILD_VERSION", "v0"),
    "profile": os.environ.get("PROFILE", "dev-cpu"),
    "auto_refresh": False,
    "refresh_s": 5,
    "trace_history": [],      # list of last query responses (redacted)
    "lab_params": {"k1": 1.25, "b": 0.75, "rrf_k": 60, "threshold": 0.42},
}


@dataclass(frozen=True)
class Ctx:
    """Immutable snapshot of the global sidebar selections handed to views."""
    tenant_id: str
    user_id: str
    session_id: str
    backend_url: str
    version: str
    profile: str


def init_state() -> None:
    """Populate any missing keys with defaults (idempotent)."""
    for k, v in DEFAULTS.items():
        st.session_state.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)(v))
    if not st.session_state.get("session_id"):
        st.session_state["session_id"] = str(uuid.uuid4())


def get(key: str) -> Any:
    """Read a session key (must exist in DEFAULTS)."""
    return st.session_state[key]


def set_(key: str, value: Any) -> None:
    """Write a session key."""
    st.session_state[key] = value


def push_trace(entry: dict) -> None:
    """Append to the bounded trace history (last 50)."""
    h = st.session_state["trace_history"]
    h.append(entry)
    del h[:-50]


def ctx() -> Ctx:
    """Build a :class:`Ctx` from current state."""
    s = st.session_state
    return Ctx(s["tenant_id"], s["user_id"], s["session_id"], s["backend_url"], s["version"], s["profile"])
