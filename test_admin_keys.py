#!/usr/bin/env python3
"""
Tests for the admin key endpoints (POST /api/admin/keys/issue + /revoke)
and keystore expiry (time-boxed evaluation keys).

Covered cases:
  1. ADMIN_SECRET not set in env            -> 403 (fail closed)
  2. Wrong admin_secret                     -> 403
  3. Issue with bad tier / missing email    -> 400
  4. Issue enterprise key, no expiry        -> 200, key grants enterprise
  5. Issue with expires_at in the past      -> key confers only free
  6. Issue with expires_at in the future    -> key confers its tier until then
  7. Revoke by email                        -> key downgraded to free

Run:
    pytest test_admin_keys.py -v
"""

import os
import time
import pytest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient

# ── isolate keystore so tests never touch production files ────────────────────
import core.keystore as keystore

keystore.KEY_PATH     = Path("/tmp/test_admin_keys_api_keys.json")
keystore.SESSION_PATH = Path("/tmp/test_admin_keys_sessions.json")
keystore.RESEND_PATH  = Path("/tmp/test_admin_keys_resend.json")
keystore._store       = {}
keystore._session_map = {}
keystore._resend_store = {}

import zerobeacon_mf_1000_main as main_module
from zerobeacon_mf_1000_main import app, _route_tier

client = TestClient(app, raise_server_exceptions=True)

_SECRET = "test-admin-secret"
_LABEL = "judges@test.local"


@pytest.fixture(autouse=True)
def clean_state():
    keystore._store = {}
    keystore._session_map = {}
    for p in (keystore.KEY_PATH, keystore.SESSION_PATH, keystore.RESEND_PATH):
        p.unlink(missing_ok=True)
    yield
    for p in (keystore.KEY_PATH, keystore.SESSION_PATH, keystore.RESEND_PATH):
        p.unlink(missing_ok=True)


def _issue(body: dict, env_secret: str | None = _SECRET):
    env = {} if env_secret is None else {"ADMIN_SECRET": env_secret}
    with patch.dict(os.environ, env, clear=False):
        if env_secret is None:
            os.environ.pop("ADMIN_SECRET", None)
            try:
                return client.post("/api/admin/keys/issue", json=body)
            finally:
                pass
        return client.post("/api/admin/keys/issue", json=body)


def _first_route(prefix: str) -> str:
    for path in _route_tier:
        if path.startswith(prefix + "/"):
            return path
    raise RuntimeError(f"No routes under {prefix}")


class TestAdminGate:
    def test_no_env_secret_forbidden(self):
        r = _issue({"admin_secret": _SECRET, "tier": "enterprise_1000", "email": _LABEL},
                   env_secret=None)
        assert r.status_code == 403

    def test_wrong_secret_forbidden(self):
        r = _issue({"admin_secret": "nope", "tier": "enterprise_1000", "email": _LABEL})
        assert r.status_code == 403

    def test_bad_tier_400(self):
        r = _issue({"admin_secret": _SECRET, "tier": "platinum", "email": _LABEL})
        assert r.status_code == 400

    def test_missing_email_400(self):
        r = _issue({"admin_secret": _SECRET, "tier": "pro_10"})
        assert r.status_code == 400


class TestIssueAndExpiry:
    def test_enterprise_key_grants(self):
        r = _issue({"admin_secret": _SECRET, "tier": "enterprise_1000", "email": _LABEL})
        assert r.status_code == 200
        key = r.json()["key"]
        assert key.startswith("zbk_")
        assert keystore.tier_of(key) == "enterprise_1000"
        resp = client.get(_first_route("/api/mf/17"), headers={"X-API-Key": key})
        assert resp.status_code == 200, resp.text

    def test_expired_key_is_free(self):
        r = _issue({"admin_secret": _SECRET, "tier": "enterprise_1000",
                    "email": _LABEL, "expires_at": 1})
        assert r.status_code == 200
        key = r.json()["key"]
        assert keystore.tier_of(key) == "free"
        resp = client.get(_first_route("/api/mf/17"), headers={"X-API-Key": key})
        assert resp.status_code == 403
        assert "expired" in resp.json()["message"].lower() or \
               resp.json()["error"] == "tier_required"

    def test_future_expiry_still_grants(self):
        r = _issue({"admin_secret": _SECRET, "tier": "enterprise_1000",
                    "email": _LABEL, "expires_at": int(time.time()) + 86400})
        key = r.json()["key"]
        assert keystore.tier_of(key) == "enterprise_1000"


class TestRevoke:
    def test_revoke_by_email(self):
        r = _issue({"admin_secret": _SECRET, "tier": "enterprise_1000", "email": _LABEL})
        key = r.json()["key"]
        with patch.dict(os.environ, {"ADMIN_SECRET": _SECRET}):
            rr = client.post("/api/admin/keys/revoke",
                             json={"admin_secret": _SECRET, "email": _LABEL})
        assert rr.status_code == 200
        assert rr.json()["revoked"] == 1
        assert keystore.tier_of(key) == "free"
