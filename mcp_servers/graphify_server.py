#!/usr/bin/env python3
"""mcp-graphify-vault (stdio): ``resolve_subgraph`` tool + ``vault://notes/{id}`` resource (design §1.2.2).

Tenant filtering: the vault is shared enterprise content with no tenant-private notes in v0; the
``tenant_id`` is still validated and echoed in the response metadata so the isolation check is exercised.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from src.config import get_settings  # noqa: E402
from src.contracts.models import SubgraphIn  # noqa: E402
from src.graphify import GraphIndex, GraphStale  # noqa: E402

S = get_settings()
mcp = FastMCP("graphify-vault")
_idx: GraphIndex | None = None
_stale = False


def idx() -> GraphIndex:
    """Lazy-load the graph (raises GraphStale so the host falls back to raw chunks)."""
    global _idx, _stale
    if _idx is None:
        try:
            _idx = GraphIndex(S.vault_dir, S.graph_path)
        except GraphStale:
            _stale = True
            raise
    return _idx


@mcp.tool()
def resolve_subgraph(req: dict) -> dict:
    """Resolve the anchor node and return the pruned ≤depth-2 subgraph (contract SubgraphIn → SubgraphOut)."""
    t = time.perf_counter()
    m = SubgraphIn.model_validate_json(json.dumps(req))
    out = idx().subgraph(m.anchor, m.depth, m.max_tokens)
    out["ms"] = (time.perf_counter() - t) * 1000
    out["tenant_id"] = m.tenant_id
    return out


@mcp.resource("vault://notes/{note_id}")
def read_note(note_id: str) -> str:
    """Return the raw atomic note (frontmatter + body)."""
    n = idx().notes.get(note_id)
    if n is None:
        raise ValueError(f"unknown note {note_id}")
    return n.raw


@mcp.resource("contract://subgraph")
def contract() -> str:
    """JSON schema of the tool contract."""
    return json.dumps(SubgraphIn.model_json_schema())


if __name__ == "__main__":
    try:
        idx()                        # eager load; a stale graph is reported on first call (FMEA #10), not at spawn
    except GraphStale:
        pass
    mcp.run(transport="stdio")
