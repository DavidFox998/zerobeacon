#!/usr/bin/env python3
"""Receipts test suite.

Three arenas:
  crypto — RFC 8032 Ed25519 test vector #1 (empty message), exactly:
           derived public key, deterministic signature, verify, and
           rejection of a flipped signature bit and a changed message.
  chain  — append/verify round-trip in the library, then every tamper
           class the README promises to detect: altered body, reordered
           receipts, tail truncation (count), forged count (head), bad
           signature, wrong public key.
  mcp    — receipts_mcp.py driven as a real stdio MCP server in a temp
           RECEIPTS_DATA_DIR: initialize, tools/list (exactly the two REST
           mirrors), append_receipt twice, verify_receipts PASS, tampered
           bundle FAIL at its index, input errors, unknown tool. Plus the
           in-process recorder hook the hosted harness uses.

Run:  python3 tests/run_tests.py
Exit: 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent
RECEIPTS_MCP = PKG / "receipts_mcp.py"

sys.path.insert(0, str(PKG))
import receipts  # noqa: E402

RESULTS = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition)))
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {name}{suffix}", flush=True)


# ---------------------------------------------------------------------
# crypto: RFC 8032 section 7.1, TEST 1
# ---------------------------------------------------------------------

RFC1_SECRET = "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60"
RFC1_PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RFC1_SIGNATURE = (
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
    "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)


def test_crypto() -> None:
    secret = bytes.fromhex(RFC1_SECRET)
    public = receipts.ed25519_publickey(secret)
    check("C1. RFC 8032 #1 public key derives exactly",
          public.hex() == RFC1_PUBLIC, public.hex())
    signature = receipts.ed25519_sign(b"", secret, public)
    check("C2. RFC 8032 #1 signature matches byte-for-byte",
          signature.hex() == RFC1_SIGNATURE)
    check("C3. RFC 8032 #1 signature verifies",
          receipts.ed25519_verify(bytes.fromhex(RFC1_SIGNATURE), b"",
                                  bytes.fromhex(RFC1_PUBLIC)) is True)
    flipped = bytearray(bytes.fromhex(RFC1_SIGNATURE))
    flipped[0] ^= 0x01
    check("C4. flipped signature bit is rejected",
          receipts.ed25519_verify(bytes(flipped), b"",
                                  bytes.fromhex(RFC1_PUBLIC)) is False)
    check("C5. changed message is rejected",
          receipts.ed25519_verify(bytes.fromhex(RFC1_SIGNATURE), b"x",
                                  bytes.fromhex(RFC1_PUBLIC)) is False)
    check("C6. malformed lengths are rejected, never raise",
          receipts.ed25519_verify(b"short", b"", b"short") is False)


# ---------------------------------------------------------------------
# chain
# ---------------------------------------------------------------------

def _action(path: str) -> dict:
    return {"method": "POST", "host": "test.local", "path": path}


def test_chain(workdir: Path) -> None:
    key_path = workdir / "chain-key.json"
    keypair = receipts.Keypair.load_or_create(str(key_path))
    mode = stat.S_IMODE(os.stat(key_path).st_mode)
    check("K1. signing key file is created mode 0600", mode == 0o600,
          oct(mode))

    chain = receipts.ReceiptChain(keypair)
    for i in range(3):
        chain.append("suite", _action(f"/step/{i}"), 200 + i)
    bundle = chain.to_bundle()
    ok, detail = receipts.verify_bundle(bundle)
    check("L1. 3-receipt chain verifies", ok is True and detail == 3,
          str(detail))
    check("L2. genesis prev_hash is 64 zeros",
          bundle["receipts"][0]["prev_hash"] == receipts.GENESIS_HASH)

    altered = copy.deepcopy(bundle)
    altered["receipts"][1]["result_status"] = 500
    ok, detail = receipts.verify_bundle(altered)
    check("L3. altered body fails at its index",
          ok is False and detail[0] == 1 and "hash mismatch" in detail[1],
          str(detail))

    reordered = copy.deepcopy(bundle)
    reordered["receipts"][1], reordered["receipts"][2] = (
        reordered["receipts"][2], reordered["receipts"][1])
    ok, detail = receipts.verify_bundle(reordered)
    check("L4. reordered receipts break the chain",
          ok is False and "chain broken" in detail[1], str(detail))

    truncated = copy.deepcopy(bundle)
    truncated["receipts"] = truncated["receipts"][:2]
    ok, detail = receipts.verify_bundle(truncated)
    check("L5. tail truncation fails the count check",
          ok is False and detail[0] == -1 and "count mismatch" in detail[1],
          str(detail))

    truncated["count"] = 2
    ok, detail = receipts.verify_bundle(truncated)
    check("L6. truncation with forged count fails the head check",
          ok is False and detail[0] == -1 and "head_hash" in detail[1],
          str(detail))

    badsig = copy.deepcopy(bundle)
    sig = bytearray(bytes.fromhex(badsig["receipts"][2]["signature"]))
    sig[5] ^= 0x01
    badsig["receipts"][2]["signature"] = bytes(sig).hex()
    ok, detail = receipts.verify_bundle(badsig)
    check("L7. corrupted signature fails at its index",
          ok is False and detail[0] == 2, str(detail))

    other = receipts.Keypair(os.urandom(32))
    wrongkey = copy.deepcopy(bundle)
    wrongkey["public_key"] = other.public_hex
    ok, detail = receipts.verify_bundle(wrongkey)
    check("L8. wrong public key fails at receipt 0",
          ok is False and detail[0] == 0, str(detail))

    reloaded = receipts.ReceiptChain.from_bundle(
        receipts.Keypair.load_or_create(str(key_path)), bundle)
    reloaded.append("suite", _action("/step/3"), 204)
    ok, detail = receipts.verify_bundle(reloaded.to_bundle())
    check("L9. chain reloaded from bundle extends and verifies",
          ok is True and detail == 4, str(detail))


# ---------------------------------------------------------------------
# mcp over stdio
# ---------------------------------------------------------------------

def _mcp_session(messages: list[dict], data_dir: Path) -> list[dict]:
    env = dict(os.environ, RECEIPTS_DATA_DIR=str(data_dir))
    stdin = "".join(json.dumps(m) + "\n" for m in messages)
    proc = subprocess.run([sys.executable, str(RECEIPTS_MCP)], input=stdin,
                          capture_output=True, text=True, env=env,
                          timeout=120)
    return [json.loads(line) for line in proc.stdout.splitlines()
            if line.strip()]


def _payload(response: dict):
    return json.loads(response["result"]["content"][0]["text"])


def test_mcp(workdir: Path) -> None:
    data_dir = workdir / "mcp-data"
    action = _action("/mcp/append")
    responses = _mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "ping"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
            "name": "append_receipt", "arguments": {
                "actor": "mcp-suite", "action": action,
                "result_status": "first"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
            "name": "append_receipt", "arguments": {
                "actor": "mcp-suite", "action": action,
                "result_status": "second"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {
            "name": "append_receipt", "arguments": {
                "actor": "mcp-suite", "action": action}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {
            "name": "append_receipt", "arguments": {
                "actor": 7, "action": action, "result_status": 1}}},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {
            "name": "no_such_tool", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 9, "method": "bogus/method"},
    ], data_dir)
    by_id = {r.get("id"): r for r in responses}

    check("M1. notification gets no response, 9 replies for 9 requests",
          len(responses) == 9, str(len(responses)))
    init = by_id[1]["result"]
    check("M2. initialize reports protocol 2025-11-25 and server receipts",
          init["protocolVersion"] == "2025-11-25"
          and init["serverInfo"]["name"] == "receipts")
    check("M3. ping answers", by_id[2].get("result") == {})
    names = [t["name"] for t in by_id[3]["result"]["tools"]]
    check("M4. tools/list is exactly the two REST mirrors",
          names == ["append_receipt", "verify_receipts"], str(names))

    first, second = _payload(by_id[4]), _payload(by_id[5])
    check("M5. append_receipt returns ok and a 1-receipt bundle",
          by_id[4]["result"]["isError"] is False and first["ok"] is True
          and first["bundle"]["count"] == 1)
    check("M6. second append chains onto the first",
          second["bundle"]["count"] == 2
          and second["receipt"]["prev_hash"] == first["receipt"]["hash"])
    check("M7. missing result_status is an input error",
          by_id[6]["result"]["isError"] is True
          and _payload(by_id[6])["error"] == "result_status is required")
    check("M8. non-string actor is an input error",
          by_id[7]["result"]["isError"] is True
          and "actor and action" in _payload(by_id[7])["error"])
    check("M9. unknown tool is an error result",
          by_id[8]["result"]["isError"] is True)
    check("M10. unknown method is JSON-RPC -32601",
          by_id[9]["error"]["code"] == -32601)

    bundle = second["bundle"]
    tampered = copy.deepcopy(bundle)
    tampered["receipts"][0]["actor"] = "someone-else"
    verify = _mcp_session([
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "verify_receipts", "arguments": {"bundle": bundle}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "verify_receipts", "arguments": bundle}},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "verify_receipts", "arguments": {"bundle": tampered}}},
    ], data_dir)
    v = {r["id"]: r for r in verify}
    check("M11. verify_receipts passes the appended bundle",
          _payload(v[1]) == {"ok": True, "detail": 2}, str(_payload(v[1])))
    check("M12. verify_receipts accepts the bundle as bare arguments (REST parity)",
          _payload(v[2]) == {"ok": True, "detail": 2})
    bad = _payload(v[3])
    check("M13. tampered bundle fails at index 0 with a reason",
          bad["ok"] is False and bad["detail"][0] == 0, str(bad))

    key_file = data_dir / "receipts-key.json"
    check("M14. standalone key lives in RECEIPTS_DATA_DIR, mode 0600",
          key_file.exists()
          and stat.S_IMODE(os.stat(key_file).st_mode) == 0o600)
    check("M15. standalone chain persisted across sessions",
          json.loads((data_dir / "receipts-bundle.json")
                     .read_text())["count"] == 2)

    # The hosted harness passes its own recorder; prove the hook is used.
    spec = importlib.util.spec_from_file_location("receipts_mcp_suite",
                                                  RECEIPTS_MCP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []
    chain = receipts.ReceiptChain(receipts.Keypair(os.urandom(32)))

    def recorder(actor, act, status):
        calls.append(actor)
        receipt = chain.append(actor, act, status)
        b = chain.to_bundle()
        ok, detail = receipts.verify_bundle(b)
        return {"receipt": receipt, "bundle": b, "bundle_ok": ok,
                "bundle_detail": detail}

    result = module.call_tool("append_receipt", {
        "actor": "hooked", "action": action, "result_status": 1,
    }, recorder=recorder)
    check("M16. call_tool routes append through a supplied recorder",
          calls == ["hooked"] and result["isError"] is False
          and json.loads(result["content"][0]["text"])["bundle"]["count"] == 1)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="receipts-suite-") as tmp:
        workdir = Path(tmp)
        test_crypto()
        test_chain(workdir)
        test_mcp(workdir)
    passed = sum(1 for _, ok in RESULTS if ok)
    print()
    print(f"{passed}/{len(RESULTS)} checks passed")
    failures = [name for name, ok in RESULTS if not ok]
    if failures:
        print("FAILURES:")
        for name in failures:
            print(f"  - {name}")
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
