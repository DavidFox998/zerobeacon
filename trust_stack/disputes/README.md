# Disputes — rail-aware dispute routing and evidence packets

A gated purchase can still go wrong after capture: the goods never
arrive, or they are not what was ordered. When that happens, what a
buyer can actually do depends entirely on the rail the money moved
on — and the rails do not answer alike. This module encodes the
difference and pairs it with the evidence the Receipts chain
already holds.

## What it does

Given a recorded purchase, `route_dispute` returns:

- **The pathway for the rail** — the steps, the channel, and the
  published limits (caps, clocks), encoded as data in
  `disputes.py` with each fact's source noted in comments.
- **An evidence packet** — the purchase's identifiers and amounts,
  the gate verdicts recorded for it, the Receipts chain segment
  from its payment receipt to the head, and a chain verification
  that is *recomputed* from the bundle, never asserted.

Unknown rails are refused in the harness style: `ok: false` and a
refusal naming the failed check (`unknown_rail`).

## The pathways

| Rail | Route | Provider dispute API? |
|---|---|---|
| `paypal` | PayPal dispute → escalation → PayPal decision | Yes — Resolution Center / Disputes API (the catalogue's `pay_dispute` tool operates it) |
| `amazon_pay` | Amazon A-to-z Guarantee claim, buyer-filed | **No API exists.** Cap $2,500 for physical goods; investigation up to 45 business days; card chargeback as fallback |
| `amazon` | Amazon A-to-z Guarantee claim, buyer-filed | **No API exists.** Same terms as Amazon Pay |

For the Amazon rails the prepared packet *is* the deliverable: it
is the evidence a merchant hands to Amazon in Seller Central, or a
buyer attaches to their claim. There is nothing to call.

## The hard limit

**Routing and evidence only.** Nothing in this module files,
submits, opens, or initiates a dispute or claim with any provider,
and nothing here performs network I/O. Filing remains a human act
through the provider's own channel (or, on PayPal, a deliberate
call to the dispute tool by an operator — never by this module).

## Surfaces

- REST: `POST /api/trust/disputes/route` — body `{rail, order_id?,
  capture_id?, charge_id?, session_id?}`; the harness assembles the
  evidence from its persisted captured-order stores and live chain.
- MCP: `dispute_route` on `POST /api/trust/mcp`, same arguments;
  standalone callers may pass a recorded `purchase` dict and a
  receipts `bundle` directly.

Both sit behind the same enterprise API-key gate as the rest of the
trust harness.
