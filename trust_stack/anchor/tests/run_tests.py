#!/usr/bin/env python3
"""Anchor test suite — grammar, signed-registry verification, tamper
detection, resolution, the boundary test, and an MCP smoke test.

The boundary test (section B) is a first-class part of this suite: it
scans anchor.py's source and FAILS the build if the sealed core leaks
in — any allocation vocabulary in function names or anywhere in the
source, any mutation of a loaded registry's entries, or any code
constructing a registry entry. The absence of issuance machinery is a
tested property of the public layer, not an omission.

Standard library only. Run: python3 tests/run_tests.py
Exit code 0 iff every check passes.
"""

from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ANCHOR_DIR = os.path.dirname(HERE)
ANCHOR_PY = os.path.join(ANCHOR_DIR, "anchor.py")
ANCHOR_MCP_PY = os.path.join(ANCHOR_DIR, "anchor_mcp.py")
REGISTRY_JSON = os.path.join(ANCHOR_DIR, "registry.json")
SOURCE_JSON = os.path.join(ANCHOR_DIR, "demo-registry-source.json")
DEMO_KEY = os.path.join(ANCHOR_DIR, "DEMO-KEY-not-production.json")

sys.path.insert(0, ANCHOR_DIR)
import anchor  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}"
          + (f" — {detail}" if detail else ""))


def run_cli(*args: str) -> tuple[int, str]:
    proc = subprocess.run([sys.executable, ANCHOR_PY, *args],
                          capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout


def load_demo_registry() -> dict:
    with open(REGISTRY_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


# =====================================================================
# G — grammar
# =====================================================================

def test_grammar() -> None:
    trio = {
        "/api/mf/03/cart_checkout": "cart_checkout",
        "/api/mf/03/delivery_final_proof": "delivery_final_proof",
        "/api/mf/03/dispute_resolve": "dispute_resolve",
    }
    for address, tool in trio.items():
        parsed = anchor.parse_address(address)
        check(f"G parse {address}",
              parsed["router"] == "mf" and parsed["block"] == "mf/03"
              and parsed["block_number"] == 3 and parsed["tool"] == tool
              and parsed["canonical"] == address,
              f"router=mf block=mf/03 nn=3 tool={tool}")

    # Canonicalization (SPEC C1): case is normalized, not an error.
    rep = anchor.validate_address("/API/MF/03/Cart_Checkout")
    check("G case variant canonicalizes",
          rep["valid"] and rep["canonical"] == "/api/mf/03/cart_checkout"
          and rep["input_is_canonical"] is False,
          f"canonical={rep['canonical']}")
    rep2 = anchor.validate_address("/api/mf/03/cart_checkout")
    check("G canonical input is canonical",
          rep2["valid"] and rep2["input_is_canonical"] is True)

    invalid = [
        ("/api/mf/3/cart_checkout", "R-BLOCKNN", "one-digit block number"),
        ("/api/mf/003/cart_checkout", "R-BLOCKNN", "three-digit block"),
        ("/api/mf/03", "R-SEGMENTS", "missing tool segment"),
        ("/api/mf/03/cart-checkout", "R-TOOL", "illegal character '-'"),
        ("/api/mf/03/cart_checkout/", "R-TRAILING", "trailing slash"),
        ("/api/mfx/03/cart_checkout", "R-ROUTER", "three-letter router"),
        ("/api/mf//cart_checkout", "R-EMPTY", "empty segment"),
        ("api/mf/03/cart_checkout", "R-ROOT", "missing leading slash"),
        ("/api/mf/03/cart checkout", "R-FORM", "whitespace in address"),
    ]
    for address, rule, label in invalid:
        rep = anchor.validate_address(address)
        check(f"G invalid: {label}",
              (not rep["valid"]) and rep["rule"] == rule,
              f"{address!r} -> {rep['rule']}")


# =====================================================================
# R — registry verification + tamper
# =====================================================================

def test_registry() -> None:
    registry = load_demo_registry()
    report = anchor.verify_registry(registry)
    names = {c["check"]: c["ok"] for c in report["checks"]}
    check("R demo registry verifies (module)",
          report["ok"] and len(report["checks"]) == 7,
          f"checks={sorted(names)}")
    code, out = run_cli("verify-registry", REGISTRY_JSON)
    check("R demo registry verifies (CLI)", code == 0 and "PASS" in out)

    # Tamper 1: rename one entry's tool -> the signature must fail.
    tampered = copy.deepcopy(registry)
    tampered["entries"][0]["tool"] = "renamed_tool"
    rep = anchor.verify_registry(tampered)
    sig = next(c for c in rep["checks"] if c["check"] == "signature")
    check("R tampered tool name breaks signature",
          (not rep["ok"]) and (not sig["ok"]),
          "signature check FAILs on altered body")

    # Tamper 2: a case-variant duplicate of an existing entry -> the
    # duplicate check must fail, naming the canonical address.
    dup = copy.deepcopy(registry)
    dup["entries"].append({"address": "/api/MF/03/CART_CHECKOUT",
                           "tool": "cart_checkout", "status": "active"})
    rep = anchor.verify_registry(dup)
    dupcheck = next(c for c in rep["checks"]
                    if c["check"] == "no_duplicate_addresses")
    check("R case-variant duplicate detected",
          (not rep["ok"]) and (not dupcheck["ok"])
          and "/api/mf/03/cart_checkout" in dupcheck["detail"],
          dupcheck["detail"])

    # Tamper 3: illegal status.
    bad = copy.deepcopy(registry)
    bad["entries"][1]["status"] = "pending"
    rep = anchor.verify_registry(bad)
    st = next(c for c in rep["checks"] if c["check"] == "statuses_legal")
    check("R illegal status detected", not st["ok"], st["detail"])

    # Round-trip: re-signing the hand-authored source reproduces a
    # registry that verifies. The signing key is ephemeral: a fresh
    # receipts.Keypair over random bytes, written only to a temp file
    # (0600) for the CLI and deleted with it. DEMO-KEY-not-production.json
    # stays withheld and is not read.
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "roundtrip.json")
        key_path = os.path.join(tmp, "ephemeral-key.json")
        anchor.receipts_mod.Keypair.load_or_create(key_path)
        code, _ = run_cli("sign-registry", SOURCE_JSON,
                          "--key", key_path, "--out", out_path)
        ok = code == 0
        if ok:
            with open(out_path, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
            ok = anchor.verify_registry(doc)["ok"]
        check("R sign-registry round-trip verifies", ok)


# =====================================================================
# V — verify-address resolution
# =====================================================================

def test_resolution() -> None:
    registry = load_demo_registry()
    for address, tool in [
            ("/api/mf/03/cart_checkout", "cart_checkout"),
            ("/api/mf/03/delivery_final_proof", "delivery_final_proof"),
            ("/api/mf/03/dispute_resolve", "dispute_resolve")]:
        res = anchor.verify_address(registry, address)
        check(f"V ISSUED {address}",
              res["verdict"] == "ISSUED" and res["tool"] == tool
              and res["status"] == "active")
    res = anchor.verify_address(registry, "/api/mf/03/never_issued")
    check("V never-issued slug -> NOT ISSUED",
          res["verdict"] == "NOT ISSUED")
    res = anchor.verify_address(registry, "/api/mf/04/cart_checkout")
    check("V same slug, different block -> NOT ISSUED (not an error)",
          res["verdict"] == "NOT ISSUED")
    res = anchor.verify_address(registry, "/API/MF/03/CART_CHECKOUT")
    check("V uppercase query canonicalizes -> ISSUED",
          res["verdict"] == "ISSUED" and res["tool"] == "cart_checkout")
    res = anchor.verify_address(registry, "/api/mf/3/cart_checkout")
    check("V grammar-invalid query -> INVALID_ADDRESS R-BLOCKNN",
          res["verdict"] == "INVALID_ADDRESS"
          and res["rule"] == "R-BLOCKNN")
    code, out = run_cli("verify-address", REGISTRY_JSON,
                         "/api/mf/03/never_issued")
    check("V CLI NOT ISSUED exits 0", code == 0 and "NOT ISSUED" in out)
    # An unverifiable registry resolves nothing.
    forged = copy.deepcopy(registry)
    forged["entries"][0]["tool"] = "forged"
    res = anchor.verify_address(forged, "/api/mf/03/cart_checkout")
    check("V forged registry -> REGISTRY_INVALID, resolves nothing",
          res["verdict"] == "REGISTRY_INVALID")


# =====================================================================
# B — the boundary test: the sealed core is absent, provably
# =====================================================================

FORBIDDEN = ("allocat", "assign", "next_free", "pick", "choos")


def test_boundary() -> None:
    with open(ANCHOR_PY, "r", encoding="utf-8") as fh:
        source = fh.read()
    lowered = source.lower()

    hits = [tok for tok in FORBIDDEN if tok in lowered]
    check("B no allocation vocabulary anywhere in anchor.py source",
          not hits, f"found: {hits}" if hits else "clean")

    tree = ast.parse(source)
    bad_defs = [node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(tok in node.name.lower() for tok in FORBIDDEN)]
    check("B no function name carries allocation vocabulary",
          not bad_defs, f"offenders: {bad_defs}" if bad_defs else "clean")

    mutations = [pat for pat in ("entries.append", "entries.insert",
                                 "entries.extend", "entries +=")
                 if pat in source]
    check("B no code path mutates a registry's entry list",
          not mutations,
          f"found: {mutations}" if mutations else "entries are read-only")

    entry_shaped = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            keys = {k.value for k in node.keys
                    if isinstance(k, ast.Constant)
                    and isinstance(k.value, str)}
            if {"address", "status"} <= keys:
                entry_shaped.append(sorted(keys))
    check("B no code constructs a registry entry",
          not entry_shaped,
          f"entry-shaped dicts: {entry_shaped}" if entry_shaped
          else "entries only load from data")


# =====================================================================
# M — MCP smoke
# =====================================================================

def test_mcp() -> None:
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "anchor-tests",
                                   "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "verify_address",
                    "arguments": {"address": "/api/mf/03/cart_checkout"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "registry_info", "arguments": {}}},
    ]
    proc = subprocess.run(
        [sys.executable, ANCHOR_MCP_PY],
        input="\n".join(json.dumps(r) for r in requests) + "\n",
        capture_output=True, text=True, timeout=60)
    by_id = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            msg = json.loads(line)
            if "id" in msg:
                by_id[msg["id"]] = msg
    init = by_id.get(1, {}).get("result", {})
    check("M initialize answers protocol 2025-11-25",
          init.get("protocolVersion") == "2025-11-25"
          and init.get("serverInfo", {}).get("name") == "anchor")
    tools = by_id.get(2, {}).get("result", {}).get("tools", [])
    names = sorted(t["name"] for t in tools)
    check("M tools/list returns the three Anchor tools",
          names == ["parse_address", "registry_info", "verify_address"],
          f"tools={names}")
    call = by_id.get(3, {}).get("result", {})
    text = call.get("content", [{}])[0].get("text", "")
    payload = json.loads(text) if text else {}
    check("M verify_address call -> ISSUED cart_checkout",
          payload.get("verdict") == "ISSUED"
          and payload.get("tool") == "cart_checkout"
          and call.get("isError") is False)
    info = json.loads(by_id.get(4, {}).get("result", {})
                      .get("content", [{}])[0].get("text", "{}"))
    check("M registry_info -> demo registry, 3 entries, verifies",
          info.get("registry_id") == "anchor-demo-registry"
          and info.get("entries") == 3 and info.get("verifies") is True)


def main() -> int:
    test_grammar()
    test_registry()
    test_resolution()
    test_boundary()
    test_mcp()
    failed = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILURES:")
        for name, _, detail in failed:
            print(f"  - {name}: {detail}")
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
