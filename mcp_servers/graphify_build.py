#!/usr/bin/env python3
"""``graphify build ./src ./knowledge_vault --output graph.json`` (design Gate 17.2).

In-repo implementation: no public ``graphify`` CLI with this interface could be verified, so the builder
lives here (audit register D-12). Nodes: Obsidian notes, Python modules, Pydantic schemas.
Edges: wikilinks, imports, module→schema definitions.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path

from src.vault import load_vault, vault_hash
from utils.token_calc import count


def build(src_dir: Path, vault_dir: Path) -> dict:
    """Return the graph dict (nodes, edges, hashes)."""
    notes = load_vault(vault_dir)
    nodes = [{"id": n.id, "kind": "note", "domain": n.domain, "risk_tier": n.risk_tier, "tokens": count(n.body)} for n in notes.values()]
    edges = [[n.id, l] for n in notes.values() for l in n.links]
    mods = {}
    for p in sorted(src_dir.rglob("*.py")):
        if p.name == "__init__.py" and p.stat().st_size == 0:
            continue
        mod = ".".join(p.relative_to(src_dir.parent).with_suffix("").parts)
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        mods[mod] = tree
        nodes.append({"id": f"py:{mod}", "kind": "module", "tokens": count(p.read_text())})
    for mod, tree in mods.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(getattr(b, "id", getattr(b, "attr", "")) in ("Strict", "BaseModel") for b in node.bases):
                sid = f"schema:{mod}.{node.name}"
                nodes.append({"id": sid, "kind": "schema", "tokens": 20}); edges.append([f"py:{mod}", sid])
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                for n in names:
                    if n in mods:
                        edges.append([f"py:{mod}", f"py:{n}"])
    h = hashlib.sha256(json.dumps([nodes, edges], sort_keys=True).encode()).hexdigest()
    return {"nodes": nodes, "edges": edges, "vault_hash": vault_hash(vault_dir), "graph_hash": h}


def main() -> None:
    """CLI entry."""
    ap = argparse.ArgumentParser(prog="graphify build")
    ap.add_argument("src"); ap.add_argument("vault"); ap.add_argument("--output", default="artifacts/graph.json")
    a = ap.parse_args()
    g = build(Path(a.src), Path(a.vault))
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(g))
    kinds = {}
    for n in g["nodes"]:
        kinds[n["kind"]] = kinds.get(n["kind"], 0) + 1
    print(f"graph: {len(g['nodes'])} nodes {kinds}, {len(g['edges'])} edges, hash {g['graph_hash'][:12]} -> {a.output}")


if __name__ == "__main__":
    main()
