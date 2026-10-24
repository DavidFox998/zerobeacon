# ZeroBeacon — PayPal Judges Key Manual

**Key lane:** PayPal AI Hackathon judges
**Tier:** ENTERPRISE (enterprise_1000)
**Platform:** https://api.zerobeacon.ai · **Code & technical docs:**
github.com/DavidFox998/zerobeacon

This key unlocks the full ZeroBeacon platform. For this lane the
manual leads with the **Trust Stack harness** — the entry's subject:
a verification layer that lets an agent's work be audited, verified,
certified, and recorded before it is trusted. The toolset is
covered second.

## Authentication

Every call carries the key as a header:

```
X-API-Key: zbk_…
```

A missing or under-tier key returns `tier_required` naming
`enterprise_1000`. Keys are issued per lane; this key serves this
lane only.

## 1. The trust harness (primary)

Four modules and one composed flow, hosted outside the catalogue's
routers (`GET /health` reports the catalogue unchanged: 1,052
tools, 21 routers):

- **Apathy — audit.** Mechanical verification with zero self-trust:
  every count and claim re-measured by a second, independent method
  before it is believed. A claim that fails re-measurement stops the
  flow here.
- **Proven — verify.** Live probes check claimed capabilities
  against the real system (claimed-vs-measured), not against
  documentation.
- **Anchor — certify.** Issues the certificate for work that passed
  audit and verification. The certificate format and its
  verification are public; the issuance internals are not.
- **Receipts — record.** A chained, tamper-evident ledger of what
  was checked and what was issued, so the sequence can be replayed
  and verified after the fact.
- **Flow — the composition.** Proposal in → audit + verify →
  certificate out (pass) or a named refusal (fail) → receipt
  recorded. This is the endpoint an integrating app calls.

### The evaluation path (10 minutes)

1. `GET /trust` — the public page naming the four modules and the
   flow.
2. `POST /api/trust/flow` with a well-formed proposal — returns a
   certificate (**ISSUED**) and a chained receipt.
3. Re-submit with one claim altered — returns a **refusal naming
   the failed check** (e.g. the audit's recount), certificate null,
   and nothing recorded.
4. Fetch the receipt chain and verify it — the pass is replayable,
   the refusal left no trace in the ledger.

### Surfaces

- REST: `/api/trust/{apathy, proven, anchor, receipts, flow}`
- MCP: `POST /api/trust/mcp` — 9 tools (the composed flow and the
  module calls an agent needs)
- Public: `GET /trust`

## 2. The toolset (secondary)

Behind the same key, the full ZeroBeacon catalogue: **1,052
operations across 21 routers**, 1,003 callable over MCP (protocol
revisions 2024-11-05 → 2025-11-25), the remainder REST-only.
Blocks include the Market Router, Math Engine, Amplum Everyday,
and the Brain Router (MF-17–MF-21). Counts are live-checkable via
`GET /health` and MCP `tools/list`; the repository README carries
the full description and self-verification checks.

## Honest status

- Pass path, tamper path, tier gate, and catalogue invariance were
  independently probed at acceptance (2026-10-23).
- Two items are mid-build and disclosed, not smoothed over:
  **Receipts currently answers REST only** — its MCP tools are in
  build, so step 4 of the evaluation path runs over REST until
  that lands; and **Anchor certificates currently resolve against
  a demonstration registry** — live issuance is in build. The
  certificate format and verification behavior are as described;
  what changes when the builds land is where the record lives,
  not what a judge can check.
