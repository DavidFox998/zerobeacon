#!/usr/bin/env python3
"""Showroom — the legibility layer for agent sessions.

Showroom mirrors what an agent is doing into plain language an owner can
read at a glance, consolidates everything the owner must do into ONE
ordered prompt queue on one device, and issues a takeover ticket at
exactly the blocked step. It exists because on Oct 20, 2026 the owner
could not see his own agent's browser (black/blank tabs), prompts fired
across three devices out of order, and he clicked blind before giving up
and doing the steps by hand.

Input contract (agents emit JSON lines, one event per line, to `ingest`):

    {ts, session_id, seq,
     type: "step"|"blocked"|"prompt"|"done"|"error",
     actor, summary, detail?, needs_owner, owner_action?, secret_names?}

`summary` is the agent's technical wording (URLs/paths allowed); `detail`
is an optional object ({page, tool, field, url, ...}). Events must never
carry secret VALUES — and Showroom does not trust that: every rendering
path (feed, queue, ticket, view, MCP output) passes through the redaction
filter in this file before anything is shown. See README.md for the full
contract and the exact redaction rules.

Storage (under the data dir; see data_dir()):
  sessions/<session_id>.jsonl   the mirror log (append-only, source events)
  queue.json                    prompt-queue state
  signals/<session_id>.resume   resume signal written by `resolve`
  tickets/<prompt_id>.txt       takeover tickets
  view/index.html               the rendered owner view
  ingest-errors.log             rejected-event audit trail

Data dir resolution: --dir flag > $SHOWROOM_DIR > this file's directory.

Standard library only. Python 3.12.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

EVENT_TYPES = {"step", "blocked", "prompt", "done", "error"}
BULLET = "••••"

# Credential shapes from this package. zbk_ = a ZeroBeacon key: mask fully.
# gl_ = a Grantline surrogate (safe by design, but long): keep 8 chars.
_ZBK_RE = re.compile(r"zbk_[A-Za-z0-9_\-.]+")
_GL_RE = re.compile(r"gl_[A-Za-z0-9_\-.]+")
_URL_RE = re.compile(r"https?://\S+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_dir(cli_dir: str | None = None) -> Path:
    if cli_dir:
        return Path(cli_dir)
    env = os.environ.get("SHOWROOM_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Redaction — the release gate. NOTHING renders without passing through here.
# ---------------------------------------------------------------------------

def load_denylist(dd: Path) -> list[str]:
    """Owner-planted secret values, one per line ('#' starts a comment).

    Read from the data dir first, then from beside this file. Longest
    first so overlapping values mask completely.
    """
    values: list[str] = []
    for cand in (dd / "secrets-denylist.txt",
                 Path(__file__).resolve().parent / "secrets-denylist.txt"):
        try:
            lines = cand.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if line and not line.startswith("#"):
                values.append(line)
    return sorted(set(values), key=len, reverse=True)


def redact(text, denylist: list[str]):
    """Mask deny-list values and credential-shaped tokens in a string."""
    if text is None:
        return None
    out = str(text)
    for value in denylist:
        if value:
            out = out.replace(value, BULLET)
    out = _ZBK_RE.sub(BULLET, out)
    out = _GL_RE.sub(lambda m: m.group(0)[:8] + BULLET, out)
    return out


def redact_obj(obj, denylist: list[str]):
    """Recursively redact every string in a JSON-shaped object."""
    if isinstance(obj, str):
        return redact(obj, denylist)
    if isinstance(obj, dict):
        return {k: redact_obj(v, denylist) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact_obj(v, denylist) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Narration — technical agent events -> owner-plain lines.
# v1 is a deterministic template narrator. The Narrator interface is the
# plug point for the planned Meta-model narrator (see README).
# ---------------------------------------------------------------------------

HOST_NAMES = {
    "rapidapi.com": "RapidAPI",
    "fly.io": "Fly.io",
    "github.com": "GitHub",
    "accounts.google.com": "Google",
    "google.com": "Google",
    "stripe.com": "Stripe",
    "youtube.com": "YouTube",
    "studio.youtube.com": "YouTube Studio",
    "cerebralvalley.ai": "Cerebral Valley",
    "smithery.ai": "Smithery",
    "devpost.com": "Devpost",
}

# Path fragment -> owner-language noun phrase (checked in order).
PATH_PHRASES = [
    ("/gateway", "gateway settings"),
    ("/pricing", "pricing page"),
    ("/secrets", "secrets settings"),
    ("/applications", "applications"),
    ("/projects", "project list"),
    ("/settings", "settings"),
]


def clean_summary(text: str) -> str:
    """Strip raw URLs and collapse whitespace for headline use."""
    out = _URL_RE.sub("", str(text))
    out = re.sub(r"\s+", " ", out).strip(" -—·")
    return out.strip()


class Narrator:
    """Interface: turn one event into one glanceable headline line."""

    def headline(self, event: dict, denylist: list[str]) -> str:
        raise NotImplementedError


class TemplateNarrator(Narrator):
    """Deterministic, model-free narrator (the v1 implementation)."""

    def _thing(self, event: dict) -> str | None:
        """A short 'what surface' phrase from detail/summary, or None."""
        detail = event.get("detail") or {}
        url = detail.get("url")
        if not url:
            m = _URL_RE.search(event.get("summary", ""))
            url = m.group(0) if m else None
        if url:
            try:
                parsed = urlparse(url)
            except ValueError:
                parsed = None
            if parsed is not None:
                host = HOST_NAMES.get(parsed.netloc, parsed.netloc)
                for frag, phrase in PATH_PHRASES:
                    if frag in parsed.path:
                        return f"{host} {phrase}"
                if host:
                    return host
        page = detail.get("page")
        if page:
            return clean_summary(page)
        return None

    def headline(self, event: dict, denylist: list[str]) -> str:
        etype = event["type"]
        summary = clean_summary(event.get("summary", ""))
        action = event.get("owner_action")
        if etype == "step":
            thing = self._thing(event)
            line = f"Working on {thing}" if thing else summary
        elif etype == "blocked":
            line = f"Stopped — {action or summary}"
        elif etype == "prompt":
            line = f"Needs you — {action or summary}"
        elif etype == "done":
            line = f"Finished: {summary}"
        elif etype == "error":
            line = f"Hit a problem — {summary}"
        else:  # unreachable after validation
            line = summary
        return redact(line, denylist)


NARRATOR: Narrator = TemplateNarrator()


# ---------------------------------------------------------------------------
# Event schema + mirror log
# ---------------------------------------------------------------------------

def validate_event(obj) -> str | None:
    """Return an error string, or None if the event is well-formed."""
    if not isinstance(obj, dict):
        return "event is not a JSON object"
    for field in ("ts", "session_id", "actor", "summary"):
        if not isinstance(obj.get(field), str) or not obj[field]:
            return f"field '{field}' must be a non-empty string"
    seq = obj.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool):
        return "field 'seq' must be an integer"
    if obj.get("type") not in EVENT_TYPES:
        return f"field 'type' must be one of {sorted(EVENT_TYPES)}"
    if not isinstance(obj.get("needs_owner"), bool):
        return "field 'needs_owner' must be a boolean"
    detail = obj.get("detail")
    if detail is not None and not isinstance(detail, dict):
        return "field 'detail' must be an object"
    action = obj.get("owner_action")
    if action is not None and not isinstance(action, str):
        return "field 'owner_action' must be a string or null"
    names = obj.get("secret_names")
    if names is not None and not (isinstance(names, list)
                                  and all(isinstance(n, str) for n in names)):
        return "field 'secret_names' must be a list of strings"
    return None


def session_path(dd: Path, session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", session_id)
    return dd / "sessions" / f"{safe}.jsonl"


def read_session(dd: Path, session_id: str) -> list[dict]:
    path = session_path(dd, session_id)
    events = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # mirror log is append-only by us; tolerate tears
    events.sort(key=lambda e: (e.get("ts", ""), e.get("seq", 0)))
    return events


def list_sessions(dd: Path) -> list[str]:
    sdir = dd / "sessions"
    if not sdir.is_dir():
        return []
    sessions = []
    for path in sorted(sdir.glob("*.jsonl")):
        events = read_session(dd, path.stem)
        if events:
            sessions.append(events[0]["session_id"])
    return sessions


# ---------------------------------------------------------------------------
# Prompt queue — the ONE ordered list of everything the owner must do.
# ---------------------------------------------------------------------------

def _queue_path(dd: Path) -> Path:
    return dd / "queue.json"


def load_queue(dd: Path) -> dict:
    try:
        return json.loads(_queue_path(dd).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"next_id": 1, "items": []}


def save_queue(dd: Path, queue: dict) -> None:
    dd.mkdir(parents=True, exist_ok=True)
    tmp = _queue_path(dd).with_suffix(".tmp")
    tmp.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    tmp.replace(_queue_path(dd))


def _kind_rank(item: dict) -> int:
    return 0 if item.get("kind") == "blocked" else 1


def pending_prompts(dd: Path) -> list[dict]:
    """Pending items, blocked first, then FIFO (insertion order)."""
    queue = load_queue(dd)
    indexed = [(i, it) for i, it in enumerate(queue["items"])
               if it.get("status") == "pending"]
    indexed.sort(key=lambda pair: (_kind_rank(pair[1]), pair[0]))
    return [it for _, it in indexed]


def _enqueue(dd: Path, event: dict, denylist: list[str]) -> dict:
    queue = load_queue(dd)
    pid = f"p{queue['next_id']}"
    queue["next_id"] += 1
    action = event.get("owner_action")
    if not action:
        action = ("Take over this step" if event["type"] == "blocked"
                  else "Review and respond")
    item = {
        "id": pid,
        "session_id": event["session_id"],
        "created_ts": event["ts"],
        "seq": event["seq"],
        "kind": event["type"],
        "what": NARRATOR.headline(event, denylist),
        "why": redact(clean_summary(event.get("summary", "")), denylist),
        "action_label": redact(action, denylist),
        "status": "pending",
        "resolution_ts": None,
        "note": None,
    }
    queue["items"].append(item)
    save_queue(dd, queue)
    return item


def resolve_prompt(dd: Path, prompt_id: str, note: str | None = None) -> dict:
    """Mark a prompt resolved and drop the agent's resume signal."""
    queue = load_queue(dd)
    for item in queue["items"]:
        if item["id"] == prompt_id:
            if item["status"] == "resolved":
                raise ValueError(f"prompt {prompt_id} is already resolved")
            item["status"] = "resolved"
            item["resolution_ts"] = now_iso()
            item["note"] = note
            save_queue(dd, queue)
            sig_dir = dd / "signals"
            sig_dir.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", item["session_id"])
            (sig_dir / f"{safe}.resume").write_text(prompt_id + "\n",
                                                     encoding="utf-8")
            return item
    raise ValueError(f"unknown prompt id: {prompt_id}")


def session_state(dd: Path, session_id: str) -> str:
    events = read_session(dd, session_id)
    if not events:
        return "unknown"
    if events[-1]["type"] == "done":
        return "done"
    if any(p["session_id"] == session_id for p in pending_prompts(dd)):
        return "waiting-on-owner"
    return "working"


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def ingest_lines(dd: Path, lines, out, err) -> int:
    """Validate + append each JSON line. Returns the error count.

    Malformed events are rejected loudly: an error line on `err`, an entry
    in ingest-errors.log, and a nonzero process exit (see cmd_ingest).
    """
    denylist = load_denylist(dd)
    errors = 0
    for lineno, raw in enumerate(lines, 1):
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            problem = f"invalid JSON: {exc}"
            errors += 1
            _reject(dd, err, lineno, problem, raw)
            continue
        problem = validate_event(obj)
        if problem:
            errors += 1
            _reject(dd, err, lineno, problem, raw)
            continue
        path = session_path(dd, obj["session_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj) + "\n")
        prompt_id = None
        if obj.get("needs_owner"):
            prompt_id = _enqueue(dd, obj, denylist)["id"]
        out.write(json.dumps({"accepted": True, "session_id": obj["session_id"],
                              "seq": obj["seq"], "prompt_id": prompt_id}) + "\n")
        out.flush()
    return errors


def _reject(dd: Path, err, lineno: int, problem: str, raw: str) -> None:
    msg = f"ingest error: line {lineno}: {problem}"
    err.write(msg + "\n")
    err.flush()
    try:
        dd.mkdir(parents=True, exist_ok=True)
        with (dd / "ingest-errors.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{now_iso()} line {lineno}: {problem} :: {raw[:200]}\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Feed + ticket (all output redacted)
# ---------------------------------------------------------------------------

def feed_lines(dd: Path, session_id: str | None = None,
               limit: int | None = None) -> list[str]:
    """Narrated headline lines, chronological, per session."""
    denylist = load_denylist(dd)
    sessions = [session_id] if session_id else list_sessions(dd)
    lines: list[str] = []
    for sid in sessions:
        events = read_session(dd, sid)
        if limit:
            events = events[-limit:]
        if len(sessions) > 1:
            lines.append(f"== {sid} ({session_state(dd, sid)}) ==")
        for ev in events:
            stamp = ev.get("ts", "")[11:16]
            lines.append(f"{stamp}  {NARRATOR.headline(ev, denylist)}")
    return lines


def build_ticket(dd: Path, prompt_id: str) -> str:
    """Compose the takeover ticket for a prompt (redacted), save + return."""
    denylist = load_denylist(dd)
    queue = load_queue(dd)
    item = next((it for it in queue["items"] if it["id"] == prompt_id), None)
    if item is None:
        raise ValueError(f"unknown prompt id: {prompt_id}")
    event = next((e for e in read_session(dd, item["session_id"])
                  if e.get("seq") == item["seq"]), {})
    detail = redact_obj(event.get("detail") or {}, denylist)
    ctx = []
    for key in ("page", "field", "tool", "url"):
        if detail.get(key):
            ctx.append(f"  {key}: {detail[key]}")
    names = event.get("secret_names") or []
    if names:
        ctx.append(f"  secrets involved (names only): {', '.join(names)}")
    text = "\n".join([
        f"TAKEOVER TICKET — prompt {item['id']} "
        f"({'RESOLVED' if item['status'] == 'resolved' else 'WAITING ON YOU'})",
        f"Session: {item['session_id']}",
        f"Stopped at: {item['what']}",
        f"Why: {item['why']}",
        "Context:",
        *(ctx or ["  (none recorded)"]),
        f"You need to: {item['action_label']}",
        f"Then: resolve prompt {item['id']} "
        f"(`showroom.py resolve {item['id']}`) — the agent watches for the "
        "resume signal and continues from this exact step.",
        "",
    ])
    tdir = dd / "tickets"
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / f"{item['id']}.txt").write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# Owner view — one static page: queue on top, narrated feed below.
# ---------------------------------------------------------------------------

_CSS = """
body { background:#0d1117; color:#e6edf3; font-family:Verdana,DejaVu Sans,sans-serif;
       margin:2em auto; max-width:960px; padding:0 1em; }
a { color:#58a6ff; } code { color:#a5d6ff; }
.dim { color:#8b949e; } .card { background:#161b22; border:1px solid #30363d;
       border-radius:6px; padding:10px 14px; margin:10px 0; }
.state-working { color:#58a6ff; } .state-waiting-on-owner { color:#d29922; }
.state-done { color:#3fb950; } .blocked { color:#f85149; }
ol.feed { margin:6px 0 18px 0; } ol.feed li { margin:3px 0; font-size:14px; }
details pre { background:#0d1117; border:1px solid #30363d; padding:8px;
       font-size:12px; overflow:auto; }
"""


def build_view(dd: Path) -> Path:
    denylist = load_denylist(dd)
    pending = pending_prompts(dd)
    queue = load_queue(dd)
    resolved = [it for it in queue["items"] if it["status"] == "resolved"]
    parts = [
        "<!DOCTYPE html>",
        '<html><head><meta charset="utf-8">',
        "<title>Showroom — what your agent is doing</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>Showroom</h1>",
        f'<p class="dim">Generated {html.escape(now_iso())} · '
        f"{len(resolved)} prompt(s) resolved · "
        "secret values are never shown (names and shapes only).</p>",
        f"<h2>Needs you ({len(pending)})</h2>",
    ]
    if not pending:
        parts.append('<p class="dim">Nothing is waiting on you.</p>')
    for item in pending:
        parts.append(
            '<div class="card">'
            f"<strong>{html.escape(item['action_label'])}</strong> "
            f'<span class="blocked">[{html.escape(item["kind"])}]</span><br>'
            f"{html.escape(item['what'])}<br>"
            f'<span class="dim">session {html.escape(item["session_id"])} · '
            f"prompt {html.escape(item['id'])} · "
            f"ticket: <code>showroom.py ticket {html.escape(item['id'])}</code>"
            "</span></div>")
    for sid in list_sessions(dd):
        state = session_state(dd, sid)
        parts.append(f"<h2>{html.escape(sid)} — "
                     f'<span class="state-{html.escape(state)}">'
                     f"{html.escape(state)}</span></h2>")
        parts.append('<ol class="feed">')
        for ev in read_session(dd, sid):
            headline = html.escape(NARRATOR.headline(ev, denylist))
            stamp = html.escape(ev.get("ts", "")[11:16])
            detail_html = ""
            if ev.get("detail"):
                blob = html.escape(json.dumps(
                    redact_obj(ev["detail"], denylist), indent=2,
                    ensure_ascii=False))
                detail_html = (f"<details><summary>details</summary>"
                               f"<pre>{blob}</pre></details>")
            parts.append(f'<li><span class="dim">{stamp}</span> '
                         f"{headline}{detail_html}</li>")
        parts.append("</ol>")
    parts.append("</body></html>")
    out = dd / "view" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _add_dir(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dir", default=None,
                        help="data dir (default: $SHOWROOM_DIR or script dir)")


def cmd_ingest(args) -> int:
    dd = data_dir(args.dir)
    errors = ingest_lines(dd, sys.stdin, sys.stdout, sys.stderr)
    return 2 if errors else 0


def cmd_feed(args) -> int:
    dd = data_dir(args.dir)
    for line in feed_lines(dd, args.session, args.limit):
        print(line)
    return 0


def cmd_queue(args) -> int:
    dd = data_dir(args.dir)
    items = pending_prompts(dd)
    if not items:
        print("queue empty — nothing is waiting on you")
        return 0
    for item in items:
        print(f"{item['id']}  [{item['kind']}]  {item['action_label']}")
        print(f"      {item['what']}  (session {item['session_id']})")
    return 0


def cmd_resolve(args) -> int:
    dd = data_dir(args.dir)
    try:
        item = resolve_prompt(dd, args.prompt_id, args.note)
    except ValueError as exc:
        print(f"resolve error: {exc}", file=sys.stderr)
        return 2
    print(f"resolved {item['id']} — resume signal written for "
          f"session {item['session_id']}")
    return 0


def cmd_ticket(args) -> int:
    dd = data_dir(args.dir)
    try:
        print(build_ticket(dd, args.prompt_id), end="")
    except ValueError as exc:
        print(f"ticket error: {exc}", file=sys.stderr)
        return 2
    return 0


def cmd_sessions(args) -> int:
    dd = data_dir(args.dir)
    for sid in list_sessions(dd):
        print(f"{sid}: {session_state(dd, sid)}")
    return 0


def cmd_build_view(args) -> int:
    dd = data_dir(args.dir)
    out = build_view(dd)
    print(f"view written: {out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="showroom",
        description="Legibility layer for agent sessions: narrated feed, "
                    "one owner prompt queue, takeover tickets.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="read event JSON lines from stdin")
    _add_dir(p)
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("feed", help="print the narrated feed")
    _add_dir(p)
    p.add_argument("--session", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_feed)

    p = sub.add_parser("queue", help="list pending owner prompts, in order")
    _add_dir(p)
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("resolve", help="resolve a prompt + signal the agent")
    _add_dir(p)
    p.add_argument("prompt_id")
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("ticket", help="print/write a takeover ticket")
    _add_dir(p)
    p.add_argument("prompt_id")
    p.set_defaults(func=cmd_ticket)

    p = sub.add_parser("sessions", help="list sessions and their states")
    _add_dir(p)
    p.set_defaults(func=cmd_sessions)

    p = sub.add_parser("build-view", help="render view/index.html")
    _add_dir(p)
    p.set_defaults(func=cmd_build_view)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
