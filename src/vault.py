"""Obsidian vault loader (pure Python): frontmatter, wikilinks, atomic notes (design Gate 17.1)."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

_FM = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)
_LINK = re.compile(r"\[\[([^\]\|#]+)(?:\|[^\]]*)?\]\]")
RISK_TO_TIER = {"LOW": "TIER_1_READ", "MEDIUM": "TIER_2_COMPUTE", "HIGH": "TIER_3_MUTATE"}  # vault risk → Chunk.risk_tier


@dataclass
class Note:
    """One atomic note."""
    id: str
    domain: str
    risk_tier: str
    version: str
    body: str                       # prose without the heading and the Related line
    links: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def chunk_hash(self) -> str:
        """sha256 of the chunk text."""
        return hashlib.sha256(self.body.encode()).hexdigest()

    @property
    def chunk_id(self) -> str:
        """First 16 hex chars of the chunk hash (contract ChunkId)."""
        return self.chunk_hash[:16]

    @property
    def tier(self) -> str:
        """Contract risk tier derived from the vault frontmatter."""
        return RISK_TO_TIER.get(self.risk_tier, "TIER_1_READ")


def parse_note(path: Path) -> Note:
    """Parse one markdown note; raises ValueError when the strict frontmatter is invalid."""
    raw = path.read_text()
    m = _FM.match(raw)
    if not m:
        raise ValueError(f"{path.name}: missing YAML frontmatter")
    fm = dict(line.split(": ", 1) for line in m.group(1).splitlines() if ": " in line)
    for k in ("id", "domain", "risk_tier", "version"):
        if k not in fm:
            raise ValueError(f"{path.name}: frontmatter missing '{k}'")
    if fm["risk_tier"] not in RISK_TO_TIER:
        raise ValueError(f"{path.name}: bad risk_tier {fm['risk_tier']}")
    body = m.group(2)
    links = list(dict.fromkeys(_LINK.findall(body)))
    text = "\n".join(l for l in body.splitlines() if not l.startswith("# ") and not l.startswith("Related:")).strip()
    return Note(fm["id"], fm["domain"], fm["risk_tier"], fm["version"], text, links, raw)


def load_vault(vault_dir: Path) -> dict[str, Note]:
    """Load every ``*.md`` note keyed by id; validates that all wikilinks resolve."""
    notes = {}
    for p in sorted(vault_dir.glob("*.md")):
        n = parse_note(p)
        notes[n.id] = n
    broken = [(n.id, l) for n in notes.values() for l in n.links if l not in notes]
    if broken:
        raise ValueError(f"broken wikilinks: {broken[:5]}")
    return notes


def vault_hash(vault_dir: Path) -> str:
    """Content hash of the whole vault (recorded in the index manifest, FMEA #10)."""
    h = hashlib.sha256()
    for p in sorted(vault_dir.glob("*.md")):
        h.update(p.name.encode()); h.update(p.read_bytes())
    return h.hexdigest()
