#!/usr/bin/env python3
"""Resolution Layer MCP server — dependency-free, hand-rolled
JSON-RPC over stdio, mirroring the hosted REST surface:

  - resolution_evaluate {checkpoint_id, subject, evidence?, limits?}
        Assess a subject against a checkpoint's declared limits
        and return the decision record: APPROVED, or DECLINED with
        one primary reason and every reason behind it, in the
        published precedence order, each carrying its limit, its
        operator, its numbers, and its steps_to_resolve. Every
        decision is a case, retrievable by case_id; the hosted
        harness writes it to the Receipts chain.

Protocols: MCP 2025-11-25, newline-delimited JSON-RPC 2.0.
Standard library only. Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resolution  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "resolution", "version": "1.0.0"}

TOOLS = [
    {
        "name": "resolution_evaluate",
        "description": "Evaluate a subject at a Resolution Layer "
                       "checkpoint and return the decision record. "
                       "A decline is a 200-style answer, not an "
                       "error: decision DECLINED, a primary reason "
                       "equal to reasons[0], every reason carrying "
                       "reason_code, limit_type, limit_name, "
                       "operator, limit_value, assessed_value, unit "
                       "(required when the values are numeric) and "
                       "steps_to_resolve. Where the checkpoint "
                       "requires validation, a missing or expired "
                       "validation record declines the case alone "
                       "(VALIDATION_MISSING / VALIDATION_EXPIRED, "
                       "typed instants, never mixed with subject "
                       "reasons). The hosted harness writes every "
                       "decision to the Receipts chain; the record "
                       "is retrievable by its case_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "checkpoint_id": {
                    "type": "string",
                    "description": "The checkpoint that decides, "
                                   "e.g. paypal_packet_gate.",
                },
                "subject": {
                    "type": "object",
                    "description": "What is being decided: "
                                   "{kind, ref}.",
                },
                "evidence": {
                    "type": "object",
                    "description": "The embedded evidence envelope: "
                                   "{property, value, unit, check_id, "
                                   "validation_id, "
                                   "validation_expires_at?}.",
                },
                "limits": {
                    "type": "array",
                    "description": "Declared limits to assess: "
                                   "{limit_name, limit_type, "
                                   "operator, limit_value, "
                                   "assessed_value, unit, check_id, "
                                   "validation_id, "
                                   "steps_to_resolve?}. Defaults "
                                   "to the checkpoint's standing "
                                   "limits.",
                    "items": {"type": "object"},
                },
            },
            "required": ["checkpoint_id", "subject"],
        },
    },
]

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)


def _text(payload, is_error: bool) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=2)}],
            "isError": is_error}


def call_tool(name: str, arguments: dict, evaluator=None) -> dict:
    """Dispatch a tools/call and return the MCP result object.

    evaluator, when given, is the hosted harness's decision path:
    it takes the request dict and returns {"decision": record,
    "receipt": receipt} after writing the decision to the Receipts
    chain and the case store. Standalone runs evaluate without
    persistence (receipt None).
    """
    try:
        if name == "resolution_evaluate":
            if not isinstance(arguments, dict):
                return _text({"ok": False,
                              "error": "arguments must be an object"},
                             True)
            if evaluator is not None:
                payload = evaluator(arguments)
                return _text({"ok": True, **payload}, False)
            record = resolution.evaluate(arguments)
            return _text({"ok": True, "decision": record,
                          "receipt": None}, False)
        return {"content": [{"type": "text",
                             "text": f"unknown tool: {name}"}],
                "isError": True}
    except resolution.ResolutionRequestError as exc:
        return _text({"ok": False, "error": str(exc)}, True)
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text",
                             "text": f"resolution error: {exc}"}],
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
