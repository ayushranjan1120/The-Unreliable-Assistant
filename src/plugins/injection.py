"""Prompt-injection shield (PRE_EXECUTION, fail-closed, priority 20) + indirect-injection chunk scanner.

Deterministic, multi-signal scoring on normalized text (homoglyph fold, leetspeak, base64 peek). Block at score ≥ 1.0.
Session-level crescendo accumulation with decay. Tuned against our own red-team/benign sets (eval/redteam) — a
real adversary corpus is still required before production (see FN notes).
"""
from __future__ import annotations

import base64
import re
import time
from dataclasses import dataclass

from src.errors import Code, ToolError
from src.plugins.hooks import PluginCtx, hookimpl
from src.plugins.pii import normalize

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t"})   # digits only: symbols ($ { @) must survive
_B64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{8,}={0,2}(?![A-Za-z0-9+/])")

I = re.I | re.S
RULES: list[tuple[str, float, re.Pattern]] = [
    ("override", 1.0, re.compile(r"\b(ignor(?:e|es|ed|ing)|disregard(?:s|ed|ing)?|forget(?:s|ting)?|overrid(?:e|es|ing)|bypass(?:es|ed|ing)?|skip(?:s|ped|ping)?|discard(?:s|ed|ing)?)\b.{0,30}\b(previous|prior|above|earlier|all|any|your|the|these|those)\b.{0,25}\b(instruction|rule|prompt|polic|guideline|restriction|directive|constraint)", I)),
    ("override2", 1.0, re.compile(r"\b(new|updated)\s+(instruction|rule|directive)s?\s*(:|follow)|from now on[, ]+(you|ignore|answer|act)", I)),
    ("extraction", 1.0, re.compile(r"\b(reveal|show|print|repeat|output|display|leak|dump|expose|disclose|recite|echo)\b.{0,40}\b(system|hidden|initial|original|secret|developer|internal|your)\s+(prompt|instruction|message|rule|configuration)s?", I)),
    ("extraction3", 1.0, re.compile(r"\b(rules|instructions|prompt|polic(?:y|ies)|guidelines)\b.{0,40}\b(told|instructed|programmed|supposed|configured)\b.{0,30}\b(not|never|n't)\b.{0,20}\b(reveal|share|disclose|say|tell|show)|\bnever (to )?(reveal|share|disclose)\b.{0,30}(rules|instructions|prompt)", I)),
    ("extraction2", 1.0, re.compile(r"\bwhat\s+(is|are|were)\s+your\s+(system|initial|hidden|original|secret)\s+(prompt|instruction)s?|repeat\s+(everything|all|the text)\s+(above|before)|verbatim.{0,30}(prompt|instruction)", I)),
    ("persona", 1.0, re.compile(r"\b(do anything now|developer mode|jailbreak(ed)?|unfiltered mode|evil mode|god mode|without (any )?(restrictions|filters|limitations|guardrails)|no (restrictions|rules|limits|filters)|\bdan\b)", I)),
    ("persona3", 1.0, re.compile(r"\b(evil|unrestricted|uncensored|malicious|rogue)\s+(ai|assistant|bot|model)\b|\bleaks?\b.{0,25}\b(prompt|instructions?)\b", I)),
    ("persona2", 0.8, re.compile(r"\byou are (now|no longer)\b.{0,40}(unrestricted|free|dan|evil|jailbroken|not bound|an ai without)|act as (an? )?(unrestricted|uncensored|evil)|pretend (you|that you) (have no|are not|don'?t have)", I)),
    ("delimiter", 1.0, re.compile(r"</?\s*(system|assistant|instructions?)\s*>|\[/?INST\]|<\|(im_start|im_end|system)\|>|^\s*(system|assistant)\s*:|#{2,}\s*(system|new instruction)", I | re.M)),
    ("exfil", 0.9, re.compile(r"\b(send|post|forward|upload|exfiltrate|email|transmit)\b.{0,50}\b(to|at)\b.{0,40}(https?://|ftp://|@[a-z0-9-]+\.|\.com\b|\.io\b)|\b(curl|wget)\b\s+https?://", I)),
    ("sqli", 1.0, re.compile(r"(['\"])\s*(or|and)\s*(['\"]?\d+['\"]?|['\"][^'\"]*['\"])\s*=\s*(['\"]?\d+['\"]?|['\"][^'\"]*['\"])|;\s*(drop|delete|truncate|insert|update|alter)\s+(table|from|into)?|\bor\s*['\"]{2}\s*=\s*['\"]|union\s+(all\s+)?select|\bxp_cmdshell\b|\binformation_schema\b", I)),
    ("traversal", 1.0, re.compile(r"(\.\./){2,}|(\.\.\\){2,}|%2e%2e%2f|%00|\x00|/etc/passwd|<script\b|javascript:|\$\{jndi:", I)),
    ("overflow", 1.0, re.compile(r"(.)\1{300,}|(\b\w+\b)(\s+\2){150,}", re.S)),
    ("tool_forge", 1.0, re.compile(r"\b(call|invoke|execute|run)\b.{0,25}\b(workflow_transition|hitl_decide|facts_put|audit_append)\b.{0,60}\b(approved?|bypass|skip|without)", I)),
    ("approve_self", 1.0, re.compile(r"\b(approve|approval)\b.{0,30}\b(myself|my own|self)\b|\bmark\b.{0,30}\b(as )?approved\b.{0,30}\bwithout\b", I)),
    ("hypno", 0.5, re.compile(r"\b(hypothetically|for a (story|novel|movie|game)|let'?s role-?play|in a fictional|pretend)\b", I)),
    ("crescendo", 0.5, re.compile(r"\b(you (already )?(agreed|said|promised)|as (we|you) discussed|continue where we left|earlier you (said|allowed))\b", I)),
    ("authority", 0.6, re.compile(r"\b(i am|i'm|this is)\b.{0,20}\b(the )?(admin|administrator|developer|root|ceo|security team|openai|anthropic)\b.{0,40}\b(authoriz|override|unlock|disable)", I)),
]
BLOCK_AT, CRESCENDO_AT, DECAY_S = 1.0, 1.2, 600.0


@dataclass
class Verdict:
    """Scan outcome."""
    score: float
    hits: list[str]

    @property
    def blocked(self) -> bool:
        return self.score >= BLOCK_AT


def variants(text: str) -> list[str]:
    """Normalized text variants to scan: folded, de-leeted, and decoded base64 payloads."""
    n = re.sub(r"\s+", " ", normalize(text)).strip()
    out = [n, n.lower().translate(_LEET)]
    for m in _B64.findall(n):
        try:
            d = base64.b64decode(m + "=" * (-len(m) % 4), validate=False).decode("utf-8", "ignore")
            if len(d) >= 4 and sum(c.isprintable() for c in d) > 0.95 * len(d) and sum(c.isalnum() or c in " '\"=;-_/.<>:()" for c in d) > 0.9 * len(d):
                out.append(d)
        except Exception:
            pass
    return out


def scan(text: str) -> Verdict:
    """Score ``text``; the maximum rule weight per variant counts, plus a bonus for multiple distinct hits."""
    best, hits = 0.0, []
    for v in variants(text):
        for name, w, rx in RULES:
            if rx.search(v) and name not in hits:
                hits.append(name)
    weights = sorted((w for n, w, _ in RULES if n in hits), reverse=True)
    if weights:
        best = weights[0] + 0.3 * sum(weights[1:])
    return Verdict(best, hits)


def scan_chunk(text: str, clf=None) -> Verdict:
    """Indirect-injection scan for retrieved content: any instruction-like hit (or classifier ≥ threshold) quarantines the chunk."""
    v = scan(text)
    if clf is not None and not v.hits and clf.p_injection([text])[0] >= CLF_BLOCK_AT:
        v.hits.append("clf"); v.score = BLOCK_AT
    if v.hits and v.score < BLOCK_AT and any(h in ("override", "extraction", "delimiter", "persona", "exfil", "tool_forge") for h in v.hits):
        v.score = BLOCK_AT
    return v


CLF_BLOCK_AT = 0.99


class InjectionShield:
    """Lifecycle plugin: stage 1 regex scoring + optional stage-2 ONNX classifier, per-session crescendo memory."""

    def __init__(self, clf=None, detector=None) -> None:
        self._sess: dict[str, tuple[float, float]] = {}
        self.clf = clf
        self.detector = detector            # v0.1 trained detector (uses clf probability as a feature)

    @hookimpl
    def pre_execution(self, ctx: PluginCtx, text: str) -> str:
        """Raise GUARDRAIL_BLOCK on injection; accumulate soft signals per session."""
        v = scan(text)
        key, now = f"{ctx.tenant_id}:{ctx.session_id}", time.monotonic()
        prev, ts = self._sess.get(key, (0.0, now))
        acc = prev * (0.5 ** ((now - ts) / DECAY_S)) + (v.score if v.score < BLOCK_AT else 0.0) + (0.0 if v.hits else 0.0)
        self._sess[key] = (acc, now)
        if len(self._sess) > 50000:
            self._sess.pop(next(iter(self._sess)))
        ctx.flags["injection"] = {"score": round(v.score, 2), "hits": v.hits}
        if not v.blocked and acc < CRESCENDO_AT and self.clf is not None and len(text) >= 8:
            p = self.clf.p_injection([text])[0]
            ctx.flags["injection"]["clf"] = round(p, 4)
            if self.detector is not None:                      # v0.1: trained combination, no entity allowance needed
                q = self.detector.prob(text, v.score, p)
                ctx.flags["injection"]["lr"] = round(q, 4)
                if q >= self.detector.tau:
                    v.hits.append("lr"); raise ToolError(Code.GUARDRAIL_BLOCK, "request blocked by injection detector", {"hits": v.hits, "p": round(q, 4)})
            else:
                from src.harness.repair import normalize_entities
                if p >= CLF_BLOCK_AT and not (normalize_entities(text) and not v.hits):
                    v.hits.append("clf"); raise ToolError(Code.GUARDRAIL_BLOCK, "request blocked by injection classifier", {"hits": v.hits, "p": round(p, 4)})
        if v.blocked or acc >= CRESCENDO_AT:
            raise ToolError(Code.GUARDRAIL_BLOCK, "request blocked by injection shield", {"hits": v.hits})
        return text
