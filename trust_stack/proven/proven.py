#!/usr/bin/env python3
"""Proven — live-probe directory for MCP servers.

Proven CALLS listed MCP servers (initialize + tools/list, never tools/call)
and publishes what it measures — tool count, schema fingerprint, protocol
version, initialize latency — beside the author's claims, never blended
with them. Measurements are timestamped snapshots; a mismatch reads
"claimed X, measured Y at time T".

Usage:
    proven.py probe <name>        probe one server, append to its history
    proven.py probe-all           probe every registered server
    proven.py drift <name>        diff the latest two probes of a server
    proven.py facts <name>        latest measured vs claimed, with verdict
    proven.py build-site          regenerate site/ (index + per-server pages)
    proven.py badge <name>        regenerate site/badges/<name>.svg

Data lives beside this file (registry.json, probes/, site/) unless
PROVEN_DIR points elsewhere (used by the tests to stay hermetic).

Standard library only. Python 3.12.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import queue
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

CLIENT_PROTOCOL = "2025-11-25"
CLIENT_INFO = {"name": "proven", "version": "1.0.0"}
USER_AGENT = "Proven/1.0.0 (live-probe directory; +https://github.com/DavidFox998)"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
HTTP_TIMEOUT = 60


# --------------------------------------------------------------------------
# Paths / registry / history
# --------------------------------------------------------------------------

def data_root() -> str:
    return os.environ.get("PROVEN_DIR") or SCRIPT_DIR


def registry_path() -> str:
    return os.path.join(data_root(), "registry.json")


def probes_dir() -> str:
    return os.path.join(data_root(), "probes")


def site_dir() -> str:
    return os.path.join(data_root(), "site")


def load_registry() -> dict:
    with open(registry_path(), encoding="utf-8") as fh:
        return json.load(fh)


def registry_entry(name: str) -> dict:
    for entry in load_registry().get("servers", []):
        if entry.get("name") == name:
            return entry
    raise KeyError(f"no registry entry named {name!r}")


def load_probes(name: str) -> list[dict]:
    path = os.path.join(probes_dir(), f"{name}.jsonl")
    if not os.path.exists(path):
        return []
    records = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_probe(record: dict) -> str:
    os.makedirs(probes_dir(), exist_ok=True)
    path = os.path.join(probes_dir(), f"{record['name']}.jsonl")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------

def schema_fingerprint(tools: list[dict]) -> str:
    """SHA-256 over canonical JSON of sorted {name, description, inputSchema}."""
    entries = [
        {
            "name": t.get("name", ""),
            "description": t.get("description", ""),
            "inputSchema": t.get("inputSchema", {}),
        }
        for t in tools
    ]
    entries.sort(key=lambda e: e["name"])
    blob = json.dumps(entries, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------
# HTTP (streamable) transport
# --------------------------------------------------------------------------

def _http_post(url: str, payload: dict | None, headers: dict) -> tuple[int, object, str]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    req.add_header("User-Agent", USER_AGENT)
    for key, value in headers.items():
        req.add_header(key, value)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", "replace")
        return resp.status, resp.headers, body


def _parse_rpc_body(body: str, content_type: str | None) -> dict | None:
    """Extract the JSON-RPC response object from a plain-JSON or SSE body."""
    if content_type and "text/event-stream" in content_type:
        found = None
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and ("result" in msg or "error" in msg):
                    found = msg
        return found
    try:
        msg = json.loads(body)
    except json.JSONDecodeError:
        return None
    return msg if isinstance(msg, dict) else None


def probe_http(entry: dict, record: dict) -> dict:
    url = entry["url"]
    init_payload = {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": CLIENT_PROTOCOL, "capabilities": {},
                   "clientInfo": CLIENT_INFO},
    }
    started = time.perf_counter()
    _status, headers, body = _http_post(url, init_payload, {})
    record["latency_ms"] = round((time.perf_counter() - started) * 1000)
    init_resp = _parse_rpc_body(body, headers.get("Content-Type"))
    if not init_resp or "result" not in init_resp:
        detail = ""
        if init_resp and "error" in init_resp:
            detail = f": {init_resp['error']}"
        raise RuntimeError(f"initialize returned no result{detail}")

    result = init_resp["result"]
    record["protocolVersion"] = result.get("protocolVersion")
    record["serverInfo"] = result.get("serverInfo")

    follow_headers: dict[str, str] = {}
    session_id = headers.get("Mcp-Session-Id")
    if session_id:
        follow_headers["Mcp-Session-Id"] = session_id
        record["session_id_issued"] = True
    if record["protocolVersion"]:
        follow_headers["MCP-Protocol-Version"] = record["protocolVersion"]

    # notifications/initialized — response (usually 202/empty) is ignored.
    try:
        _http_post(url, {"jsonrpc": "2.0", "method": "notifications/initialized"},
                   follow_headers)
    except Exception:
        pass

    tools: list[dict] = []
    cursor = None
    req_id = 2
    while True:
        params = {} if cursor is None else {"cursor": cursor}
        _s, h, b = _http_post(url, {"jsonrpc": "2.0", "id": req_id,
                                    "method": "tools/list", "params": params},
                              follow_headers)
        resp = _parse_rpc_body(b, h.get("Content-Type"))
        if not resp or "result" not in resp:
            detail = ""
            if resp and "error" in resp:
                detail = f": {resp['error']}"
            raise RuntimeError(f"tools/list returned no result{detail}")
        tools.extend(resp["result"].get("tools", []))
        cursor = resp["result"].get("nextCursor")
        req_id += 1
        if not cursor:
            break

    record["tools"] = [t.get("name", "") for t in tools]
    record["tool_count"] = len(tools)
    record["fingerprint"] = schema_fingerprint(tools)
    record["reachable"] = True
    return record


# --------------------------------------------------------------------------
# stdio transport
# --------------------------------------------------------------------------

class StdioClient:
    """Newline-delimited JSON-RPC over a spawned process's stdio."""

    def __init__(self, command: list[str]):
        self.proc = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, cwd=SCRIPT_DIR)
        self._queue: queue.Queue = queue.Queue()
        self._next_id = 0
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        try:
            for line in self.proc.stdout:  # type: ignore[union-attr]
                line = line.strip()
                if not line:
                    continue
                try:
                    self._queue.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass

    def request(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        self._next_id += 1
        mid = self._next_id
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"stdio server did not answer {method} in {timeout}s")
            msg = self._queue.get(timeout=remaining)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method} error: {msg['error']}")
                return msg.get("result") or {}

    def notify(self, method: str) -> None:
        self._send({"jsonrpc": "2.0", "method": method})

    def _send(self, msg: dict) -> None:
        self.proc.stdin.write(json.dumps(msg) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


def _resolve_command(command: list[str]) -> list[str]:
    """Resolve relative script paths in a registry command against SCRIPT_DIR."""
    resolved = []
    for token in command:
        if (not os.path.isabs(token) and ("/" in token or token.endswith(".py"))):
            resolved.append(os.path.normpath(os.path.join(SCRIPT_DIR, token)))
        else:
            resolved.append(token)
    return resolved


def probe_stdio(entry: dict, record: dict) -> dict:
    client = StdioClient(_resolve_command(entry["command"]))
    try:
        started = time.perf_counter()
        result = client.request("initialize", {
            "protocolVersion": CLIENT_PROTOCOL, "capabilities": {},
            "clientInfo": CLIENT_INFO})
        record["latency_ms"] = round((time.perf_counter() - started) * 1000)
        record["protocolVersion"] = result.get("protocolVersion")
        record["serverInfo"] = result.get("serverInfo")
        client.notify("notifications/initialized")

        tools: list[dict] = []
        cursor = None
        while True:
            params = {} if cursor is None else {"cursor": cursor}
            page = client.request("tools/list", params)
            tools.extend(page.get("tools", []))
            cursor = page.get("nextCursor")
            if not cursor:
                break

        record["tools"] = [t.get("name", "") for t in tools]
        record["tool_count"] = len(tools)
        record["fingerprint"] = schema_fingerprint(tools)
        record["reachable"] = True
        return record
    finally:
        client.close()


# --------------------------------------------------------------------------
# Probing / drift / facts
# --------------------------------------------------------------------------

def probe_entry(entry: dict) -> dict:
    record: dict = {
        "ts": utc_now(),
        "name": entry["name"],
        "transport": entry.get("transport"),
        "reachable": False,
        "protocolVersion": None,
        "serverInfo": None,
        "tool_count": None,
        "tools": None,
        "fingerprint": None,
        "latency_ms": None,
        "error": None,
    }
    try:
        if entry.get("transport") == "http":
            probe_http(entry, record)
        elif entry.get("transport") == "stdio":
            probe_stdio(entry, record)
        else:
            raise RuntimeError(f"unknown transport: {entry.get('transport')!r}")
    except urllib.error.HTTPError as exc:
        record["error"] = f"HTTP {exc.code} {exc.reason}"
    except Exception as exc:  # a probe reports failure; it never crashes the run
        record["error"] = f"{type(exc).__name__}: {exc}"
    return record


def probe_server(name: str) -> dict:
    record = probe_entry(registry_entry(name))
    append_probe(record)
    return record


def compute_drift(name: str) -> dict:
    probes = load_probes(name)
    if len(probes) < 2:
        return {"name": name, "probes": len(probes),
                "error": "need at least two probes to compute drift"}
    old, new = probes[-2], probes[-1]
    old_names = set(old.get("tools") or [])
    new_names = set(new.get("tools") or [])
    return {
        "name": name,
        "from_ts": old.get("ts"),
        "to_ts": new.get("ts"),
        "added": sorted(new_names - old_names),
        "removed": sorted(old_names - new_names),
        "count_delta": (new.get("tool_count") or 0) - (old.get("tool_count") or 0),
        "fingerprint_changed": old.get("fingerprint") != new.get("fingerprint"),
        "old_fingerprint": old.get("fingerprint"),
        "new_fingerprint": new.get("fingerprint"),
    }


def get_facts(name: str) -> dict:
    entry = registry_entry(name)
    claimed = (entry.get("claimed") or {}).get("tools")
    probes = load_probes(name)
    latest = probes[-1] if probes else None
    facts = {
        "name": name,
        "transport": entry.get("transport"),
        "endpoint": entry.get("url") or " ".join(entry.get("command") or []),
        "claimed_tools": claimed,
        "claimed_note": (entry.get("claimed") or {}).get("note"),
        "measured_tools": None,
        "protocolVersion": None,
        "latency_ms": None,
        "fingerprint": None,
        "last_probe_ts": None,
        "verdict": "UNPROBED",
    }
    if latest is not None:
        facts["last_probe_ts"] = latest.get("ts")
        facts["protocolVersion"] = latest.get("protocolVersion")
        facts["latency_ms"] = latest.get("latency_ms")
        facts["fingerprint"] = latest.get("fingerprint")
        if not latest.get("reachable"):
            facts["verdict"] = "UNREACHABLE"
            facts["error"] = latest.get("error")
        else:
            facts["measured_tools"] = latest.get("tool_count")
            if claimed is None:
                facts["verdict"] = "NO_CLAIM"
            elif claimed == facts["measured_tools"]:
                facts["verdict"] = "MATCH"
            else:
                facts["verdict"] = "MISMATCH"
    return facts


# --------------------------------------------------------------------------
# Badge
# --------------------------------------------------------------------------

def _text_width(text: str, per_char: float = 6.8) -> int:
    return int(round(len(text) * per_char)) + 12


def badge_svg(name: str) -> str:
    facts = get_facts(name)
    label = "PROVEN"
    if facts["verdict"] == "MATCH":
        date = (facts["last_probe_ts"] or "")[:10]
        value = f"measured {facts['measured_tools']} tools · {date}"
        color = "#2ea44f"
    elif facts["verdict"] == "MISMATCH":
        value = f"claimed {facts['claimed_tools']} ≠ measured {facts['measured_tools']}"
        color = "#d73a49"
    elif facts["verdict"] == "UNREACHABLE":
        value = "unreachable at last probe"
        color = "#e07b24"
    else:
        value = "not probed"
        color = "#6a737d"
    lw = _text_width(label)
    vw = _text_width(value)
    total = lw + vw
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" '
        f'role="img" aria-label="{html.escape(label)}: {html.escape(value)}">'
        f"<title>{html.escape(label)}: {html.escape(value)}</title>"
        f'<rect width="{lw}" height="20" fill="#555"/>'
        f'<rect x="{lw}" width="{vw}" height="20" fill="{color}"/>'
        f'<g fill="#fff" font-family="Verdana,DejaVu Sans,sans-serif" font-size="11">'
        f'<text x="{lw / 2}" y="14" text-anchor="middle">{html.escape(label)}</text>'
        f'<text x="{lw + vw / 2}" y="14" text-anchor="middle">{html.escape(value)}</text>'
        f"</g></svg>"
    )


def write_badge(name: str) -> str:
    badge_dir = os.path.join(site_dir(), "badges")
    os.makedirs(badge_dir, exist_ok=True)
    path = os.path.join(badge_dir, f"{name}.svg")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(badge_svg(name))
    return path


# --------------------------------------------------------------------------
# Static site
# --------------------------------------------------------------------------

_CSS = """
body { background:#0d1117; color:#e6edf3; font-family:Verdana,DejaVu Sans,sans-serif;
       margin:2em auto; max-width:960px; padding:0 1em; }
a { color:#58a6ff; } table { border-collapse:collapse; width:100%; }
th, td { border:1px solid #30363d; padding:6px 10px; text-align:left; font-size:14px; }
th { background:#161b22; } code { color:#a5d6ff; }
.match { color:#3fb950; } .mismatch { color:#f85149; } .dim { color:#8b949e; }
"""


def _verdict_cell(facts: dict) -> str:
    cls = {"MATCH": "match", "MISMATCH": "mismatch"}.get(facts["verdict"], "dim")
    return f'<span class="{cls}">{html.escape(facts["verdict"])}</span>'


def build_site() -> list[str]:
    os.makedirs(site_dir(), exist_ok=True)
    entries = load_registry().get("servers", [])
    rows = []
    written = []
    for entry in entries:
        name = entry["name"]
        facts = get_facts(name)
        rows.append((facts["last_probe_ts"] or "", name, facts))
    rows.sort(key=lambda r: r[0], reverse=True)  # most recently probed first

    trs = []
    for _ts, name, facts in rows:
        claimed = facts["claimed_tools"]
        measured = facts["measured_tools"]
        trs.append(
            "<tr>"
            f'<td><a href="{html.escape(name)}.html">{html.escape(name)}</a></td>'
            f"<td>{html.escape(facts['transport'] or '')}</td>"
            f"<td>{'—' if claimed is None else claimed}</td>"
            f"<td>{'—' if measured is None else measured}</td>"
            f"<td>{_verdict_cell(facts)}</td>"
            f"<td>{html.escape(facts['last_probe_ts'] or 'never')}</td>"
            "</tr>")
    index = (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>Proven — measured MCP directory</title>"
        f"<style>{_CSS}</style></head><body>"
        "<h1>Proven — measured, not claimed</h1>"
        "<p>Every number below comes from a live probe (initialize + tools/list) "
        "run by Proven against the server itself, shown beside the author's "
        "claim — never blended. Latency is a single sample from one region.</p>"
        "<table><tr><th>Server</th><th>Transport</th><th>Claimed tools</th>"
        "<th>Measured tools</th><th>Verdict</th><th>Last probe (UTC)</th></tr>"
        + "".join(trs) +
        "</table></body></html>")
    index_path = os.path.join(site_dir(), "index.html")
    with open(index_path, "w", encoding="utf-8") as fh:
        fh.write(index)
    written.append(index_path)

    for entry in entries:
        name = entry["name"]
        facts = get_facts(name)
        probes = load_probes(name)
        latest = probes[-1] if probes else None
        drift = compute_drift(name) if len(probes) >= 2 else None

        parts = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>Proven — {html.escape(name)}</title>",
            f"<style>{_CSS}</style></head><body>",
            f"<h1>{html.escape(name)}</h1>",
            f"<p><a href='index.html'>← directory</a> · "
            f"<code>{html.escape(facts['endpoint'])}</code></p>",
            "<h2>Claimed vs measured</h2>",
            "<table><tr><th></th><th>Tools</th></tr>",
            f"<tr><td>Claimed (author)</td><td>{facts['claimed_tools'] if facts['claimed_tools'] is not None else '—'}</td></tr>",
            f"<tr><td>Measured (Proven probe)</td><td>{facts['measured_tools'] if facts['measured_tools'] is not None else '—'}</td></tr>",
            "</table>",
            f"<p>Verdict: {_verdict_cell(facts)}"
            + (f" · claim note: {html.escape(facts['claimed_note'])}"
               if facts["claimed_note"] else "")
            + "</p>",
        ]
        if latest:
            parts += [
                "<h2>Latest probe</h2>",
                f"<p>At {html.escape(latest.get('ts') or '')} UTC · protocol "
                f"{html.escape(latest.get('protocolVersion') or '—')} · initialize "
                f"latency {latest.get('latency_ms')} ms (single sample) · "
                f"fingerprint <code>{html.escape((latest.get('fingerprint') or '—')[:16])}…</code></p>",
            ]
            if latest.get("tools"):
                tool_items = "".join(
                    f"<li><code>{html.escape(t)}</code></li>" for t in latest["tools"])
                parts.append(f"<details><summary>{len(latest['tools'])} tools</summary>"
                             f"<ul>{tool_items}</ul></details>")
        if drift and "error" not in drift:
            parts += [
                "<h2>Drift vs previous probe</h2>",
                f"<p>Count delta {drift['count_delta']:+d} · fingerprint "
                f"{'changed' if drift['fingerprint_changed'] else 'unchanged'}<br>"
                f"Added: {', '.join(map(html.escape, drift['added'])) or '—'}<br>"
                f"Removed: {', '.join(map(html.escape, drift['removed'])) or '—'}</p>",
            ]
        if probes:
            hist = []
            for p in reversed(probes):
                hist.append(
                    "<tr>"
                    f"<td>{html.escape(p.get('ts') or '')}</td>"
                    f"<td>{p.get('tool_count') if p.get('tool_count') is not None else '—'}</td>"
                    f"<td>{p.get('latency_ms') if p.get('latency_ms') is not None else '—'}</td>"
                    f"<td><code>{html.escape((p.get('fingerprint') or '—')[:12])}</code></td>"
                    f"<td>{'ok' if p.get('reachable') else html.escape(p.get('error') or 'unreachable')}</td>"
                    "</tr>")
            parts += [
                "<h2>Probe history</h2>",
                "<table><tr><th>Time (UTC)</th><th>Tools</th><th>Latency ms</th>"
                "<th>Fingerprint</th><th>Status</th></tr>",
                "".join(hist), "</table>",
            ]
        parts.append("</body></html>")
        page_path = os.path.join(site_dir(), f"{name}.html")
        with open(page_path, "w", encoding="utf-8") as fh:
            fh.write("".join(parts))
        written.append(page_path)
    return written


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _print_facts(facts: dict) -> None:
    claimed = facts["claimed_tools"]
    measured = facts["measured_tools"]
    print(f"{facts['name']}: claimed "
          f"{'—' if claimed is None else claimed} · measured "
          f"{'—' if measured is None else measured} · {facts['verdict']}")
    if facts["last_probe_ts"]:
        print(f"  last probe {facts['last_probe_ts']} UTC · protocol "
              f"{facts['protocolVersion'] or '—'} · initialize latency "
              f"{facts['latency_ms']} ms (single sample)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="proven", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("probe", "drift", "facts", "badge"):
        p = sub.add_parser(cmd)
        p.add_argument("name")
    sub.add_parser("probe-all")
    sub.add_parser("build-site")
    args = parser.parse_args(argv)

    if args.cmd == "probe":
        record = probe_server(args.name)
        path = os.path.join(probes_dir(), f"{args.name}.jsonl")
        if record["reachable"]:
            print(f"probed {args.name}: {record['tool_count']} tools · protocol "
                  f"{record['protocolVersion']} · initialize {record['latency_ms']} ms "
                  f"· fingerprint {record['fingerprint'][:12]}… → {path}")
            return 0
        print(f"probe FAILED for {args.name}: {record['error']} → {path}")
        return 1

    if args.cmd == "probe-all":
        ok = True
        for entry in load_registry().get("servers", []):
            record = probe_server(entry["name"])
            state = (f"{record['tool_count']} tools" if record["reachable"]
                     else f"UNREACHABLE ({record['error']})")
            print(f"{entry['name']}: {state}")
            ok = ok and record["reachable"]
        return 0 if ok else 1

    if args.cmd == "drift":
        print(json.dumps(compute_drift(args.name), indent=2))
        return 0

    if args.cmd == "facts":
        facts = get_facts(args.name)
        _print_facts(facts)
        return 0

    if args.cmd == "badge":
        print(write_badge(args.name))
        return 0

    if args.cmd == "build-site":
        for path in build_site():
            print(path)
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())
