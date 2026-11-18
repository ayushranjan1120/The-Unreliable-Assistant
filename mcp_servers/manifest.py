"""Signed MCP server manifests (Gate 13): Ed25519 over {server, version, code_sha256, tool schema hash, index/graph hash}.

The host refuses to spawn a server whose manifest signature, code hash or index hash does not verify.
The dev private key lives in ``artifacts/keys`` (gitignored); production would use a KMS-held key.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

ROOT = Path(__file__).resolve().parent.parent
KEYS, MANIFESTS = ROOT / "artifacts" / "keys", Path(__file__).resolve().parent / "manifests"
SERVERS = {"hybrid-rag": "rag_server.py", "graphify-vault": "graphify_server.py", "state-store": "state_server.py"}


def _keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    KEYS.mkdir(parents=True, exist_ok=True)
    sk_path = KEYS / "manifest_ed25519.pem"
    if not sk_path.exists():
        sk = Ed25519PrivateKey.generate()
        sk_path.write_bytes(sk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        sk_path.chmod(0o600)
    sk = serialization.load_pem_private_key(sk_path.read_bytes(), None)
    return sk, sk.public_key()  # type: ignore[return-value]


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _body(server: str, version: str, index_hash: str) -> dict:
    schema_hash = _sha(ROOT / "src" / "contracts" / "models.py")
    return {"server": server, "version": version, "code_sha256": _sha(Path(__file__).resolve().parent / SERVERS[server]),
            "contracts_sha256": schema_hash, "index_hash": index_hash}


def sign_all(version: str, index_hash: str) -> list[str]:
    """(Re)generate and sign every server manifest."""
    sk, _ = _keypair()
    MANIFESTS.mkdir(exist_ok=True)
    out = []
    for name in SERVERS:
        body = _body(name, version, index_hash if name == "hybrid-rag" else "")
        sig = sk.sign(json.dumps(body, sort_keys=True).encode()).hex()
        (MANIFESTS / f"{name}.json").write_text(json.dumps({"body": body, "sig": sig}, indent=1))
        out.append(name)
    return out


def verify(server: str, index_hash: str = "") -> tuple[bool, str]:
    """Verify signature, code hash and (for hybrid-rag) index hash. Returns ``(ok, reason)``."""
    p = MANIFESTS / f"{server}.json"
    if not p.exists():
        return False, "manifest missing"
    m = json.loads(p.read_text())
    _, pk = _keypair()
    try:
        pk.verify(bytes.fromhex(m["sig"]), json.dumps(m["body"], sort_keys=True).encode())
    except InvalidSignature:
        return False, "bad signature"
    cur = _body(server, m["body"]["version"], m["body"]["index_hash"])
    if cur["code_sha256"] != m["body"]["code_sha256"]:
        return False, "server code changed since signing"
    if cur["contracts_sha256"] != m["body"]["contracts_sha256"]:
        return False, "tool contracts changed since signing"
    if server == "hybrid-rag" and index_hash and m["body"]["index_hash"] != index_hash:
        return False, "index hash mismatch"
    return True, "ok"


if __name__ == "__main__":
    import sys
    idx = json.loads((ROOT / "artifacts" / "index" / "manifest.json").read_text()) if (ROOT / "artifacts" / "index" / "manifest.json").exists() else {}
    ih = hashlib.sha256(json.dumps(idx, sort_keys=True).encode()).hexdigest() if idx else ""
    print("signed:", sign_all(sys.argv[1] if len(sys.argv) > 1 else "v0", ih))
    print({s: verify(s, ih if s == "hybrid-rag" else "") for s in SERVERS})
