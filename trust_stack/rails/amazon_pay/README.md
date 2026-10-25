# Amazon Pay rail adapter — STAGED, fixture mode

**Amazon Pay merchant account not connected; fixture responses
only, no Amazon system was called.**

That sentence is the state of this adapter, and it is printed in
every response it produces (`"rail_mode": "fixture"`, `"mode_note"`).
This is a staged build: the gate wiring, the operation shapes, and
the state machines are real and tested; the merchant account is not
connected, and no code path in this module talks to Amazon.

## What is built

- **Gate-first, same contract as PayPal.** `POST
  /api/trust/amazonpay/propose` runs the composed trust flow first.
  A refusal never reaches the rail — the transport records zero
  calls. Only a passing flow (certificate issued) may create a
  checkout session, and `POST /api/trust/amazonpay/capture`
  completes the session, captures the charge, and records the
  payment in Receipts exactly once (a repeat capture answers
  `already_captured` and records nothing new), tagged
  `rail=amazon_pay`, `mode=fixture` in the captured-order record.
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
  - `sandbox`: builds the signed-request-ready call for
    `pay-api.amazon.com/sandbox` (`build_request` returns it for
    inspection) but raises `NotConfiguredError` unless
    `AMAZON_PAY_MERCHANT_ID`, `AMAZON_PAY_PUBLIC_KEY_ID`, and
    `AMAZON_PAY_PRIVATE_KEY_PATH` are all set — and, in this staged
    build, also when they are set: the live transport stays
    disabled until the merchant account exists and a human enables
    it. No call is ever sent to Amazon by this module as shipped.

## What is not built (deliberately)

- No merchant account, no credentials, no signing, no network
  calls. Sandbox mode is request *construction*, not execution.
- No buyer-facing checkout UI; the fixture session is an API
  object, not a hosted Amazon Pay button.

## Turning it live, later

1. Create the Amazon Pay merchant account; register the public key
   and keep the private key on the server only.
2. Set the three environment variables above plus
   `AMAZON_PAY_MODE=sandbox`.
3. Replace the staged `SandboxTransport.request` refusal with the
   signed send, behind a fresh review — that change is a gate
   decision, not a config flip.
