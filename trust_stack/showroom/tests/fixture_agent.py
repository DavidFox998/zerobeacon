#!/usr/bin/env python3
"""Fixture agent: replays the Oct 20, 2026 RapidAPI<->Fly secret pairing
as a scripted Showroom session.

Sequence: Studio project list -> ENTERPRISE project -> gateway settings
-> BLOCKED #1 (sign-in check, owner-only) -> waits for the resume signal
-> reads the proxy-secret field (and, simulating a buggy agent, leaks the
field's value into one summary) -> BLOCKED #2 (secret copy is owner-only)
-> waits -> deploy -> verification call -> done.

Emission goes through the real contract: each event is piped to
`showroom.py ingest` as a subprocess, and the prompt id Showroom assigns
is read back from ingest's stdout. Waiting polls
signals/<session_id>.resume for that exact prompt id.

Usage: fixture_agent.py --showroom PATH --dir DATA_DIR [--timeout SECS]
Exit: 0 on done, 3 on signal timeout, 2 on ingest failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SESSION_ID = "fixture-rapidapi-pairing"

# The value the buggy agent leaks into a summary. The test plants this in
# the data dir's secrets-denylist.txt; Showroom must mask it everywhere.
PLANTED_SECRET = "PLANTED-SECRET-9f27c1d4-DO-NOT-LEAK"
# A ZeroBeacon-shaped key (shape-redaction target) and a Grantline
# surrogate (partial-mask target), both fake.
ZBK_TOKEN = "zbk_ZZZTEST999888"
GL_TOKEN = "gl_Ab3dEf5Gh7Jk9mN0pQ"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Emitter:
    def __init__(self, showroom: str, data_dir: str):
        self.showroom = showroom
        self.data_dir = data_dir
        self.seq = 0

    def emit(self, etype: str, summary: str, detail=None,
             needs_owner: bool = False, owner_action: str | None = None,
             secret_names=None) -> str | None:
        self.seq += 1
        event = {
            "ts": now_iso(),
            "session_id": SESSION_ID,
            "seq": self.seq,
            "type": etype,
            "actor": "browser-agent",
            "summary": summary,
            "detail": detail,
            "needs_owner": needs_owner,
            "owner_action": owner_action,
            "secret_names": secret_names or [],
        }
        env = dict(os.environ, SHOWROOM_DIR=self.data_dir)
        proc = subprocess.run(
            [sys.executable, self.showroom, "ingest"],
            input=json.dumps(event) + "\n",
            capture_output=True, text=True, env=env, timeout=30)
        if proc.returncode != 0:
            print(f"fixture: ingest failed: {proc.stderr.strip()}",
                  file=sys.stderr)
            raise SystemExit(2)
        ack = json.loads(proc.stdout.strip().splitlines()[-1])
        return ack.get("prompt_id")

    def wait_for(self, prompt_id: str, timeout: float) -> None:
        signal = Path(self.data_dir) / "signals" / f"{SESSION_ID}.resume"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if signal.read_text(encoding="utf-8").strip() == prompt_id:
                    return
            except OSError:
                pass
            time.sleep(0.2)
        print(f"fixture: timeout waiting for resume of {prompt_id}",
              file=sys.stderr)
        raise SystemExit(3)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--showroom", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--timeout", type=float, default=120.0)
    args = ap.parse_args()

    em = Emitter(args.showroom, args.dir)
    gateway = ("https://rapidapi.com/studio/projects/"
               "zerobeacon-ai-enterprise/gateway")

    em.emit("step", "Opened project list at "
            "https://rapidapi.com/studio/projects",
            detail={"url": "https://rapidapi.com/studio/projects",
                    "page": "Studio project list"})
    em.emit("step", "Opened ENTERPRISE project settings",
            detail={"url": "https://rapidapi.com/studio/projects/"
                           "zerobeacon-ai-enterprise/settings",
                    "page": "Project settings"})
    em.emit("step", "Opened gateway configuration page",
            detail={"url": gateway, "page": "Gateway settings",
                    "field": "X-RapidAPI-Proxy-Secret"})

    p1 = em.emit("blocked",
                 "Identity check interrupted the session at the "
                 "accounts.google.com challenge",
                 detail={"url": "https://accounts.google.com/v3/signin",
                         "page": "Verify it's you"},
                 needs_owner=True,
                 owner_action="Complete the sign-in check")
    print(f"fixture: blocked at prompt {p1}; waiting", flush=True)
    em.wait_for(p1, args.timeout)

    # The buggy leak: the field's VALUE lands in a summary. Showroom's
    # redaction gate — not this agent's care — is what keeps it hidden.
    em.emit("step",
            f"Proxy-secret field value read back as {PLANTED_SECRET}; "
            f"judges key on file is {ZBK_TOKEN}",
            detail={"url": gateway, "page": "Gateway settings",
                    "field": "X-RapidAPI-Proxy-Secret",
                    "note": f"judges key on file: {ZBK_TOKEN}"},
            secret_names=["ENTERPRISE proxy secret"])
    p2 = em.emit("blocked",
                 "Copying the proxy secret into Fly is an owner-only "
                 f"step; the field currently shows {PLANTED_SECRET}",
                 detail={"url": "https://fly.io/apps/zerobeacon-mf-1000/"
                                "secrets",
                         "page": "Fly secrets",
                         "field": "RAPIDAPI_PROXY_SECRET"},
                 needs_owner=True,
                 owner_action="Copy the project secret and paste it "
                              "into the Fly secret")
    print(f"fixture: blocked at prompt {p2}; waiting", flush=True)
    em.wait_for(p2, args.timeout)

    em.emit("step", "Deploy step running; secrets updated on Fly",
            detail={"url": "https://fly.io/apps/zerobeacon-mf-1000",
                    "page": "App overview", "grant": GL_TOKEN})
    em.emit("step", "Verification call returned the expected tier response",
            detail={"tool": "verify_rapidapi_request"})
    em.emit("done", "Pairing complete: all three listings verified")
    print("fixture: done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
