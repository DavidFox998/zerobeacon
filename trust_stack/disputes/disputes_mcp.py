#!/usr/bin/env python3
"""Disputes MCP server — dependency-free, hand-rolled JSON-RPC over stdio.

Protocol: MCP 2025-11-25, newline-delimited JSON-RPC 2.0 messages.
Implements: initialize, notifications/initialized, ping, tools/list,
tools/call. Exposes one tool, the MCP mirror of the hosted REST
surface POST /api/trust/disputes/route:

  - dispute_route {rail, order_id?, capture_id?, charge_id?,
                   session_id?, purchase?, bundle?}
        The dispute pathway for the rail plus an evidence packet
        assembled from the Receipts chain. Routing and evidence only:
        the tool never files, submits, or initiates a dispute or
        claim with any provider.

Evidence: the hosted harness passes an evidence_provider that reads
its persisted captured-order stores and its live chain. Standalone,
the caller may pass a recorded purchase dict and a receipts bundle
directly as arguments; without them the packet reports the purchase
as not found.

Standard library only (plus the sibling disputes/receipts imports).
Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import disputes  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "disputes", "version": "1.0.0"}

TOOLS = [
    {
        "name": "dispute_route",
        "description": "Route a gated purchase to the dispute pathway "
                       "for its payment rail and assemble the evidence "
                       "packet held in the Receipts chain (identifiers, "
                       "amounts, gate verdicts, the chain segment, and "
                       "a recomputed chain verification). PayPal routes "
                       "to its dispute API surface; Amazon Pay and "
                       "Amazon marketplace purchases route to a "
                       "buyer-filed A-to-z Guarantee claim, for which "
                       "the packet is the deliverable. Routing and "
                       "evidence only: nothing is filed or initiated "
                       "with any provider.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rail": {
                    "type": "string",
                    "description": "paypal | amazon_pay | amazon",
                },
                "order_id": {"type": "string"},
                "capture_id": {"type": "string"},
                "charge_id": {"type": "string"},
                "session_id": {"type": "string"},
                "purchase": {
                    "type": "object",
                    "description": "Standalone use: a recorded purchase "
                                   "dict, as held in a captured-order "
                                   "record.",
                },
                "bundle": {
                    "type": "object",
                    "description": "Standalone use: a Receipts bundle "
                                   "{public_key, count, head_hash, "
                                   "receipts[]}.",
                },
            },
            "required": ["rail"],
        },
    },
]

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)


def _text(payload, is_error: bool) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=2)}],
            "isError": is_error}


def call_tool(name: str, arguments: dict, evidence_provider=None) -> dict:
    """Dispatch a tools/call and return the MCP result object.

    evidence_provider(rail, arguments) -> (purchase, bundle). The
    hosted harness passes its own provider reading the persisted
    stores and live chain; standalone runs use any purchase/bundle
    passed directly in the arguments.
    """
    try:
        if name == "dispute_route":
            rail = arguments.get("rail")
            if not isinstance(rail, str) or not rail.strip():
                return _text({"ok": False,
                              "error": "rail is required"}, True)
            if evidence_provider is not None:
                purchase, bundle = evidence_provider(rail, arguments)
            else:
                purchase = arguments.get("purchase")
                bundle = arguments.get("bundle")
            result = disputes.route_dispute(rail, purchase, bundle)
            return _text(result, result.get("ok") is not True)
        return {"content": [{"type": "text",
                             "text": f"unknown tool: {name}"}],
                "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text",
                             "text": f"disputes error: {exc}"}],
                "isError": True}


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
