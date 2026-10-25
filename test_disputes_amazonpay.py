"""Dispute routing and the staged Amazon Pay rail.

Disputes: given a recorded gated purchase, the route for its rail
plus an evidence packet whose chain verification is recomputed, not
asserted. PayPal routes to its dispute API surface; Amazon Pay
routes to the buyer-filed A-to-z Guarantee (no provider API exists)
with its $2,500 cap and 45-business-day clock in the pathway data;
an unknown rail is refused by name. Nothing here files anything.

Amazon Pay rail: staged in fixture mode. A passing gate creates a
deterministic fixture checkout session; a refused gate never calls
the transport; capture records the payment in Receipts exactly
once, tagged rail=amazon_pay, mode=fixture. Sandbox mode without
merchant credentials answers NotConfigured.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import core.keystore as keystore
import routers.trust_harness as harness
from zerobeacon_mf_1000_main import app

client = TestClient(app, raise_server_exceptions=True)

_KEY_PATH = Path("/tmp/test_disputes_amazonpay_api_keys.json")
_SESSION_PATH = Path("/tmp/test_disputes_amazonpay_sessions.json")
_RESEND_PATH = Path("/tmp/test_disputes_amazonpay_resend.json")

_SECRET = "disputes-test-admin-secret"
_LABEL = "judges@test.local"
_PASS_MANIFEST = {
    "sorry_count": 0,
    "admit_count": 0,
    "axiom_count": 0,
    "file_count": 1,
}
_TAMPER_MANIFEST = {
    "sorry_count": 3,
    "admit_count": 0,
    "axiom_count": 0,
    "file_count": 1,
}
_PASS_LEAN = "theorem t : True := True\n"
_FIXTURE_NOTE = (
    "Amazon Pay merchant account not connected; fixture responses "
    "only, no Amazon system was called."
)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    saved_paths = (keystore.KEY_PATH, keystore.SESSION_PATH, keystore.RESEND_PATH)
    saved_store = keystore._store
    saved_sessions = keystore._session_map
    saved_resend = keystore._resend_store
    keystore.KEY_PATH = _KEY_PATH
    keystore.SESSION_PATH = _SESSION_PATH
    keystore.RESEND_PATH = _RESEND_PATH
    keystore._store = {}
    keystore._session_map = {}
    keystore._resend_store = {}
    for path in (_KEY_PATH, _SESSION_PATH, _RESEND_PATH):
        path.unlink(missing_ok=True)
    harness._CHAIN = None
    harness._BUNDLE_PATH = None
    harness._AMAZON_CLIENT = None
    harness._AMAZON_PENDING.clear()
    monkeypatch.setenv("TRUST_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANCHOR_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PAYPAL_ENV", raising=False)
    monkeypatch.delenv("AMAZON_PAY_MODE", raising=False)
    monkeypatch.delenv("AMAZON_PAY_MERCHANT_ID", raising=False)
    monkeypatch.delenv("AMAZON_PAY_PUBLIC_KEY_ID", raising=False)
    monkeypatch.delenv("AMAZON_PAY_PRIVATE_KEY_PATH", raising=False)
    yield
    harness._CHAIN = None
    harness._BUNDLE_PATH = None
    harness._AMAZON_CLIENT = None
    harness._AMAZON_PENDING.clear()
    for path in (_KEY_PATH, _SESSION_PATH, _RESEND_PATH):
        path.unlink(missing_ok=True)
    keystore.KEY_PATH, keystore.SESSION_PATH, keystore.RESEND_PATH = saved_paths
    keystore._store = saved_store
    keystore._session_map = saved_sessions
    keystore._resend_store = saved_resend


def _issue(tier: str = "enterprise_1000") -> str:
    with patch.dict(os.environ, {"ADMIN_SECRET": _SECRET}):
        response = client.post("/api/admin/keys/issue", json={
            "admin_secret": _SECRET,
            "tier": tier,
            "email": _LABEL,
        })
    assert response.status_code == 200, response.text
    return response.json()["key"]


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


def _proposal(root: Path, manifest: dict) -> dict:
    (root / "Sample.lean").write_text(_PASS_LEAN, encoding="utf-8")
    return {
        "repo": str(root),
        "manifest": manifest,
        "server": "zerobeacon",
        "address": "/api/mf/03/cart_checkout",
    }


class FakePayPal:
    """Records every call; answers like the sandbox Orders v2 API."""

    def __init__(self):
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url})
        if url.endswith("/v1/oauth2/token"):
            return 200, {"access_token": "TEST-TOKEN", "token_type": "Bearer"}
        if url.endswith("/v2/checkout/orders"):
            return 201, {
                "id": "ORDER-123",
                "status": "CREATED",
                "links": [
                    {"rel": "approve",
                     "href": "https://sandbox.example/approve/ORDER-123"},
                ],
            }
        if url.endswith("/v2/checkout/orders/ORDER-123/capture"):
            return 201, {
                "id": "ORDER-123",
                "status": "COMPLETED",
                "purchase_units": [{
                    "payments": {"captures": [{
                        "id": "CAP-9",
                        "amount": {"value": "25.00", "currency_code": "USD"},
                    }]},
                }],
            }
        return 404, {"name": "RESOURCE_NOT_FOUND"}


def _paypal_purchase(tmp_path, monkeypatch):
    fake = FakePayPal()
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid-test")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "csecret-test")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(harness, "_paypal_http", fake)
    key = _issue()
    headers = _headers(key)
    proposed = client.post("/api/trust/paypal/propose", headers=headers, json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert proposed.json()["gate"] == "passed", proposed.text
    captured = client.post("/api/trust/paypal/capture", headers=headers, json={
        "paypal_order_id": "ORDER-123",
    })
    assert captured.json()["recorded"] is True, captured.text
    return headers


def _amazon_purchase(tmp_path):
    key = _issue()
    headers = _headers(key)
    proposed = client.post("/api/trust/amazonpay/propose", headers=headers, json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    body = proposed.json()
    assert body["gate"] == "passed", proposed.text
    session_id = body["amazonpay"]["checkoutSessionId"]
    captured = client.post("/api/trust/amazonpay/capture", headers=headers,
                           json={"session_id": session_id})
    assert captured.json()["recorded"] is True, captured.text
    return headers, session_id, captured.json()


# -- auth ----------------------------------------------------------------

def test_new_endpoints_require_enterprise_key(tmp_path):
    response = client.post("/api/trust/disputes/route", json={"rail": "paypal"})
    assert response.status_code == 403
    assert response.json()["error"] == "tier_required"
    response = client.post("/api/trust/amazonpay/propose", json={})
    assert response.status_code == 403
    assert response.json()["error"] == "tier_required"
    response = client.get("/api/trust/amazonpay/status")
    assert response.status_code == 403


# -- dispute routing -----------------------------------------------------

def test_dispute_route_paypal_api_route_and_evidence(tmp_path, monkeypatch):
    headers = _paypal_purchase(tmp_path, monkeypatch)
    response = client.post("/api/trust/disputes/route", headers=headers, json={
        "rail": "paypal", "order_id": "ORDER-123",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["rail"] == "paypal"
    pathway = body["pathway"]
    assert pathway["channel"] == "api"
    assert pathway["provider_dispute_api"] is True
    packet = body["evidence_packet"]
    assert packet["found"] is True
    assert packet["purchase"]["identifiers"] == {
        "order_id": "ORDER-123", "capture_id": "CAP-9"}
    assert packet["purchase"]["amount"] == "25.00"
    assert packet["purchase"]["currency"] == "USD"
    assert packet["gate_verdicts"]["payment_receipt_status"] == \
        "paypal_payment_executed"
    assert packet["chain_verification"]["verifies"] is True
    segment = packet["chain_segment"]
    assert segment and segment[0]["result_status"] == \
        "paypal_payment_executed"
    assert "filed" in body["scope"]


def test_dispute_route_amazon_pay_atoz_and_packet(tmp_path):
    headers, session_id, captured = _amazon_purchase(tmp_path)
    response = client.post("/api/trust/disputes/route", headers=headers, json={
        "rail": "amazon_pay", "session_id": session_id,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    pathway = body["pathway"]
    assert pathway["channel"] == "buyer_filed_claim"
    assert pathway["provider_dispute_api"] is False
    assert "A-to-z" in pathway["route_name"]
    assert "$2,500" in pathway["limits"]["coverage_cap"]
    assert "45 business days" in pathway["limits"]["investigation_clock"]
    packet = body["evidence_packet"]
    assert packet["found"] is True
    identifiers = packet["purchase"]["identifiers"]
    assert identifiers["session_id"] == session_id
    assert identifiers["charge_id"] == captured["amazonpay"]["charge_id"]
    assert packet["purchase"]["rail_mode"] == "fixture"
    assert packet["gate_verdicts"]["certificate"]["verdict"] == "ISSUED"
    assert packet["gate_verdicts"]["payment_receipt_status"] == \
        "amazonpay_payment_executed"
    assert packet["chain_verification"]["verifies"] is True
    assert packet["chain_segment"][0]["result_status"] == \
        "amazonpay_payment_executed"


def test_dispute_route_unknown_rail_refused(tmp_path):
    key = _issue()
    headers = _headers(key)
    response = client.post("/api/trust/disputes/route", headers=headers,
                           json={"rail": "venmo"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    failed = body["refusal"]["failed_checks"]
    assert failed[0]["check"] == "unknown_rail"
    assert "paypal" in failed[0]["detail"]

    mcp = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "dispute_route", "arguments": {"rail": "venmo"}},
    })
    result = mcp.json()["result"]
    assert result["isError"] is True
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is False
    assert payload["refusal"]["failed_checks"][0]["check"] == "unknown_rail"


def test_dispute_route_over_mcp_matches_rest(tmp_path):
    headers, session_id, _captured = _amazon_purchase(tmp_path)
    mcp = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "dispute_route",
                   "arguments": {"rail": "amazon_pay",
                                 "session_id": session_id}},
    })
    result = mcp.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["rail"] == "amazon_pay"
    assert payload["evidence_packet"]["found"] is True


def test_evidence_chain_verification_failure_is_reported(tmp_path):
    _headers_key, session_id, _captured = _amazon_purchase(tmp_path)
    bundle = harness._chain().to_bundle()
    assert harness.receipts.verify_bundle(bundle)[0] is True
    bundle["receipts"][0]["result_status"] = "forged"
    result = harness.disputes.route_dispute(
        "amazon_pay",
        {"rail": "amazon_pay", "session_id": session_id,
         "receipt": _captured["receipt"]},
        bundle,
    )
    assert result["ok"] is True  # the pathway is static; the packet reports
    verification = result["evidence_packet"]["chain_verification"]
    assert verification["verifies"] is False

    standalone = harness.disputes_mcp.call_tool(
        "dispute_route",
        {"rail": "paypal",
         "purchase": {"rail": "paypal", "order_id": "ORDER-123"},
         "bundle": bundle},
    )
    payload = json.loads(standalone["content"][0]["text"])
    assert payload["evidence_packet"]["chain_verification"]["verifies"] \
        is False


def test_dispute_route_requires_rail(tmp_path):
    key = _issue()
    response = client.post("/api/trust/disputes/route",
                           headers=_headers(key), json={})
    assert response.status_code == 400


# -- Amazon Pay rail -----------------------------------------------------

def test_amazonpay_status_is_fixture_labelled(tmp_path):
    key = _issue()
    response = client.get("/api/trust/amazonpay/status",
                          headers=_headers(key))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rail"] == "amazon_pay"
    assert body["rail_mode"] == "fixture"
    assert body["configured"] is False
    assert body["mode_note"] == _FIXTURE_NOTE


def test_amazonpay_pass_creates_fixture_session(tmp_path):
    key = _issue()
    response = client.post("/api/trust/amazonpay/propose",
                           headers=_headers(key), json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "passed"
    assert body["certificate"]["verdict"] == "ISSUED"
    amazonpay = body["amazonpay"]
    assert amazonpay["status"] == "session_created"
    assert amazonpay["rail_mode"] == "fixture"
    assert amazonpay["mode_note"] == _FIXTURE_NOTE
    assert amazonpay["statusDetails"]["state"] == "Open"
    assert amazonpay["chargeAmount"] == {
        "amount": "25.00", "currencyCode": "USD"}
    transport = harness._amazon_client().transport
    assert transport.calls == ["create_checkout_session"]


def test_amazonpay_refusal_never_calls_the_rail(tmp_path):
    key = _issue()
    response = client.post("/api/trust/amazonpay/propose",
                           headers=_headers(key), json={
        "proposal": _proposal(tmp_path, _TAMPER_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["gate"] == "refused"
    assert body["certificate"] is None
    assert body["amazonpay"] is None
    transport = harness._amazon_client().transport
    assert transport.calls == []
    assert transport.sessions == {}


def test_amazonpay_capture_records_exactly_once(tmp_path):
    key = _issue()
    headers = _headers(key)
    proposed = client.post("/api/trust/amazonpay/propose", headers=headers,
                           json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    session_id = proposed.json()["amazonpay"]["checkoutSessionId"]

    first = client.post("/api/trust/amazonpay/capture", headers=headers,
                        json={"session_id": session_id})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ok"] is True
    assert body["recorded"] is True
    assert body["receipt_verifies"] is True
    assert body["amazonpay"]["status"] == "captured"
    assert body["amazonpay"]["rail"] == "amazon_pay"
    assert body["amazonpay"]["rail_mode"] == "fixture"
    assert body["amazonpay"]["mode_note"] == _FIXTURE_NOTE
    assert body["amazonpay"]["amount"] == "25.00"
    assert body["amazonpay"]["charge_state"] == "Completed"
    assert body["receipt"]["result_status"] == "amazonpay_payment_executed"

    second = client.post("/api/trust/amazonpay/capture", headers=headers,
                         json={"session_id": session_id})
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["amazonpay"]["status"] == "already_captured"
    assert body2["recorded"] is False

    bundle = harness._chain().to_bundle()
    executed = [r for r in bundle["receipts"]
                if r["result_status"] == "amazonpay_payment_executed"]
    assert len(executed) == 1
    transport = harness._amazon_client().transport
    assert transport.calls.count("capture_charge") == 1


def test_amazonpay_capture_requires_session_id(tmp_path):
    key = _issue()
    response = client.post("/api/trust/amazonpay/capture",
                           headers=_headers(key), json={})
    assert response.status_code == 400


def test_amazonpay_sandbox_without_credentials_is_unconfigured(
        tmp_path, monkeypatch):
    monkeypatch.setenv("AMAZON_PAY_MODE", "sandbox")
    harness._AMAZON_CLIENT = None
    key = _issue()
    response = client.post("/api/trust/amazonpay/propose",
                           headers=_headers(key), json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "passed"
    assert body["amazonpay"]["status"] == "unconfigured"
    assert body["amazonpay"]["rail_mode"] == "sandbox"
    assert "AMAZON_PAY_MERCHANT_ID" in body["amazonpay"]["detail"]
    assert "no call was sent to Amazon" in body["amazonpay"]["detail"]


# -- adapter units ---------------------------------------------------------

def test_adapter_idempotency_keys_are_required():
    amazon_pay = harness.amazon_pay
    rail_client = amazon_pay.AmazonPayClient(amazon_pay.FixtureTransport())
    with pytest.raises(amazon_pay.RailError):
        rail_client.create_charge({"chargePermissionId": "p"}, None)
    with pytest.raises(amazon_pay.RailError):
        rail_client.create_checkout_session({}, "")


def test_adapter_fixture_state_machines():
    amazon_pay = harness.amazon_pay
    rail_client = amazon_pay.AmazonPayClient(amazon_pay.FixtureTransport())
    session = rail_client.create_checkout_session(
        {"chargeAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="unit-1")
    assert session["statusDetails"]["state"] == "Open"
    replay = rail_client.create_checkout_session(
        {"chargeAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="unit-1")
    assert replay["checkoutSessionId"] == session["checkoutSessionId"]

    completed = rail_client.complete_checkout_session(
        session["checkoutSessionId"], {}, idempotency_key="unit-1-complete")
    assert completed["statusDetails"]["state"] == "Completed"
    charge_id = completed["chargeId"]
    captured = rail_client.capture_charge(
        charge_id, {}, idempotency_key="unit-1-capture")
    assert captured["statusDetails"]["state"] == "Completed"
    assert captured["state_history"] == [
        "Authorized", "CaptureInitiated", "Completed"]

    refund = rail_client.create_refund(
        {"chargeId": charge_id,
         "refundAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="unit-1-refund")
    assert refund["statusDetails"]["state"] == "Refunded"
    assert rail_client.get_refund(refund["refundId"])["refundId"] == \
        refund["refundId"]

    charge = rail_client.create_charge(
        {"chargePermissionId": session["chargePermissionId"],
         "chargeAmount": {"amount": "5.00", "currencyCode": "USD"}},
        idempotency_key="unit-2")
    canceled = rail_client.cancel_charge(charge["chargeId"])
    assert canceled["statusDetails"]["state"] == "Canceled"
    with pytest.raises(amazon_pay.RailError):
        rail_client.capture_charge(charge["chargeId"], {},
                                   idempotency_key="unit-2-capture")


def test_adapter_sandbox_transport_never_sends():
    amazon_pay = harness.amazon_pay
    transport = amazon_pay.SandboxTransport({})
    assert transport.configured is False
    with pytest.raises(amazon_pay.NotConfiguredError) as excinfo:
        transport.request("create_checkout_session", {},
                          {}, "idem")
    assert "no call was sent to Amazon" in str(excinfo.value)
    request = transport.build_request(
        "create_checkout_session", {}, {"x": 1}, "idem-9")
    assert request["method"] == "POST"
    assert request["url"] == \
        "https://pay-api.amazon.com/sandbox/v2/checkoutSessions"
    assert request["headers"]["x-amz-pay-idempotency-key"] == "idem-9"
