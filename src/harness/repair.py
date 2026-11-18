"""Deterministic regex auto-repair (design §1.2.1). Never guesses: ambiguous input is left unrepaired."""
from __future__ import annotations

import re
from typing import Any

_SKU = re.compile(r"^\s*sku\s*[-_ ]?\s*(\d{1,3})\s*$", re.I)
_LOT = re.compile(r"^\s*lot\s*[-_ ]?\s*(\d{1,5})\s*$", re.I)
_ENTITY_SKU_FIELDS = {"sku"}
_ENTITY_LOT_FIELDS = {"lot"}
_ENUM_FIELDS = {"transition"}


def repair_sku(v: str) -> str | None:
    """``SKU99`` / ``sku 99`` → ``SKU-099``; None if not repairable."""
    m = _SKU.match(v)
    return f"SKU-{int(m.group(1)):03d}" if m else None


def repair_lot(v: str) -> str | None:
    """``lot 44`` → ``LOT-00044``; None if not repairable."""
    m = _LOT.match(v)
    return f"LOT-{int(m.group(1)):05d}" if m else None


def repair_args(args: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Return ``(repaired_args, repairs)``; each repair is a string like ``sku:SKU99->SKU-099``."""
    out, repairs = dict(args), []
    for k, v in args.items():
        if not isinstance(v, str):
            continue
        new: str | None = None
        if k in _ENTITY_SKU_FIELDS:
            new = repair_sku(v)
        elif k in _ENTITY_LOT_FIELDS:
            new = repair_lot(v)
        elif k == "entity":
            new = repair_sku(v) or repair_lot(v)
        elif k in _ENUM_FIELDS:
            new = v.strip().upper().replace(" ", "_")
        if new is not None and new != v:
            out[k] = new
            repairs.append(f"{k}:{v}->{new}")
    return out, repairs


_ENTITY_RE = re.compile(r"\b(?:sku\s*[-_ ]?\s*(\d{1,3})|lot\s*[-_ ]?\s*(\d{1,5}))\b", re.I)


def normalize_entities(text: str) -> list[str]:
    """Extract canonical SKU/LOT ids from free text (used by GoalAnchorBlock and triage)."""
    ids: list[str] = []
    for m in _ENTITY_RE.finditer(text):
        e = f"SKU-{int(m.group(1)):03d}" if m.group(1) else f"LOT-{int(m.group(2)):05d}"
        if e not in ids:
            ids.append(e)
    return ids
