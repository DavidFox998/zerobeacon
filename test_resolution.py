"""Resolution Layer tests for the trust harness.

The contract is measured rule by rule, the way the spec numbers
them: a decline is a 200 answer (1); reason == reasons[0] under
the published precedence (2); validation reasons short-circuit
and never mix with subject reasons (3); unit and operator are
required (4, 5); an APPROVED decision never rests on a null
validation_id where the checkpoint requires validation (6);
validation reasons carry typed instants, not numbers (7), and
name their check and validation record (8); every decision is
written to the Receipts chain and retrievable by case_id (9).
On top of that, the PayPal packet gate: a tampered packet now
also returns the Resolution decision record naming the changed
file, and a clean packet behaves exactly as before.
"""

import json
import os
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import core.keystore as keystore
import routers.trust_harness as harness
from zerobeacon_mf_1000_main import app

client = TestClient(app, raise_server_exceptions=True)

_KEY_PATH = Path("/tmp/test_resolution_api_keys.json")
_SESSION_PATH = Path("/tmp/test_resolution_sessions.json")
_RESEND_PATH = Path("/tmp/test_resolution_resend.json")

_SECRET = "resolution-test-admin-secret"
_LABEL = "judges@test.local"
_PASS_MANIFEST = {
    "sorry_count": 0, "admit_count": 0, "axiom_count": 0, "file_count": 1,
}
_PASS_LEAN = "theorem t : True := True\n"

_DEMO_ROOT = Path(__file__).parent / "trust_stack" / "demo"
_CLEAN_PACKET = _DEMO_ROOT / "payment_packet_clean"
_TAMPERED_PACKET = _DEMO_ROOT / "payment_packet_tampered"
_CLEAN_RECORD = json.loads(
    (_DEMO_ROOT / "payment_packet_clean.lock.json").read_text("utf-8"))

_FUTURE = "2999-01-01T00:00:00+00:00"
_PAST = "2000-01-01T00:00:00+00:00"
_VALIDATION_ID = "val_tier_limits_2026-11-01"
_CHECK_ID = "tier-limit-check-v1"


import pytest


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
    monkeypatch.setenv("TRUST_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANCHOR_SIGNING_SECRET", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_ID", raising=False)
    monkeypatch.delenv("PAYPAL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PAYPAL_ENV", raising=False)
    yield
    harness._CHAIN = None
    harness._BUNDLE_PATH = None
    for path in (_KEY_PATH, _SESSION_PATH, _RESEND_PATH):
        path.unlink(missing_ok=True)
    keystore.KEY_PATH, keystore.SESSION_PATH, keystore.RESEND_PATH = saved_paths
    keystore._store = saved_store
    keystore._session_map = saved_sessions
    keystore._resend_store = saved_resend


def _issue(tier: str = "enterprise_1000") -> str:
    with patch.dict(os.environ, {"ADMIN_SECRET": _SECRET}):
        response = client.post("/api/admin/keys/issue", json={
            "admin_secret": _SECRET, "tier": tier, "email": _LABEL,
        })
    assert response.status_code == 200, response.text
    return response.json()["key"]


def _headers(key: str) -> dict:
    return {"X-API-Key": key}


def _evidence(**overrides) -> dict:
    evidence = {
        "property": "transaction_amount",
        "value": 2450,
        "unit": "USD",
        "check_id": _CHECK_ID,
        "validation_id": _VALIDATION_ID,
        "validation_expires_at": _FUTURE,
    }
    evidence.update(overrides)
    return evidence


def _limit(**overrides) -> dict:
    limit = {
        "limit_name": "transaction_amount_limit",
        "limit_type": "eligibility_limit",
        "operator": "lte",
        "limit_value": 2000,
        "assessed_value": 2450,
        "unit": "USD",
        "check_id": _CHECK_ID,
        "validation_id": _VALIDATION_ID,
    }
    limit.update(overrides)
    return limit


def _evaluate(headers, **overrides):
    body = {
        "checkpoint_id": "paypal_transaction_review",
        "subject": {"kind": "transaction", "ref": "txn:test-1"},
        "evidence": _evidence(),
        "limits": [_limit()],
    }
    body.update(overrides)
    return client.post("/api/trust/resolution/evaluate",
                       headers=headers, json=body)


# -- rule 1: a decline is a 200 answer ------------------------------------

def test_decline_is_a_200_answer_not_an_error():
    key = _issue()
    response = _evaluate(_headers(key))
    assert response.status_code == 200, response.text
    record = response.json()["decision"]
    assert record["decision"] == "DECLINED"
    assert record["checkpoint_id"] == "paypal_transaction_review"
    assert record["gate_version"] == "1.0.0"
    assert record["decided_by"] == "paypal_transaction_review"
    assert record["case_id"].startswith("case_")
    assert record["subject"] == {"kind": "transaction", "ref": "txn:test-1"}
    # The evidence envelope is embedded in the record.
    assert record["evidence"]["check_id"] == _CHECK_ID
    assert record["evidence"]["validation_id"] == _VALIDATION_ID
    assert record["evidence"]["evidence_id"].startswith("ev_")


def test_resolution_endpoints_require_enterprise_key():
    response = client.post("/api/trust/resolution/evaluate", json={})
    assert response.status_code == 403
    assert response.json()["error"] == "tier_required"


def test_unknown_checkpoint_is_a_404_not_a_decision():
    key = _issue()
    response = _evaluate(_headers(key), checkpoint_id="no_such_checkpoint")
    assert response.status_code == 404


# -- rule 2: reason == reasons[0], published deterministic precedence ------

def test_reason_equals_first_reason_and_precedence_is_published_order():
    key = _issue()
    limits = [
        _limit(limit_name="document_type_accepted", limit_type="document",
               operator="eq", limit_value="accepted",
               assessed_value="unreadable_image", unit=None),
        _limit(),  # eligibility -> LIMIT_NOT_MET
        _limit(limit_name="goods_category_permitted",
               limit_type="restricted_category", operator="eq",
               limit_value="permitted", assessed_value="restricted",
               unit=None),
        _limit(limit_name="rail_single_transaction_cap",
               limit_type="hard_policy_limit", operator="lte",
               limit_value=10000, assessed_value=12500),
        _limit(limit_name="bundle_certificate_match",
               limit_type="verification", operator="eq", limit_value=1,
               assessed_value=0, unit="match"),
    ]
    limits = [{k: v for k, v in limit.items() if v is not None}
              for limit in limits]
    response = _evaluate(_headers(key), limits=limits)
    assert response.status_code == 200, response.text
    record = response.json()["decision"]
    codes = [r["reason_code"] for r in record["reasons"]]
    assert codes == [
        "VERIFICATION_MISMATCH",
        "POLICY_LIMIT_EXCEEDED",
        "RESTRICTED_CATEGORY",
        "LIMIT_NOT_MET",
        "DOCUMENT_NOT_ACCEPTED",
    ]
    assert record["reason"] == record["reasons"][0]
    assert record["reason"]["reason_code"] == "VERIFICATION_MISMATCH"


def test_checkpoint_description_publishes_precedence_limits_and_validation():
    key = _issue()
    response = client.get(
        "/api/trust/resolution/checkpoints/paypal_packet_gate",
        headers=_headers(key))
    assert response.status_code == 200, response.text
    checkpoint = response.json()["checkpoint"]
    assert checkpoint["requires_validation"] is False
    assert checkpoint["gate_version"] == "2.0.0"
    assert checkpoint["limits"][0]["limit_name"] == "bundle_certificate_match"
    assert checkpoint["limits"][0]["operator"] == "eq"
    assert checkpoint["precedence"][:3] == [
        "VALIDATION_MISSING", "VALIDATION_EXPIRED", "VERIFICATION_MISMATCH"]

    response = client.get(
        "/api/trust/resolution/checkpoints/paypal_transaction_review",
        headers=_headers(key))
    assert response.json()["checkpoint"]["requires_validation"] is True

    response = client.get(
        "/api/trust/resolution/checkpoints/nope", headers=_headers(key))
    assert response.status_code == 404


# -- rule 3 + 7 + 8: validation short-circuit, typed instants, naming ------

def test_expired_validation_short_circuits_and_carries_typed_instants():
    key = _issue()
    response = _evaluate(
        _headers(key),
        evidence=_evidence(validation_id="val_old",
                           validation_expires_at=_PAST),
        limits=[_limit(validation_id="val_old")])
    record = response.json()["decision"]
    assert record["decision"] == "DECLINED"
    # Short-circuit: the failing subject limit is never mixed in.
    assert len(record["reasons"]) == 1
    reason = record["reason"]
    assert reason["reason_code"] == "VALIDATION_EXPIRED"
    # Rule 7: typed instants, no numeric pair.
    assert reason["required_valid_at"] == record["evaluated_at"]
    assert reason["validation_expires_at"] == _PAST
    assert "limit_value" not in reason
    assert "assessed_value" not in reason
    # Rule 8: the check and the record are named.
    assert reason["check_id"] == _CHECK_ID
    assert reason["validation_id"] == "val_old"


def test_missing_validation_short_circuits():
    key = _issue()
    response = _evaluate(
        _headers(key),
        evidence=_evidence(validation_id=None, validation_expires_at=None),
        limits=[_limit(validation_id=None)])
    record = response.json()["decision"]
    assert [r["reason_code"] for r in record["reasons"]] == \
        ["VALIDATION_MISSING"]
    assert record["reason"]["check_id"] == _CHECK_ID
    assert record["reason"]["validation_id"] is None


# -- rules 4 + 5: unit and operator are required ----------------------------

def test_numeric_limit_without_unit_is_a_request_error():
    key = _issue()
    bad = _limit()
    del bad["unit"]
    response = _evaluate(_headers(key), limits=[bad])
    assert response.status_code == 400
    assert "unit" in response.json()["error"]


def test_limit_without_operator_is_a_request_error():
    key = _issue()
    bad = _limit()
    del bad["operator"]
    response = _evaluate(_headers(key), limits=[bad])
    assert response.status_code == 400
    assert "operator" in response.json()["error"]


def test_templated_steps_agree_with_the_operator():
    key = _issue()
    limit = _limit()
    response = _evaluate(_headers(key), limits=[limit])
    steps = response.json()["decision"]["reason"]["steps_to_resolve"]
    assert "lte" in steps
    assert "2000" in steps


# -- rule 6: APPROVED never rests on a null validation_id -------------------

def test_passing_limits_without_validation_still_decline():
    key = _issue()
    response = _evaluate(
        _headers(key),
        evidence=_evidence(value=100, validation_id=None,
                           validation_expires_at=None),
        limits=[_limit(assessed_value=100, validation_id=None)])
    record = response.json()["decision"]
    assert record["decision"] == "DECLINED"
    assert record["reason"]["reason_code"] == "VALIDATION_MISSING"


def test_approved_decision_rests_on_a_validation_id():
    key = _issue()
    response = _evaluate(
        _headers(key),
        evidence=_evidence(value=1450),
        limits=[_limit(assessed_value=1450)])
    record = response.json()["decision"]
    assert record["decision"] == "APPROVED"
    assert record["reason"] is None
    assert record["reasons"] == []
    assert record["evidence"]["validation_id"] == _VALIDATION_ID
    assert response.json()["receipt_verifies"] is True


# -- rule 9: receipted and retrievable by case_id ----------------------------

def test_case_retrievable_and_decision_on_the_receipts_chain():
    key = _issue()
    headers = _headers(key)
    response = _evaluate(headers)
    record = response.json()["decision"]
    receipt = response.json()["receipt"]
    assert receipt["hash"]

    fetched = client.get(
        f"/api/trust/resolution/cases/{record['case_id']}",
        headers=headers)
    assert fetched.status_code == 200, fetched.text
    body = fetched.json()
    assert body["decision"] == record
    assert body["receipt"]["hash"] == receipt["hash"]

    bundle = harness._chain().to_bundle()
    ok, _detail = harness.receipts.verify_bundle(bundle)
    assert ok is True
    statuses = [r["result_status"] for r in bundle["receipts"]]
    assert any(isinstance(s, dict) and s.get("case_id") == record["case_id"]
               and s.get("decision") == "DECLINED" for s in statuses)

    missing = client.get("/api/trust/resolution/cases/case_nope",
                         headers=headers)
    assert missing.status_code == 404


def test_resolution_tool_over_mcp_records_a_case():
    key = _issue()
    headers = _headers(key)
    listed = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    })
    names = [t["name"] for t in listed.json()["result"]["tools"]]
    assert "resolution_evaluate" in names
    assert len(names) == 15
    assert "resolution_evaluate" in harness.HARNESS_TOOL_NAMES
    assert len(harness.HARNESS_TOOL_NAMES) == 15

    called = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "resolution_evaluate", "arguments": {
            "checkpoint_id": "paypal_transaction_review",
            "subject": {"kind": "transaction", "ref": "txn:mcp-1"},
            "evidence": _evidence(),
            "limits": [_limit()],
        }},
    })
    result = called.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    record = payload["decision"]
    assert record["decision"] == "DECLINED"
    assert record["reason"]["reason_code"] == "LIMIT_NOT_MET"
    assert payload["receipt"]["hash"]
    fetched = client.get(
        f"/api/trust/resolution/cases/{record['case_id']}",
        headers=headers)
    assert fetched.json()["decision"]["case_id"] == record["case_id"]


# -- module level: the packet checkpoint's standing limit -------------------

def test_packet_checkpoint_standing_limit_declines_and_approves():
    resolution = harness.resolution
    declined = resolution.evaluate({
        "checkpoint_id": "paypal_packet_gate",
        "subject": {"kind": "payment_packet", "ref": "bundle:x"},
        "evidence": {"property": "bundle_integrity", "value": 0,
                     "unit": "match", "check_id": "bundle-verify-v1",
                     "validation_id": None},
    })
    assert declined["decision"] == "DECLINED"
    reason = declined["reason"]
    assert reason["reason_code"] == "VERIFICATION_MISMATCH"
    assert reason["operator"] == "eq"
    assert reason["limit_value"] == 1
    assert reason["assessed_value"] == 0
    assert reason["unit"] == "match"

    approved = resolution.evaluate({
        "checkpoint_id": "paypal_packet_gate",
        "subject": {"kind": "payment_packet", "ref": "bundle:x"},
        "evidence": {"property": "bundle_integrity", "value": 1,
                     "unit": "match", "check_id": "bundle-verify-v1",
                     "validation_id": None},
    })
    assert approved["decision"] == "APPROVED"


# -- PayPal packet gate: the decline now carries a decision record ----------

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
                "id": "ORDER-123", "status": "CREATED",
                "links": [{"rel": "approve",
                           "href": "https://sandbox.example/approve/ORDER-123"}],
            }
        return 404, {"name": "RESOURCE_NOT_FOUND"}


def _configure_paypal(monkeypatch, fake):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid-test")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "csecret-test")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(harness, "_paypal_http", fake)


def _packet_body(tmp_path, packet_folder: Path) -> dict:
    (tmp_path / "Sample.lean").write_text(_PASS_LEAN, encoding="utf-8")
    return {
        "proposal": {
            "repo": str(tmp_path), "manifest": _PASS_MANIFEST,
            "server": "zerobeacon",
            "address": "/api/mf/03/cart_checkout",
        },
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
        "packet": {"folder": str(packet_folder),
                   "record": json.loads(json.dumps(_CLEAN_RECORD))},
    }


def test_tampered_packet_decline_carries_the_decision_record(
        tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure_paypal(monkeypatch, fake)
    key = _issue()
    headers = _headers(key)
    response = client.post("/api/trust/paypal/propose-packet",
                           headers=headers,
                           json=_packet_body(tmp_path, _TAMPERED_PACKET))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "refused"
    assert fake.calls == []

    record = body["resolution"]
    assert record["decision"] == "DECLINED"
    assert record["checkpoint_id"] == "paypal_packet_gate"
    assert record["gate_version"] == "2.0.0"
    assert record["decided_by"] == "paypal_packet_gate"
    assert record["subject"]["kind"] == "payment_packet"
    assert record["evidence"]["property"] == "bundle_integrity"
    assert record["evidence"]["value"] == 0
    reason = record["reason"]
    assert reason == record["reasons"][0]
    assert reason["reason_code"] == "VERIFICATION_MISMATCH"
    assert reason["operator"] == "eq"
    assert reason["limit_value"] == 1
    assert reason["assessed_value"] == 0
    assert reason["unit"] == "match"
    assert "invoice.txt" in reason["steps_to_resolve"]
    assert "1 changed" in reason["steps_to_resolve"]

    fetched = client.get(
        f"/api/trust/resolution/cases/{record['case_id']}",
        headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["decision"]["case_id"] == record["case_id"]


def test_clean_packet_regression_no_resolution_record(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure_paypal(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose-packet",
                           headers=_headers(key),
                           json=_packet_body(tmp_path, _CLEAN_PACKET))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "passed"
    assert body["packet"]["verdict"] == "LOCK HOLDS"
    assert body["paypal"]["status"] == "created"
    assert "resolution" not in body
    orders = [c for c in fake.calls if c["url"].endswith("/v2/checkout/orders")]
    assert len(orders) == 1
