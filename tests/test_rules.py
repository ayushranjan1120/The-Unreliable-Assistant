"""Enforces CLAUDE.md non-negotiable rules + core unit properties (L0)."""
import ast
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SRC = [p for p in (ROOT / "src").rglob("*.py")] + [p for p in (ROOT / "mcp_servers").glob("*.py")]


def test_no_raw_redis_keys_outside_keys_module():
    for p in SRC:
        if p.name == "keys.py":
            continue
        assert not re.search(r"""f?["']t:\{""", p.read_text()), f"raw tenant key literal in {p.relative_to(ROOT)} — use src.keys"


def test_no_sql_outside_repo():
    for p in SRC:
        if p.name == "repo.py":
            continue
        txt = p.read_text()
        assert not re.search(r"\.(execute|fetch|fetchrow|fetchval)\(\s*[\"']", txt), f"raw SQL call in {p.relative_to(ROOT)} — use src.repo"


def test_utils_pure_and_workbench_modules_exist():
    for need in ("app.py", "state.py", "cached.py", "components/sidebar.py", "views/scorecard.py", "utils/kpi_eval.py"):
        assert (ROOT / need).exists(), need


def test_contracts_are_strict_extra_forbid():
    from src.contracts import models
    from src.contracts.common import Strict
    for name, cls in vars(models).items():
        if isinstance(cls, type) and issubclass(cls, Strict) and cls is not Strict:
            assert cls.model_config.get("strict") and cls.model_config.get("extra") == "forbid", name


def test_bm25_matches_rank_bm25():
    from rank_bm25 import BM25Okapi
    from src.rag.bm25 import BM25Index
    docs = [["alpha", "beta", "beta"], ["beta", "gamma"], ["delta"], ["alpha", "alpha", "gamma", "epsilon"], ["zeta", "eta", "theta"]]
    ours, ref = BM25Index(docs), BM25Okapi(docs, k1=1.25, b=0.75)
    q = ["alpha", "gamma"]
    got = dict(ours.search(q, 1.25, 0.75, 10))
    for i, s in enumerate(ref.get_scores(q)):
        assert got.get(i, 0.0) == pytest.approx(float(s), rel=1e-9, abs=1e-9)


def test_rrf_dedup_and_normalization():
    from src.rag.fusion import rrf
    hashes = ["a", "b", "c", "a"]                        # doc 3 duplicates doc 0
    out = rrf([[0, 1, 2], [3, 1]], hashes, k=60, pool=10)
    docs = [d for d, _, _ in out]
    assert not (0 in docs and 3 in docs)                 # deduped by chunk_hash
    assert all(0 < s <= 1 for _, s, _ in out) and [r for *_, r in out] == list(range(1, len(out) + 1))


def test_chunker_overlap():
    from src.rag.index import chunk_text
    text = " ".join(f"w{i}" for i in range(2000))
    ch = chunk_text(text)
    assert len(ch) > 1 and all(len(c.split()) <= 394 for c in ch)
    assert set(ch[0].split()[-49:]) & set(ch[1].split()[:60])      # ≈64-token overlap


def test_keys_isolation_and_ttl_jitter():
    from src import keys
    from src.errors import ToolError
    assert keys.l1_key("tenant_1", "user_1", "Q?") != keys.l1_key("tenant_2", "user_1", "Q?")
    assert keys.tenant_of_key(keys.l1_key("tenant_1", "u", "q")) == "tenant_1"
    assert keys.l1_key("tenant_1", "user_1", "What  is X?") == keys.l1_key("tenant_1", "user_1", "what is x")
    with pytest.raises(ToolError):
        keys.l1_key("evil", "u", "q")
    ttls = {keys.ttl() for _ in range(300)}
    assert min(ttls) >= 255 and max(ttls) <= 345 and len(ttls) > 20


async def test_cache_tenant_assertion_and_lru_fallback():
    from src.cache import TwoTierCache
    from src.errors import Code, ToolError
    c = TwoTierCache(None)                                # redis=None → per-worker LRU (FMEA #1)
    await c.store("tenant_1", "user_1", "q", {"route": "HYBRID_RAG", "answer": "a", "citations": [], "abstained": False}, shareable=True)
    assert (await c.lookup("tenant_1", "user_1", "q"))[0] == "L1"
    assert (await c.lookup("tenant_1", "user_2", "q"))[0] == "L2"       # shareable → other user, same tenant
    assert await c.lookup("tenant_2", "user_1", "q") is None            # other tenant never sees it
    k = next(iter(c._lru))                                              # corrupt an entry's tenant → must raise, not leak
    c._lru[k] = (c._lru[k][0], json.dumps({"tenant_id": "tenant_9", "user_id": "user_1", "payload": {}}))
    with pytest.raises(ToolError) as e:
        await c.lookup("tenant_1", "user_1", "q")
    assert e.value.code == Code.TENANT_VIOLATION


def test_plugins_fail_closed_and_order():
    from src.errors import Code, ToolError
    from src.plugins.hooks import PluginCtx, PluginMeta, PluginRegistry, hookimpl
    from src.plugins.pii import PiiRedactor
    ctx = PluginCtx("tenant_1", "user_1", "s", "a" * 32)
    reg = PluginRegistry()
    reg.register(PiiRedactor(), PluginMeta("pii_redactor", "PRE_EXECUTION", 10, True))
    class Boom:
        @hookimpl
        def pre_execution(self, ctx, text): raise RuntimeError("x")
    reg.register(Boom(), PluginMeta("boom", "PRE_EXECUTION", 20, True))
    with pytest.raises(ToolError) as e:
        reg.run_pre(ctx, "hello")
    assert e.value.code == Code.GUARDRAIL_BLOCK                          # plugin crash with fail_closed → reject, never pass through
    reg.set_enabled("boom", False)
    with pytest.raises(ToolError):                                       # disabling a fail-closed guard must not silently pass traffic
        reg.run_pre(ctx, "hello")


def test_pii_redaction_examples_and_no_raw_values():
    from src.plugins.pii import redact
    for raw, kind in (("mail john.doe[at]example[dot]com now", "EMAIL"), ("ssn 078-05-1120", "SSN"), ("call +1 (415) 555-0132", "PHONE"), ("аdmin@exаmple.com", "EMAIL")):
        out, c = redact(raw)
        assert kind in c and f"<{kind}:" in out and "example" not in out.lower().replace("<email", "") .replace("exаmple", "")
    assert redact("LOT-88392 and SKU-014 and 12345")[1] == {}            # ids/short numbers are not PII


def test_manifest_detects_tamper(tmp_path, monkeypatch):
    from mcp_servers import manifest
    manifest.sign_all("t", "ih")
    assert manifest.verify("graphify-vault")[0]
    p = manifest.MANIFESTS / "graphify-vault.json"
    m = json.loads(p.read_text()); m["body"]["code_sha256"] = "0" * 64; p.write_text(json.dumps(m))
    assert manifest.verify("graphify-vault") == (False, "bad signature")
    manifest.sign_all("v0", "")                                          # restore


async def test_audit_chain_tamper_evident_and_append_only():
    import asyncpg
    from src.config import get_settings
    from src.repo import Repo
    repo = await Repo.create(get_settings().pg_dsn, 4)
    t = "tenant_unit_audit"
    try:
        for i in range(3):
            await repo.audit_append(t, "UNIT", "tester", {"i": i})
        assert (await repo.audit_verify(t))["ok"]
        with pytest.raises(asyncpg.PostgresError):                        # append-only trigger
            async with repo.tx(t) as c:
                await c.execute("UPDATE audit_log SET actor='x' WHERE tenant_id=$1", t)
        async with repo.tx("tenant_other") as c:                          # RLS: another tenant sees nothing of ours
            assert await c.fetchval("SELECT count(*) FROM audit_log WHERE tenant_id=$1", t) == 0
    finally:
        await repo.pool.close()
