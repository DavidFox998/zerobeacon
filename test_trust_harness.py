"""Acceptance battery for the trust harness.

Pass path returns a certificate and a receipt. Tamper path names the
failed check and returns no certificate. Catalogue counts stay
1003 / 1052 / 21. Harness tools (12, receipts and dispute routing
included) are counted on their own endpoint.
"""

import json
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
    assert "append_receipt" in text
    assert "verify_receipts" in text
    assert "no MCP server" not in text


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
        "append_receipt", "verify_receipts",
        "dispute_route",
    ]
    assert len(names) == 12
    assert "receipts_mcp_gap" not in listed.json()["result"]

    init = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "initialize",
    })
    assert "receipts_mcp_gap" not in init.json()["result"]
    assert "receipts_mcp" not in init.json()["result"]
    assert not hasattr(harness, "RECEIPTS_MCP_GAP")


def test_receipts_over_mcp_round_trip():
    key = _issue()
    headers = _headers(key)
    appended = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "append_receipt", "arguments": {
            "actor": "acceptance-mcp",
            "action": {"method": "POST", "host": "test", "path": "/api/trust/mcp"},
            "result_status": "recorded",
        }},
    })
    assert appended.status_code == 200, appended.text
    result = appended.json()["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["bundle"]["count"] == 1

    # Same chain as REST: a REST append lands after the MCP one.
    rest = client.post("/api/trust/receipts/append", headers=headers, json={
        "actor": "acceptance-rest",
        "action": {"method": "POST", "host": "test", "path": "/api/trust/receipts/append"},
        "result_status": "recorded",
    })
    bundle = rest.json()["bundle"]
    assert bundle["count"] == 2
    assert bundle["receipts"][0]["hash"] == payload["receipt"]["hash"]

    verified = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "verify_receipts", "arguments": {"bundle": bundle}},
    })
    assert json.loads(verified.json()["result"]["content"][0]["text"]) == {"ok": True, "detail": 2}

    bundle["receipts"][0]["result_status"] = "forged"
    tampered = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 3, "method": "tools/call",
        "params": {"name": "verify_receipts", "arguments": {"bundle": bundle}},
    })
    body = json.loads(tampered.json()["result"]["content"][0]["text"])
    assert body["ok"] is False
    assert body["detail"][0] == 0


def test_receipts_mcp_requires_enterprise_key():
    call = {
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "verify_receipts", "arguments": {"bundle": {}}},
    }
    response = client.post("/api/trust/mcp", json=call)
    assert response.status_code == 403
    assert response.json()["error"] == "tier_required"
    low = client.post("/api/trust/mcp", headers=_headers(_issue("pro_100")), json=call)
    assert low.status_code == 403
    assert low.json()["error"] == "tier_required"


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


# -- Path B: live issuance --------------------------------------------------
# The test secret is generated per run and only ever placed in the process
# environment. No key material is written into this file.


def _live_file(tmp_path: Path) -> Path:
    return tmp_path / "anchor-live-registry.json"


def _pass_flow(key: str, repo: Path, address: str = "/api/mf/03/cart_checkout"):
    proposal = _proposal(repo, _PASS_MANIFEST)
    proposal["address"] = address
    response = client.post("/api/trust/flow", headers=_headers(key), json=proposal)
    assert response.status_code == 200, response.text
    return response.json()


def _tamper_flow(key: str, repo: Path):
    response = client.post("/api/trust/flow", headers=_headers(key),
                           json=_proposal(repo, _PASS_MANIFEST))
    assert response.status_code == 200, response.text
    return response.json()


def test_live_first_pass_issues_and_verifies(tmp_path, monkeypatch):
    secret = os.urandom(32).hex()
    monkeypatch.setenv("ANCHOR_SIGNING_SECRET", secret)
    key = _issue()
    repo = tmp_path / "repo"
    repo.mkdir()
    _tree(repo, _PASS_LEAN)
    assert not _live_file(tmp_path).exists()

    body = _pass_flow(key, repo)
    assert body["ok"] is True
    assert body["certificate"]["verdict"] == "ISSUED"
    assert body["certificate"]["registry_source"] == "live"
    assert body["registry_source"] == "live"
    assert body["live_issuance"] == "issued"
    assert body["live_registry"]["entries"] == 1
    assert body["live_registry"]["verifies"] is True
    assert secret not in json.dumps(body)

    doc = json.loads(_live_file(tmp_path).read_text())
    assert doc["registry_id"] == "anchor-live-registry"
    assert doc["entries"] == [{
        "address": "/api/mf/03/cart_checkout",
        "tool": "cart_checkout",
        "status": "active",
    }]
    assert harness.anchor.verify_registry(doc)["ok"] is True
    assert doc["public_key"] == harness.receipts.Keypair(bytes.fromhex(secret)).public_hex

    second = _pass_flow(key, repo, "/api/mf/03/dispute_resolve")
    assert second["live_issuance"] == "issued"
    doc = json.loads(_live_file(tmp_path).read_text())
    assert len(doc["entries"]) == 2
    assert harness.anchor.verify_registry(doc)["ok"] is True


def test_live_repeat_pass_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ANCHOR_SIGNING_SECRET", os.urandom(32).hex())
    key = _issue()
    repo = tmp_path / "repo"
    repo.mkdir()
    _tree(repo, _PASS_LEAN)
    assert _pass_flow(key, repo)["live_issuance"] == "issued"
    before = _live_file(tmp_path).read_bytes()

    again = _pass_flow(key, repo)
    assert again["ok"] is True
    assert again["certificate"]["verdict"] == "ISSUED"
    assert again["certificate"]["registry_source"] == "live"
    assert again["live_issuance"] == "already_issued"
    assert again["receipt"]["result_status"] == "certificate"
    assert again["receipt_verifies"] is True
    assert _live_file(tmp_path).read_bytes() == before
    doc = json.loads(before)
    assert len(doc["entries"]) == 1
    assert harness.anchor.verify_registry(doc)["ok"] is True


def test_live_refusal_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ANCHOR_SIGNING_SECRET", os.urandom(32).hex())
    key = _issue()
    good = tmp_path / "good"
    good.mkdir()
    _tree(good, _PASS_LEAN)
    bad = tmp_path / "bad"
    bad.mkdir()
    _tree(bad, "theorem gap : True := by\n  sorry\n")

    refused = _tamper_flow(key, bad)
    assert refused["ok"] is False and refused["certificate"] is None
    assert refused["live_issuance"] == "not_issued"
    assert not _live_file(tmp_path).exists()

    assert _pass_flow(key, good)["live_issuance"] == "issued"
    before = _live_file(tmp_path).read_bytes()
    refused = _tamper_flow(key, bad)
    assert refused["ok"] is False
    assert refused["live_issuance"] == "not_issued"
    assert refused["live_registry"]["entries"] == 1
    assert _live_file(tmp_path).read_bytes() == before

    unissued = _pass_flow(key, good, "/api/mf/03/never_issued")
    assert unissued["ok"] is False
    assert _live_file(tmp_path).read_bytes() == before


def test_live_unconfigured_falls_back_to_demo(tmp_path):
    key = _issue()
    _tree(tmp_path, _PASS_LEAN)
    body = _pass_flow(key, tmp_path)
    assert body["ok"] is True
    assert body["certificate"]["verdict"] == "ISSUED"
    assert body["certificate"]["registry_id"] == "anchor-demo-registry"
    assert body["certificate"]["registry_source"] == "demo"
    assert body["live_issuance"] == "unconfigured"
    assert "ANCHOR_SIGNING_SECRET is not set" in body["live_issuance_detail"]
    assert body["live_registry"]["configured"] is False
    assert not _live_file(tmp_path).exists()

    rest = client.post("/api/trust/anchor/registry", headers=_headers(key), json={}).json()
    assert rest["registry_source"] == "demo"
    assert rest["registry_id"] == "anchor-demo-registry"
    assert rest["live"]["registry_source"] == "live"
    assert rest["live"]["configured"] is False


def test_live_malformed_secret_is_unconfigured_and_not_echoed(tmp_path, monkeypatch):
    malformed = "not-hex-" + "z" * 56
    monkeypatch.setenv("ANCHOR_SIGNING_SECRET", malformed)
    key = _issue()
    _tree(tmp_path, _PASS_LEAN)
    response = client.post("/api/trust/flow", headers=_headers(key),
                           json=_proposal(tmp_path, _PASS_MANIFEST))
    body = response.json()
    assert body["certificate"]["registry_source"] == "demo"
    assert body["live_issuance"] == "unconfigured"
    assert "malformed" in body["live_issuance_detail"]
    assert malformed not in response.text
    assert not _live_file(tmp_path).exists()


def test_registry_info_reports_demo_and_live(tmp_path, monkeypatch):
    monkeypatch.setenv("ANCHOR_SIGNING_SECRET", os.urandom(32).hex())
    key = _issue()
    repo = tmp_path / "repo"
    repo.mkdir()
    _tree(repo, _PASS_LEAN)
    _pass_flow(key, repo)
    live_doc = json.loads(_live_file(tmp_path).read_text())

    rest = client.post("/api/trust/anchor/registry", headers=_headers(key), json={}).json()
    assert rest["registry_source"] == "demo" and rest["entries"] == 3
    assert rest["live"]["configured"] is True
    assert rest["live"]["entries"] == 1
    assert rest["live"]["verifies"] is True
    assert rest["live"]["public_key"] == live_doc["public_key"]
    assert rest["live"]["digest"] == harness.anchor.registry_digest(live_doc)

    mcp = client.post("/api/trust/mcp", headers=_headers(key), json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "registry_info", "arguments": {}},
    }).json()["result"]
    info = json.loads(mcp["content"][0]["text"])
    assert info["registry_source"] == "demo"
    assert info["registry_id"] == "anchor-demo-registry"
    assert info["live"]["entries"] == 1 and info["live"]["verifies"] is True

    # Demo resolution is unchanged by live issuance.
    resolved = client.post("/api/trust/anchor/verify", headers=_headers(key), json={
        "address": "/api/mf/03/cart_checkout",
    }).json()
    assert resolved["registry_id"] == "anchor-demo-registry"
    assert resolved["resolution"]["verdict"] == "ISSUED"


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
