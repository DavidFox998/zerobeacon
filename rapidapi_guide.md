# RapidAPI Listing Guide — ZeroBeacon.ai

This guide walks you through creating and securing the ZeroBeacon.ai listing on RapidAPI Hub.

---

## Step 1 — Sign in to RapidAPI

Go to https://rapidapi.com/provider and sign in (or create) your provider account.

---

## Step 2 — Create a new API

1. Click **"Add New API"**.
2. Fill in the basics:
   - **Name:** `ZeroBeacon.ai`
   - **Short description:** `1000 beacon-anchored tools: market router, math engine (Arakelov/RH/BSD/NS/Yang-Mills), and Amplum everyday tools. Collision-proof commerce router for AI agents.`
   - **Category:** `Tools` (or `Finance` / `Data`)
   - **Visibility:** Public

---

## Step 3 — Import the OpenAPI spec

Choose **"Import OpenAPI"** and paste this URL:

```
https://api.zerobeacon.ai/openapi-rapidapi-all.json
```

This spec covers all 1000 tools, grouped into three tags:
- **Market-Router** — Tools 1–300 (payment routing, escrow, delivery proof, budget, notary)
- **Math-Engine** — Tools 301–700 (Arakelov, RH, BSD, Navier-Stokes, Yang-Mills, P vs NP)
- **Amplum-Everyday** — Tools 701–1000 (scheduling, memory, legal, will, mesh, consciousness proof)

---

## Step 4 — Set the base URL

```
https://api.zerobeacon.ai
```

---

## Step 5 — Configure the Proxy Secret (REQUIRED for paid tiers)

RapidAPI supports a **Proxy Secret** that proves requests are coming from the RapidAPI
gateway rather than being forged directly by users.  ZeroBeacon requires this to be set
before any paid RapidAPI subscriber can access PRO/ULTRA/MEGA tools.

### 5a — Set the secret in the RapidAPI dashboard

1. Go to your API listing → **Security** tab → **Proxy Secret**
2. Enter the value from your Fly.io secret (`RAPIDAPI_PROXY_SECRET`)
3. Save

### 5b — Set the secret in Fly.io

```bash
fly secrets set RAPIDAPI_PROXY_SECRET=<your-secret-value> --app zerobeacon-mf-1000
fly apps restart zerobeacon-mf-1000
```

Both values **must match exactly**.  Until they do, all RapidAPI requests arrive
without a valid proxy secret and are treated as unauthenticated (fail-closed).

**Multiple listings (2026-10-18):** each Studio project generates its OWN secret
— it is view/copy only in Studio (Gateway → Firewall Settings) and cannot be
typed in or regenerated. The server accepts a **comma-separated list** in
`RAPIDAPI_PROXY_SECRET` (one secret per listing, any match validates), so when
adding a listing, APPEND its secret to the Fly value: `secret1,secret2,...`

### 5c — Verify

```bash
curl -s https://zerobeacon.ai/health | python3 -m json.tool | grep rapidapi
```

The `/health` endpoint reports `"rapidapi_proxy_secret":"configured"` when the secret
is set, or `"rapidapi_proxy_secret":"NOT SET — paid subscribers will be blocked"` when it
is missing.

---

## Step 6 — Configure authentication

- **Auth type:** `API Key`
- **Header name:** `X-API-Key`
- The RapidAPI gateway also injects `X-RapidAPI-Key`, `X-RapidAPI-Subscription`, and
  `X-RapidAPI-Proxy-Secret` automatically on every forwarded request.

---

## Step 7 — Configure pricing tiers

The RapidAPI plan names **must** match the values the server reads from
`X-RapidAPI-Subscription`:

| RapidAPI Plan | Header value | ZeroBeacon tier    | Tools unlocked |
|---------------|-------------|--------------------|----------------|
| BASIC         | `BASIC`     | free               | 100 (no key)   |
| PRO           | `PRO`       | pro_10 ($10/mo)    | 400            |
| ULTRA         | `ULTRA`     | pro_100 ($100/mo)  | 800            |
| MEGA          | `MEGA`      | enterprise_1000    | All 1052       |

Recommended pricing to set in the RapidAPI dashboard:

| Plan  | Price     | Requests/mo |
|-------|-----------|-------------|
| BASIC | $0        | 100         |
| PRO   | $10/mo    | 10 000      |
| ULTRA | $100/mo   | 100 000     |
| MEGA  | $199/mo   | 7 133 333 (Studio caps "unlimited" at this price) |

---

## Step 8 — Write the listing description

```
ZeroBeacon.ai — 1052 beacon-anchored tools for AI agents.

• Market Router (tools 1–300): payment routing, escrow, delivery proof, budget, notary
• Math Engine (tools 301–700): Arakelov geometry, Riemann Hypothesis, BSD conjecture,
  Navier-Stokes, Yang-Mills, P vs NP
• Amplum Everyday (tools 701–1000): scheduling, memory, legal, will creation,
  mesh treasury, consciousness proof

BASIC: first 100 tools, no key required.
PRO ($10/mo): 400 tools. ULTRA ($100/mo): 800 tools. MEGA ($199/mo): all 1052 tools.
RapidAPI gateway handles auth — no separate API key needed for subscribers.

Beacon: 1d2c7a5b · d=2303582338
```

---

## Step 9 — Publish

Click **"Make Public"** and submit for review. (The 2026-10-18 listing went
public immediately, no review wait.)

⚠️ **450-path cap:** RapidAPI rejects any spec with more than 450 distinct
route paths ("environment" maximum). The `-all` spec (1,030 paths) will be
REFUSED on import. Ship one listing per trimmed spec below instead.

---

## How RapidAPI tier access works (server-side)

On every RapidAPI gateway request the server receives three injected headers:

| Header                    | Source      | Purpose                              |
|---------------------------|-------------|--------------------------------------|
| `X-RapidAPI-Key`          | RapidAPI    | Subscriber's gateway key             |
| `X-RapidAPI-Subscription` | RapidAPI    | Plan name (BASIC/PRO/ULTRA/MEGA)     |
| `X-RapidAPI-Proxy-Secret` | RapidAPI    | Proves request came from the gateway |

Auth is handled in `core/rapidapi_auth.py`:

1. If `X-RapidAPI-Key` is absent → not a RapidAPI request, fall through to zbk_ key check.
2. If `RAPIDAPI_PROXY_SECRET` env var is not set → fail closed (503/403).
3. Constant-time compare `X-RapidAPI-Proxy-Secret` against `RAPIDAPI_PROXY_SECRET`.
   - Mismatch → reject (forged or gateway-bypassed request).
4. Verified → map `X-RapidAPI-Subscription` to ZeroBeacon tier and grant access.

Direct zbk_ key holders and Smithery gateway subscribers are unaffected.

---

## Useful spec endpoints

| Endpoint                              | Contents                            |
|---------------------------------------|-------------------------------------|
| `/openapi-rapidapi-all.json`          | All 1000 tools — use for RapidAPI   |
| `/openapi-rapidapi.json`              | FREE + PRO tools (MF-01–08, ~400)   |
| `/openapi-rapidapi-pro-plus.json`     | PRO+ tools (MF-09–16, ~400)         |
| `/openapi-rapidapi-enterprise.json`   | ENTERPRISE tools (MF-17–20, ~200)   |

---

## After publishing

Once the listing is live, update the landing page links and README to include the
RapidAPI marketplace URL so users can discover ZeroBeacon through both channels.
