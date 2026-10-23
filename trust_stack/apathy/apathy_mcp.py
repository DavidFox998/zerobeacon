#!/usr/bin/env python3
"""Apathy MCP server — dependency-free, hand-rolled JSON-RPC over stdio.

Protocol: MCP 2025-11-25, newline-delimited JSON-RPC 2.0 messages.
Implements: initialize, notifications/initialized, ping, tools/list,
tools/call. Exposes the Apathy core (see apathy.py):

  - audit_repo    {repo, manifest?}   full dual-method audit (+ manifest)
  - check_manifest{repo, manifest}    claimed-vs-measured claim checks
  - verify_ref    {repo, sha, branch?} pinned-SHA vs remote verification

`manifest` may be a filesystem path or an inline JSON object.

Standard library only. Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apathy  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "apathy", "version": "1.0.0"}

TOOLS = [
    {
        "name": "audit_repo",
        "description": "Dual-method audit of every .lean file in a repo: "
                       "comment/string-stripped sorry/admit/axiom counts with "
                       "file+line hits, per-directory parts that must sum to "
                       "the whole, and a method-agreement check. Optionally "
                       "checks a manifest of claims.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "Path to the repo/tree"},
                "manifest": {"description": "Manifest JSON object or path to a manifest file"},
            },
            "required": ["repo"],
        },
    },
    {
        "name": "check_manifest",
        "description": "Check manifest claims (sorry_count / admit_count / "
                       "axiom_count / file_count, optional scope; ref claims "
                       "{sha, branch}) against fresh measurements.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "manifest": {"description": "Manifest JSON object or path to a manifest file"},
            },
            "required": ["repo", "manifest"],
        },
    },
    {
        "name": "verify_ref",
        "description": "Verify a pinned git SHA against the remote: fetch "
                       "origin (errors reported, not silenced), read the remote "
                       "branch HEAD, and check ancestry.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "sha": {"type": "string"},
                "branch": {"type": "string", "default": "main"},
            },
            "required": ["repo", "sha"],
        },
    },
]


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tools/call and return the MCP result object."""
    try:
        if name == "audit_repo":
            manifest = None
            if arguments.get("manifest") is not None:
                manifest = apathy.load_manifest(arguments["manifest"])
            payload = apathy.build_report(arguments["repo"], manifest)
            is_error = not payload["ok"]
        elif name == "check_manifest":
            manifest = apathy.load_manifest(arguments["manifest"])
            payload = apathy.check_manifest(arguments["repo"], manifest)
            is_error = not payload["all_pass"]
        elif name == "verify_ref":
            payload = apathy.verify_ref(arguments["repo"], arguments["sha"],
                                        arguments.get("branch", "main"))
            is_error = payload.get("on_remote") is not True
        else:
            return {"content": [{"type": "text", "text": f"unknown tool: {name}"}],
                    "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text", "text": f"apathy error: {exc}"}],
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
