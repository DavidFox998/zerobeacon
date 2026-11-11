"""Refusal layer tests — the gates contract on the PayPal packet gate.

The contract, measured the way the harness measures everything: a
refusal is a 200 verdict, never an HTTP error; every refusal carries
its numbers (bound_value, observed_value) and a
what_would_change_verdict that names the actual certificate and
counts; no calibration, no measurement; and every verdict — PASS
or REFUSED — is retrievable afterwards by trace_id.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import core.keystore as keystore
import routers.trust_harness as harness
from zerobeacon_mf_1000_main import app

client = TestClient(app, raise_server_exceptions=True)

_KEY_PATH = Path("/tmp/test_refusal_layer_api_keys.json")
_SESSION_PATH = Path("/tmp/test_refusal_layer_sessions.json")
_RESEND_PATH = Path("/tmp/test_refusal_layer_resend.json")

_SECRET = "refusal-test-admin-secret"
_LABEL = "judges@test.local"
_GATE = "/gates/paypal_packet_gate/evaluate"
_CURRENT_CAL = "cal_bundle_verify_2026-11-05"
_EXPIRED_CAL = "cal_bundle_verify_2024-11-05"


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


def _measurement(calibration_id=_CURRENT_CAL, value=1) -> dict:
    measurement = {
        "measurement_id": "m_test_01",
        "property": "bundle_integrity",
        "value": value,
        "unit": "match",
        "instrument_id": "bundle-verify-v1",
    }
    if calibration_id is not None:
        measurement["calibration_id"] = calibration_id
    return measurement


def _evaluate(key, ref, measurements=None):
    body = {"subject": {"kind": "payment_packet", "ref": ref}}
    if measurements is not None:
        body["measurements"] = measurements
    return client.post(_GATE, headers=_headers(key), json=body)


# -- verdicts ---------------------------------------------------------------

def test_clean_packet_passes():
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_clean",
                         [_measurement()])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "PASS"
    assert body["gated_by"] == "paypal_packet_gate_v2"
    assert body["measurement_id"] == "m_test_01"
    assert body["trace_id"].startswith("trace_")
    assert "refusal" not in body


def test_tampered_packet_refused_integrity_mismatch():
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_tampered",
                         [_measurement(value=0)])
    assert response.status_code == 200, response.text  # a refusal is not an error
    body = response.json()
    assert body["decision"] == "REFUSED"
    assert body["measurement_id"] == "m_test_01"
    refusal = body["refusal"]
    assert refusal["refusal_code"] == "INTEGRITY_MISMATCH"
    assert refusal["bound_type"] == "integrity"
    assert refusal["bound_name"] == "bundle_certificate_match"
    assert refusal["bound_value"] == 1
    assert refusal["observed_value"] == 0
    assert refusal["unit"] == "match"
    # The flip condition names the actual certificate and the actual counts.
    what = refusal["what_would_change_verdict"]
    assert "06bedb36" in what
    assert "invoice.txt" in what
    assert "1 changed" in what
    assert body["refusals"] == [refusal]


def test_missing_calibration_refused():
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_clean",
                         [_measurement(calibration_id=None)])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "REFUSED"
    assert body["measurement_id"] is None  # null on calibration refusals
    refusal = body["refusal"]
    assert refusal["refusal_code"] == "CALIBRATION_MISSING"
    assert refusal["bound_type"] == "calibration"
    assert refusal["bound_name"] == "bundle_verify_calibration"
    assert refusal["bound_value"] is not None
    assert refusal["observed_value"] is not None
    assert "cal_bundle_verify_2026-11-05" in \
        refusal["what_would_change_verdict"]


def test_no_measurements_refused_calibration_missing():
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_clean")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "REFUSED"
    assert body["refusal"]["refusal_code"] == "CALIBRATION_MISSING"


def test_unknown_calibration_refused_missing():
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_clean",
                         [_measurement(calibration_id="cal_nope")])
    assert response.status_code == 200, response.text
    assert response.json()["refusal"]["refusal_code"] == "CALIBRATION_MISSING"


def test_expired_calibration_refused():
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_clean",
                         [_measurement(calibration_id=_EXPIRED_CAL)])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["decision"] == "REFUSED"
    assert body["measurement_id"] is None
    refusal = body["refusal"]
    assert refusal["refusal_code"] == "CALIBRATION_EXPIRED"
    assert refusal["bound_type"] == "calibration"
    assert refusal["unit"] == "calibration_expires_at"
    assert refusal["observed_value"] == "2025-11-05T00:00:00Z"
    assert _EXPIRED_CAL in refusal["what_would_change_verdict"]


def test_calibration_checked_before_integrity():
    """A tampered packet with no papers is refused on calibration —
    the gate does not proceed on an unmeasured number."""
    key = _issue()
    response = _evaluate(key, "bundle:payment_packet_tampered",
                         [_measurement(calibration_id=None, value=0)])
    body = response.json()
    assert body["decision"] == "REFUSED"
    assert body["refusal"]["refusal_code"] == "CALIBRATION_MISSING"
    assert len(body["refusals"]) == 1


# -- lineage: verdicts retrievable by trace_id -------------------------------

def test_verdict_retrievable_by_trace_id():
    key = _issue()
    headers = _headers(key)
    refused = _evaluate(key, "bundle:payment_packet_tampered",
                        [_measurement(value=0)]).json()
    fetched = client.get(f"/verdicts/{refused['trace_id']}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == refused  # exactly as issued

    passed = _evaluate(key, "bundle:payment_packet_clean",
                       [_measurement()]).json()
    fetched = client.get(f"/verdicts/{passed['trace_id']}", headers=headers)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == passed


def test_unknown_trace_id_is_404_problem():
    key = _issue()
    response = client.get("/verdicts/trace_nope", headers=_headers(key))
    assert response.status_code == 404
    assert response.json()["title"] == "Unknown trace_id"


# -- gate description, protocol problems, auth --------------------------------

def test_gate_description_publishes_bounds():
    key = _issue()
    response = client.get("/gates/paypal_packet_gate",
                          headers=_headers(key))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate_id"] == "paypal_packet_gate"
    bounds = {b["bound_name"]: b for b in body["bounds"]}
    assert bounds["bundle_certificate_match"]["bound_type"] == "integrity"
    assert bounds["bundle_certificate_match"]["bound_value"] == 1
    assert bounds["bundle_verify_calibration"]["bound_type"] == "calibration"


def test_unknown_gate_is_404_problem():
    key = _issue()
    headers = _headers(key)
    response = client.get("/gates/nope", headers=headers)
    assert response.status_code == 404
    assert response.json()["title"] == "Unknown gate"
    response = client.post("/gates/nope/evaluate", headers=headers,
                           json={"subject": {"kind": "payment_packet",
                                             "ref": "bundle:x"}})
    assert response.status_code == 404
    assert response.json()["title"] == "Unknown gate"


def test_malformed_requests_are_400_problems():
    key = _issue()
    headers = _headers(key)
    response = client.post(_GATE, headers=headers, json={})
    assert response.status_code == 400
    assert response.json()["title"] == "Malformed evaluation request"
    response = client.post(_GATE, headers=headers, json={
        "subject": {"kind": "payment_packet",
                    "ref": "bundle:payment_packet_clean"},
        "measurements": [{"measurement_id": "m_x"}],  # missing fields
    })
    assert response.status_code == 400
    response = client.post(
        _GATE, headers={**headers, "Content-Type": "application/json"},
        content=b"this is not json")
    assert response.status_code == 400
    assert response.json()["title"] == "Malformed evaluation request"


def test_unresolvable_subject_is_400_not_a_verdict():
    key = _issue()
    response = _evaluate(key, "bundle:no_such_packet", [_measurement()])
    assert response.status_code == 400
    assert response.json()["title"] == "Malformed evaluation request"


def test_gates_require_enterprise_key():
    response = client.post(_GATE, json={
        "subject": {"kind": "payment_packet",
                    "ref": "bundle:payment_packet_clean"}})
    assert response.status_code == 403
    response = client.get("/gates/paypal_packet_gate")
    assert response.status_code == 403
