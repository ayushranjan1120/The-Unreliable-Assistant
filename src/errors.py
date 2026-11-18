"""JSON-RPC / HTTP error codes (design §1.2.3)."""
from __future__ import annotations

from enum import IntEnum


class Code(IntEnum):
    """Application error codes."""
    VALIDATION_FAILED = -32001
    RBAC_DENIED = -32002
    IDEMPOTENCY_CONFLICT = -32003
    BREAKER_OPEN = -32004
    TOOL_TIMEOUT = -32005
    HITL_PENDING = -32006
    TENANT_VIOLATION = -32007
    GUARDRAIL_BLOCK = -32008
    OVERLOAD_SHED = -32009
    DEPENDENCY_DEGRADED = -32010


HTTP_STATUS: dict[int, int] = {
    Code.VALIDATION_FAILED: 422, Code.RBAC_DENIED: 403, Code.IDEMPOTENCY_CONFLICT: 409, Code.BREAKER_OPEN: 200,
    Code.TOOL_TIMEOUT: 504, Code.HITL_PENDING: 202, Code.TENANT_VIOLATION: 403, Code.GUARDRAIL_BLOCK: 200,
    Code.OVERLOAD_SHED: 429, Code.DEPENDENCY_DEGRADED: 200,
}


class ToolError(Exception):
    """A deterministic, coded failure raised by the harness, plugins or MCP layer."""

    def __init__(self, code: Code, message: str, data: dict | None = None) -> None:
        super().__init__(f"[{int(code)}] {message}")          # code survives the MCP JSON-RPC boundary as a text prefix
        self.code = code
        self.message = message
        self.data = data or {}

    def to_jsonrpc(self) -> dict:
        """Render as a JSON-RPC error object."""
        return {"code": int(self.code), "message": self.message, "data": self.data}
