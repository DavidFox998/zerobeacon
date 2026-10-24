# ZeroBeacon — Meta Key Manual

**Key lane:** Meta Global AI Developer Hackathon judges
**Tier:** ENTERPRISE (enterprise_1000)
**Platform:** https://api.zerobeacon.ai · **Code & technical docs:**
github.com/DavidFox998/zerobeacon

This key unlocks the full ZeroBeacon platform. For this lane the
manual leads with the **Trust Stack harness**: the hosted, live
form of the seven-module Agent Trust Stack submitted to this
hackathon. Five of the seven modules run here as one composed
service; the toolset is covered second.

## Authentication

Every call carries the key as a header:

```
X-API-Key: zbk_…
```

A missing or under-tier key returns `tier_required` naming
`enterprise_1000`. Keys are issued per lane; this key serves this
lane only.

## 1. The trust harness (primary)

The modules, in flow order:

- **Apathy — audit.** Mechanical verification with zero self-trust:
  counts taken two independent ways, parts summed against the
  whole, references checked against the real system. The audit
  engine the rest of the stack measures with.
- **Proven — verify.** Live-probe verification: capabilities are
  measured by calling the system (claimed-vs-measured), never
  taken from an author's description.
- **Anchor — certify.** Collision-anchored certification of
  verified results. Format and verification are public; issuance
  internals stay sealed — the moat is issuance, not verification.
- **Receipts — record.** Signed, chained evidence of what was
  checked and issued; the sequence replays and verifies after the
  fact.
- **Showroom — display.** The public legibility page at
  `GET /trust`, naming the modules and the flow.
- **Flow — the composition** (`POST /api/trust/flow`): proposal in
  → audit + verify → certificate out (pass) or a named refusal
  (fail) → receipt recorded.

Surfaces: REST under `/api/trust/{apathy, proven, anchor,
receipts, flow}`; MCP at `POST /api/trust/mcp` (11 tools); the
harness is hosted **outside** the catalogue's routers, so the
platform's counts are untouched by it (verify: `GET /health`).

### Two modules of the seven are not hosted here — by design

- **Grantline** (scoped, expiring credential grants) is a secrets
  broker. Custodying credentials inside a public, key-gated API
  inverts the security posture the stack exists to create; its
  seed asset already lives in ZeroBeacon's key system itself.
- **Nightwatch** (the human-gated production protocol) is the
  internal factory this team runs — an operating discipline, not a
  callable verification primitive. It is a second-phase
  productization, not part of the client surface.

## 2. The toolset (secondary)

Behind the same key, the full ZeroBeacon catalogue: **1,052
operations across 21 routers**, 1,003 callable over MCP (protocol
revisions 2024-11-05 → 2025-11-25), the remainder REST-only.
Blocks include the Market Router, Math Engine, Amplum Everyday,
and the Brain Router (MF-17–MF-21). Counts are live-checkable via
`GET /health` and MCP `tools/list`; the repository README carries
the full description, the controlled-collision addressing scheme,
and self-verification checks.

## Honest status

- The harness was independently probed at acceptance
  (2026-10-23): pass path issues a certificate with a chained
  receipt; tamper path refuses, naming the failed check;
  catalogue counts identical before and after.
- Both build items are closed. Receipts answers over both REST
  and MCP (closed 2026-10-24) — an MCP-only client can now fetch
  and verify the receipt chain in-protocol. **Anchor live
  issuance is built and deployed (2026-10-24)**: a passing flow
  records its certificate in a persisted live registry signed
  under a server-side key; re-certification is idempotent, never
  duplicated. Production issuance activates when the signing
  secret is provisioned on Fly; until then the service
  self-reports live issuance as unconfigured and resolves against
  the demonstration registry, exactly as before. The keyed judge
  lanes operate meanwhile.
