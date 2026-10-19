# ZeroBeacon — Operations Handover

*Reconstructed 2026-10-18 by Talitha from the production codebase, the
October 6 tool scan, and the founder's architecture notes. The original
builder agent is gone and took its context with it; this document is the
replacement for that lost knowledge. Keep it current — an agent that
cannot read this file should not deploy.*

## What ZeroBeacon is

A collision-anchored commerce router for AI agents: 1,000+ MCP/REST tools
in 21 blocks, sold in tiers, live at https://zerobeacon.ai. Tool names
repeat across blocks by design; the block-anchored address
`/api/{block}/{nn}/{tool}` disambiguates them and selects the tier/variant.
October 2026 measurement: 1,003 callable paths, 699 distinct names, 296
colliding names, 9 controlled collisions at the core.

## Production topology (Fly.io, org `david-fox-287`)

- **App `zerobeacon-mf-1000`** — production. Region sjc, one machine,
  Dockerfile build, internal port 8080, autostop off, min 1 running.
- **Volume `zerobeacon_data`** (1 GB, sjc) mounted at `/app/data`. Holds
  `api_keys.json`, `api_sessions.json`, `resend_attempts.json`. This
  volume is the business: customer keys live here, not in the image.
- **App `zerobeacon`** — LEGACY v49.1 (51 tools), no volume. Its machine
  was stopped 2026-10-18 as part of the takeover cleanup. Do not revive
  it without a reason; do not destroy it without the founder's word.
- Fly.io operations run through the `custom.flyio` connector (org token;
  the founder holds it in Muse's secure storage). Inventory commands:
  `flyapi.py verify | machines <app> | volumes <app> | stop <app> <id>`.

## Repository map (github.com/DavidFox998/zerobeacon, branch main)

- `zerobeacon_mf_1000_main.py` — the production app (~2,440 lines):
  FastAPI factory, tier gating, MCP Streamable HTTP at `POST /mcp`,
  landing page template (starts ~line 708), Stripe webhook, admin.
  `zerobeacon_mf_300_main.py` / `_600_` are the smaller tier variants.
- `routers/` — one router per 50-tool slice, named
  `zerobeacon_mf_{NN}_050_{block}_{family}.py` (trust, billing, commerce,
  sovereign, will, mesh, sieve, boring, amplum, arakelov, ...).
  `generate_routers.py` generates them.
- `core/` — `beacon.py` (the address generator), `keystore.py` (key
  issuance/lookup against the volume), `auth.py`, `tier_guard.py`,
  `emailer.py` (Resend), `rapidapi_auth.py`, `log_redactor.py`.
- Config/specs: `fly.toml`, `Dockerfile`, `openapi-commerce.yaml`,
  `openapi-math.yaml`, `openapi-amplum.yaml`, `smithery.yaml`,
  `smithery.json`, `mcp.json`, `tool_schemas.py`, `MARKETING.md`,
  `rapidapi_guide.md`.
- Tests at repo root (`test_*.py`): tier gates, Stripe webhook, Resend
  email/TTL/persistence, RapidAPI gates, smoke tests. CI runs the
  tier-gate + Stripe subset as the deploy gate.

## Deploy pipeline

- `.github/workflows/main.yml`: push to `main` → tier-gate/Stripe tests →
  deploy to Fly.io. **A push to main is a production deploy.** There is
  no staging. Land changes in reviewed batches, never casually.
- `uptime-check.yml` runs every ~15 minutes against zerobeacon.ai.
- Known gap: GitHub's copy may lag the Replit workspace (unpushed Repl
  changes were never confirmed either way). A Repl zip export is the only
  full reconciliation; until then, treat this repo as production truth
  but flag drift when behavior disagrees with code.

## Secrets (names only — values live in Fly secrets / secure storage)

`STRIPE_WEBHOOK_SECRET`, `RESEND_API_KEY`, `RAPIDAPI_PROXY_SECRET`,
`SHOPIFY_WEBHOOK_SECRET`, `SESSION_SECRET`, `ADMIN_SECRET`,
`ALERT_WEBHOOK_URL`, `EMAIL_FROM`, `FLY_APP_NAME`, plus Resend/RapidAPI
check-interval and test-recipient settings. The landing page shows a
warning banner when `RESEND_API_KEY` is invalid — key-delivery email
depends on it.

## Money flow

- Landing page embeds a Stripe pricing table (js.stripe.com) and PayPal
  links. Tiers: FREE $0 (100 tools, no key), PRO $10/mo (400),
  PRO+ $100/mo (800), ENTERPRISE $1,000 one-time (all).
- Stripe webhook → keystore writes the key to the volume → Resend emails
  the key to the buyer. If keys stop arriving: check the webhook secret,
  the keystore write, and the Resend key, in that order.
- RapidAPI distribution authenticates through `RAPIDAPI_PROXY_SECRET`;
  Smithery metadata lives in `smithery.yaml` / `smithery.json`.

### Stripe inventory (read 2026-10-18 via the Stripe connector, account "Entangled Technologies", live)

- **Pricing decision: Option B (founder, 2026-10-18) — all-monthly
  ladder.** PRO $10/mo (400 tools) · PRO+ $100/mo (800) · ENTERPRISE
  $199/mo (all 1,052). The old $1,000 one-time enterprise tier is
  retired; the internal tier key `enterprise_1000` is unchanged (it is
  an identifier baked into issued keys, not a price).
- New Stripe catalog: products "ZeroBeacon PRO" / "ZeroBeacon PRO+" /
  "ZeroBeacon ENTERPRISE" with monthly prices $10 / $100 / $199 and
  per-tier payment links (buy.stripe.com), redirecting to
  /success?session_id={CHECKOUT_SESSION_ID}. Products carry tax code
  txcd_10102000 (Managed Payments requires it — link creation fails
  without it).
- The pre-B catalog was an older "Amplum" lineup (Positivity $9/mo,
  Pro $49/mo, Enterprise $199/mo) referenced by the landing page's
  embedded pricing table and by a generic payment link embedded in
  every tool description. The landing now links the three payment
  links directly; tool descriptions and tier errors point at
  https://zerobeacon.ai/pricing. The Amplum products/prices and the
  old payment link were archived (deactivated) 2026-10-18 after the
  new path verified live. The orphaned Stripe pricing-table object
  still exists in the dashboard but is embedded nowhere.
- **Webhook amount mapping was threshold-based and wrong for B:**
  `>= 1000 → enterprise` would have filed a $199 payment as PRO+.
  Fixed to `>= 199 → enterprise_1000` in both handlers
  (checkout.session.completed and customer.subscription.updated),
  with a regression test at exactly $199 (test_stripe_webhook.py 5b).
- Webhook: exactly one endpoint, enabled, at
  `https://zerobeacon-mf-1000.fly.dev/webhook` (the fly.dev hostname,
  not zerobeacon.ai — functional, but note it).
- Subscriptions: 0. Charges: 0. Balance: $0. Stripe has never processed
  a zerobeacon payment; there is no revenue history to disturb.
- Access: Muse's Stripe connector (OAuth sign-in) is the working route.
  The founder also holds an API key; a vault copy was attempted
  2026-10-18 (form was buggy) — the key remains retrievable from his
  dashboard if ever needed. His dashboard 2FA still points at a dead
  phone number; recovery via Stripe support + photo ID is pending him.

## Known issues / cleanup ledger

1. `/llms.txt` — RESOLVED 2026-10-18. The file sits at repo root and a
   `/llms.txt` route in the main app serves it as plain text; verified
   live (HTTP 200) after the CI deploy.
2. Landing page stats row and footer — RESOLVED 2026-10-18. The
   `ω²=48/13` segments were replaced with plain language ("every
   delivery proven"; "collision-anchored addressing — every tool at a
   precise address"). Founder's verdict on the page (2026-10-11):
   "looks like a weird math draft." Note: the OpenAPI app description
   still carries an ω² string — that is machine-facing metadata and was
   left deliberately.
3. Raw-JSON elements — reviewed 2026-10-18, not defects: the homepage
   beacon box is a styled design element, and the `<pre id="out">`
   block is the live output of the Brain demo page, which is supposed
   to show JSON.
4. Legacy app `zerobeacon` (v49.1) — machine stopped 2026-10-18;
   destroy decision pending.
7. Landing page REBUILT 2026-10-18 for the Amazon Build-Ship-Shape
   entry (founder's order): hero carries the video's closing line
   ("the right call is the only callable call"), a contest strip
   (Alexa+ track, GitHub/Smithery/llms.txt links), the collision
   problem in plain English, the demo trio as three steps, a
   copy-paste MCP connect snippet, and an "Honest status" box — the
   product has no customers yet and the page says so; proof offered
   is the live server and the free tier, not testimonials. Sans body
   type replaces the monospace wall (mono kept for code/beacon).
   Also fixed: /upgrade returned 404 while tier-error messages point
   there — it now redirects (307) to the landing page.
5. RapidAPI — RESOLVED 2026-10-18. The stale entropy-era listing
   ("ZeroBeacon", wired to the stopped legacy app) was deleted on the
   founder's exact confirmation and rebuilt the same day as
   "ZeroBeacon.ai" (rapidapi.com/davidjfox998/api/zerobeacon-ai):
   honest description, X-API-Key auth, base URL
   https://api.zerobeacon.ai, plans BASIC $0/100 · PRO $10/10k ·
   ULTRA $100/100k · MEGA $199 (Studio caps "unlimited" at
   7,133,333/mo). RapidAPI caps one API at 450 paths, so the listing
   carries the FREE+PRO spec (427 paths, MF-01-08, incl. the full
   demo trio) = 829 endpoints. The spec's stale info block (1050
   tools, $19/$99 tiers line) was fixed at the source first
   (commit 7ed2baa5). Account sign-in is via Google
   (davidjfox998@gmail.com); a saved email/password is NOT the
   credential. Still open: sibling listings for the PRO+ (427 paths)
   and ENTERPRISE (227) specs — the Brain endpoints ride in every
   spec — and a deprecated duplicate BASIC plan artifact (500k/mo;
   only subscriber is the founder's own account).
8. Proxy-secret pairing (2026-10-18): each Studio project generates
   its OWN X-RapidAPI-Proxy-Secret (view/copy only — it cannot be
   set or regenerated in Studio). Pairing = copy it in Studio and
   set it in Fly (RAPIDAPI_PROXY_SECRET); the value moves by
   clipboard/dashboard only, never through chat or files. The
   server accepts a comma-separated LIST of secrets, one per
   listing (core/rapidapi_auth.py, commit f4d68745; tests in
   test_rapidapi_tier_gate.py::TestProxySecretList). The main
   listing was paired 2026-10-18. Until a listing is paired, its
   paid tiers fail closed; free/BASIC traffic is not secret-gated.
   Note: Fly's dashboard "Deploy Secrets" button was unresponsive
   to automation — a repo CI deploy applies staged secrets too.
6. Stripe reconciled to Option B 2026-10-18 (see Money flow): new
   $10/$100/$199 catalog live, webhook threshold fixed, site and code
   swept, Amplum catalog and old payment link archived.
7. RapidAPI verification state, corrected 2026-10-19: the tier bodies
   carry an `auth_path` field (native / rapidapi_unverified /
   rapidapi_verified). Playground/console calls arrive with NO
   RapidAPI headers (auth_path native) and never exercise the secret
   check; a genuine consumer call through the main gateway host also
   read `native` (2026-10-19) — it carried no X-RapidAPI-Key, which
   `verify_rapidapi_request` requires before it even looks at the
   proxy secret. RapidAPI's gateway does not forward the consumer's
   key to the origin (by design); provenance is the proxy secret
   alone. Confirmation path: tier-error bodies carry a
   `rapidapi_headers_seen` field (header presence flags, never
   values) — re-run a genuine consumer call per listing and read the
   flags; the fix is to drop the
   key-header requirement and verify on the secret + subscription
   headers alone. Server-side fail-closed behavior is proven (forged
   MEGA + wrong/no secret -> rapidapi_unverified, refused).
8. Evaluation (judges') keys, added 2026-10-19: keystore supports
   time-boxed keys (`expires_at` on the record; an expired key
   confers only the free tier). Mint/revoke via POST
   /api/admin/keys/issue and /api/admin/keys/revoke, gated by the
   ADMIN_SECRET env var (fail-closed 403 until the owner sets it in
   Fly). First planned use: one ENTERPRISE key labeled
   amazon-judges@zerobeacon.ai expiring 2027-04-01 (epoch 1806537600),
   distributed only through the contest submission's judges-only
   fields — never in the video, repo, or landing page.

## Operating rules

- Read-first on money surfaces (Stripe, PayPal, RapidAPI billing).
- Destructive actions (listing deletes, app destroys, volume changes)
  only on the founder's exact confirmation.
- Nothing from the founder's private patent notes goes into public copy.
  Public claims must trace to the live site, this repo, or the public
  evidence packet.
- The Amazon Build-Ship-Shape entry (deadline Oct 23, 2026) demos the
  commerce trio `cart_checkout` → `delivery_final_proof` →
  `dispute_resolve`. Keep those endpoints healthy above all else this
  week.

   Addendum (2026-10-19 evening): the headers_seen telemetry shipped
   at 25cc729d, was regressed by the paypal-199 merge (93ccf66c —
   its main file was built from an unfetched origin/main), and was
   repaired at a4cd288e (middleware import + body field, live-verified
   by a direct call). Genuine consumer calls via the Replit agent
   (env: RAPIDAPI_TEST_APP_KEY, FLY_API_TOKEN; no ADMIN var) routed
   correctly on all three listings with correct tier labels.
