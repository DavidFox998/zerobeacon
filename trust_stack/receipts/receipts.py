#!/usr/bin/env python3
"""Receipts — signed, hash-chained evidence records for agent work.

A Receipt attests that a recorded action happened, in order, under a
recorded actor, with a recorded result. It proves *process integrity* —
these actions, in this order, with these results — not that the work's
conclusion is true. That limit is part of the format's honesty contract.

Crypto: pure-Python Ed25519 exactly per RFC 8032 (reference formulas,
hashlib.sha512). No third-party code. Signing keys are demo-grade (see
README): a 0600 JSON file, not a KMS/HSM.

Chain: each receipt body includes prev_hash (the previous receipt's
hash; genesis = 64 zero hex chars). hash = SHA-256 over the canonical
JSON of the body. signature = Ed25519(hash bytes) under the chain key.
A bundle is {public_key, count, head_hash, receipts[]}; the verifier
recomputes every hash, checks chain continuity, verifies every
signature, and checks count/head so tail truncation is detected
(anchoring the head externally is the production upgrade — see README).

CLI:
  receipts.py verify <bundle.json>   PASS <n> receipts | FAIL index <i>: <reason>
  receipts.py show   <bundle.json>   readable chain

Standard library only. Python 3.12.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid

# =====================================================================
# Ed25519 per RFC 8032 (reference formulas, affine coordinates)
# =====================================================================

_B = 256
_Q = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _Q - 2, _Q)


_D = (-121665 * _inv(121666)) % _Q
_I = pow(2, (_Q - 1) // 4, _Q)


def _xrecover(y: int) -> int:
    xx = (y * y - 1) * _inv(_D * y * y + 1) % _Q
    x = pow(xx, (_Q + 3) // 8, _Q)
    if (x * x - xx) % _Q != 0:
        x = (x * _I) % _Q
    if x % 2 != 0:
        x = _Q - x
    return x


_BY = (4 * _inv(5)) % _Q
_BX = _xrecover(_BY)
_BASE = (_BX % _Q, _BY % _Q)
_IDENTITY = (0, 1)


def _edwards(P, Q):
    x1, y1 = P
    x2, y2 = Q
    t = _D * x1 * x2 * y1 * y2
    return (
        (x1 * y2 + x2 * y1) * _inv(1 + t) % _Q,
        (y1 * y2 + x1 * x2) * _inv(1 - t) % _Q,
    )


def _scalarmult(P, e: int):
    Q = _IDENTITY
    for i in range(e.bit_length() - 1, -1, -1):
        Q = _edwards(Q, Q)
        if (e >> i) & 1:
            Q = _edwards(Q, P)
    return Q


def _bit(data: bytes, i: int) -> int:
    return (data[i // 8] >> (i % 8)) & 1


def _encodeint(y: int) -> bytes:
    return y.to_bytes(32, "little")


def _encodepoint(P) -> bytes:
    x, y = P
    bits = bytearray(y.to_bytes(32, "little"))
    if x & 1:
        bits[31] |= 0x80
    return bytes(bits)


def _decodeint(s: bytes) -> int:
    return int.from_bytes(s, "little")


def _isoncurve(P) -> bool:
    x, y = P
    return (-x * x + y * y - 1 - _D * x * x * y * y) % _Q == 0


def _decodepoint(s: bytes):
    if len(s) != 32:
        raise ValueError("point encoding must be 32 bytes")
    y = int.from_bytes(s, "little") & (2 ** 255 - 1)
    x = _xrecover(y)
    if (x & 1) != ((s[31] >> 7) & 1):
        x = _Q - x
    P = (x, y)
    if not _isoncurve(P):
        raise ValueError("point is not on the curve")
    return P


def _secret_scalar(h: bytes) -> int:
    return 2 ** (_B - 2) + sum(2 ** i * _bit(h, i) for i in range(3, _B - 2))


def ed25519_publickey(secret: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte secret seed."""
    if len(secret) != 32:
        raise ValueError("secret seed must be 32 bytes")
    h = hashlib.sha512(secret).digest()
    a = _secret_scalar(h)
    return _encodepoint(_scalarmult(_BASE, a))


def _hint(data: bytes) -> int:
    """RFC 8032 Hint(): the SHA-512 digest read as a little-endian integer."""
    digest = hashlib.sha512(data).digest()
    return sum(2 ** i * _bit(digest, i) for i in range(2 * _B))


def ed25519_sign(message: bytes, secret: bytes, public: bytes) -> bytes:
    """Deterministic Ed25519 signature (RFC 8032) — 64 bytes."""
    h = hashlib.sha512(secret).digest()
    a = _secret_scalar(h)
    r = _hint(h[32:64] + message) % _L
    R = _scalarmult(_BASE, r)
    S = (r + _hint(_encodepoint(R) + public + message) * a) % _L
    return _encodepoint(R) + _encodeint(S)


def ed25519_verify(signature: bytes, message: bytes, public: bytes) -> bool:
    """Return True iff the Ed25519 signature is valid. Never raises."""
    try:
        if len(signature) != 64 or len(public) != 32:
            return False
        R = _decodepoint(signature[0:32])
        A = _decodepoint(public)
        S = _decodeint(signature[32:64])
        if S >= _L:
            return False
        h = _hint(signature[0:32] + public + message)
        return _scalarmult(_BASE, S) == _edwards(R, _scalarmult(A, h))
    except Exception:
        return False


# =====================================================================
# Receipts: hash-chained, signed records
# =====================================================================

GENESIS_HASH = "0" * 64


def _canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _receipt_hash(body: dict) -> str:
    return hashlib.sha256(_canonical(body)).hexdigest()


class Keypair:
    """A demo-grade Ed25519 keypair persisted as JSON (file mode 0600)."""

    def __init__(self, secret: bytes):
        self.secret = secret
        self.public = ed25519_publickey(secret)

    @classmethod
    def load_or_create(cls, path: str) -> "Keypair":
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return cls(bytes.fromhex(data["secret"]))
        secret = os.urandom(32)
        kp = cls(secret)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"secret": secret.hex(), "public": kp.public.hex()}, fh,
                      indent=2)
        os.chmod(path, 0o600)
        return kp

    @property
    def public_hex(self) -> str:
        return self.public.hex()


class ReceiptChain:
    """An append-only chain of signed receipts.

    Construct empty (genesis) or from a loaded bundle dict. append()
    returns the new receipt; to_bundle() exports the verifiable bundle.
    """

    def __init__(self, keypair: Keypair, receipts: list | None = None):
        self.keypair = keypair
        self.receipts: list[dict] = list(receipts) if receipts else []

    @classmethod
    def from_bundle(cls, keypair: Keypair, bundle: dict) -> "ReceiptChain":
        return cls(keypair, bundle.get("receipts", []))

    def append(self, actor: str, action: dict, result_status) -> dict:
        prev = self.receipts[-1]["hash"] if self.receipts else GENESIS_HASH
        body = {
            "receipt_id": uuid.uuid4().hex,
            "ts": time.time(),
            "actor": actor,
            "action": {
                "method": action.get("method", ""),
                "host": action.get("host", ""),
                "path": action.get("path", ""),
            },
            "result_status": result_status,
            "prev_hash": prev,
        }
        digest = _receipt_hash(body)
        signature = ed25519_sign(bytes.fromhex(digest),
                                 self.keypair.secret, self.keypair.public)
        receipt = dict(body)
        receipt["hash"] = digest
        receipt["signature"] = signature.hex()
        self.receipts.append(receipt)
        return receipt

    def to_bundle(self) -> dict:
        return {
            "public_key": self.keypair.public_hex,
            "count": len(self.receipts),
            "head_hash": (self.receipts[-1]["hash"]
                          if self.receipts else GENESIS_HASH),
            "receipts": self.receipts,
        }


def verify_bundle(bundle: dict) -> tuple[bool, object]:
    """Verify a bundle end to end.

    Returns (True, count) on success, or (False, (index, reason)) at the
    first defect. index == -1 for bundle-level defects (count/head).
    """
    receipts = bundle.get("receipts")
    if not isinstance(receipts, list):
        return False, (-1, "bundle has no receipts list")
    if bundle.get("count") != len(receipts):
        return False, (-1, f"count mismatch: bundle declares "
                           f"{bundle.get('count')}, contains {len(receipts)} "
                           f"(truncated or extended chain)")
    try:
        public = bytes.fromhex(bundle["public_key"])
    except Exception:
        return False, (-1, "bad public_key encoding")
    prev = GENESIS_HASH
    for i, receipt in enumerate(receipts):
        body = {k: receipt.get(k) for k in
                ("receipt_id", "ts", "actor", "action", "result_status",
                 "prev_hash")}
        if body["prev_hash"] != prev:
            return False, (i, f"chain broken: prev_hash does not match "
                              f"receipt {i - 1}'s hash")
        digest = _receipt_hash(body)
        if digest != receipt.get("hash"):
            return False, (i, "hash mismatch: receipt body was altered")
        try:
            signature = bytes.fromhex(receipt.get("signature", ""))
        except Exception:
            return False, (i, "bad signature encoding")
        if not ed25519_verify(signature, bytes.fromhex(digest), public):
            return False, (i, "signature does not verify")
        prev = digest
    if bundle.get("head_hash") != prev:
        return False, (-1, "head_hash mismatch: chain head was altered")
    return True, len(receipts)


# =====================================================================
# CLI
# =====================================================================

def _load_bundle(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in ("verify", "show"):
        print("usage: receipts.py {verify|show} <bundle.json>", file=sys.stderr)
        return 2
    bundle = _load_bundle(argv[2])
    if argv[1] == "verify":
        ok, detail = verify_bundle(bundle)
        if ok:
            print(f"PASS: {detail} receipts verified "
                  f"(hash chain + Ed25519 signatures)")
            return 0
        index, reason = detail
        where = f"receipt index {index}" if index >= 0 else "bundle"
        print(f"FAIL at {where}: {reason}")
        return 1
    # show
    ok, detail = verify_bundle(bundle)
    print(f"bundle: {bundle.get('count')} receipts, "
          f"public_key {str(bundle.get('public_key'))[:16]}…, "
          f"verification: {'PASS' if ok else 'FAIL ' + str(detail)}")
    for i, r in enumerate(bundle.get("receipts", [])):
        a = r.get("action", {})
        print(f"  [{i}] {r.get('actor')} {a.get('method')} "
              f"{a.get('host')}{a.get('path')} -> {r.get('result_status')} "
              f"hash {str(r.get('hash'))[:12]}… prev {str(r.get('prev_hash'))[:12]}…")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
