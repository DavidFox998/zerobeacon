#!/usr/bin/env python3
"""Disputes test suite (standalone).

Covers the library surface only: pathway data per rail, the
unknown-rail refusal, evidence packet assembly against a real
Receipts chain, and the recomputed chain verification failing on a
tampered bundle. The hosted REST/MCP wiring is covered by the repo
pytest file test_disputes_amazonpay.py.

Run:  python3 tests/run_tests.py
Exit: 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[2]

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


disputes = _load("disputes", _ROOT / "trust_stack" / "disputes" / "disputes.py")
disputes_mcp = _load("disputes_mcp",
                     _ROOT / "trust_stack" / "disputes" / "disputes_mcp.py")
receipts = _load("receipts", _ROOT / "trust_stack" / "receipts" / "receipts.py")


def main() -> int:
    chain = receipts.ReceiptChain(receipts.Keypair(os.urandom(32)))
    receipt = chain.append(
        "trust-paypal",
        {"method": "POST", "host": "", "path": "/api/trust/paypal/capture"},
        "paypal_payment_executed")
    bundle = chain.to_bundle()
    purchase = {
        "rail": "paypal", "order_id": "ORDER-1", "capture_id": "CAP-1",
        "amount": "25.00", "currency": "USD", "environment": "sandbox",
        "receipt": receipt,
    }

    routed = disputes.route_dispute("paypal", purchase, bundle)
    check("D1. paypal routes to the API pathway",
          routed["ok"] is True
          and routed["pathway"]["channel"] == "api"
          and routed["pathway"]["provider_dispute_api"] is True)
    packet = routed["evidence_packet"]
    check("D2. packet carries identifiers, verdicts, segment, verification",
          packet["found"] is True
          and packet["purchase"]["identifiers"]["capture_id"] == "CAP-1"
          and packet["gate_verdicts"]["payment_receipt_status"]
          == "paypal_payment_executed"
          and packet["chain_segment"][0]["hash"] == receipt["hash"]
          and packet["chain_verification"]["verifies"] is True)

    for rail in ("amazon_pay", "amazon"):
        routed = disputes.route_dispute(rail, None, bundle)
        limits = routed["pathway"]["limits"]
        check(f"D3. {rail} routes to A-to-z with cap and clock",
              routed["pathway"]["provider_dispute_api"] is False
              and "A-to-z" in routed["pathway"]["route_name"]
              and "$2,500" in limits["coverage_cap"]
              and "45 business days" in limits["investigation_clock"])

    refused = disputes.route_dispute("venmo", purchase, bundle)
    check("D4. unknown rail is refused by name",
          refused["ok"] is False
          and refused["refusal"]["failed_checks"][0]["check"]
          == "unknown_rail")

    tampered = json.loads(json.dumps(bundle))
    tampered["receipts"][0]["result_status"] = "forged"
    routed = disputes.route_dispute("paypal", purchase, tampered)
    check("D5. tampered chain fails the recomputed verification",
          routed["evidence_packet"]["chain_verification"]["verifies"]
          is False)

    result = disputes_mcp.call_tool(
        "dispute_route",
        {"rail": "amazon_pay", "purchase": purchase, "bundle": bundle})
    payload = json.loads(result["content"][0]["text"])
    check("D6. MCP tool mirrors the library result",
          result["isError"] is False and payload["rail"] == "amazon_pay"
          and payload["evidence_packet"]["found"] is True)
    check("D7. tool list is exactly dispute_route",
          disputes_mcp.TOOL_NAMES == ("dispute_route",))

    passed = sum(1 for _, ok in RESULTS if ok)
    print()
    print(f"{passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
