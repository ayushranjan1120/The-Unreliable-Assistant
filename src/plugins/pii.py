"""PII redactor (PRE_EXECUTION, fail-closed, priority 10). Design KPI-01 (≤15 ms), KPI-02 (recall ≥ 99.8%).

Normalizes before matching (NFKC, homoglyphs, zero-width, obfuscated '@'/'.'), then replaces each match with a typed,
salted-hash token such as ``<EMAIL:7f3a>``. Raw values are never stored, logged or returned. Regex + normalizer
(Presidio is not used — audit register D-13); recall is measured on our own fixtures (eval/pii_bench.py).
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata

from src.plugins.hooks import PluginCtx, hookimpl

_SALT = os.environ.get("PII_SALT", "dev-salt")
_ZW = re.compile("[\u200b-\u200f\u2060\u2066-\u2069\ufeff\u00ad]")
_CONF = str.maketrans({  # Cyrillic/Greek look-alikes → Latin (subset of Unicode confusables)
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "һ": "h", "ӏ": "l", "ᴍ": "m",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C", "Т": "T", "Х": "X", "І": "I", "ο": "o", "α": "a", "ε": "e", "ν": "v", "ρ": "p", "τ": "t", "υ": "u", "κ": "k", "ι": "i", "β": "b", "һ": "h"})
_AT = re.compile(r"\s*[\[\(\{<]\s*at\s*[\]\)\}>]\s*|\s*\bat\b\s*(?=[\w-]+\s*(?:[\[\(\{<]?\s*dot\s*[\]\)\}>]?|\.)\s*[a-z]{2,})", re.I)
_DOT = re.compile(r"\s*[\[\(\{<]\s*dot\s*[\]\)\}>]\s*|\s+dot\s+", re.I)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}\s?@\s?[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z0-9]{2,}")
SSN = re.compile(r"(?<!\d)(?!000|666|9\d\d)\d{3}[-\s.]\d{2}[-\s.]\d{4}(?!\d)|(?i:\bssn\b[:#\s]*)\d{9}(?!\d)")
PHONE = re.compile(r"(?<![\d-])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])\d{3,4}[\s.-]\d{3,4}(?:[\s.-]\d{1,4})?(?![\d-])|(?<!\d)\+\d{10,14}(?!\d)|(?<!\d)\d{10}(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]{0,2}){13,19}(?!\d)")


def normalize(text: str) -> str:
    """NFKC + homoglyph fold + zero-width strip."""
    return _ZW.sub("", unicodedata.normalize("NFKC", text)).translate(_CONF)


def _deobfuscate(text: str) -> str:
    """Rewrite 'name [at] host (dot) com' / 'name at host dot com' to canonical form."""
    t = _DOT.sub(".", text)
    return _AT.sub("@", t)


def _luhn(num: str) -> bool:
    d = [int(c) for c in re.sub(r"\D", "", num)][::-1]
    return 13 <= len(d) <= 19 and sum(x if i % 2 == 0 else (x * 2 - 9 if x * 2 > 9 else x * 2) for i, x in enumerate(d)) % 10 == 0


def _tok(kind: str, val: str) -> str:
    return f"<{kind}:{hashlib.sha256((_SALT + val).encode()).hexdigest()[:4]}>"


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Return ``(redacted_text, counts_by_kind)``."""
    t = _deobfuscate(normalize(text))
    counts = {"EMAIL": 0, "SSN": 0, "PHONE": 0, "CARD": 0}
    def sub(kind: str, rx: re.Pattern, s: str, ok=lambda m: True) -> str:
        def f(m: re.Match) -> str:
            if not ok(m.group(0)):
                return m.group(0)
            counts[kind] += 1
            return _tok(kind, m.group(0))
        return rx.sub(f, s)
    t = sub("EMAIL", EMAIL, t)
    t = sub("CARD", CARD, t, _luhn)
    t = sub("SSN", SSN, t)
    t = sub("PHONE", PHONE, t, lambda v: len(re.sub(r"\D", "", v)) >= 10)
    return t, {k: v for k, v in counts.items() if v}


class PiiRedactor:
    """Lifecycle plugin wrapper."""

    @hookimpl
    def pre_execution(self, ctx: PluginCtx, text: str) -> str:
        """Redact PII; record counts (never values) on the context."""
        out, counts = redact(text)
        if counts:
            ctx.flags["pii"] = counts
        return out
