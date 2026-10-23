#!/usr/bin/env python3
"""Anchor MCP server — dependency-free, hand-rolled JSON-RPC over stdio.

Protocol: MCP 2025-11-25, newline-delimited JSON-RPC 2.0 messages.
Implements: initialize, notifications/initialized, ping, tools/list,
tools/call. Exposes the public verification surface of Anchor
(see anchor.py and SPEC.md):

  - parse_address {address}
        Components + canonical form, or the grammar rule violated.
  - verify_address {address, registry?}
        ISSUED (tool + status) or NOT ISSUED against a registry that
        must itself verify first. `registry` is a path to a registry
        document; defaults to the demo registry shipped beside this
        server.
  - registry_info {registry?}
        Registry id, issue time, entry counts, public key, and
        whether the registry passes full verification.

Resolution only. There is no tool here that issues, modifies, or
extends a registry — the public layer formats and verifies; issuance
is out of scope for this package (SPEC.md section 8).

Standard library only (plus the sibling anchor/receipts imports).
Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import anchor  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "anchor", "version": "1.0.0"}

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.join(_HERE, "registry.json")

TOOLS = [
    {
        "name": "parse_address",
        "description": "Parse an Anchor address (/api/<router>/<nn>/<tool>) "
                       "into router, block, block number, and tool, with "
                       "its canonical form. Invalid addresses report the "
                       "SPEC grammar rule they violate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
            },
            "required": ["address"],
        },
    },
    {
        "name": "verify_address",
        "description": "Resolve an address against a signed Anchor "
                       "registry: ISSUED with tool and status, or a "
                       "precise NOT ISSUED. The registry is verified "
                       "(signature + consistency) before any resolution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "registry": {"type": "string",
                             "description": "Path to a registry document; "
                                            "defaults to the shipped demo "
                                            "registry."},
            },
            "required": ["address"],
        },
    },
    {
        "name": "registry_info",
        "description": "Facts about an Anchor registry document: id, "
                       "issue time, entry counts by status, public key, "
                       "and full verification outcome.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "registry": {"type": "string",
                             "description": "Path to a registry document; "
                                            "defaults to the shipped demo "
                                            "registry."},
            },
        },
    },
]


def _registry_path(arguments: dict) -> str:
    return arguments.get("registry") or DEFAULT_REGISTRY


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tools/call and return the MCP result object."""
    try:
        if name == "parse_address":
            report = anchor.validate_address(arguments["address"])
            payload = report
            is_error = not report["valid"]
        elif name == "verify_address":
            registry = anchor.load_registry(_registry_path(arguments))
            payload = anchor.verify_address(registry, arguments["address"])
            is_error = payload["verdict"] not in ("ISSUED", "NOT ISSUED")
        elif name == "registry_info":
            registry = anchor.load_registry(_registry_path(arguments))
            report = anchor.verify_registry(registry)
            entries = registry.get("entries", [])
            payload = {
                "registry_id": registry.get("registry_id"),
                "issued_at": registry.get("issued_at"),
                "entries": len(entries),
                "active": sum(1 for e in entries
                              if e.get("status") == "active"),
                "retired": sum(1 for e in entries
                               if e.get("status") == "retired"),
                "public_key": registry.get("public_key"),
                "verifies": report["ok"],
                "checks": report["checks"],
            }
            is_error = not report["ok"]
        else:
            return {"content": [{"type": "text",
                                 "text": f"unknown tool: {name}"}],
                    "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text", "text": f"anchor error: {exc}"}],
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
                "error": {"code": -32601,
                          "message": f"method not found: {method}"}}
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
