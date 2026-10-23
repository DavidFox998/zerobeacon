# Showroom — the legibility layer for agent sessions

Module 5 of the Agent Trust Stack (Meta Global AI Developer Hackathon).
One-liner: **Showroom turns an agent's technical event stream into a
plain-language feed an owner can read at a glance, consolidates
everything the owner must do into one ordered prompt queue, and hands
the owner a takeover ticket at exactly the blocked step.**

Founding incident (Oct 20, 2026): the owner could not see his own
agent's browser — shared windows rendered black or as a single blank
tab even while the banner said "You're in control." Verification
prompts fired across phone, laptop, and web client out of order, none
saying which step it unlocked. He clicked blind more than once, then
abandoned the shared browser and finished the steps manually in his own
Chrome. The agent's work was sound; the supervision surface failed.
Showroom is that surface, rebuilt text-first: it renders on a phone over
a bad connection when pixels fail.

## Input contract

Agents emit **events** — one JSON object per line — piped to
`showroom.py ingest` on stdin:

| field | type | notes |
|---|---|---|
| `ts` | string | ISO-8601 timestamp |
| `session_id` | string | stable per agent session |
| `seq` | integer | per-session sequence number |
| `type` | string | `step` \| `blocked` \| `prompt` \| `done` \| `error` |
| `actor` | string | who emitted it (e.g. `browser-agent`) |
| `summary` | string | the agent's technical wording; URLs/paths allowed |
| `detail` | object \| null | optional `{page, tool, field, url, ...}` |
| `needs_owner` | boolean | true ⇒ the event enters the prompt queue |
| `owner_action` | string \| null | what the owner must do, in plain words |
| `secret_names` | string[] | secret *labels* involved — never values |

`ingest` validates every line against this schema. A malformed event is
**rejected loudly**: an error on stderr, a nonzero exit, and an entry in
`ingest-errors.log`. Nothing is silently dropped. For each accepted
event, ingest prints one JSON ack —
`{"accepted": true, "session_id": ..., "seq": ..., "prompt_id": "p3"|null}` —
so an agent learns the prompt id it should wait on.

**Contract rule:** events must never carry secret *values*. Showroom
does not trust agents to obey this — see the redaction gate below.

## The redaction gate (a release gate, not a feature)

Every rendering path — narrated feed, prompt queue, takeover ticket,
owner view, MCP output — passes its text through `redact()` before
anything is shown. The rules, exactly:

1. **Deny-list values.** Exact occurrences of any value in
   `secrets-denylist.txt` (one per line, `#` comments) are replaced with
   `••••`. The list is checked in the data dir, then beside `showroom.py`.
   It exists for the buggy-agent case: a value that leaked into an event
   anyway. The file itself is owner-only (chmod 600) — it contains the
   values it hides.
2. **`zbk_…` tokens** (ZeroBeacon keys): the *entire* token is replaced
   with `••••`.
3. **`gl_…` tokens** (Grantline surrogates): only the first 8 characters
   are shown, the rest masked — `gl_Ab3dE••••`. A surrogate is safe by
   design, but it is still a credential; the owner needs to recognize it,
   not read it in full.

The mirror log (`sessions/<id>.jsonl`) is the source record and stores
events as emitted; redaction governs everything *rendered from* it. If
any rendered artifact ever shows a planted value, that is a failing
build — the test suite greps every artifact to prove it doesn't.

## Narration

`Narrator` is the interface; `TemplateNarrator` is the v1
implementation — deterministic, no model dependency. (The planned
plug-in is a Meta-model narrator behind the same interface; template
output is the floor it must beat, and the fallback when no model is
reachable.)

Headline rules:

- **step** → `Working on <surface>` when a surface can be derived from
  `detail.url` (friendly host map: `rapidapi.com → RapidAPI`,
  `fly.io → Fly.io`, …; path phrases: `/gateway → gateway settings`,
  `/pricing → pricing page`, …); otherwise the cleaned summary.
- **blocked** → `Stopped — <owner_action>`.
- **prompt** → `Needs you — <owner_action>`.
- **done** → `Finished: <summary>`; **error** → `Hit a problem — <summary>`.
- Raw URLs are stripped from every headline. URLs live only in the
  detail expand of the owner view — never in the glanceable feed.

## Prompt queue + resume signals + tickets

Every `needs_owner` event becomes one queue item in `queue.json`:
`{id, session_id, created_ts, what (narrated), why, action_label,
status, resolution_ts, note}`. Ordering is total and boring on purpose:
**blocked prompts first, then FIFO**. One queue, one device, no prompt
fires anywhere else.

- `showroom.py queue` — pending items in order.
- `showroom.py resolve <id> [--note]` — marks the item resolved and
  writes `signals/<session_id>.resume` containing the prompt id. That
  file is the contract a waiting agent polls: when it appears with your
  prompt id, continue from the exact step you stopped at.
- `showroom.py ticket <id>` — the takeover ticket (printed and saved to
  `tickets/<id>.txt`): the session, the narrated blocked line, the
  redacted context (page / field / tool / url, secret *names*), the one
  thing the owner must do, and the resume instruction.

## Owner view

`showroom.py build-view` renders `view/index.html` (static, dark, no
external assets — same house style as Proven's directory): the pending
prompt queue on top with action labels, then per-session narrated feeds
with state badges — `working` / `waiting-on-owner` / `done` — and the
resolved-prompt count. Details hide behind a per-event expand.

## MCP surface

`showroom_mcp.py` — dependency-free stdio MCP server, protocol
**2025-11-25**, same hand-rolled pattern as Apathy's:

- `get_feed {session_id?, limit?}` — narrated headline lines
- `list_prompts {}` — pending queue items, in order
- `resolve_prompt {id, note?}` — resolve + write the resume signal

All outputs are the core's already-redacted renderings.

## Data dir

`--dir` flag > `$SHOWROOM_DIR` > the directory beside `showroom.py`.
Layout: `sessions/`, `queue.json`, `signals/`, `tickets/`, `view/`,
`ingest-errors.log`, `secrets-denylist.txt`.

## Tests

`tests/run_tests.py` (35 checks): ordering and ingest-validation probes,
plus `tests/fixture_agent.py` — a scripted replay of the Oct 20
RapidAPI↔Fly pairing that blocks twice on owner-only steps and waits for
the real resume signals while the suite works the queue as the owner
would. One fixture event deliberately leaks a secret value into its
summary; the suite greps the feed, queue output, `queue.json`, the
ticket, the view HTML, and the MCP output for it. **35/35 pass.**

## Honest limits

- v1 narration is templates, not a model: it is exact and testable, and
  its phrasing ceiling is the domain map. The model narrator is a
  scoped upgrade behind the same interface, not a rewrite.
- Redaction masks the shapes it knows (deny-list + two token formats).
  A novel credential format in a summary is caught only if the owner
  deny-lists its value — defense in depth is the agent-side contract
  (names, never values), with this gate as the net.
- Showroom surfaces and hands off; it never acts on the session itself,
  stores no cookies or credentials, and cannot unblock anything alone —
  the owner is the actuator at every blocked step, by design.
- The mirror log stores events as emitted (including a leaked value, if
  an agent sends one) because it is the audit record; treat the data
  dir as owner-private.
