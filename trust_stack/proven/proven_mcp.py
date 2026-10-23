#!/usr/bin/env python3
"""Proven MCP server — dependency-free, hand-rolled JSON-RPC over stdio.

Protocol: MCP 2025-11-25, newline-delimited JSON-RPC 2.0 messages.
Implements: initialize, notifications/initialized, ping, tools/list,
tools/call. Exposes the Proven probe directory (see proven.py):

  - probe_server {name}   call the named server (initialize + tools/list
                          only — never tools/call) and record the probe
  - get_facts    {name}   latest measured vs claimed, with verdict
  - check_drift  {name}   diff of the latest two probes of a server

Standard library only. Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import proven  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "proven", "version": "1.0.0"}

TOOLS = [
    {
        "name": "probe_server",
        "description": "Probe a registered MCP server live: initialize + "
                       "tools/list only (never tools/call). Records tool "
                       "count, schema fingerprint, protocol version, and "
                       "initialize latency to the server's probe history.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Registry entry name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_facts",
        "description": "Latest measured facts for a registered server beside "
                       "the author's claim, with a MATCH / MISMATCH / "
                       "UNPROBED / UNREACHABLE verdict.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Registry entry name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "check_drift",
        "description": "Diff the latest two probes of a registered server: "
                       "added/removed tool names, count delta, and whether "
                       "the schema fingerprint changed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Registry entry name"},
            },
            "required": ["name"],
        },
    },
]


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tools/call and return the MCP result object."""
    try:
        if name == "probe_server":
            payload = proven.probe_server(arguments["name"])
            is_error = not payload["reachable"]
        elif name == "get_facts":
            payload = proven.get_facts(arguments["name"])
            is_error = False
        elif name == "check_drift":
            payload = proven.compute_drift(arguments["name"])
            is_error = "error" in payload
        else:
            return {"content": [{"type": "text", "text": f"unknown tool: {name}"}],
                    "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text", "text": f"proven error: {exc}"}],
                "isError": True}
    return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
            "isError": is_error}


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        result = call_tool(params.get("name"), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method is not None and method.startswith("notifications/"):
        return None  # notifications get no response
    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        response = handle(msg)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
