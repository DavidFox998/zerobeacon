"""PayPal gate tests for the trust harness.

The payment is the gated action: a refused flow makes ZERO PayPal
calls; a passing flow creates exactly one sandbox order; capture
records exactly one payment receipt; a repeat capture records
nothing new. PayPal's HTTP surface is replaced by a recorder.
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

_KEY_PATH = Path("/tmp/test_paypal_trust_api_keys.json")
_SESSION_PATH = Path("/tmp/test_paypal_trust_sessions.json")
_RESEND_PATH = Path("/tmp/test_paypal_trust_resend.json")

_SECRET = "paypal-test-admin-secret"
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
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        if url.endswith("/v1/oauth2/token"):
            return 200, {"access_token": "TEST-TOKEN", "token_type": "Bearer"}
        if url.endswith("/v2/checkout/orders"):
            return 201, {
                "id": "ORDER-123",
                "status": "CREATED",
                "links": [
                    {"rel": "self", "href": "https://sandbox.example/orders/ORDER-123"},
                    {"rel": "approve", "href": "https://sandbox.example/approve/ORDER-123"},
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

    def urls(self):
        return [call["url"] for call in self.calls]


def _configure(monkeypatch, fake):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid-test")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "csecret-test")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(harness, "_paypal_http", fake)


def test_status_unconfigured(tmp_path):
    key = _issue()
    response = client.get("/api/trust/paypal/status", headers=_headers(key))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["configured"] is False
    assert body["environment"] == "sandbox"
    assert body["gate"] == "live"


def test_paypal_endpoints_require_enterprise_key(tmp_path):
    response = client.post("/api/trust/paypal/propose", json={})
    assert response.status_code == 403
    assert response.json()["error"] == "tier_required"


def test_propose_rejects_bad_amounts(tmp_path):
    key = _issue()
    headers = _headers(key)
    proposal = _proposal(tmp_path, _PASS_MANIFEST)
    for bad in ("abc", "0", "-5", "10.005", ""):
        response = client.post("/api/trust/paypal/propose", headers=headers, json={
            "proposal": proposal,
            "purchase": {"item": "Inspection", "amount_usd": bad},
        })
        assert response.status_code == 400, (bad, response.text)
    response = client.post("/api/trust/paypal/propose", headers=headers, json={
        "proposal": proposal,
    })
    assert response.status_code == 400


def test_refused_flow_makes_no_paypal_call(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose", headers=_headers(key), json={
        "proposal": _proposal(tmp_path, _TAMPER_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["gate"] == "refused"
    assert body["certificate"] is None
    assert body["paypal"] is None
    assert fake.calls == []


def test_pass_flow_creates_one_order(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose", headers=_headers(key), json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["gate"] == "passed"
    assert body["certificate"]["verdict"] == "ISSUED"
    assert body["paypal"]["status"] == "created"
    assert body["paypal"]["order_id"] == "ORDER-123"
    assert body["paypal"]["approve_url"] == "https://sandbox.example/approve/ORDER-123"
    order_calls = [c for c in fake.calls if c["url"].endswith("/v2/checkout/orders")]
    assert len(order_calls) == 1
    sent = json.loads(order_calls[0]["body"].decode("utf-8"))
    assert sent["intent"] == "CAPTURE"
    assert sent["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "25.00"}


def test_pass_flow_unconfigured_reports_both_halves(tmp_path, monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(harness, "_paypal_http", fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose", headers=_headers(key), json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "passed"
    assert body["certificate"]["verdict"] == "ISSUED"
    assert body["paypal"]["status"] == "unconfigured"
    assert fake.calls == []


def test_capture_records_exactly_once(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    headers = _headers(key)
    client.post("/api/trust/paypal/propose", headers=headers, json={
        "proposal": _proposal(tmp_path, _PASS_MANIFEST),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    first = client.post("/api/trust/paypal/capture", headers=headers, json={
        "paypal_order_id": "ORDER-123",
    })
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["ok"] is True
    assert body["recorded"] is True
    assert body["receipt_verifies"] is True
    assert body["paypal"]["status"] == "captured"
    assert body["paypal"]["capture_id"] == "CAP-9"
    assert body["paypal"]["amount"] == "25.00"

    second = client.post("/api/trust/paypal/capture", headers=headers, json={
        "paypal_order_id": "ORDER-123",
    })
    assert second.status_code == 200, second.text
    body2 = second.json()
    assert body2["paypal"]["status"] == "already_captured"
    assert body2["recorded"] is False
    capture_calls = [c for c in fake.calls if c["url"].endswith("/capture")]
    assert len(capture_calls) == 1


def test_capture_requires_order_id(tmp_path):
    key = _issue()
    response = client.post("/api/trust/paypal/capture", headers=_headers(key), json={})
    assert response.status_code == 400


_DEMO_ROOT = Path(__file__).parent / "trust_stack" / "demo"


def _bundled_proposal(repo_name: str) -> dict:
    return {
        "repo": str(_DEMO_ROOT / repo_name),
        "manifest": dict(_PASS_MANIFEST),
        "server": "zerobeacon",
        "address": "/api/mf/03/cart_checkout",
    }


def test_bundled_clean_sample_passes(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose", headers=_headers(key), json={
        "proposal": _bundled_proposal("sample_repo_clean"),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "passed"
    assert body["paypal"]["status"] == "created"


def test_bundled_dirty_sample_refused_with_clean_manifest(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose", headers=_headers(key), json={
        "proposal": _bundled_proposal("sample_repo_dirty"),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "refused"
    assert body["certificate"] is None
    assert fake.calls == []
