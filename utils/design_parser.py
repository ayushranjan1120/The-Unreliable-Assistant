"""Parse design.md / VERSIONS.md for the workbench. Pure Python."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path


def parse_checklist(design_path: Path) -> list[tuple[bool, str]]:
    """Return ``[(ticked, text)]`` from the Review Checklist at the end of design.md."""
    if not design_path.exists():
        return []
    text = design_path.read_text()
    idx = text.find("## Review Checklist")
    if idx < 0:
        return []
    return [(m.group(1) == "x", m.group(2).strip())
            for m in re.finditer(r"^- \[( |x)\] (.+)$", text[idx:], flags=re.M)]


def lock_status(root: Path) -> dict[str, str | bool]:
    """Return lock state: ``locked`` / ``drifted`` / ``unlocked`` plus the recorded hash and design version."""
    design, lock = root / "design.md", root / "DESIGN.lock"
    ver = ""
    if design.exists():
        m = re.search(r"\| Design version \| `([^`]+)`", design.read_text())
        ver = m.group(1) if m else ""
    if not lock.exists() or not design.exists():
        return {"state": "unlocked", "design_version": ver, "hash": ""}
    recorded = (lock.read_text().split() or [""])[0]
    actual = hashlib.sha256(design.read_bytes()).hexdigest()
    return {"state": "locked" if recorded == actual else "drifted", "design_version": ver, "hash": recorded[:12]}


def parse_versions_ledger(path: Path) -> list[dict[str, str]]:
    """Return rows of the Build ledger table in VERSIONS.md."""
    if not path.exists():
        return []
    text = path.read_text()
    idx = text.find("## Build ledger")
    rows: list[dict[str, str]] = []
    if idx < 0:
        return rows
    for line in text[idx:].splitlines():
        if line.startswith("|") and not line.startswith("|---") and "Version" not in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 7:
                rows.append(dict(zip(["version", "date", "highest_rung", "kpis_failing", "notes", "rollback", "detail"], cells)))
    return rows
