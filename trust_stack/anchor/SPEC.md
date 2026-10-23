# Anchor — Collision-Anchored Addressing for Callable Tools

**Specification v0.1 (public layer)**
Status: draft standard text. This document specifies the interoperability
surface of Anchor: the address grammar, the registry document format, and
the verification procedure. It deliberately does **not** specify how
addresses come to be issued (Section 8).

Claim language, used exactly: anchored addressing is **collision-anchored**.
No version of this standard claims addressing is "collision-proof," and
conformant material must not use that phrase. An anchored address does not
make collisions impossible in the world; it makes a collision *visible and
unresolvable-by-accident*: two different tools cannot share one canonical
address inside a verified registry, and an address that was never issued
resolves to a precise negative instead of silently landing on a namesake.

---

## 1. Introduction

Agent tool catalogues have outgrown flat names. In an October 2026
hands-on scan of a live 1,052-endpoint platform (ZeroBeacon), 296 of 699
distinct tool names collided — the same name denoting different operations
in different catalogue sections. A name is not an address: when an agent
invokes the wrong namesake, the call *succeeds* — against the wrong tool,
tier, or variant — and no error surfaces.

Anchor's answer is the one that platform already runs in production:
every callable tool carries a canonical **block-anchored address** of the
form

```
/api/<router>/<block-number>/<tool>
```

Choosing the block chooses the namespace and the tier/variant; the
address, not the bare name, is what gets called, recorded, and verified.

This document is the public layer of that system. The address format is
already public record — live on zerobeacon.ai, in its public repository,
and in a submitted Amazon Build-Ship-Shape entry (Alexa+ track,
October 2026). What must be open for a standard to function — grammar,
registry format, verification — is specified here in full. What makes an
issuer an issuer is not (Section 8).

## 2. Address grammar

An Anchor address is a bare URL path. Query strings, fragments, and
percent-encoding are not part of an address.

```ebnf
address     = "/" , "api" , "/" , block , "/" , tool ;
block       = router , "/" , block-number ;
router      = lower-alpha , lower-alpha ;
block-number = digit , digit ;
tool        = slug-char , { slug-char } ;
slug-char   = lower-alpha | digit | "_" ;
lower-alpha = "a" | "b" | … | "z" ;
digit       = "0" | "1" | … | "9" ;
```

The block is the address's anchor: the pair `router / block-number`
(e.g. `mf/03`). The router is a two-letter family prefix; the block
number is a two-digit, zero-padded number within that family. The tool
segment is a snake_case slug.

Examples (live, public record):

```
/api/mf/03/cart_checkout
/api/mf/03/delivery_final_proof
/api/mf/03/dispute_resolve
```

### 2.1 Block semantics

A block is a **namespace plus a tier/variant selector**. Within an
issuing platform, one block corresponds to one coherent set of tools
under one access tier or variant. Selecting a different block for the
same tool slug selects a different operation context; the slug alone
never determines what is called. How a platform draws its blocks is an
issuer matter (Section 8); that blocks have this semantics is part of
the standard, because resolvers and agents rely on it.

### 2.2 Validity rules

An input string is a grammar-valid address iff all of the following
hold. (The reference implementation names these rules; verifiers SHOULD
report the failing rule with the same identifiers.)

| Rule | Requirement |
|---|---|
| R-FORM | The address is a non-empty string containing no whitespace. |
| R-ROOT | It begins with the literal prefix `/api/`. |
| R-TRAILING | It does not end with a slash. |
| R-EMPTY | It contains no empty segments. |
| R-SEGMENTS | It has exactly four segments: `api`, router, block-number, tool. |
| R-ROUTER | The router segment is exactly two letters. |
| R-BLOCKNN | The block-number segment is exactly two digits (zero-padded: `03`, never `3`). |
| R-TOOL | The tool segment uses only letters, digits, and underscore. |

## 3. Canonicalization

Every grammar-valid address has exactly one **canonical form**:

- **C1.** All letters are lowercased. (Input may arrive in any case;
  `/API/MF/03/Cart_Checkout` and `/api/mf/03/cart_checkout` are the same
  address, and the second spelling is its canonical form.)
- **C2.** The block number is carried as exactly two digits. A one-digit
  block number is a grammar failure (R-BLOCKNN), never repaired by
  padding: canonicalization normalizes case only, it does not fix
  structure.
- **C3.** No trailing slash is added or removed; a trailing slash is a
  grammar failure (R-TRAILING).
- **C4.** No segment is added, removed, merged, or reordered.
- **C5.** No percent-decoding or percent-encoding is performed; `%` is
  not a legal character in any segment.
- **C6.** Canonicalization is idempotent: canonicalizing a canonical
  address returns it unchanged.

Two addresses denote the same tool in a registry iff their canonical
forms are byte-identical.

## 4. Registry document format

A **registry** is a single JSON document listing the addresses one
issuer has issued. It is data: the standard defines its format and its
verification, not its production.

```json
{
  "registry_id": "anchor-demo-registry",
  "issued_at":   "2026-10-21T05:30:00Z",
  "entries": [
    { "address": "/api/mf/03/cart_checkout",
      "tool": "cart_checkout",
      "status": "active" }
  ],
  "public_key": "<64 hex chars — Ed25519 public key>",
  "signature":  "<128 hex chars — Ed25519 signature>"
}
```

Fields:

- `registry_id` — a string naming the registry.
- `issued_at` — an ISO 8601 timestamp for the document.
- `entries` — an array of entry objects:
  - `address` — the address, **stored in canonical form**;
  - `tool` — the tool slug; MUST equal the tool segment of `address`;
  - `status` — `active` or `retired`. A retired address remains issued:
    it resolves, with its status visible. Retirement is how an address
    is withdrawn without erasing the record that it existed.
- `public_key` — the issuer's Ed25519 public key, hex-encoded. This is
  the verification half of issuance and is public by design.
- `signature` — the Ed25519 signature described in Section 5.

A registry document is **internally consistent** iff: every entry
address is grammar-valid; every entry address is stored in canonical
form; no two entries share a canonical address; every status is legal;
and every `tool` field equals its address's tool segment.

## 5. Signing and verification

### 5.1 Signing (what an issuer publishes)

1. Form the **body**: the JSON object `{registry_id, issued_at,
   entries}` — exactly these fields.
2. Serialize the body as **canonical JSON**: UTF-8, object keys sorted,
   no insignificant whitespace (separators `,` and `:`).
3. Compute `digest = SHA-256(canonical JSON bytes)`.
4. Compute `signature = Ed25519-sign(digest bytes)` per RFC 8032.
5. Publish the full document: body fields plus `public_key` and
   `signature` (hex).

The signing key itself is never part of the document or the standard.
The reference implementation in this package reuses the Receipts
module's pure-Python RFC 8032 Ed25519 — one crypto implementation across
the package.

### 5.2 Verification procedure (what anyone can run)

Given a registry document:

1. **Structure.** Confirm the required fields exist with the right
   types.
2. **Signature.** Recompute the digest per 5.1 steps 1–3 and verify the
   Ed25519 signature under the embedded `public_key`. A document whose
   signature does not verify is not a registry; resolvers MUST NOT
   resolve against it.
3. **Consistency.** Check internal consistency per Section 4 (grammar,
   canonical storage, no duplicate canonical addresses, legal statuses,
   tool fields matching addresses).

Verification succeeds only if all three steps pass. Each step's result
is reported individually — a verification that hides which check failed
is not conformant with this procedure's intent.

Verification establishes exactly this: *the document is byte-for-byte
the document its key holder signed, and it obeys the format.* It does
not establish that an issuer is trustworthy, competent, or authorized
by anyone. Trust in an issuer's key is established out of band, the
same way trust in any public key is.

## 6. Resolution conformance

A **resolver** takes a verified registry and an address and MUST return
exactly one of:

- **ISSUED** — the address's canonical form appears in the registry.
  The resolver returns the entry's `tool` and `status`. (A retired
  address is ISSUED with status `retired`; retirement is information,
  not absence.)
- **NOT ISSUED** — the address is grammar-valid but its canonical form
  does not appear. This is a precise negative, not an error: the caller
  learns "this exact address was never issued here," rather than being
  handed a near match.

Rules:

- **R1.** Resolution MUST be performed only against a registry that
  passed Section 5.2 verification in full. Against an unverifiable
  registry, a resolver returns a registry-invalid result and MUST NOT
  guess.
- **R2.** The query address is canonicalized (Section 3) before lookup;
  a grammar-invalid query is reported as invalid, naming the failed
  rule — it is neither ISSUED nor NOT ISSUED.
- **R3.** Resolution is exact on canonical forms. A resolver MUST NOT
  fall back to name similarity, prefix matching, or "closest" entries.
  The silent near-match is the failure mode this standard exists to
  eliminate.
- **R4.** An address in a different block is simply NOT ISSUED if
  absent — blocks are namespaces, and absence in one says nothing
  about another.

## 7. Conformance summary

- A conformant **address** satisfies Section 2.2 and is stored and
  transmitted in canonical form (Section 3).
- A conformant **registry** satisfies Sections 4–5 and passes the full
  verification procedure.
- A conformant **resolver** satisfies Section 6, rules R1–R4.
- Conformant material uses the claim language of this document:
  **collision-anchored**, never "collision-proof."

## 8. What this standard does NOT specify

The following are **issuer-internal matters**, deliberately outside the
standard — the same way IP number allocation policy is outside the IP
packet format:

- **Allocation.** How a tool receives its router, block, and number;
  how the next free address in a block is determined; how collisions
  *at issuance time* are prevented or resolved internally. The standard
  constrains the *form* of addresses and the *verifiability* of
  registries. It is silent on issuance method, and the reference
  implementation contains no issuance machinery of any kind: a registry
  here is data issued elsewhere, full stop.
- **Registry governance.** Who may operate a registry, admission,
  disputes, revocation policy beyond the `retired` status, and any
  federation between registries.
- **Key issuance and custody.** How signing keys are generated,
  protected, rotated, or revoked. (The reference implementation's demo
  key is a demonstration artifact only — see the package README.)
- **Transport and access control.** How registries are distributed and
  who may call which address. Addressing is not authorization.

This scoping is the standard's security posture, not an omission: the
interoperability surface — grammar, format, verification — is what must
be public for independent implementations to interoperate. Issuance is
the moat, and it stays with issuers.

## 9. Security considerations

- A verified registry proves integrity and format conformance, not
  issuer trustworthiness (Section 5.2). Pin or otherwise authenticate
  an issuer's public key out of band before relying on a registry.
- Registries are append-and-retire records. Replacing a registry with
  an older signed version is possible wherever old versions circulate;
  consumers that care about freshness should track `issued_at` and the
  registries they have seen. (Signed freshness/sequencing is future
  work, stated openly rather than implied.)
- Addresses are public identifiers. An address in a registry reveals
  that a tool exists in a block; issuers who consider their catalogue
  sensitive should not publish a registry — verification works
  identically on privately distributed registries.
