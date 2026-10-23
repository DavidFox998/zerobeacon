# Anchor — the standard's public layer, as a working reference implementation

Anchor gives every callable agent tool a canonical **block-anchored
address** — `/api/<router>/<block-number>/<tool>` — so an agent resolves
an exact address instead of trusting a bare name that may have namesakes.
In the October 2026 scan that motivates this standard, 296 of 699
distinct tool names on a live platform collided. Addresses are
**collision-anchored** (the scoped claim; never "collision-proof"): two
different tools cannot share one canonical address inside a verified
registry, and an address that was never issued resolves to a precise
NOT ISSUED instead of silently landing on a namesake.

This directory is the **public layer only**: the publishable spec, the
address parser/canonicalizer, the signed-registry format, and the
verification/resolution surface — proven against a small demo registry
seeded with the three demo-trio addresses that are already public record
(the Amazon Build-Ship-Shape demo: `cart_checkout`,
`delivery_final_proof`, `dispute_resolve`, all in block `mf/03`).

## Files

| File | What it is |
|---|---|
| `SPEC.md` | The publishable standard text: grammar (EBNF), canonicalization, registry format, signing + verification procedure, resolver conformance, and the explicit non-goals (SPEC §8). Stays in the build tree; nothing here is published by this build. |
| `anchor.py` | Reference implementation of the public layer: `parse`, `validate`, `sign-registry`, `verify-registry`, `verify-address`. |
| `anchor_mcp.py` | Stdio MCP server (protocol 2025-11-25): `parse_address`, `verify_address`, `registry_info`. |
| `demo-registry-source.json` | The unsigned body of the demo registry — hand-authored data. |
| `registry.json` | The signed demo registry (verifies with `anchor.py verify-registry registry.json`). |
| `DEMO-KEY-not-production.json` | The demo signing keypair. **A demonstration artifact only** — see the warning below. |
| `tests/run_tests.py` | The test suite, including the boundary test described below. |

## Quick start

```bash
python3 anchor.py parse /api/mf/03/cart_checkout
python3 anchor.py validate /api/MF/03/Cart_Checkout     # valid; canonical form reported
python3 anchor.py validate /api/mf/3/cart_checkout      # invalid: R-BLOCKNN
python3 anchor.py verify-registry registry.json         # seven checks, each reported
python3 anchor.py verify-address registry.json /api/mf/03/cart_checkout   # ISSUED
python3 anchor.py verify-address registry.json /api/mf/03/never_issued    # NOT ISSUED
python3 anchor.py sign-registry demo-registry-source.json \
    --key DEMO-KEY-not-production.json --out registry.json
python3 tests/run_tests.py
```

## The boundary — what was deliberately NOT built

This build implements the disclosure boundary settled for Anchor
(public / interface / sealed-core layers):

- **Built:** the address format and grammar, canonicalization, the
  registry document format, Ed25519 signing of registries, the full
  verification procedure, and exact-match resolution. Verification is
  the half of a standard that *must* be open — anyone can check a
  registry with nothing but the public key in the document.
- **Deliberately absent — the sealed core:** any allocation method
  (how a tool receives its router, block, and number; how the next
  free address is determined), collision-resolution internals beyond
  the published grammar and canonicalization rules, registry
  governance workflows, and any production key material.

The absence is enforced, not just asserted: `tests/run_tests.py`
includes a **boundary test** that scans `anchor.py`'s source and fails
the suite if any function name carries allocation vocabulary
(allocate / assign / next_free / pick / choose), if any code mutates a
loaded registry's entry list, or if any code constructs a registry
entry. There is no function that issues an address. A registry in this
package is **data issued elsewhere**; Anchor formats, signs, and
verifies — nothing more. The demo registry above is hand-authored for
exactly this reason: producing it with an allocator would have built
the thing the boundary forbids.

Why: the moat is *issuance*, not verification. Publishing the
verification surface makes independent implementations possible;
publishing issuance would give away the asset the standard is built
on. SPEC §8 states this scoping in the standard's own text.

## Demo key warning

`DEMO-KEY-not-production.json` is a fresh Ed25519 keypair generated for
this demonstration. It is **not** ZeroBeacon's key, not any production
issuance key, and it attests to nothing beyond this demo registry. Its
private half sits in the build tree (mode 0600) so the sign/verify
round-trip is reproducible in tests. Real issuers keep signing keys in
proper custody; key issuance and custody are explicitly out of scope
for the standard (SPEC §8).

## Crypto: one implementation

Signing and verification reuse the package's single Ed25519
implementation — `../receipts/receipts.py` (pure-Python, exactly per
RFC 8032), imported the same way Grantline imports it. Anchor adds no
crypto of its own. A registry signature signs the SHA-256 digest of
the canonical-JSON registry body `{registry_id, issued_at, entries}`,
the same canonicalization the Receipts module uses.

## Honest limits

- **Verification ≠ trust.** A verified registry proves the document is
  byte-for-byte what its key holder signed and that it obeys the
  format. It does not prove the issuer is trustworthy; authenticate an
  issuer's public key out of band before relying on its registry
  (SPEC §5.2, §9).
- **Freshness.** An older signed registry still verifies. Consumers
  that care should track `issued_at` and seen versions; signed
  sequencing is openly listed as future work in SPEC §9, not implied.
- **Demo scale.** The shipped registry carries three entries — the
  public demo trio — to exercise the format. The production platform
  behind the motivating scan runs 1,052 issued endpoints (1,003
  MCP-callable); onboarding a full catalogue is a data exercise, not a
  code change, and no production registry is shipped here.
- **Catalogue visibility.** A published registry reveals that its
  addresses exist. Verification works identically on privately
  distributed registries (SPEC §9).
- **Pure-Python Ed25519** is reference-grade, not constant-time — the
  same caveat the Receipts module carries. Fine for verification and
  demo signing; production signing belongs behind hardened key custody.
