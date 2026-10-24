[![smithery badge](https://smithery.ai/badge/davidjfox998/zerobeacon-1050)](https://smithery.ai/servers/davidjfox998/zerobeacon-1050) [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21926563.svg)](https://doi.org/10.5281/zenodo.21926563) [![CI](https://github.com/DavidFox998/zerobeacon/actions/workflows/main.yml/badge.svg)](https://github.com/DavidFox998/zerobeacon/actions/workflows/main.yml)

# ZeroBeacon.ai — Collision-Anchored Commerce Router

**1,003 MCP operations** for agent commerce, legal sealing, and math research.  
Live API: `https://zerobeacon.ai`  
MCP endpoint: `POST https://zerobeacon.ai/mcp` — Streamable HTTP; negotiates protocol revisions 2024-11-05 through 2025-11-25.  
Counts, reconciled: `tools/list` returns the 1,003 operations callable over MCP; `/health` reports `tools: 1052, routers: 21` — the full catalogue, including the REST-only endpoint surface. Both figures are live-checkable.

---

## What this is (and what it isn't)

ZeroBeacon has two distinct layers:

| Layer | What it does | Collision claim |
|---|---|---|
| **Commerce Router** (tools 1–1000) | Escrow, notary, delivery proof, wills, mesh treasury | **Collision-anchored** — 9 controlled collisions at moat anchors P1/P2 |
| **Opera Numerorum** (research lab) | Arakelov, Riemann Hypothesis, BSD, Navier-Stokes, Yang-Mills, P vs NP math tools | Probabilistic guarantees; not a proof system |

The beacon is **not collision-proof** and does not claim to be.  
It is **collision-anchored**: two known primes (P1 = 3000105001, P2 = 5303687339) are intentional fixed points that produce the canonical beacon value `1d2c7a5b`. Everything else is probabilistic.

Every tool is registered at its own block-anchored address, `/api/mf/{block}/{tool}` (e.g. `/api/mf/01/beacon`). Tool names repeat across blocks by design; the address, not the name, is a tool's identity.
The addressing is generated from four primes — 11, 13, 17, 19 — and the constant 1419 = 3 × 11 × 43.
The anchoring method descends from the Eutheos programme, a Lean 4–verified witness family maintained in the separate [eutheos-property](https://github.com/DavidFox998/eutheos-property) repo. That verification covers the witness family, not this API.

---

## The Trust Layer

ZeroBeacon's toolset gives agents tools to call. The trust layer
checks the work those calls produce before anyone relies on it.

In plain terms: an agent submits a piece of work and the claims
that come with it. The trust layer re-measures the claims instead
of believing them, checks them live against the real system, and
then either certifies the result or refuses it and names the
check that failed. Every outcome is written to a chained,
tamper-evident record that can be replayed later. Nothing is
certified on the agent's say-so.

It works as four modules and one composed flow:

- **Apathy** — the audit. Counts and claims are re-measured by a
  second, independent method before they are believed.
- **Proven** — the verification. Capabilities are checked by live
  probes against the real system, not taken from a description.
- **Anchor** — the certificate. Work that passed is certified;
  the certificate format and its verification are public.
- **Receipts** — the record. A signed, chained ledger of what was
  checked and what was issued.
- **Flow** — the composition: proposal in, certificate or named
  refusal out, receipt recorded.

Its place in the platform: the harness lives **alongside** the
toolset, not inside it. It is hosted outside the catalogue's
routers, so the 1,052 tools work exactly as before, and it uses
the same API keys and tiers. It is reachable over REST and MCP
under `/api/trust/`, with a public page at `/trust`.

Each judging lane's manual is delivered with its key, not published
here: `GET /api/lane-manual` with the lane's `X-API-Key` returns that
lane's manual, and only that lane's. The key selects the lane, so no
key can read another lane's manual, and access ends when the key
expires. See `key-manuals/README.md`.

Two further architecture implementations from the same programme
are not hosted endpoints, but can augment the architecture of
other systems and further automation:

- **Grantline** — scoped, expiring, revocable credential grants:
  an agent uses a secret without ever possessing it.
- **Nightwatch** — a human-gated automation protocol: work runs
  in verified batches, and a gate a human controls decides what
  proceeds.

Engineering summaries and code for both are available upon
request.

Both items that were mid-build at first publication are now
closed: Receipts answers over both REST and MCP (closed
2026-10-24), and Anchor live issuance is built and deployed
(2026-10-24) — a passing flow records its certificate in a
persisted live registry signed under a server-side key, and a
repeat certification is idempotent rather than duplicated.
Production issuance activates when the signing secret is
provisioned on Fly; until then the service self-reports live
issuance as unconfigured and resolves against the demonstration
registry, exactly as before. The toolset is unaffected.

## The 9 Controlled Collisions

The beacon function `frac(p × π/10 × 2³²)` maps primes to 32-bit hex strings.  
Nine primes are known to collide with the anchor value `1d2c7a5b`:

| # | Prime | Beacon output | Role |
|---|---|---|---|
| 1 | P1 = 3000105001 | `1d2c7a5b` | Moat anchor |
| 2 | P2 = 5303687339 | `1d2c7a5b` | Moat anchor |
| 3–9 | Internal sieve primes | `1d2c7a5b` | Commerce routing anchors |

**Why this is intentional:** The moat design *requires* P1 and P2 to produce the same beacon value. This is what makes beacon verification deterministic. The collision bound for a random attacker is `(9/4M)^35 ≈ 1e-197` — practically zero, but not a mathematical impossibility.

---

## Tool Groups

Catalogue organization only — the four groups span the 21 blocks (MF-01–MF-21) and cross tier boundaries. Access is gated per block, not per group; see Access Tiers.

| Group | Tools | Count |
|---|---|---|
| **Market Router** — payment, escrow, delivery, budget, notary | 1–300 | 300 |
| **Math Engine** — Arakelov, RH, BSD, Navier-Stokes, Yang-Mills, P vs NP | 301–700 | 400 |
| **Amplum Everyday** — scheduling, memory, legal, will, mesh treasury | 701–1000 | 300 |
| **Brain Router** — meta-tools that route the full catalogue: route, chain, think, swarm, consensus | 1001–1052 | 52 |

---

## Access Tiers

Tiers are cumulative. Each tier unlocks whole blocks at `/api/mf/{block}` — the labels below are exactly the gates enforced in code (`ROUTERS` in `zerobeacon_mf_1000_main.py`). Every block appears exactly once, so every tool carries its tier by construction.

| Tier | Monthly | Tools (cumulative) | Blocks unlocked |
|---|---|---|---|
| FREE | $0 | 100 | MF-01–MF-02 |
| PRO | $10 | 400 | + MF-03–MF-08 |
| PRO+ | $100 | 800 | + MF-09–MF-16 |
| ENTERPRISE | $199 | 1,052 | + MF-17–MF-21 |

**FREE tier:** no API key required.  
**Paid tiers:** pass `X-API-Key: zbk_…` header.  
Get a key at https://zerobeacon.ai after Stripe checkout.

MCP note: `tools/list` exposes 1,003 operations — the 1,000 block tools (MF-01–MF-20) plus MF-21's `brain`, `brain/fire`, and `brain/status`. The remaining MF-21 endpoints are REST-only; that difference is the 1,052 catalogue vs 1,003 MCP counts above.

---

## Install from Smithery

Install the current Smithery CLI (Node.js 20+), connect ZeroBeacon, and inspect the live tool catalog:

```bash
npm install -g smithery@latest
smithery auth login
smithery mcp add https://zerobeacon.ai/mcp --id zerobeacon
smithery tool list zerobeacon
```

To add the remote server directly to a supported local client, use:

```bash
smithery mcp add https://zerobeacon.ai/mcp --client claude
# Replace claude with another supported client name when needed.
```

Smithery listing: https://smithery.ai/servers/davidjfox998/zerobeacon-1050

---

## 30-Second Self-Verification

Agents can verify the beacon anchor in one curl call:

```bash
# 1. Verify the moat anchor — P1 must return beacon=1d2c7a5b
curl -s "https://zerobeacon.ai/api/mf/01/beacon?p=3000105001" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['beacon'] == '1d2c7a5b', f'Unexpected beacon: {d[\"beacon\"]}'
assert d['d'] == 2303582338,      f'Unexpected d: {d[\"d\"]}'
print('✓ P1 anchor verified:', d['beacon'])
"

# 2. Verify P2 anchor
curl -s "https://zerobeacon.ai/api/mf/01/beacon?p=5303687339" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['beacon'] == '1d2c7a5b', f'Unexpected beacon: {d[\"beacon\"]}'
print('✓ P2 anchor verified:', d['beacon'])
"

# 3. Verify genesis prime
curl -s "https://zerobeacon.ai/api/mf/01/beacon?p=82843" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print('genesis beacon:', d['beacon'], '— d:', d['d'])
"
```

Expected output:
```
✓ P1 anchor verified: 1d2c7a5b
✓ P2 anchor verified: 1d2c7a5b
genesis beacon: <value> — d: 2303582338
```

---

## Beacon Constants

| Constant | Value | Meaning |
|---|---|---|
| `beacon` | `1d2c7a5b` | Canonical anchor value at P1, P2 |
| `d` | `2303582338` | Discriminant — identifies this beacon chain |
| `genesis` | `82843` | First prime used in the chain |
| `P1` | `3000105001` | Moat anchor 1 |
| `P2` | `5303687339` | Moat anchor 2 |

---

## Authentication

```http
GET /api/mf/01/beacon
X-API-Key: zbk_your_key_here
```

FREE tools (1–100): no key required.  
Paid tools: key issued automatically after Stripe checkout at https://zerobeacon.ai

RapidAPI subscribers: use the RapidAPI gateway — no separate key needed.

---

## Honest Claims

- ✅ The beacon is **collision-anchored** at P1 and P2 (9 known collisions total)
- ✅ The collision bound for random attackers is `(9/4M)^35 ≈ 1e-197`
- ✅ Commerce routing decisions are deterministic given the anchor
---

## Opera Numerorum Research Lab

The Math Engine (tools 301–700) implements numerical experiments related to:

- **Arakelov positivity** — height bounds on arithmetic surfaces
- **Riemann Hypothesis descent** — sieve residue structure mod 211
- **BSD conjecture** — L-function analytic rank witnesses  
- **Navier-Stokes** — energy dissipation heuristics
- **Yang-Mills** — mass gap numerical bounds
- **P vs NP** — GapMCSP gap witness via 35-brother self-symmetry

These are computational tools for researchers, not machine-verified proofs.  
Formal Lean 4 proofs live in the companion repos:  
[eutheos-property](https://github.com/DavidFox998/eutheos-property) · [p5-boundary](https://github.com/DavidFox998/p5-boundary) · [brothers-desert-proof](https://github.com/DavidFox998/brothers-desert-proof)

---

## Health & Status

```bash
curl -s https://zerobeacon.ai/health | python3 -m json.tool
```

Returns: Resend key status, RapidAPI secret status, uptime, tool count.

---

*d=2303582338 · beacon=1d2c7a5b · genesis=82843*
