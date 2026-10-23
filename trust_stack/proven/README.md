# Proven — live-probe directory for MCP servers

Proven **calls** listed MCP servers and publishes what it measures, beside
the author's claims — never blended with them. Every registry stores copy
typed once and never checked; Proven is CI for listings: measured facts
with timestamps, so a mismatch always reads "claimed X, measured Y at
time T".

## What a probe does

For each registered server, a probe performs exactly two MCP operations:

1. `initialize` — records the negotiated protocol version, `serverInfo`,
   and the initialize round-trip latency (capturing `Mcp-Session-Id` when
   the server issues one, as ZeroBeacon does).
2. `tools/list` — follows `nextCursor` pagination to exhaustion; records
   the tool count, the tool-name list, and a **schema fingerprint**:
   SHA-256 over the canonical JSON of the sorted
   `{name, description, inputSchema}` entries.

**Politeness rule (hard): probes never call `tools/call`.** Proven reads
public discovery surfaces only. Transports: streamable HTTP (POST
JSON-RPC, plain-JSON or SSE responses) and stdio (spawned command,
newline-delimited JSON-RPC).

## Pieces

- `proven.py` — probe engine, registry, drift, badges, static site, CLI:
  `probe <name>` · `probe-all` · `drift <name>` · `facts <name>` ·
  `build-site` · `badge <name>`
- `proven_mcp.py` — the same core as an MCP server (`probe_server`,
  `get_facts`, `check_drift`), stdlib-only, protocol 2025-11-25.
- `registry.json` — servers and their author claims. Seeded with
  **ZeroBeacon** (subject #1; claimed 1,003 MCP tools — catalogue 1,052
  including REST-only endpoints), **Apathy**, and **Grantline**.
- `probes/<name>.jsonl` — append-only probe history per server.
- `site/` — generated directory: `index.html` (claimed vs measured,
  verdict, last probe; sorted most-recently-probed first), one page per
  server (details, drift vs previous probe, full history), and
  `badges/<name>.svg` — an embeddable badge: green
  "measured N tools · date" on match, red "claimed C ≠ measured M" on
  mismatch.

**Drift** diffs the latest two probes: added/removed tool names, count
delta, fingerprint change. A rename appears as one removal plus one
addition — name-based diffing cannot tell a rename from a swap, and the
report does not pretend otherwise.

## Honest limits

- **A probe measures the free/anonymous surface.** A server may expose
  more tools behind authentication or paid tiers; the measured number is
  what an unauthenticated discovery call sees, and pages say so by
  construction (claimed sits beside measured, each labeled).
- **Latency is a single sample** from a single region, taken at
  initialize; it is labeled as such everywhere it appears. No uptime
  claims are made — drift is pull-based, there is no SLA and no alerting.
- **Fingerprints cover name + description + inputSchema.** Behavior
  changes that leave all three untouched are invisible to the fingerprint.
- Measurements are snapshots, not verdicts on quality: "stale" means the
  copy and the server disagreed at probe time, and the snapshot is the
  evidence either party can point to.

Standard library only. Python 3.12.
