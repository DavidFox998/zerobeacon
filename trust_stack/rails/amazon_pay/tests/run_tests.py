#!/usr/bin/env python3
"""Amazon Pay adapter test suite (standalone).

Covers the library surface only: fixture state machines, idempotent
replays, idempotency-key enforcement, the fixture labelling on every
response, and the sandbox transport raising NotConfiguredError —
without credentials, and (in this staged build) with them. The
hosted gate wiring is covered by the repo pytest file
test_disputes_amazonpay.py. No network I/O anywhere in this suite.

Run:  python3 tests/run_tests.py
Exit: 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[3]

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


amazon_pay = _load(
    "amazon_pay",
    _ROOT / "trust_stack" / "rails" / "amazon_pay" / "amazon_pay.py")


def main() -> int:
    client = amazon_pay.AmazonPayClient(amazon_pay.FixtureTransport())

    session = client.create_checkout_session(
        {"chargeAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="s1")
    check("A1. fixture session opens, labelled",
          session["statusDetails"]["state"] == "Open"
          and session["rail_mode"] == "fixture"
          and session["mode_note"] == amazon_pay.FIXTURE_NOTE)
    replay = client.create_checkout_session(
        {"chargeAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="s1")
    check("A2. create with the same idempotency key replays",
          replay["checkoutSessionId"] == session["checkoutSessionId"])

    completed = client.complete_checkout_session(
        session["checkoutSessionId"], {}, idempotency_key="s1-complete")
    check("A3. complete moves Open -> Completed and authorizes a charge",
          completed["statusDetails"]["state"] == "Completed"
          and completed["chargeId"] in client.transport.charges)
    captured = client.capture_charge(
        completed["chargeId"], {}, idempotency_key="s1-capture")
    check("A4. capture walks Authorized -> CaptureInitiated -> Completed",
          captured["state_history"] == ["Authorized", "CaptureInitiated",
                                         "Completed"])
    refund = client.create_refund(
        {"chargeId": completed["chargeId"],
         "refundAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="s1-refund")
    check("A5. refund on a completed charge settles",
          refund["statusDetails"]["state"] == "Refunded"
          and client.get_refund(refund["refundId"])["refundId"]
          == refund["refundId"])

    try:
        client.create_charge({"chargePermissionId": "p"}, None)
        check("A6. idempotency key is enforced", False)
    except amazon_pay.RailError:
        check("A6. idempotency key is enforced", True)

    sandbox = amazon_pay.SandboxTransport({})
    try:
        sandbox.request("create_checkout_session", {}, {}, "k")
        check("A7. sandbox without credentials raises NotConfigured", False)
    except amazon_pay.NotConfiguredError as exc:
        check("A7. sandbox without credentials raises NotConfigured",
              "no call was sent to Amazon" in str(exc))
    full_env = {name: "x" for name in amazon_pay.SANDBOX_ENV_VARS}
    staged = amazon_pay.SandboxTransport(full_env)
    try:
        staged.request("create_checkout_session", {}, {}, "k")
        check("A8. staged sandbox sends nothing even with credentials",
              False)
    except amazon_pay.NotConfiguredError as exc:
        check("A8. staged sandbox sends nothing even with credentials",
              "disabled in this staged build" in str(exc))
    built = staged.build_request("capture_charge", {"charge_id": "c1"},
                                 {}, "idem-1")
    check("A9. build_request shapes the sandbox call",
          built["method"] == "POST"
          and built["url"] == "https://pay-api.amazon.com/sandbox"
                               "/v2/charges/c1/capture"
          and built["headers"]["x-amz-pay-idempotency-key"] == "idem-1")

    passed = sum(1 for _, ok in RESULTS if ok)
    print()
    print(f"{passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
