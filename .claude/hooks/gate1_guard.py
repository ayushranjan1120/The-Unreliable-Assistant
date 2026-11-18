#!/usr/bin/env python3
"""PreToolUse hook: enforce Gate 1 (no application code until design.md is locked).

Reads the hook JSON on stdin. Exit 0 = allow, exit 2 = block (stderr is shown to Claude).
Run as:  python3 -I "$CLAUDE_PROJECT_DIR/.claude/hooks/gate1_guard.py"
"""
import hashlib
import json
import os
import sys
from pathlib import Path

try:
    event = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # never wedge the session on malformed input

tool_input = event.get("tool_input") or {}
target = tool_input.get("file_path") or tool_input.get("notebook_path")
if not target:
    sys.exit(0)

root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()).resolve()
path = Path(target)
path = (path if path.is_absolute() else root / path).resolve()
try:
    rel = path.relative_to(root).as_posix()
except ValueError:
    sys.exit(0)  # outside the project: not our concern

design, lock = root / "design.md", root / "DESIGN.lock"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


state = "unlocked"
if lock.exists() and design.exists():
    recorded = lock.read_text().split()[0] if lock.read_text().split() else ""
    state = "locked" if recorded == sha(design) else "drifted"

DOC_PREFIXES = ("docs/", "failure_notes/", "audit/", "scripts/")
DOC_FILES = {"design.md", "CLAUDE.md", "VERSIONS.md", "README.md", ".gitignore"}


def is_doc_path(r: str) -> bool:
    return r in DOC_FILES or r.startswith(DOC_PREFIXES) or (r.endswith(".md") and "/" not in r)


def block(msg: str):
    print(f"GATE 1 GUARD: {msg}", file=sys.stderr)
    sys.exit(2)


# Never let the guard or the lock be edited through the tool.
if rel.startswith(".claude/") or rel == "DESIGN.lock":
    block(f"'{rel}' is protected. Change it manually (outside Claude) if you really intend to.")

if rel == "design.md":
    if state == "locked":
        block("design.md is LOCKED. Run scripts/unlock_design.sh \"<reason>\" first, "
              "then edit, bump the design version, re-review and re-lock.")
    sys.exit(0)

if is_doc_path(rel):
    sys.exit(0)

if state == "locked":
    sys.exit(0)
if state == "drifted":
    block(f"design.md changed since it was locked. Re-review and run scripts/lock_design.sh "
          f"--i-reviewed before writing '{rel}'.")
block(f"design.md is not locked, so application code/config ('{rel}') cannot be written yet. "
      "Finish review, then run scripts/lock_design.sh --i-reviewed.")
