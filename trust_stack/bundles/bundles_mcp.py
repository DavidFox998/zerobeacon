#!/usr/bin/env python3
"""Bundle Lock MCP server — dependency-free, hand-rolled JSON-RPC
over stdio, mirroring the hosted REST surface:

  - bundle_lock   {folder, label}
        Fingerprint the folder and return its lock record: the
        bundle certificate (the chain lock over the canonical
        "path:hash" table), its beacon stamp, and the full manifest.
        The hosted harness also persists the record under its label
        and writes a Receipts entry.

  - bundle_verify {folder, record} or {folder, label}
        Recompute the folder and compare against the lock record.
        LOCK HOLDS, or BROKEN with the changed / added / removed
        files named. The record must first recompute to its own
        certificate, so an edited record reports BROKEN on itself.

Folders are paths on the host, the same convention as the Apathy
audit's repo argument. Protocols: MCP 2025-11-25, newline-delimited
JSON-RPC 2.0. Standard library only. Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bundle_lock  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "bundles", "version": "1.0.0"}

TOOLS = [
    {
        "name": "bundle_lock",
        "description": "Lock a folder: fingerprint every file "
                       "(SHA-256), hash the canonical path:hash table "
                       "into one bundle certificate (the chain lock), "
                       "stamp it with the beacon, and return the lock "
                       "record with the full manifest.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string",
                           "description": "Path of the folder to lock."},
                "label": {"type": "string",
                          "description": "Name for this lock record."},
            },
            "required": ["folder", "label"],
        },
    },
    {
        "name": "bundle_verify",
        "description": "Verify a folder against its lock record. "
                       "Returns LOCK HOLDS, or BROKEN with the files "
                       "that changed, were added, or were removed "
                       "named individually. Pass the lock record, or "
                       "a label the hosted harness has stored.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "folder": {"type": "string",
                           "description": "Path of the folder to verify."},
                "record": {"type": "object",
                           "description": "A bundle_lock lock record."},
                "label": {"type": "string",
                          "description": "Hosted use: label of a stored "
                                         "lock record."},
            },
            "required": ["folder"],
        },
    },
]

TOOL_NAMES = tuple(tool["name"] for tool in TOOLS)


def _text(payload, is_error: bool) -> dict:
    return {"content": [{"type": "text",
                         "text": json.dumps(payload, indent=2)}],
            "isError": is_error}


def call_tool(name: str, arguments: dict, store=None) -> dict:
    """Dispatch a tools/call and return the MCP result object.

    store, when given, is the hosted harness's record store with
    save(label, record) and load(label); it persists bundle_lock
    records under their labels so bundle_verify can resolve a label.
    Standalone runs pass lock records directly.
    """
    try:
        if name == "bundle_lock":
            folder = arguments.get("folder")
            label = arguments.get("label")
            if not isinstance(folder, str) or not folder.strip():
                return _text({"ok": False,
                              "error": "folder is required"}, True)
            if not isinstance(label, str) or not label.strip():
                return _text({"ok": False,
                              "error": "label is required"}, True)
            record = bundle_lock.lock_folder(folder, label)
            if store is not None:
                store.save(label, record)
            return _text({"ok": True, "record": record}, False)
        if name == "bundle_verify":
            folder = arguments.get("folder")
            if not isinstance(folder, str) or not folder.strip():
                return _text({"ok": False,
                              "error": "folder is required"}, True)
            record = arguments.get("record")
            if record is None and store is not None \
                    and isinstance(arguments.get("label"), str):
                record = store.load(arguments["label"])
                if record is None:
                    return _text(
                        {"ok": False,
                         "error": f"no lock record stored under label "
                                  f"{arguments['label']!r}"}, True)
            if not isinstance(record, dict):
                return _text({"ok": False,
                              "error": "record object (or a stored "
                                       "label) is required"}, True)
            report = bundle_lock.verify_folder(folder, record)
            return _text(report, report.get("ok") is not True)
        return {"content": [{"type": "text",
                             "text": f"unknown tool: {name}"}],
                "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text",
                             "text": f"bundles error: {exc}"}],
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
