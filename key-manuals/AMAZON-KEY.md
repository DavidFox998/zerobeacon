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

## The Friction Moments

Every developer knows the feeling. Nothing is exactly broken, but
nothing is moving either. A cloud server sits idle somewhere,
burning its little meter. A LoRA comes back malformed. A
credential that exists on one machine is needed on another, and
both machines were designed, quite properly, not to trust anyone.
These are friction moments, and they are not the exception in a
developer's day. They are the day.

Apathy was born in one of those moments. The mathematics behind
this project is written in Lean, and Lean has an honest little
word for an unfinished proof: `sorry` — a placeholder that says
*the gap is here*. The trouble is that the word also lives
everywhere else. It appears in comments, in file headers, in the
ordinary prose around the proofs — anywhere a human wrote *about*
a gap rather than leaving one. Read naively, a healthy codebase
looks riddled with holes. Read carefully, almost none of them are
real.

So Apathy learned to read carefully. It ignores every mention of
"sorry" that is not a sorry — the comments, the headers, the
discussion — and zeroes in on the true sorries: the genuine
mathematical gaps, the places where a proof actually stops. That
discrimination, small as it sounds, is the difference between an
audit you can trust and an alarm that cries all day.

But the largest share of friction has a different source:
secrets. Moving a secret from where it lives to where it is
needed — and keeping it a secret the whole way, across machines
and systems built security-first — is where the hours go. Our
answer to that one is Grantline, and it is in beta testing now.

Step back, and the project's whole friction story is really a
story about overcoming paradigm. Two paradigms, specifically.

The first: in any connected device, collision is the biggest
problem facing us today. Addresses collide. Names collide.
Intentions collide. The second: the world's capital economy is
built on scarcity — on the assumption that the valuable thing is
the rare thing. To move faster through the friction of creating,
we built the opposite assumption into this system: an economy
based on abundance.

A note for engineers, because you will run into the word: this
structure has a name, and the name is Amplum. Mentions of Amplum
linger in the older corners of the project like a ghost — a
retired listing here, an archived product there — and they are
not a different thing. Amplum is this: an economy built not on
scarcity but on abundance. ZeroBeacon was created on Amplum,
deliberately, to strategically address friction moments. And
its positivity is not a mood. It is true positivity, and
measured — counted, checked, and verified the way everything in
this system is.

Because collisions, it turns out, are abundant. There is no
shortage of them, and no stopping them. So the question changes.
Not *how do we avoid collisions?* but *how do collisions become
an asset?* And then the sharper question underneath it: *which
collisions are unavoidable?*

Those — the unavoidable ones — are the beacon. They are the
anchor. A BIOS, if you will: the small, reliable layer everything
else boots from, the first thing that loads because it is the
thing that cannot not be there. There are very few of them.
Fewer than ten — nine, by this system's count — collisions that
are predicted, anticipated, and reliable. Those are the anchors,
and they are what enables everything built on top.

Friction does not disappear. It gets classified. The false
signals are ignored — that is Apathy. The secrets are brokered
instead of dragged from machine to machine — that is Grantline,
in beta. And the handful of collisions that cannot be avoided
are not fought at all. They are made load-bearing.

## Honest status

- The catalogue and harness figures above are the deployed state,
  verified by independent probes on 2026-10-23 (`/health`
  unchanged before and after the harness deploy).
- One harness item remains mid-build and is disclosed here rather
  than smoothed over: **Anchor certificates currently resolve
  against a demonstration registry** (live issuance is in build).
  Receipts answers over both REST and MCP (closed 2026-10-24).
  Neither affects the toolset, which is complete as described.
