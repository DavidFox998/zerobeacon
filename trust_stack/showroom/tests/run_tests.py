#!/usr/bin/env python3
"""Showroom v1 test suite.

Two arenas:
  dirA — unit-ish probes: blocked-before-prompt queue ordering, malformed
         ingest rejection (never silently dropped), valid ingest accepted.
  dirB — the Oct 20 replay end-to-end: the fixture agent blocks twice on
         owner-only steps; the test works the queue the way the owner
         would (read feed, resolve, read ticket, resolve) and the session
         must reach done with an empty queue.

The redaction gate is a RELEASE GATE: the planted secret value (in dirB's
secrets-denylist.txt, leaked into two fixture summaries by the "buggy
agent") must appear in NO rendered artifact — feed, queue output,
queue.json, ticket, view HTML, MCP output — and credential-shaped tokens
must be masked per the documented rules.

Run:  python3 tests/run_tests.py
Exit: 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHOWROOM = HERE.parent / "showroom.py"
SHOWROOM_MCP = HERE.parent / "showroom_mcp.py"
FIXTURE = HERE / "fixture_agent.py"

PLANTED = "PLANTED-SECRET-9f27c1d4-DO-NOT-LEAK"
ZBK_TOKEN = "zbk_ZZZTEST999888"
GL_TOKEN = "gl_Ab3dEf5Gh7Jk9mN0pQ"
GL_MASKED_PREFIX = "gl_Ab3dE"

RESULTS = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition)))
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {name}{suffix}", flush=True)


def cli(args, dd, inp=None):
    env = dict(os.environ, SHOWROOM_DIR=str(dd))
    return subprocess.run([sys.executable, str(SHOWROOM), *args],
                          input=inp, capture_output=True, text=True,
                          env=env, timeout=60)


def event(seq, etype, summary, session="ordering-probe", needs_owner=False,
          owner_action=None, ts="2026-10-20T21:00:00+00:00"):
    return {"ts": ts, "session_id": session, "seq": seq, "type": etype,
            "actor": "test", "summary": summary, "detail": None,
            "needs_owner": needs_owner, "owner_action": owner_action,
            "secret_names": []}


def wait_until(pred, timeout=60.0, interval=0.2):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


def pending(dd):
    """Pending items in documented queue order (blocked first, then FIFO)."""
    qfile = Path(dd) / "queue.json"
    if not qfile.exists():
        return []
    items = json.loads(qfile.read_text())["items"]
    indexed = [(i, it) for i, it in enumerate(items)
               if it["status"] == "pending"]
    indexed.sort(key=lambda p: (0 if p[1]["kind"] == "blocked" else 1, p[0]))
    return [it for _, it in indexed]


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="showroom-tests-"))
    dir_a = root / "a"
    dir_b = root / "b"
    for d in (dir_a, dir_b):
        d.mkdir(parents=True)
        (d / "secrets-denylist.txt").write_text(PLANTED + "\n")

    # ------------------------------------------------------------------
    # dirA: ordering + ingest validation
    # ------------------------------------------------------------------
    r = cli(["ingest"], dir_a,
            inp=json.dumps(event(1, "prompt", "Weekly summary is ready",
                                 needs_owner=True,
                                 owner_action="Review the weekly summary"))
            + "\n")
    check("A0 valid prompt event accepted", r.returncode == 0, r.stderr.strip())
    r = cli(["ingest"], dir_a,
            inp=json.dumps(event(2, "blocked", "Payment needs a human",
                                 needs_owner=True,
                                 owner_action="Approve the payment")) + "\n")
    check("A0b valid blocked event accepted", r.returncode == 0)

    out = cli(["queue"], dir_a).stdout
    idx_blocked = out.find("Approve the payment")
    idx_prompt = out.find("Review the weekly summary")
    check("A1 blocked prompts sort before informational ones",
          0 <= idx_blocked < idx_prompt,
          f"blocked@{idx_blocked} prompt@{idx_prompt}")
    items_a = pending(dir_a)
    check("A1b queue items carry ids and labels",
          [it["id"] for it in items_a] == ["p2", "p1"] and
          items_a[0]["action_label"] == "Approve the payment",
          f"order={[it['id'] for it in items_a]}")

    r = cli(["ingest"], dir_a, inp="this is not json\n")
    check("A2 malformed JSON rejected loudly",
          r.returncode != 0 and "ingest error" in r.stderr,
          f"rc={r.returncode}")
    bad = event(3, "step", "x")
    del bad["summary"]
    r = cli(["ingest"], dir_a, inp=json.dumps(bad) + "\n")
    check("A2b schema-invalid event rejected",
          r.returncode != 0 and "summary" in r.stderr, f"rc={r.returncode}")
    check("A2c rejections are audited, not silently dropped",
          (dir_a / "ingest-errors.log").exists() and
          "line 1" in (dir_a / "ingest-errors.log").read_text())
    r = cli(["ingest"], dir_a,
            inp=json.dumps(event(4, "step", "A clean step")) + "\n")
    check("A2d valid events still accepted after rejections",
          r.returncode == 0)

    # ------------------------------------------------------------------
    # dirB: the Oct 20 replay, worked by the "owner" (this test)
    # ------------------------------------------------------------------
    env_b = dict(os.environ, SHOWROOM_DIR=str(dir_b))
    fixture = subprocess.Popen(
        [sys.executable, str(FIXTURE), "--showroom", str(SHOWROOM),
         "--dir", str(dir_b), "--timeout", "120"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env_b)
    ticket_text = ""
    try:
        ok = wait_until(lambda: any(
            it["kind"] == "blocked" and "sign-in" in it["action_label"]
            for it in pending(dir_b)))
        check("B1 first block reaches the queue", ok)
        pend = pending(dir_b)
        p1 = pend[0]["id"] if pend else None
        check("B1b first prompt is the sign-in block, labelled for the owner",
              bool(pend) and pend[0]["action_label"] ==
              "Complete the sign-in check",
              f"items={[(it['id'], it['action_label']) for it in pend]}")

        q_out = cli(["queue"], dir_b).stdout
        check("B1c queue output shows the pending block", p1 in q_out
              if p1 else False)

        feed = cli(["feed", "--session", "fixture-rapidapi-pairing"],
                   dir_b).stdout
        check("B2 feed headlines carry no raw URLs",
              "://" not in feed, feed.replace("\n", " | ")[:160])
        check("B2b feed reads as plain language",
              "RapidAPI" in feed and "gateway" in feed and "Stopped" in feed)

        r = cli(["resolve", p1], dir_b) if p1 else None
        sig = dir_b / "signals" / "fixture-rapidapi-pairing.resume"
        check("B3 resolving prompt 1 writes its resume signal",
              r is not None and r.returncode == 0 and sig.exists() and
              sig.read_text().strip() == p1)

        ok = wait_until(lambda: any(
            "Copy the project secret" in it["action_label"]
            for it in pending(dir_b)))
        check("B4 second block reaches the queue after resume", ok)
        pend = pending(dir_b)
        p2 = pend[0]["id"] if pend else None

        feed = cli(["feed", "--session", "fixture-rapidapi-pairing"],
                   dir_b).stdout
        check("B5 planted secret absent from the narrated feed",
              PLANTED not in feed)
        check("B5b zbk_ token fully masked in the feed",
              ZBK_TOKEN not in feed)
        check("B5c feed headlines still URL-free after the leak event",
              "://" not in feed)
        q_json = (dir_b / "queue.json").read_text()
        check("B5d planted secret absent from queue.json",
              PLANTED not in q_json)
        q_out = cli(["queue"], dir_b).stdout
        check("B5e planted secret absent from queue output",
              PLANTED not in q_out)

        if p2:
            r = cli(["ticket", p2], dir_b)
            ticket_text = r.stdout
            ticket_file = (dir_b / "tickets" / f"{p2}.txt").read_text()
            check("B6 ticket is written and printed",
                  "TAKEOVER TICKET" in ticket_text and
                  ticket_text == ticket_file)
            check("B6b ticket says exactly what the owner must do",
                  "Copy the project secret and paste it" in ticket_text and
                  "RAPIDAPI_PROXY_SECRET" in ticket_text)
            check("B6c planted secret absent from the ticket",
                  PLANTED not in ticket_text and PLANTED not in ticket_file)
            r = cli(["resolve", p2, "--note", "pasted into Fly"], dir_b)
            check("B7 resolving prompt 2 succeeds", r.returncode == 0,
                  r.stderr.strip())

        try:
            rc = fixture.wait(timeout=90)
        except subprocess.TimeoutExpired:
            fixture.kill()
            rc = "timeout"
        check("B8 fixture reaches done after both resolves", rc == 0,
              f"rc={rc}")

        r = cli(["build-view"], dir_b)
        view = (dir_b / "view" / "index.html").read_text()
        check("B9 view builds", r.returncode == 0 and len(view) > 500)
        check("B9b planted secret absent from the view", PLANTED not in view)
        check("B9c zbk_ token absent from the view", ZBK_TOKEN not in view)
        check("B9d gl_ surrogate shown masked to 8 chars in the view",
              GL_TOKEN not in view and f"{GL_MASKED_PREFIX}••••" in view)
        check("B9e view shows the session done and the queue empty",
              "done" in view and "Needs you (0)" in view)
        q_out = cli(["queue"], dir_b).stdout
        check("B9f queue output reports empty at the end",
              "queue empty" in q_out)
        feed = cli(["feed", "--session", "fixture-rapidapi-pairing"],
                   dir_b).stdout
        check("B9g feed closes with the finished line",
              "Finished: Pairing complete" in feed)
    finally:
        if fixture.poll() is None:
            fixture.kill()

    # ------------------------------------------------------------------
    # MCP smoke (dirB): initialize, tools/list, redacted list_prompts
    # ------------------------------------------------------------------
    probe = event(1, "prompt",
                  f"Sanity-check the MCP path; note shows {PLANTED}",
                  session="mcp-probe", needs_owner=True,
                  owner_action="Check the MCP path")
    cli(["ingest"], dir_b, inp=json.dumps(probe) + "\n")
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                    "clientInfo": {"name": "showroom-test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "list_prompts", "arguments": {}}},
    ]
    proc = subprocess.run(
        [sys.executable, str(SHOWROOM_MCP)],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True, text=True, env=env_b, timeout=60)
    responses = {}
    for line in proc.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "id" in msg:
            responses[msg["id"]] = msg
    init = responses.get(1, {}).get("result", {})
    check("C1 MCP initialize answers with protocol 2025-11-25",
          init.get("protocolVersion") == "2025-11-25")
    tools = [t["name"] for t in
             responses.get(2, {}).get("result", {}).get("tools", [])]
    check("C2 MCP tools/list returns the three Showroom tools",
          tools == ["get_feed", "list_prompts", "resolve_prompt"],
          f"tools={tools}")
    call_text = (responses.get(3, {}).get("result", {})
                 .get("content", [{}])[0].get("text", ""))
    check("C3 MCP list_prompts returns the pending prompt, redacted",
          "Check the MCP path" in call_text and PLANTED not in call_text)

    failed = [name for name, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
