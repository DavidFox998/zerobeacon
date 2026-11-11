"""Bundle Lock tests for the trust harness.

The folder seal is measured the way the harness measures everything:
known vectors first (a demo bundle whose fingerprints, certificate and
beacon stamp were fixed before this module existed), then behaviour —
lock, hold, three kinds of tampering each named, restore, and a lock
record that no longer recomputes to itself. On top of that, the
PayPal packet gate: a packet that holds faces the composed flow and
creates exactly one order; a tampered packet is refused before the
flow runs and makes ZERO PayPal calls.
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

_KEY_PATH = Path("/tmp/test_bundle_lock_api_keys.json")
_SESSION_PATH = Path("/tmp/test_bundle_lock_sessions.json")
_RESEND_PATH = Path("/tmp/test_bundle_lock_resend.json")

_SECRET = "bundle-test-admin-secret"
_LABEL = "judges@test.local"
_PASS_MANIFEST = {
    "sorry_count": 0, "admit_count": 0, "axiom_count": 0, "file_count": 1,
}
_TAMPER_MANIFEST = {
    "sorry_count": 3, "admit_count": 0, "axiom_count": 0, "file_count": 1,
}
_PASS_LEAN = "theorem t : True := True\n"

_DEMO_ROOT = Path(__file__).parent / "trust_stack" / "demo"
_CLEAN_PACKET = _DEMO_ROOT / "payment_packet_clean"
_TAMPERED_PACKET = _DEMO_ROOT / "payment_packet_tampered"
_CLEAN_RECORD = json.loads(
    (_DEMO_ROOT / "payment_packet_clean.lock.json").read_text("utf-8"))

# A demo bundle fixed before this module was written: fingerprints,
# certificate and beacon stamp are known values, recomputed here by
# the module under test — a stranger's evidence, re-derived.
_SCHOOL_MANIFEST = {
    "figures.csv": "441f43850e6092562758b1f73151b45be59da25d5ecd43ebfd46c97460500b22",
    "letter.txt": "f90fe74fd575ea96f6146c943242c7d506f466dd9ba8fb9472f42174aa4541da",
    "photo.bin": "e7026a78a6dc80cbd85d7b344f0ee49ec6ace1d3ce8b80dce56e4782d048d731",
    "sub/appendix.md": "eb13d53796e37b6b40f313498416f97edc01690a4d3704b3091dca5020011b6a",
}
_SCHOOL_CERTIFICATE = \
    "9c248888a06a65aa904c0762ffdf5a2a9227fd90e8bafd967c3f1c5b75266962"
_LETTER = ("Dear Records Office,\n\n"
           "Enclosed are the figures for the autumn term.\n"
           "Totals were checked twice before sending.\n\n\u2014 J.\n")
_FIGURES = "item,count\nchairs,40\ndesks,25\nlamps,12\n"
_APPENDIX = ("# Appendix\n\n"
             "Room assignments follow the north corridor plan.\n")


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


# -- known vectors --------------------------------------------------------

def test_school_bundle_vectors_recompute():
    bl = harness.bundle_lock
    certificate = bl.certificate_from_manifest(_SCHOOL_MANIFEST)
    assert certificate == _SCHOOL_CERTIFICATE
    assert bl.beacon_for(certificate) == (2619639949, "e2040800")


def test_scanner_reproduces_the_school_text_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "letter.txt").write_text(_LETTER, encoding="utf-8")
    (tmp_path / "figures.csv").write_text(_FIGURES, encoding="utf-8")
    (tmp_path / "sub" / "appendix.md").write_text(_APPENDIX, encoding="utf-8")
    manifest, sizes, skipped = harness.bundle_lock.scan_folder(tmp_path)
    for name in ("letter.txt", "figures.csv", "sub/appendix.md"):
        assert manifest[name] == _SCHOOL_MANIFEST[name]
    assert sizes == {"figures.csv": 39, "letter.txt": 118,
                     "sub/appendix.md": 61}
    assert skipped == []


# -- lock / verify behaviour ----------------------------------------------

def _school_folder(root: Path) -> Path:
    (root / "sub").mkdir(parents=True)
    (root / "letter.txt").write_text(_LETTER, encoding="utf-8")
    (root / "figures.csv").write_text(_FIGURES, encoding="utf-8")
    (root / "sub" / "appendix.md").write_text(_APPENDIX, encoding="utf-8")
    return root


def test_lock_verify_round_trip_and_tamper_names(tmp_path):
    bl = harness.bundle_lock
    folder = _school_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "school")
    assert bl.verify_folder(folder, record)["verdict"] == "LOCK HOLDS"

    (folder / "figures.csv").write_text(
        _FIGURES.replace("desks,25", "desks,26"), encoding="utf-8")
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["changed"] == ["figures.csv"]
    (folder / "figures.csv").write_text(_FIGURES, encoding="utf-8")

    (folder / "stray.txt").write_text("extra\n", encoding="utf-8")
    report = bl.verify_folder(folder, record)
    assert report["added"] == ["stray.txt"]
    (folder / "stray.txt").unlink()

    (folder / "sub" / "appendix.md").unlink()
    report = bl.verify_folder(folder, record)
    assert report["removed"] == ["sub/appendix.md"]
    (folder / "sub" / "appendix.md").write_text(_APPENDIX, encoding="utf-8")
    assert bl.verify_folder(folder, record)["verdict"] == "LOCK HOLDS"


def test_edited_record_breaks_on_itself(tmp_path):
    bl = harness.bundle_lock
    folder = _school_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "school")
    forged = dict(record)
    forged["certificate"] = "0" * 64
    report = bl.verify_folder(folder, forged)
    assert report["verdict"] == "BROKEN"
    assert report["record_ok"] is False


def test_bundled_payment_packet_clean_holds_tampered_breaks():
    bl = harness.bundle_lock
    held = bl.verify_folder(_CLEAN_PACKET, _CLEAN_RECORD)
    assert held["verdict"] == "LOCK HOLDS"
    broken = bl.verify_folder(_TAMPERED_PACKET, _CLEAN_RECORD)
    assert broken["verdict"] == "BROKEN"
    assert broken["changed"] == ["invoice.txt"]


# -- bundle-lock/2: directories and symlinks --------------------------------

def _v2_folder(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "letter.txt").write_text(_LETTER, encoding="utf-8")
    (root / "figures.csv").write_text(_FIGURES, encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "appendix.md").write_text(_APPENDIX, encoding="utf-8")
    (root / "empty_dir").mkdir()
    os.symlink("letter.txt", root / "letter_link")
    os.symlink("no/such/target.txt", root / "dangling_link")
    return root


def test_v2_lock_records_directories_and_symlinks(tmp_path):
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    assert record["format"] == "bundle-lock/2"
    assert record["directories"] == ["empty_dir", "sub"]
    assert record["directory_count"] == 2
    assert record["symlinks"] == {
        "dangling_link": "no/such/target.txt",
        "letter_link": "letter.txt",
    }
    assert record["symlink_count"] == 2
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "LOCK HOLDS"
    assert report["dirs_added"] == [] and report["dirs_removed"] == []
    assert report["links_changed"] == []


def test_v2_empty_dir_added_detected(tmp_path):
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    (folder / "late_dir").mkdir()
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["dirs_added"] == ["late_dir"]


def test_v2_empty_dir_removed_detected(tmp_path):
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    (folder / "empty_dir").rmdir()
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["dirs_removed"] == ["empty_dir"]


def test_v2_file_swapped_for_dangling_symlink_detected(tmp_path):
    """THE v1 hole: a sealed file replaced by a dangling symlink at
    the same path verified as LOCK HOLDS under bundle-lock/1."""
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    (folder / "letter.txt").unlink()
    os.symlink("no/such/target.txt", folder / "letter.txt")
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["removed"] == ["letter.txt"]
    assert report["links_added"] == ["letter.txt"]


def test_v2_symlink_retarget_is_changed(tmp_path):
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    (folder / "letter_link").unlink()
    os.symlink("figures.csv", folder / "letter_link")
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["links_changed"] == ["letter_link"]


def test_v2_dangling_symlink_removed_detected(tmp_path):
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    (folder / "dangling_link").unlink()
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["links_removed"] == ["dangling_link"]


def test_v2_edited_record_breaks_on_itself(tmp_path):
    bl = harness.bundle_lock
    folder = _v2_folder(tmp_path / "bundle")
    record = bl.lock_folder(folder, "v2")
    forged = json.loads(json.dumps(record))
    forged["directories"] = ["sub"]
    report = bl.verify_folder(folder, forged)
    assert report["verdict"] == "BROKEN"
    assert report["record_ok"] is False


def _v1_record_for(folder: Path, label: str = "school-v1") -> dict:
    """A bundle-lock/1 record built by the /1 rules, as records in
    the wild were built before /2 existed."""
    bl = harness.bundle_lock
    manifest, sizes, skipped = bl.scan_folder(folder)
    certificate = bl.certificate_from_manifest(manifest)
    beacon_p, stamp = bl.beacon_for(certificate)
    return {
        "format": "bundle-lock/1",
        "label": label,
        "folder": folder.name,
        "certificate": certificate,
        "beacon_p": beacon_p,
        "beacon": stamp,
        "file_count": len(manifest),
        "total_bytes": sum(sizes.values()),
        "manifest": manifest,
        "sizes": sizes,
        "skipped": skipped,
        "locked_at": "2026-11-05T00:00:00-08:00",
    }


def test_v1_record_still_verifies(tmp_path):
    bl = harness.bundle_lock
    folder = _school_folder(tmp_path / "bundle")
    record = _v1_record_for(folder)
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "LOCK HOLDS"
    assert "dirs_added" not in report  # /1 reports keep the /1 shape


def test_v1_tamper_still_refused(tmp_path):
    bl = harness.bundle_lock
    folder = _school_folder(tmp_path / "bundle")
    record = _v1_record_for(folder)
    (folder / "figures.csv").write_text(
        _FIGURES.replace("desks,25", "desks,26"), encoding="utf-8")
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["changed"] == ["figures.csv"]


def test_v1_blind_spots_are_the_v1_contract(tmp_path):
    """The measured /1 scope, preserved exactly: an added empty
    folder and a planted dangling symlink are invisible (LOCK
    HOLDS), and a file swapped for a dangling symlink surfaces only
    as a bare removal — the link itself is never named, because /1
    reports carry no symlink entries at all. /2 closes all three."""
    bl = harness.bundle_lock
    folder = _school_folder(tmp_path / "bundle")
    record = _v1_record_for(folder)
    (folder / "new_empty").mkdir()
    assert bl.verify_folder(folder, record)["verdict"] == "LOCK HOLDS"
    (folder / "new_empty").rmdir()
    os.symlink("no/such/target.txt", folder / "planted_link")
    assert bl.verify_folder(folder, record)["verdict"] == "LOCK HOLDS"
    (folder / "planted_link").unlink()
    (folder / "figures.csv").unlink()
    os.symlink("no/such/target.csv", folder / "figures.csv")
    report = bl.verify_folder(folder, record)
    assert report["verdict"] == "BROKEN"
    assert report["removed"] == ["figures.csv"]
    assert "links_added" not in report


# -- REST + MCP surface -----------------------------------------------------

def test_bundle_endpoints_require_enterprise_key():
    response = client.post("/api/trust/bundles/lock", json={})
    assert response.status_code == 403
    assert response.json()["error"] == "tier_required"


def test_lock_and_verify_over_rest_with_receipts(tmp_path):
    key = _issue()
    headers = _headers(key)
    folder = _school_folder(tmp_path / "bundle")
    locked = client.post("/api/trust/bundles/lock", headers=headers, json={
        "folder": str(folder), "label": "school-rest",
    })
    assert locked.status_code == 200, locked.text
    body = locked.json()
    assert body["ok"] is True
    assert body["receipt_verifies"] is True
    assert body["record"]["certificate"]

    held = client.post("/api/trust/bundles/verify", headers=headers, json={
        "folder": str(folder), "label": "school-rest",
    })
    assert held.json()["verdict"] == "LOCK HOLDS"
    assert held.json()["receipt_verifies"] is True

    (folder / "letter.txt").write_text("forged\n", encoding="utf-8")
    broken = client.post("/api/trust/bundles/verify", headers=headers, json={
        "folder": str(folder), "record": body["record"],
    })
    assert broken.json()["verdict"] == "BROKEN"
    assert broken.json()["changed"] == ["letter.txt"]


def test_bundle_tools_over_mcp(tmp_path):
    key = _issue()
    headers = _headers(key)
    folder = _school_folder(tmp_path / "bundle")
    locked = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "bundle_lock",
                   "arguments": {"folder": str(folder), "label": "school-mcp"}},
    })
    result = locked.json()["result"]
    assert result["isError"] is False
    record = json.loads(result["content"][0]["text"])["record"]

    verified = client.post("/api/trust/mcp", headers=headers, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "bundle_verify",
                   "arguments": {"folder": str(folder), "label": "school-mcp"}},
    })
    payload = json.loads(verified.json()["result"]["content"][0]["text"])
    assert payload["verdict"] == "LOCK HOLDS"
    assert record["certificate"] == payload["certificate"]


# -- PayPal packet gate -----------------------------------------------------

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


def _configure(monkeypatch, fake):
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "cid-test")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "csecret-test")
    monkeypatch.setenv("PAYPAL_ENV", "sandbox")
    monkeypatch.setattr(harness, "_paypal_http", fake)


def _proposal(root: Path, manifest: dict) -> dict:
    (root / "Sample.lean").write_text(_PASS_LEAN, encoding="utf-8")
    return {
        "repo": str(root), "manifest": manifest,
        "server": "zerobeacon", "address": "/api/mf/03/cart_checkout",
    }


def _packet_body(tmp_path, packet_folder: Path, manifest: dict) -> dict:
    return {
        "proposal": _proposal(tmp_path, manifest),
        "purchase": {"item": "Inspection", "amount_usd": "25.00"},
        "packet": {"folder": str(packet_folder),
                   "record": json.loads(json.dumps(_CLEAN_RECORD))},
    }


def test_packet_holds_then_order_created(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose-packet",
                           headers=_headers(key),
                           json=_packet_body(tmp_path, _CLEAN_PACKET,
                                             _PASS_MANIFEST))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "passed"
    assert body["packet"]["verdict"] == "LOCK HOLDS"
    assert body["paypal"]["status"] == "created"
    orders = [c for c in fake.calls if c["url"].endswith("/v2/checkout/orders")]
    assert len(orders) == 1


def test_tampered_packet_refused_with_zero_paypal_calls(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose-packet",
                           headers=_headers(key),
                           json=_packet_body(tmp_path, _TAMPERED_PACKET,
                                             _PASS_MANIFEST))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["gate"] == "refused"
    assert body["certificate"] is None
    assert body["paypal"] is None
    failed = body["refusal"]["failed_checks"]
    assert failed[0]["check"] == "bundle_lock"
    assert failed[0]["changed"] == ["invoice.txt"]
    assert body["receipt_verifies"] is True
    assert fake.calls == []


def test_clean_packet_but_tampered_claim_still_refused(tmp_path, monkeypatch):
    fake = FakePayPal()
    _configure(monkeypatch, fake)
    key = _issue()
    response = client.post("/api/trust/paypal/propose-packet",
                           headers=_headers(key),
                           json=_packet_body(tmp_path, _CLEAN_PACKET,
                                             _TAMPER_MANIFEST))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["gate"] == "refused"
    assert body["packet"]["verdict"] == "LOCK HOLDS"
    assert fake.calls == []
