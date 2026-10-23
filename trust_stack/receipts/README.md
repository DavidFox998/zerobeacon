# Receipts — signed, hash-chained evidence for agent work

An agent's word is not evidence. A **Receipt** is: a small signed record
that a specific action was taken by a specific actor, in a specific
order, with a specific result — chained so that editing, reordering, or
deleting history is detectable by anyone holding the bundle.

## What a receipt proves — and what it does not

A verified bundle proves **process integrity**: these actions, in this
order, under these actors, with these recorded results, signed by the
chain key. It does **not** prove the work's conclusion is true. A
faithfully recorded bad method produces a perfectly valid chain. That
limit is deliberate and stated here rather than discovered later.

## Format

Receipt body (hashed + signed):

```json
{
  "receipt_id": "<uuid hex>",
  "ts": 1760000000.0,
  "actor": "gr_ab12cd34ef56",
  "action": {"method": "GET", "host": "api.example.com", "path": "/demo/thing"},
  "result_status": 200,
  "prev_hash": "<sha256 hex of previous receipt, or 64 zeros at genesis>"
}
```

- `hash` = SHA-256 over the canonical JSON (sorted keys, compact
  separators) of the body above.
- `signature` = Ed25519 over the 32 hash bytes, chain key.
- A **bundle** is `{public_key, count, head_hash, receipts[]}`. The
  verifier recomputes every hash, checks chain continuity
  (`prev_hash` links), verifies every signature, and checks the
  declared `count` and `head_hash` — so a tampered receipt fails at its
  index, and a tail-truncated bundle fails its count/head check.

## Crypto

Pure-Python Ed25519 implemented from RFC 8032 (reference formulas,
`hashlib.sha512`), standard library only. Proven, not asserted: the
test suite checks RFC 8032 test vector #1 (empty message) exactly, and
during development the implementation's deterministic signatures were
confirmed byte-for-byte identical to OpenSSL 3.0's Ed25519 on an
arbitrary message, with the derived public key matching the RFC vector.

## Usage

```
python3 receipts.py verify <bundle.json>   # PASS: n receipts | FAIL at index i: reason
python3 receipts.py show   <bundle.json>   # readable chain
```

As a library (this is how Grantline uses it):

```python
import receipts
kp = receipts.Keypair.load_or_create("signing_key.json")   # 0600 file
chain = receipts.ReceiptChain(kp)
chain.append(actor="gr_…", action={"method": "GET", "host": "…", "path": "/…"},
             result_status=200)
bundle = chain.to_bundle()
ok, detail = receipts.verify_bundle(bundle)
```

## Honest v1 limits

- Keys are demo-grade: a 0600 JSON file, not a KMS/HSM.
- Truncation protection rests on the bundle's declared count/head; a
  production deployment should anchor `head_hash` somewhere the chain
  writer cannot rewrite (a transparency log, a counterparty copy).
- Affine-coordinate pure-Python Ed25519 is correct but not fast and not
  constant-time — fine for receipts, not for a high-throughput signer.
