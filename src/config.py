"""Runtime settings (env-driven) and the model/threshold manifest per execution profile (design §1.16, ADR-2/7)."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _parse_env_value(raw: str) -> str:
    """Value of a KEY=VALUE line: quoted values are unquoted; unquoted values lose a trailing ' # comment'."""
    v = raw.strip()
    if v[:1] in ("'", '"') and v.endswith(v[:1]) and len(v) >= 2:
        return v[1:-1]
    return re.split(r"\s+#", v, maxsplit=1)[0].strip()


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from ./.env (gitignored). Variables already in the environment win; the FIRST occurrence of a key
    in the file wins over later duplicates; empty values never shadow a real value."""
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = _parse_env_value(v)
                if v and k.strip() not in os.environ:
                    os.environ[k.strip()] = v


_load_dotenv()


@dataclass(frozen=True)
class ModelManifest:
    """Model set + the threshold calibrated for it. Changing models re-calibrates the threshold (ADR-2)."""
    profile: str
    bi_encoder: str
    reranker: str
    nli: str
    llm_backend: str            # extractive | hf_local | openai_compat
    llm_model: str
    rerank_threshold: float     # abstention cutoff calibrated for this reranker
    embed_dim: int


MANIFESTS: dict[str, ModelManifest] = {
    "target": ModelManifest("target", "BAAI/bge-large-en-v1.5", "BAAI/bge-reranker-v2-m3", "cross-encoder/nli-deberta-v3-base",
                            "openai_compat", os.environ.get("LLM_MODEL", "UNSET-select-via-ADR-7-bakeoff"), 0.42, 1024),
    "dev-cpu": ModelManifest("dev-cpu", "BAAI/bge-small-en-v1.5", "cross-encoder/ms-marco-MiniLM-L-6-v2",
                             "cross-encoder/nli-deberta-v3-small", os.environ.get("LLM_BACKEND", "extractive"),
                             os.environ.get("LLM_MODEL", "Qwen/Qwen2.5-1.5B-Instruct"), 0.42, 384),
}


@dataclass(frozen=True)
class Settings:
    """All tunables with design defaults."""
    profile: str = os.environ.get("PROFILE", "dev-cpu")
    version: str = os.environ.get("BUILD_VERSION", "v0")
    redis_url: str = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    pg_dsn: str = os.environ.get("PG_DSN", "postgresql://ua_app:ua_dev_pw@127.0.0.1:5432/ua")
    state_url: str = os.environ.get("STATE_URL", "http://127.0.0.1:8081/mcp")
    vault_dir: Path = Path(os.environ.get("VAULT_DIR", ROOT / "knowledge_vault"))
    index_dir: Path = Path(os.environ.get("INDEX_DIR", ROOT / "artifacts" / "index"))
    graph_path: Path = Path(os.environ.get("GRAPH_PATH", ROOT / "artifacts" / "graph.json"))
    # loop (ADR-4)
    max_iterations: int = 5
    per_step_timeout_ms: int = 120
    total_loop_budget_ms: int = 450
    # rag (ADR-1)
    bm25_k1: float = 1.25
    bm25_b: float = 0.75
    top_k: int = 50
    rrf_k: int = 60
    pool: int = 40
    rerank_k: int = 4
    efsearch: int = 64
    # triage
    triage_timeout_ms: int = 30
    # bulkheads
    rag_pool: int = 64
    db_pool: int = 128
    llm_pool: int = 256
    # cache
    cache_ttl_s: int = 300
    cache_jitter_s: int = 45
    l2_cosine: float = 0.92
    # observation / memory
    obs_max_tokens: int = 180
    hot_turns: int = 3
    mcp_timeout_ms: int = 150
    # dev-cpu time dilation (design §1.16): CPU inference is ~10-20x slower than the GPU target, so loop/step/MCP
    # timeouts are multiplied. Reported latencies stay RAW; budget-compliance results are tagged with the scale.
    time_scale: float = float(os.environ.get("TIME_SCALE", "20" if os.environ.get("PROFILE", "dev-cpu") == "dev-cpu" else "1"))
    manifest: ModelManifest = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.manifest is None:
            object.__setattr__(self, "manifest", MANIFESTS[self.profile])


def scaled(s: "Settings", ms: float) -> float:
    """Apply the profile's time dilation to a budget in ms."""
    return ms * s.time_scale


def get_settings() -> Settings:
    """Return settings built from the environment."""
    return Settings()
