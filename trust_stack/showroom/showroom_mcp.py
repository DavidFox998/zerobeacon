#!/usr/bin/env python3
"""Showroom MCP server — dependency-free, hand-rolled JSON-RPC over stdio.

Protocol: MCP 2025-11-25, newline-delimited JSON-RPC 2.0 messages.
Implements: initialize, notifications/initialized, ping, tools/list,
tools/call. Exposes the Showroom core (see showroom.py):

  - get_feed      {session_id?, limit?}  narrated headline lines
  - list_prompts  {}                     pending owner prompts, in order
  - resolve_prompt{id, note?}            resolve + write the resume signal

Every output passes through Showroom's redaction filter (the queue items
and feed lines the core returns are already redacted at construction and
render time; this server returns them verbatim and adds nothing raw).

Data dir: $SHOWROOM_DIR or this file's directory (same rule as the CLI).

Standard library only. Python 3.12.
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import showroom  # noqa: E402

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "showroom", "version": "1.0.0"}

TOOLS = [
    {
        "name": "get_feed",
        "description": "The narrated, owner-plain feed of agent session "
                       "events (chronological headline lines, redacted). "
                       "Optionally restrict to one session and/or the "
                       "latest N lines.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_prompts",
        "description": "Pending owner prompts across all sessions, in "
                       "queue order (blocked first, then FIFO): id, "
                       "session, what/why in plain language, action label.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "resolve_prompt",
        "description": "Mark an owner prompt resolved and write the "
                       "resume signal the waiting agent polls for.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "note": {"type": "string"},
            },
            "required": ["id"],
        },
    },
]


def call_tool(name: str, arguments: dict) -> dict:
    """Dispatch a tools/call and return the MCP result object."""
    dd = showroom.data_dir()
    try:
        if name == "get_feed":
            lines = showroom.feed_lines(dd, arguments.get("session_id"),
                                        arguments.get("limit"))
            text = "\n".join(lines) if lines else "(no events yet)"
            is_error = False
        elif name == "list_prompts":
            items = showroom.pending_prompts(dd)
            text = json.dumps(items, indent=2)
            is_error = False
        elif name == "resolve_prompt":
            item = showroom.resolve_prompt(dd, arguments["id"],
                                           arguments.get("note"))
            text = json.dumps(item, indent=2)
            is_error = False
        else:
            return {"content": [{"type": "text",
                                 "text": f"unknown tool: {name}"}],
                    "isError": True}
    except Exception as exc:  # report, never crash the server
        return {"content": [{"type": "text", "text": f"showroom error: {exc}"}],
                "isError": True}
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


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
