"""Backend client for the workbench. Pure Python; never raises into the UI.

Every call returns :class:`ApiResult`; when the backend is down ``ok`` is False and views show "no data".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass
class ApiResult:
    """Outcome of a backend call."""
    ok: bool
    data: Any = None
    error: str = ""
    status_code: int | None = None
    latency_ms: float | None = None


class BackendClient:
    """Thin HTTP client for the FastAPI agent backend."""

    def __init__(self, base_url: str, timeout_s: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _call(self, method: str, path: str, **kw: Any) -> ApiResult:
        try:
            r = httpx.request(method, f"{self.base_url}{path}", timeout=self.timeout_s, **kw)
            lat = r.elapsed.total_seconds() * 1000
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
            return ApiResult(r.is_success, body, "" if r.is_success else f"HTTP {r.status_code}", r.status_code, lat)
        except (httpx.HTTPError, ValueError) as e:
            return ApiResult(False, None, f"backend unavailable: {type(e).__name__}")

    def health(self) -> ApiResult:
        """GET /healthz."""
        return self._call("GET", "/healthz")

    def status(self) -> ApiResult:
        """GET /api/v1/admin/status (servers, plugins, breaker, levers)."""
        return self._call("GET", "/api/v1/admin/status")

    def query(self, tenant_id: str, user_id: str, query: str, session_id: str | None = None) -> ApiResult:
        """POST /api/v1/agent/query."""
        body = {"tenant_id": tenant_id, "user_id": user_id, "query": query}
        if session_id:
            body["session_id"] = session_id
        return self._call("POST", "/api/v1/agent/query", json=body, headers={"X-Tenant-ID": tenant_id})

    def query_debug(self, tenant_id: str, user_id: str, query: str, session_id: str | None = None, role: str = "operator") -> ApiResult:
        """POST /api/v1/admin/query_debug (adds ReAct steps, spans, triage, token accounting)."""
        body = {"tenant_id": tenant_id, "user_id": user_id, "query": query, "role": role}
        if session_id:
            body["session_id"] = session_id
        return self._call("POST", "/api/v1/admin/query_debug", json=body, headers={"X-Tenant-ID": tenant_id})

    def get(self, path: str, **params: Any) -> ApiResult:
        """Generic GET for admin endpoints."""
        return self._call("GET", path, params=params)

    def post(self, path: str, body: dict | None = None, tenant_id: str | None = None) -> ApiResult:
        """Generic POST for admin endpoints."""
        return self._call("POST", path, json=body or {}, headers={"X-Tenant-ID": tenant_id} if tenant_id else {})
