#!/usr/bin/env python3
"""Proven tests — hermetic fixture checks plus one live probe.

Checks 1–6 run against a temp PROVEN_DIR with a configurable fixture stdio
MCP server (its tool set is read from a JSON file the test rewrites), plus
the real sibling Apathy and Grantline stdio servers.

Check 7 is the live subject: ZeroBeacon over HTTP, probed through the CLI
against the shipped registry and stored in probes/zerobeacon.jsonl. If the
network or server prevents it, the exact error is reported as INFO-LIVE
rather than estimated or faked; all other checks hard-fail the suite.

Standard library only. Exits non-zero if any hard check fails.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PROVEN_BUILD = os.path.dirname(HERE)                      # build/proven
BUILD_ROOT = os.path.dirname(PROVEN_BUILD)                # build/
PROVEN_PY = os.path.join(PROVEN_BUILD, "proven.py")
APATHY_MCP = os.path.join(BUILD_ROOT, "apathy", "apathy_mcp.py")
GRANTLINE_MCP = os.path.join(BUILD_ROOT, "grantline", "grantline_mcp.py")

FIXTURE_SRC = '''
import json, sys

TOOLS_PATH = sys.argv[1]

def current_tools():
    with open(TOOLS_PATH, encoding="utf-8") as fh:
        return json.load(fh)["tools"]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        out = {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}},
               "serverInfo": {"name": "fixture", "version": "0.1.0"}}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": out}) + "\\n")
        sys.stdout.flush()
    elif method == "tools/list":
        out = {"tools": current_tools()}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": out}) + "\\n")
        sys.stdout.flush()
    elif method is not None and method.startswith("notifications/"):
        pass
    elif mid is not None:
        err = {"code": -32601, "message": "method not found: %s" % method}
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": err}) + "\\n")
        sys.stdout.flush()
'''

RESULTS: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((label, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return ok


def run_cli(args: list[str], proven_dir: str | None, timeout: int = 300):
    env = dict(os.environ)
    if proven_dir is None:
        env.pop("PROVEN_DIR", None)
    else:
        env["PROVEN_DIR"] = proven_dir
    return subprocess.run([sys.executable, PROVEN_PY] + args,
                          capture_output=True, text=True, env=env,
                          cwd=PROVEN_BUILD, timeout=timeout)


def last_probe(proven_dir: str, name: str) -> dict:
    path = os.path.join(proven_dir, "probes", f"{name}.jsonl")
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh if ln.strip()]
    return json.loads(lines[-1])


def tool(name: str, desc: str) -> dict:
    return {"name": name, "description": desc,
            "inputSchema": {"type": "object", "properties": {}}}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="proven-test-")
    fixture_py = os.path.join(tmp, "fixture_mcp.py")
    tools_json = os.path.join(tmp, "fixture_tools.json")
    with open(fixture_py, "w", encoding="utf-8") as fh:
        fh.write(FIXTURE_SRC)

    def set_fixture_tools(tools: list[dict]) -> None:
        with open(tools_json, "w", encoding="utf-8") as fh:
            json.dump({"tools": tools}, fh)

    registry = {"servers": [
        {"name": "apathy", "transport": "stdio",
         "command": [sys.executable, APATHY_MCP],
         "claimed": {"tools": 3, "note": "test"}},
        {"name": "grantline", "transport": "stdio",
         "command": [sys.executable, GRANTLINE_MCP],
         "claimed": {"tools": 3, "note": "test"}},
        {"name": "fixture", "transport": "stdio",
         "command": [sys.executable, fixture_py, tools_json],
         "claimed": {"tools": 99, "note": "deliberately wrong claim"}},
    ]}
    with open(os.path.join(tmp, "registry.json"), "w", encoding="utf-8") as fh:
        json.dump(registry, fh)

    sys.path.insert(0, PROVEN_BUILD)
    os.environ["PROVEN_DIR"] = tmp
    import proven  # noqa: E402  (after PROVEN_DIR is set; it is read per call)

    # -- 1. Sibling stdio servers measure exactly 3 tools each ---------------
    run_cli(["probe", "apathy"], tmp)
    rec = last_probe(tmp, "apathy")
    check("1a. Apathy stdio probe measures exactly 3 tools",
          rec["reachable"] and rec["tool_count"] == 3,
          f"count={rec['tool_count']} tools={rec['tools']}")
    run_cli(["probe", "grantline"], tmp)
    rec = last_probe(tmp, "grantline")
    check("1b. Grantline stdio probe measures exactly 3 tools",
          rec["reachable"] and rec["tool_count"] == 3,
          f"count={rec['tool_count']} tools={rec['tools']}")

    # -- 2. Fixture probe #1: 2 tools; claim 99 -> MISMATCH -------------------
    set_fixture_tools([tool("alpha", "first"), tool("beta", "second")])
    run_cli(["probe", "fixture"], tmp)
    rec = last_probe(tmp, "fixture")
    facts = proven.get_facts("fixture")
    check("2a. Fixture probe #1 measures 2 tools",
          rec["reachable"] and rec["tool_count"] == 2,
          f"count={rec['tool_count']}")
    check("2b. Facts verdict is MISMATCH (claimed 99 vs measured 2)",
          facts["verdict"] == "MISMATCH" and facts["claimed_tools"] == 99
          and facts["measured_tools"] == 2,
          f"verdict={facts['verdict']}")

    # -- 3. Rename one, add one -> drift --------------------------------------
    set_fixture_tools([tool("beta", "second"), tool("gamma", "renamed alpha"),
                       tool("delta", "newly added")])
    run_cli(["probe", "fixture"], tmp)
    drift = proven.compute_drift("fixture")
    check("3a. Drift added is exactly [delta, gamma]",
          drift["added"] == ["delta", "gamma"], f"added={drift['added']}")
    check("3b. Drift removed is exactly [alpha]",
          drift["removed"] == ["alpha"], f"removed={drift['removed']}")
    check("3c. Drift count_delta is +1 and fingerprint changed",
          drift["count_delta"] == 1 and drift["fingerprint_changed"] is True,
          f"delta={drift['count_delta']}")

    # -- 4. No change -> stable fingerprint, empty drift ----------------------
    run_cli(["probe", "fixture"], tmp)
    rec3 = last_probe(tmp, "fixture")
    probes = proven.load_probes("fixture")
    drift = proven.compute_drift("fixture")
    check("4a. Fingerprint stable across unchanged probes",
          probes[-1]["fingerprint"] == probes[-2]["fingerprint"]
          and rec3["fingerprint"] == probes[-2]["fingerprint"],
          f"fp={rec3['fingerprint'][:12]}…")
    check("4b. Drift is empty (no added/removed, delta 0, fp unchanged)",
          drift["added"] == [] and drift["removed"] == []
          and drift["count_delta"] == 0 and drift["fingerprint_changed"] is False,
          f"drift={drift['added']}/{drift['removed']}/{drift['count_delta']}")

    # -- 5. Badge: mismatch variant, then flips when the claim is fixed ------
    run_cli(["badge", "fixture"], tmp)
    svg_path = os.path.join(tmp, "site", "badges", "fixture.svg")
    with open(svg_path, encoding="utf-8") as fh:
        svg = fh.read()
    check("5a. Badge shows the mismatch state (claimed 99 ≠ measured 3)",
          "claimed 99" in svg and "≠" in svg and "measured 3" in svg,
          "mismatch variant")
    registry["servers"][2]["claimed"]["tools"] = 3
    with open(os.path.join(tmp, "registry.json"), "w", encoding="utf-8") as fh:
        json.dump(registry, fh)
    run_cli(["badge", "fixture"], tmp)
    with open(svg_path, encoding="utf-8") as fh:
        svg = fh.read()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    check("5b. Badge flips to the matching variant once the claim is corrected",
          "measured 3 tools" in svg and "≠" not in svg and today in svg,
          f"match variant dated {today}")

    # -- 6. Site builds with the claimed-vs-measured row -----------------------
    run_cli(["build-site"], tmp)
    with open(os.path.join(tmp, "site", "index.html"), encoding="utf-8") as fh:
        index_html = fh.read()
    check("6. Site index contains the fixture claimed-vs-measured row",
          "fixture" in index_html and "MATCH" in index_html,
          "index.html built")

    # -- 7. LIVE: ZeroBeacon over HTTP via the shipped registry ----------------
    proc = run_cli(["probe", "zerobeacon"], None, timeout=240)
    zb_path = os.path.join(PROVEN_BUILD, "probes", "zerobeacon.jsonl")
    if os.path.exists(zb_path):
        with open(zb_path, encoding="utf-8") as fh:
            zb = json.loads([ln for ln in fh if ln.strip()][-1])
        if zb.get("reachable"):
            print(f"[PASS] 7. LIVE ZeroBeacon probe — measured {zb['tool_count']} "
                  f"tools · protocol {zb['protocolVersion']} · initialize "
                  f"latency {zb['latency_ms']} ms · fingerprint "
                  f"{zb['fingerprint'][:12]}…")
            RESULTS.append(("7-live", True, ""))
        else:
            print(f"[INFO-LIVE] 7. ZeroBeacon probe attempted but not reachable: "
                  f"{zb.get('error')} (CLI said: {proc.stdout.strip()}) — "
                  f"reported, not estimated.")
    else:
        print(f"[INFO-LIVE] 7. ZeroBeacon probe produced no record. "
              f"stdout={proc.stdout.strip()} stderr={proc.stderr.strip()[:300]}")

    hard_failures = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(hard_failures)}/{len(RESULTS)} hard checks passed")
    if hard_failures:
        print("FAILURES:", [r[0] for r in hard_failures])
        return 1
    print("ALL HARD CHECKS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
