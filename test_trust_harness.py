"""Acceptance battery for the trust harness.

Pass path returns a certificate and a receipt. Tamper path names the
failed check and returns no certificate. Catalogue counts stay
1003 / 1052 / 21. Harness tools are counted on their own endpoint.
"""

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import core.keystore as keystore
import routers.trust_harness as harness
import zerobeacon_mf_1000_main as main_module
from zerobeacon_mf_1000_main import ROUTERS, app

client = TestClient(app, raise_server_exceptions=True)

_KEY_PATH = Path("/tmp/test_trust_harness_api_keys.json")
_SESSION_PATH = Path("/tmp/test_trust_harness_sessions.json")
_RESEND_PATH = Path("/tmp/test_trust_harness_resend.json")

_SECRET = "test-admin-secret"
_LABEL = "judges@test.local"
_PASS_MANIFEST = {
    "sorry_count": 0,
    "admit_count": 0,
    "axiom_count": 0,
    "file_count": 1,
}
_PASS_LEAN = "theorem t : True := True\n"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Use a private keystore for these tests and put the previous one back.

    Other modules set keystore paths at import. Leaving those paths moved
    would make later persistence tests read the wrong files.
    """
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


def _tree(root: Path, source: str) -> None:
    lean = root / "Sample.lean"
    lean.write_text(source, encoding="utf-8")


def _proposal(root: Path, manifest: dict) -> dict:
    return {
        "repo": str(root),
        "manifest": manifest,
        "server": "zerobeacon",
        "address": "/api/mf/03/cart_checkout",
    }


def test_showroom_page_names_the_flow():
    response = client.get("/trust")
    assert response.status_code == 200
    text = response.text
    assert "POST /api/trust/apathy/audit" in text
    assert "POST /api/trust/proven/facts" in text
    assert "POST /api/trust/anchor/verify" in text
    assert "POST /api/trust/receipts/append" in text
    assert "POST /api/trust/flow" in text
    assert "audit_repo" in text
    assert "get_facts" in text
    assert "verify_address" in text
    assert "no MCP server" in text


def test_harness_requires_enterprise_key():
    response = client.post("/api/trust/proven/facts", json={"name": "zerobeacon"})
    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == "tier_required"


def test_module_endpoints_and_mcp_list(tmp_path):
    key = _issue()
    headers = _headers(key)
    _tree(tmp_path, _PASS_LEAN)

    audit = client.post("/api/trust/apathy/check", headers=headers, json={
        "repo": str(tmp_path),
        "manifest": _PASS_MANIFEST,
    })
    assert audit.status_code == 200, audit.text
    assert audit.json()["ok"] is True
    assert audit.json()["report"]["all_pass"] is True

    facts = client.post("/api/trust/proven/facts", headers=headers, json={"name": "zerobeacon"})
    assert facts.json()["facts"]["verdict"] == "MATCH"
    assert facts.json()["facts"]["measured_tools"] == 1003

    resolved = client.post("/api/trust/anchor/verify", headers=headers, json={
        "address": "/api/mf/03/cart_checkout",
    })
    assert resolved.json()["ok"] is True
    assert resolved.json()["resolution"]["verdict"] == "ISSUED"
    assert resolved.json()["resolution"]["tool"] == "cart_checkout"

    recorded = client.post("/api/trust/receipts/append", headers=headers, json={
        "actor": "acceptance",
        "action": {"method": "POST", "host": "test", "path": "/api/trust/receipts/append"},
        "result_status": "recorded",
    })
    assert recorded.json()["ok"] is True
    bundle = recorded.json()["bundle"]
    verified = client.post("/api/trust/receipts/verify", headers=headers, json={"bundle": bundle})
    assert verified.json() == {"ok": True, "detail": 1}

    listed = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    })
    names = [tool["name"] for tool in listed.json()["result"]["tools"]]
    assert names == [
        "audit_repo", "check_manifest", "verify_ref",
        "probe_server", "get_facts", "check_drift",
        "parse_address", "verify_address", "registry_info",
    ]
    assert "receipts" not in " ".join(names)
    assert listed.json()["result"]["receipts_mcp_gap"]


def test_pass_path_returns_certificate_and_receipt(tmp_path):
    key = _issue()
    _tree(tmp_path, _PASS_LEAN)
    response = client.post(
        "/api/trust/flow",
        headers=_headers(key),
        json=_proposal(tmp_path, _PASS_MANIFEST),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["certificate"]["verdict"] == "ISSUED"
    assert body["certificate"]["canonical_address"] == "/api/mf/03/cart_checkout"
    assert body["certificate"]["tool"] == "cart_checkout"
    assert body["certificate"]["status"] == "active"
    assert body["certificate"]["registry_id"] == "anchor-demo-registry"
    assert "refusal" not in body
    assert body["receipt"]["result_status"] == "certificate"
    assert body["receipt_verifies"] is True
    ok, detail = harness.receipts.verify_bundle({
        "public_key": body["receipt"] and harness._chain().to_bundle()["public_key"],
        "count": 1,
        "head_hash": body["receipt"]["hash"],
        "receipts": [body["receipt"]],
    })
    assert ok is True and detail == 1


def test_tamper_path_names_the_failed_check_and_issues_no_certificate(tmp_path):
    key = _issue()
    _tree(tmp_path, textwrap.dedent("""\
        theorem gap : True := by
          sorry
    """))
    manifest = {
        "sorry_count": 0,
        "admit_count": 0,
        "axiom_count": 0,
        "file_count": 1,
    }
    response = client.post(
        "/api/trust/flow",
        headers=_headers(key),
        json=_proposal(tmp_path, manifest),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["certificate"] is None
    checks = [item["check"] for item in body["refusal"]["failed_checks"]]
    assert "sorry_count" in checks
    sorry = next(item for item in body["refusal"]["failed_checks"] if item["check"] == "sorry_count")
    assert sorry["claimed"] == 0
    assert sorry["measured"] == 1
    assert body["receipt"]["result_status"].startswith("refusal:sorry_count")
    assert body["receipt_verifies"] is True
    assert "ISSUED" not in response.text


def test_catalogue_counts_unchanged():
    catalogue = client.get("/mcp")
    assert catalogue.status_code == 200
    assert len(catalogue.json()["result"]["tools"]) == 1003
    health = client.get("/health").json()
    assert health["tools"] == 1052
    assert health["routers"] == 21
    assert len(ROUTERS) == 21
    assert len(main_module._build_tool_list()) == 1003
    harness_names = set(harness.HARNESS_TOOL_NAMES)
    catalogue_names = {tool["name"] for tool in catalogue.json()["result"]["tools"]}
    assert harness_names.isdisjoint(catalogue_names)
