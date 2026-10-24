"""Key-gated lane manuals: GET /api/lane-manual.

The key is the lane selector. Own-lane key gets its manual; every other
caller is refused: no key, unknown key, non-lane key (even enterprise),
expired lane key, under-tier lane key. A lane with no manual file in the
deployment answers 503 manual_not_provisioned. Manual bodies here are
placeholders written to a temp directory; no real manual is in git.
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import core.keystore as keystore
import zerobeacon_mf_1000_main as main_module
from zerobeacon_mf_1000_main import ROUTERS, app

client = TestClient(app, raise_server_exceptions=True)

_KEY_PATH = Path("/tmp/test_lane_manuals_api_keys.json")
_SESSION_PATH = Path("/tmp/test_lane_manuals_sessions.json")
_RESEND_PATH = Path("/tmp/test_lane_manuals_resend.json")

LANES = {
    "amazon": "amazon-judges@zerobeacon.ai",
    "paypal": "paypal-judges@zerobeacon.ai",
    "meta": "meta-judges@zerobeacon.ai",
}


def _body(lane: str) -> str:
    return f"# {lane} lane manual (test placeholder)\n\nmarker-{lane}-7f3a\n"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Private keystore and manual directory; previous state restored."""
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
    manuals = tmp_path / "lane-manuals"
    manuals.mkdir()
    for lane in LANES:
        (manuals / f"{lane}.md").write_text(_body(lane), encoding="utf-8")
    monkeypatch.setattr(main_module, "LANE_MANUAL_DIR", manuals)
    yield manuals
    for path in (_KEY_PATH, _SESSION_PATH, _RESEND_PATH):
        path.unlink(missing_ok=True)
    keystore.KEY_PATH, keystore.SESSION_PATH, keystore.RESEND_PATH = saved_paths
    keystore._store = saved_store
    keystore._session_map = saved_sessions
    keystore._resend_store = saved_resend


def _key(label: str, tier: str = "enterprise_1000", expires_at: int | None = None) -> str:
    return keystore.issue_key(tier, label, expires_at=expires_at)


def _get(key: str | None = None, params: dict | None = None):
    headers = {"X-API-Key": key} if key is not None else {}
    return client.get("/api/lane-manual", headers=headers, params=params)


def _refused(response, status: int, error: str) -> None:
    assert response.status_code == status, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"] == error
    for lane in LANES:
        assert f"marker-{lane}-7f3a" not in response.text


@pytest.mark.parametrize("lane", sorted(LANES))
def test_own_lane_key_gets_its_manual(lane):
    response = _get(_key(LANES[lane]))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["cache-control"] == "no-store"
    assert response.text == _body(lane)


def test_lane_key_with_future_expiry_still_served():
    key = _key(LANES["meta"], expires_at=int(time.time()) + 3600)
    response = _get(key)
    assert response.status_code == 200
    assert response.text == _body("meta")


def test_cross_lane_key_cannot_read_another_lane():
    paypal = _key(LANES["paypal"])
    for params in (None, {"lane": "amazon"}, {"lane": "meta"}):
        response = _get(paypal, params)
        assert response.status_code == 200
        assert response.text == _body("paypal")
        assert "marker-amazon-7f3a" not in response.text
        assert "marker-meta-7f3a" not in response.text


def test_non_lane_enterprise_key_is_403():
    _refused(_get(_key("customer@example.com")), 403, "not_a_lane_key")


def test_label_match_is_exact():
    _refused(_get(_key("Amazon-Judges@zerobeacon.ai")), 403, "not_a_lane_key")
    _refused(_get(_key("amazon-judges@zerobeacon.ai.evil")), 403, "not_a_lane_key")


def test_expired_lane_key_is_refused():
    key = _key(LANES["amazon"], expires_at=int(time.time()) - 1)
    assert keystore.tier_of(key) == "free"
    _refused(_get(key), 403, "key_expired")


def test_under_tier_lane_key_is_refused():
    _refused(_get(_key(LANES["amazon"], tier="pro_100")), 403, "tier_required")


def test_no_key_is_refused():
    _refused(_get(), 403, "key_required")
    _refused(_get("   "), 403, "key_required")


def test_unknown_key_is_refused():
    _refused(_get("zbk_" + "0" * 32), 403, "invalid_key")


def test_missing_manual_file_is_503(isolated):
    (isolated / "paypal.md").unlink()
    _refused(_get(_key(LANES["paypal"])), 503, "manual_not_provisioned")
    response = _get(_key(LANES["amazon"]))
    assert response.status_code == 200


def test_empty_manual_file_is_503(isolated):
    (isolated / "meta.md").write_text("  \n", encoding="utf-8")
    _refused(_get(_key(LANES["meta"])), 503, "manual_not_provisioned")


def test_whole_directory_absent_is_503(monkeypatch, tmp_path):
    monkeypatch.setattr(main_module, "LANE_MANUAL_DIR", tmp_path / "not-there")
    _refused(_get(_key(LANES["amazon"])), 503, "manual_not_provisioned")


def test_catalogue_and_repo_invariants():
    health = client.get("/health").json()
    assert health["tools"] == 1052
    assert health["routers"] == 21
    assert len(ROUTERS) == 21
    names = {tool["name"] for tool in main_module._build_tool_list()}
    assert not any("lane" in name and "manual" in name for name in names)
    root = Path(__file__).resolve().parent
    assert "lane-manuals/" in (root / ".gitignore").read_text().splitlines()
    assert sorted(p.name for p in (root / "key-manuals").iterdir()) == ["README.md"]
