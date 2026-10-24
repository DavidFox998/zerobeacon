#!/usr/bin/env python3
"""Receipts MCP server — dependency-free, hand-rolled JSON-RPC over stdio.

Protocol: MCP 2025-11-25, newline-delimited JSON-RPC 2.0 messages.
Implements: initialize, notifications/initialized, ping, tools/list,
tools/call. Exposes the same two operations as the hosted REST surface
(POST /api/trust/receipts/append and POST /api/trust/receipts/verify),
one tool each, no more:

  - append_receipt {actor, action, result_status}
        Append one signed, hash-chained receipt to the chain and return
        the receipt, the full verifiable bundle, and the bundle check.
  - verify_receipts {bundle}
        Verify a bundle end to end: every hash, chain continuity, every
        Ed25519 signature, and the declared count/head. Returns
        {ok, detail}: detail is the receipt count on success, or
        [index, reason] at the first defect (index -1 = bundle level).

A verified bundle proves process integrity, not that the work's
conclusion is true (see README).

Chain storage: the hosted harness passes its own persisted recorder to
call_tool(), so MCP and REST append to the same chain. Run standalone,
this server keeps its chain in RECEIPTS_DATA_DIR (default: a
zerobeacon-receipts folder under the system temp directory). The signing
key is created there at runtime, file mode 0600, never in the repo.

Standard library only (plus the sibling receipts import). Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import receipts  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "receipts", "version": "1.0.0"}

TOOLS = [
    {
        "name": "append_receipt",
        "description": "Append one signed, hash-chained receipt recording "
                       "that an actor took an action with a result. "
                       "Returns the new receipt, the full verifiable "
                       "bundle, and whether the bundle verifies.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "actor": {"type": "string"},
                "action": {
                    "type": "object",
                    "description": "Recorded fields: method, host, path.",
                    "properties": {
                        "method": {"type": "string"},
                        "host": {"type": "string"},
                        "path": {"type": "string"},
                    },
                },
                "result_status": {
                    "description": "Recorded result; any JSON value.",
                },
            },
            "required": ["actor", "action", "result_status"],
        },
    },
    {
        "name": "verify_receipts",
        "description": "Verify a receipts bundle end to end: hashes, "
                       "chain continuity, Ed25519 signatures, and the "
                       "declared count and head hash. Proves process "
                       "integrity, not that the work's conclusion is true.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bundle": {
                    "type": "object",
                    "description": "{public_key, count, head_hash, "
                                   "receipts[]}. If omitted, the arguments "
                                   "object itself is read as the bundle.",
                },
            },
        },
    },
]

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)

# ---------------------------------------------------------------------
# Standalone chain (used only when no recorder is supplied)
# ---------------------------------------------------------------------

_LOCK = threading.Lock()
_CHAIN = None
_BUNDLE_PATH: str | None = None


def _data_dir() -> str:
    configured = os.environ.get("RECEIPTS_DATA_DIR", "").strip()
    if configured:
        return configured
    return os.path.join(tempfile.gettempdir(), "zerobeacon-receipts")


def _standalone_chain():
    global _CHAIN, _BUNDLE_PATH
    if _CHAIN is not None:
        return _CHAIN
    directory = _data_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    keypair = receipts.Keypair.load_or_create(
        os.path.join(directory, "receipts-key.json"))
    bundle_path = os.path.join(directory, "receipts-bundle.json")
    if os.path.exists(bundle_path):
        with open(bundle_path, "r", encoding="utf-8") as fh:
            _CHAIN = receipts.ReceiptChain.from_bundle(keypair, json.load(fh))
    else:
        _CHAIN = receipts.ReceiptChain(keypair)
    _BUNDLE_PATH = bundle_path
    return _CHAIN


def standalone_record(actor: str, action: dict, result_status) -> dict:
    """Append to the standalone chain and persist the bundle (0600)."""
    with _LOCK:
        chain = _standalone_chain()
        receipt = chain.append(actor, action, result_status)
        bundle = chain.to_bundle()
        fd = os.open(_BUNDLE_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                     0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(bundle, fh)
        os.chmod(_BUNDLE_PATH, 0o600)
        ok, detail = receipts.verify_bundle(bundle)
    return {"receipt": receipt, "bundle": bundle, "bundle_ok": ok,
            "bundle_detail": detail}


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------

def _text(payload, is_error: bool) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=2)}],
            "isError": is_error}


def call_tool(name: str, arguments: dict, recorder=None) -> dict:
    """Dispatch a tools/call and return the MCP result object.

    recorder(actor, action, result_status) -> {receipt, bundle, bundle_ok,
    bundle_detail}. The hosted harness passes its own persisted recorder;
    standalone runs use standalone_record.
    """
    record = recorder or standalone_record
    try:
        if name == "append_receipt":
            if (not isinstance(arguments.get("actor"), str)
                    or not isinstance(arguments.get("action"), dict)):
                return _text({"ok": False,
                              "error": "actor and action object are "
                                       "required"}, True)
            if "result_status" not in arguments:
                return _text({"ok": False,
                              "error": "result_status is required"}, True)
            recorded = record(arguments["actor"], arguments["action"],
                              arguments["result_status"])
            payload = {
                "ok": recorded["bundle_ok"] is True,
                "receipt": recorded["receipt"],
                "bundle": recorded["bundle"],
                "bundle_detail": recorded["bundle_detail"],
            }
            return _text(payload, not payload["ok"])
        if name == "verify_receipts":
            bundle = arguments.get("bundle", arguments)
            if not isinstance(bundle, dict):
                return _text({"ok": False,
                              "detail": [-1, "bundle must be an object"]},
                             True)
            ok, detail = receipts.verify_bundle(bundle)
            # A completed verification is a result, pass or fail.
            return _text({"ok": ok is True, "detail": detail}, False)
        return {"content": [{"type": "text",
                             "text": f"unknown tool: {name}"}],
                "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text",
                             "text": f"receipts error: {exc}"}],
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
