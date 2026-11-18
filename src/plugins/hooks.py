"""Pluggy-based lifecycle plugin registry (design §1.8). Hot-toggleable, priority-ordered, fail-closed aware.

pluggy provides hookspec validation + registration (and entry-point discovery group ``ua.plugins``); ordering by
``priority`` and fail-closed semantics are enforced by :class:`PluginRegistry`.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pluggy

from src import telemetry
from src.errors import Code, ToolError

hookspec = pluggy.HookspecMarker("ua")
hookimpl = pluggy.HookimplMarker("ua")


@dataclass
class PluginCtx:
    """Per-request context handed to plugins (never contains raw PII after the redactor ran)."""
    tenant_id: str
    user_id: str
    session_id: str
    trace_id: str
    flags: dict[str, Any] = field(default_factory=dict)


class LifecycleSpec:
    """Hook specifications for the four lifecycle stages."""

    @hookspec
    def pre_execution(self, ctx: PluginCtx, text: str) -> str:  # noqa: D102
        ...

    @hookspec
    def post_execution(self, ctx: PluginCtx, result: dict) -> dict:  # noqa: D102
        ...


@dataclass
class PluginMeta:
    """Registration metadata from ``mcp_config.json``."""
    name: str
    stage: str
    priority: int = 100
    fail_closed: bool = False
    enabled: bool = True
    options: dict = field(default_factory=dict)


class PluginRegistry:
    """Registers plugin objects and runs a lifecycle stage in priority order."""

    def __init__(self) -> None:
        self.pm = pluggy.PluginManager("ua")
        self.pm.add_hookspecs(LifecycleSpec)
        self.meta: dict[str, PluginMeta] = {}
        self.objs: dict[str, Any] = {}

    def register(self, obj: Any, meta: PluginMeta) -> None:
        """Register a plugin (pluggy validates hookimpl signatures)."""
        self.pm.register(obj, name=meta.name)
        self.meta[meta.name], self.objs[meta.name] = meta, obj

    def load_entry_points(self) -> None:
        """Discover third-party plugins from the ``ua.plugins`` entry-point group."""
        self.pm.load_setuptools_entrypoints("ua.plugins")

    def set_enabled(self, name: str, enabled: bool) -> None:
        """Hot toggle."""
        self.meta[name].enabled = enabled

    def is_enabled(self, name: str) -> bool:
        """True if registered and enabled."""
        m = self.meta.get(name)
        return bool(m and m.enabled)

    def describe(self) -> list[dict]:
        """Admin view."""
        return [{"name": m.name, "stage": m.stage, "priority": m.priority, "fail_closed": m.fail_closed, "enabled": m.enabled}
                for m in sorted(self.meta.values(), key=lambda m: (m.stage, m.priority))]

    def _ordered(self, stage: str) -> list[PluginMeta]:
        return sorted((m for m in self.meta.values() if m.stage == stage), key=lambda m: m.priority)

    def run_pre(self, ctx: PluginCtx, text: str) -> str:
        """Run PRE_EXECUTION plugins in priority order. A failing fail-closed plugin REJECTS the request."""
        with telemetry.span("hook.pre"):
            for m in self._ordered("PRE_EXECUTION"):
                if not m.enabled:
                    if m.fail_closed:                       # disabling a fail-closed guard must never silently pass traffic
                        raise ToolError(Code.GUARDRAIL_BLOCK, f"fail-closed plugin '{m.name}' is disabled")
                    continue
                try:
                    text = getattr(self.objs[m.name], "pre_execution")(ctx, text)
                except ToolError:
                    raise
                except Exception as e:
                    telemetry.METRICS.inc(f"plugin.{m.name}.error")
                    if m.fail_closed:
                        raise ToolError(Code.GUARDRAIL_BLOCK, f"plugin '{m.name}' failed (fail-closed): {type(e).__name__}")
            return text

    def run_post(self, ctx: PluginCtx, result: dict) -> dict:
        """Run POST_EXECUTION plugins (never raises into the response path)."""
        with telemetry.span("hook.post.ragas"):
            for m in self._ordered("POST_EXECUTION"):
                if not m.enabled:
                    continue
                try:
                    result = getattr(self.objs[m.name], "post_execution")(ctx, result) or result
                except Exception:
                    telemetry.METRICS.inc(f"plugin.{m.name}.error")
        return result


def load_config(path: Path) -> list[PluginMeta]:
    """Parse the ``plugins`` array of mcp_config.json."""
    cfg = json.loads(path.read_text())
    return [PluginMeta(p["name"], p["stage"], p.get("priority", 100), p.get("fail_closed", False), p.get("enabled", True),
                       {k: v for k, v in p.items() if k not in ("name", "stage", "priority", "fail_closed", "enabled")})
            for p in cfg.get("plugins", [])]
