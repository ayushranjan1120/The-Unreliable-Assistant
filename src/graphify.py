"""Graphify runtime core (pure Python): anchor resolution + depth-limited subgraph pruning (design Gate 17.2).

Pruned subgraph = anchor + notes reachable along OUTGOING wikilinks within ``depth`` edges, whole notes only,
packed into ``max_tokens``. The "raw dump" baseline (KPI-26) is the undirected 2-hop neighbourhood, unpruned.
"""
from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path

from src.vault import Note, load_vault, vault_hash
from utils.token_calc import count

_ENT = re.compile(r"\b(SKU)[-_ ]?(\d{1,3})\b", re.I)
_WORD = re.compile(r"[a-z0-9]{3,}")


class GraphStale(Exception):
    """graph.json does not match the vault (FMEA #10)."""


class GraphIndex:
    """In-memory graph over the vault notes."""

    def __init__(self, vault_dir: Path, graph_path: Path | None = None, check_hash: bool = True) -> None:
        self.notes: dict[str, Note] = load_vault(vault_dir)
        self.out = {i: list(n.links) for i, n in self.notes.items()}
        self.inn: dict[str, list[str]] = {i: [] for i in self.notes}
        for a, ls in self.out.items():
            for b in ls:
                self.inn[b].append(a)
        self.graph_hash = ""
        if graph_path and graph_path.exists():
            g = json.loads(graph_path.read_text())
            self.graph_hash = g.get("graph_hash", "")
            if check_hash and g.get("vault_hash") != vault_hash(vault_dir):
                raise GraphStale("graph.json is stale relative to knowledge_vault")
        self._tokens = {i: count(n.body) for i, n in self.notes.items()}

    def resolve_anchor(self, anchor: str) -> str | None:
        """Map an entity id, note id, or free text to a note id (None if nothing matches)."""
        a = anchor.strip()
        if a in self.notes:
            return a
        m = _ENT.search(a)
        if m:
            nid = f"sku-{int(m.group(2)):03d}"
            return nid if nid in self.notes else None
        low = a.lower()
        for nid in self.notes:                       # explicit note id mention, longest first
            if nid in low:
                return nid
        terms = set(_WORD.findall(low))
        best, score = None, 0
        for nid, n in self.notes.items():
            s = len(terms & set(_WORD.findall(n.body.lower() + " " + nid.replace("-", " "))))
            if s > score:
                best, score = nid, s
        return best if score >= 2 else None

    def subgraph(self, anchor: str, depth: int = 2, max_tokens: int = 320) -> dict:
        """Return ``{nodes, edges, text, token_count, raw_token_estimate, anchor}``; empty text when unresolved."""
        nid = self.resolve_anchor(anchor)
        if nid is None:
            return {"anchor": None, "nodes": [], "edges": [], "text": "", "token_count": 0, "raw_token_estimate": 0}
        seen, order, q = {nid: 0}, [nid], deque([nid])
        while q:                                                     # pruned: outgoing links, BFS
            cur = q.popleft()
            if seen[cur] >= depth:
                continue
            for nb in self.out[cur]:
                if nb not in seen:
                    seen[nb] = seen[cur] + 1; order.append(nb); q.append(nb)
        raw_seen, rq = {nid: 0}, deque([nid])                        # raw baseline: undirected 2-hop dump
        while rq:
            cur = rq.popleft()
            if raw_seen[cur] >= 2:
                continue
            for nb in self.out[cur] + self.inn[cur]:
                if nb not in raw_seen:
                    raw_seen[nb] = raw_seen[cur] + 1; rq.append(nb)
        raw_tokens = sum(self._tokens[i] for i in raw_seen)
        keep, used = [], 0
        for i in order:
            t = count(f"[{i}] {self.notes[i].body}") + 1
            if used + t <= max_tokens:
                keep.append(i); used += t
        ks = set(keep)
        text = "\n".join(f"[{i}] {self.notes[i].body}" for i in keep)
        return {"anchor": nid, "nodes": [{"id": i, "kind": "note", "domain": self.notes[i].domain, "dist": seen[i]} for i in keep],
                "edges": [[a, b] for a in keep for b in self.out[a] if b in ks], "text": text,
                "token_count": count(text), "raw_token_estimate": raw_tokens}
