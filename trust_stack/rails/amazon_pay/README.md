# Amazon Pay rail adapter — fixture default, sandbox transport live

**Default mode is fixture: "Amazon Pay merchant account not
connected; fixture responses only, no Amazon system was called."**

That sentence is printed in every fixture response this adapter
produces (`"rail_mode": "fixture"`, `"mode_note"`), and fixture
remains the default. The gate wiring, the operation shapes, and the
state machines are real and tested in both modes. The **sandbox**
transport is now live-capable: with the merchant credentials set,
it signs each request with Amazon Pay Signature V2 and sends it to
`pay-api.amazon.com/sandbox`. The **production** (live-money)
transport is not built.

## What is built

- **Gate-first, same contract as PayPal.** `POST
  /api/trust/amazonpay/propose` runs the composed trust flow first.
  A refusal never reaches the rail — the transport records zero
  calls. Only a passing flow (certificate issued) may create a
  checkout session, and `POST /api/trust/amazonpay/capture`
  completes the session, captures the charge, and records the
  payment in Receipts exactly once (a repeat capture answers
  `already_captured` and records nothing new), tagged
  `rail=amazon_pay` with the active `rail_mode` in the
  captured-order record.
- **The Amazon Pay API v2 operations as typed functions**
  (`amazon_pay.py`): Create / Get / Update / Complete Checkout
  Session, Create Charge, Capture Charge, Cancel Charge, Create /
  Get Refund. Idempotency keys are required where Amazon requires
  them (create session, create charge, capture, create refund);
  the client refuses to call without one.
- **A transport seam with two modes.**
  - `fixture` (default): deterministic, locally generated objects
    mimicking the sandbox shapes and state machines —
    CheckoutSession `Open → Completed/Canceled`, Charge
    `Authorized → CaptureInitiated → Completed`, Refund
    `RefundInitiated → Refunded`. Ids derive from the operation and
    idempotency key, so replays return the same object.
  - `sandbox`: signs and sends. Each request is canonicalized per
    Amazon's "Signing requests" doc (Step 1), signed with
    RSASSA-PSS (SHA-256, MGF1-SHA-256, salt length 20 — the EN doc
    value) under the algorithm name `AMZN-PAY-RSASSA-PSS-V2`, and
    sent once over HTTPS to `pay-api.amazon.com/sandbox`.
    Signing is pure stdlib: a minimal DER reader loads the RSA
    private key (PKCS#8 or PKCS#1 PEM) and the PSS padding is
    computed by hand — no new dependencies. It raises
    `NotConfiguredError` unless `AMAZON_PAY_MERCHANT_ID`,
    `AMAZON_PAY_PUBLIC_KEY_ID`, and `AMAZON_PAY_PRIVATE_KEY_PATH`
    are all set **and** the named key file loads as RSA; in either
    failure nothing is sent. Amazon error responses surface as
    `RailError` carrying the HTTP status and Amazon's
    reasonCode/message — never headers or key material.
    `build_request` returns the fully signed request for
    inspection without sending it.

## What is not built (deliberately)

- No production (live-money) mode. `AMAZON_PAY_MODE` values other
  than `sandbox` select the fixture, and no code path in this
  module can reach the live endpoints.
- No buyer-facing checkout UI; a checkout session is an API
  object, not a hosted Amazon Pay button.

## Turning sandbox on

1. The Amazon Pay merchant account exists; the sandbox key pair
   was created in Integration Central and the private key (.pem)
   is held by the owner.
2. Place the private key on the server only, and set
   `AMAZON_PAY_MERCHANT_ID`, `AMAZON_PAY_PUBLIC_KEY_ID`,
   `AMAZON_PAY_PRIVATE_KEY_PATH`, plus `AMAZON_PAY_MODE=sandbox`
   (on Fly, as secrets/volume state — never in the repo).
3. Deploy is a gate decision: this transport landed on a review
   branch first; merging and setting the variables happen only
   after review, and the first live checks are refusal-first
   (a refused gate must still never reach Amazon).
