# ZeroBeacon — Amazon Key Manual

**Key lane:** Amazon Build-Ship-Shape judges
**Tier:** ENTERPRISE (full catalogue) · **Expires:** 2027-04-01
**Platform:** https://api.zerobeacon.ai · **Code & technical docs:**
github.com/DavidFox998/zerobeacon

This key was issued for the Amazon Build-Ship-Shape entry (the
ZeroBeacon MCP server). It unlocks the full platform at the
ENTERPRISE tier. This manual leads with the toolset — the entry's
subject — and covers the trust harness second.

## Authentication

Every call carries the key as a header:

```
X-API-Key: zbk_…
```

An under-tier or missing key returns `tier_required` naming the
tier the endpoint needs. Keys are issued per lane; this key serves
this lane only.

## 1. The toolset (primary)

ZeroBeacon is a collision-anchored API platform exposing **1,052
operations across 21 routers** — 1,003 of them callable over MCP,
the remainder REST-only. Counts are live-checkable: `GET /health`
reports `tools: 1052, routers: 21`, and an MCP `tools/list` returns
the 1,003 callable operations. The server negotiates MCP protocol
revisions 2024-11-05 through 2025-11-25.

Router blocks include:

- **Market Router** — market data and routing endpoints
- **Math Engine** — computation endpoints
- **Amplum Everyday** — everyday-utility endpoints
- **Brain Router** — enterprise reasoning endpoints (MF-17–MF-21)

### Connect over MCP

Point any MCP client at the ZeroBeacon MCP endpoint with the
`X-API-Key` header set. `tools/list` enumerates the callable
surface; every operation is a pure function of its arguments —
what a tool computes does not change between callers.

### Connect over REST

The full catalogue, including the REST-only surface, is described
in the repository README and the OpenAPI documents in the repo
root. The README's "30-Second Self-Verification" section gives
three anchor checks any judge can run unaided.

## 2. The trust harness (secondary)

Hosted in the same deployment, outside the catalogue's routers, is
the **Trust Stack harness** — a verification layer for agent work:

- **Apathy** — mechanical audit: counts and claims re-measured by
  independent methods before anything is believed
- **Proven** — live-probe verification of claimed capabilities
- **Anchor** — certification of verified results
- **Receipts** — a chained, tamper-evident record of what was
  checked and issued
- **Flow** — the composed pipeline: proposal in → audit + verify →
  certificate out (pass) or a named refusal (fail) → receipt
  recorded

Surfaces: REST under `/api/trust/{apathy, proven, anchor,
receipts, flow}`, MCP at `POST /api/trust/mcp` (11 tools), and a
public page at `GET /trust`. All harness endpoints sit behind this
key's tier. A well-formed proposal returns a certificate and a
chained receipt; a proposal whose claims do not reproduce returns
a refusal naming the failed check, and no certificate is issued.

## Honest status

- The catalogue and harness figures above are the deployed state,
  verified by independent probes on 2026-10-23 (`/health`
  unchanged before and after the harness deploy).
- One harness item remains mid-build and is disclosed here rather
  than smoothed over: **Anchor certificates currently resolve
  against a demonstration registry** (live issuance is in build).
  Receipts answers over both REST and MCP (closed 2026-10-24).
  Neither affects the toolset, which is complete as described.
