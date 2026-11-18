"""W3C TraceContext spans + in-process metrics (design §1.10).

Spans use the Gate-10 names. Per-request spans are collected in a contextvar (for the Streamlit waterfall);
durations also feed bounded histograms used by KPI evaluation. OTLP export is enabled only when
``OTEL_EXPORTER_OTLP_ENDPOINT`` is set and never blocks the request path (BatchSpanProcessor drops on overflow).
"""
from __future__ import annotations

import contextlib
import os
import re
import secrets
import time
from collections import defaultdict, deque
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Iterator

GATE10_SPANS = ("hook.pre", "cache.lookup", "triage.classify", "mcp.rag.bm25", "mcp.rag.biencoder", "mcp.rag.rrf",
                "mcp.rag.rerank", "agent.react.step", "llm.generate", "hook.post.ragas")
_TRACEPARENT = re.compile(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")


def new_trace_id() -> str:
    """32-hex W3C trace id."""
    return secrets.token_hex(16)


def parse_traceparent(header: str | None) -> tuple[str, str] | None:
    """Return ``(trace_id, parent_span_id)`` from a ``traceparent`` header, or None if invalid."""
    m = _TRACEPARENT.match(header or "")
    return (m.group(1), m.group(2)) if m and m.group(1) != "0" * 32 else None


def make_traceparent(trace_id: str, span_id: str | None = None) -> str:
    """Build a ``traceparent`` header value."""
    return f"00-{trace_id}-{span_id or secrets.token_hex(8)}-01"


@dataclass
class TraceCtx:
    """Per-request trace state."""
    trace_id: str
    t0: float = field(default_factory=time.perf_counter)
    spans: list[dict] = field(default_factory=list)


_current: ContextVar[TraceCtx | None] = ContextVar("trace_ctx", default=None)


def start_trace(trace_id: str | None = None) -> TraceCtx:
    """Begin a trace for the current task."""
    tc = TraceCtx(trace_id or new_trace_id())
    _current.set(tc)
    return tc


def current() -> TraceCtx | None:
    """Return the active trace context, if any."""
    return _current.get()


class Metrics:
    """Bounded histograms + counters (process-local)."""

    def __init__(self, cap: int = 50_000) -> None:
        self.hist: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=cap))
        self.counters: dict[str, int] = defaultdict(int)

    def observe(self, name: str, ms: float) -> None:
        """Record a duration sample."""
        self.hist[name].append(ms)

    def inc(self, name: str, n: int = 1) -> None:
        """Increment a counter."""
        self.counters[name] += n

    def percentile(self, name: str, p: float) -> float | None:
        """p-th percentile (0–100) of a histogram, or None when empty."""
        d = sorted(self.hist.get(name, ()))
        if not d:
            return None
        return d[min(len(d) - 1, int(round(p / 100 * (len(d) - 1))))]

    def snapshot(self) -> dict:
        """JSON-friendly summary."""
        return {"counters": dict(self.counters),
                "hist": {k: {"n": len(v), "p50": self.percentile(k, 50), "p95": self.percentile(k, 95), "p99": self.percentile(k, 99)}
                         for k, v in self.hist.items()}}

    def reset(self) -> None:
        """Clear all samples (tests / between rungs)."""
        self.hist.clear()
        self.counters.clear()


METRICS = Metrics()
_tracer = None


def init_otel(service: str = "ua-agent") -> bool:
    """Enable OTLP export if an endpoint is configured; returns whether it was enabled."""
    global _tracer
    ep = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not ep:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    prov = TracerProvider(resource=Resource.create({"service.name": service}))
    prov.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=ep, insecure=True), max_queue_size=8192))
    trace.set_tracer_provider(prov)
    _tracer = trace.get_tracer(service)
    return True


@contextlib.contextmanager
def span(name: str, **attrs: object) -> Iterator[dict]:
    """Time a block; attributes must never contain raw PII (design §1.9)."""
    tc = _current.get()
    t = time.perf_counter()
    rec = {"name": name, "start_ms": (t - tc.t0) * 1000 if tc else 0.0, "dur_ms": 0.0, "attrs": dict(attrs)}
    otel_cm = _tracer.start_as_current_span(name) if _tracer else contextlib.nullcontext()
    try:
        with otel_cm:
            yield rec
    finally:
        rec["dur_ms"] = (time.perf_counter() - t) * 1000
        METRICS.observe(name, rec["dur_ms"])
        if tc:
            tc.spans.append(rec)
