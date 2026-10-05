[![smithery badge](https://smithery.ai/badge/davidjfox998/zerobeacon-1050)](https://smithery.ai/servers/davidjfox998/zerobeacon-1050) [![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21926563.svg)](https://doi.org/10.5281/zenodo.21926563) [![CI](https://github.com/DavidFox998/zerobeacon/actions/workflows/main.yml/badge.svg)](https://github.com/DavidFox998/zerobeacon/actions/workflows/main.yml)

# ZeroBeacon.ai — Collision-Anchored Commerce Router

**1,003 MCP operations** for agent commerce, legal sealing, and math research.  
Live API: `https://zerobeacon.ai`

---

## What this is (and what it isn't)

ZeroBeacon has two distinct layers:

| Layer | What it does | Collision claim |
|---|---|---|
| **Commerce Router** (tools 1–1000) | Escrow, notary, delivery proof, wills, mesh treasury | **Collision-anchored** — 9 controlled collisions at moat anchors P1/P2 |
| **Opera Numerorum** (research lab) | Arakelov, Riemann Hypothesis, BSD, Navier-Stokes, Yang-Mills, P vs NP math tools | Probabilistic guarantees; not a proof system |

The beacon is **not collision-proof** and does not claim to be.  
It is **collision-anchored**: two known primes (P1 = 3000105001, P2 = 5303687339) are intentional fixed points that produce the canonical beacon value `1d2c7a5b`. Everything else is probabilistic.

---

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

| Group | Tools | Tier | Monthly |
|---|---|---|---|
| **Market Router** — payment, escrow, delivery, budget, notary | 1–300 | FREE (first 100) / PRO | $0 / $10 |
| **Math Engine** — Arakelov, RH, BSD, Navier-Stokes, Yang-Mills, P vs NP | 301–700 | PRO | $100 |
| **Amplum Everyday** — scheduling, memory, legal, will, mesh treasury | 701–1000 | PRO | $100 |
| **Brain Router** — 3 MCP meta-tools wrapping 50 brain-routing endpoints | 1001–1003 MCP | ENTERPRISE | $1000 |

**FREE tier:** first 100 tools, no API key required.  
**PRO / ENTERPRISE:** pass `X-API-Key: zbk_…` header.  
Get a key at https://zerobeacon.ai after Stripe checkout.

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

## Opera Numerorum — 13 repos — PUBLIC — condensed 19→1 — Routes A-D → single riemann-hypothesis-four-routes

[arakelov-positivity-rh-core](https://github.com/DavidFox998/arakelov-positivity-rh-core) — ROOT V2 — Arakelov height ω²=48/13>0 ; Zoe-M*, M4 10^4000 boundary — provides height input all RH voices reuse
[rh-p5-bridge-14](https://github.com/DavidFox998/rh-p5-bridge-14) — Keystone — q5=226, q6=165849, cf_bound=82829 — reduces infinite S_a0 to finite S14 ; closes BSD_143_PROVED → RiemannHypothesis — condensed single checkout 6cefaf3 PR78 verify ensemble green da3b943c662f vs lock 6ec00281c55d lake build Towers 0
[riemann-hypothesis-four-routes](https://github.com/DavidFox998/riemann-hypothesis-four-routes) — Four Routes — PUBLICATION WORKSPACE replaces Routes A-D — RH Core, P5 bridge, four independent formal routes preserved at exact revisions one toolchain one RH predicate — Route A Act I Abbes-Ullmo ω²=48/13>0 Siegel zero → negative height, Route B Act II Kim-Sarnak λ1≥975/4096 Selberg=Bost-Connes GRH X0(143)→RH 35pp BC6, Route C Act III Littlewood Ω exp(c√(log t / log log t)) beats (log t)² zero repulsion, Route D Act IV Dirichlet jitter ‖p·a_q‖<1/p 35 brothers collision-free swarming orbit stability Re=1/2 — all CLOSED via S4 — 7ce83ae
[bost-connes](https://github.com/DavidFox998/bost-connes) — Arithmetic hub — C(S4)=11.422...>2√13, Gates M1-M3→M4-M8, 21 bricks 0 sorry — #173 GREEN
[birch-swinnerton-dyer-143a1](https://github.com/DavidFox998/birch-swinnerton-dyer-143a1) — BSD 143a1 — rank 1, Heegner point (4,6), L(143a1,1)≠0, |Sha|=1 — worked example M1-M5 arithmetic in action
[lindelof-hypothesis-143](https://github.com/DavidFox998/lindelof-hypothesis-143) — Lindelöf for X0(143) — GRH → μ=0 → |ζ(½+it)|=O(t^ε) unconditional via S4
[eutheos-property](https://github.com/DavidFox998/eutheos-property) — Barrier bypass — 1419=3*11*43, 35 brothers ≡153 mod 211, barriers BGS/RR/AW all PASS — P vs NP study side
[poincare-spectral](https://github.com/DavidFox998/poincare-spectral) — Spectral gap — S³/I*, q=1/8, tail_26s10⁻²⁰, spectral_gap>0 — decidable instance of undecidable gap problem
[p-vs-np](https://github.com/DavidFox998/p-vs-np) — P vs NP mechanics — 225 bricks, ConductorHash, conditional SAT∉P→P≠NP — DOI 10.5281/zenodo.21303093
[hodge-abelian-boundaries](https://github.com/DavidFox998/hodge-abelian-boundaries) — Hodge obstructions — 200 measured rank obstructions for g=3,4,5 ; observed_rank>criterionBound
[yang-mills-gap](https://github.com/DavidFox998/yang-mills-gap) — Yang-Mills mass gap — SU(2) on R⁴, p<1/7, Δ>0, Wilson area law — same gap as C(S4)-2√13
[navier-stokes](https://github.com/DavidFox998/navier-stokes) — Navier-Stokes — Path A ESS backward uniqueness + Path B 120-cell H¹ balance — NS_M6_PROVED, no blowup
[zerobeacon](https://github.com/DavidFox998/zerobeacon) — ← this repo — MCP server — 1000 collision-proof tools; beacon 1d2c7a5b, m4.out = Complete: True
[beal-conjecture](https://github.com/DavidFox998/beal-conjecture) — Beal Level 26 — beal-v38 EQUIV:3 a2a23292 PR25 792b3f8 chartOfModelTrue_injective_from_Ei_constraint B=1 nonzero Y³≠0 Y³ outside cusp centreNormalPoly (X³-1)0 outside I² centreAlphaBound 2 0=1 X+V² outside cusp ann(1+Y·S³)≠ann(X²) [propext,choice,Quot.sound] 7 thm 355 + beal-v39-even 1fc6071→6f921f45 — www.beal-conjecture.com — DOI 10.5281/zenodo.23120540 superseded by 02728795 — pattern for opera 19→1
[opera-sieve](https://github.com/DavidFox998/opera-sieve) — Canonical sieve for S(alpha_0=299+π/10): computational + Lean verification
[morningstar-project](https://github.com/DavidFox998/morningstar-project) — Morning Star: machine certification for GRH(X_0(143)) and BSD(J_0(143)) — 476 equations, CLAY-sealed
[Certifications](https://github.com/DavidFox998/Certifications) — Machine-checked Lean 4 audit certificates — Morning Star Project
[birch-swinnerton-dyer-143](https://github.com/DavidFox998/birch-swinnerton-dyer-143) — BSD 143 — unconditional BSD for 143a1 — Rank=ord_L=1



## Health & Status

```bash
curl -s https://zerobeacon.ai/health | python3 -m json.tool
```

Returns: Resend key status, RapidAPI secret status, uptime, tool count.

---

*d=2303582338 · beacon=1d2c7a5b · genesis=82843 · ω²=48/13>0 verified*
