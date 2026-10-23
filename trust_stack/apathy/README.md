# Apathy — mechanical verification with zero self-trust

Apathy is an audit engine for **claims about code**. Agents state numbers
fluently and wrongly ("SORRY: 0", "pushed, main is at X", "1,052 tools")
and nothing in the standard toolchain checks the *claims*. Apathy does:
it re-measures, two independent ways, and reports claimed-vs-measured.

Generalizes the Opera Numerorum V1–V5 audit harness
(`rh-p5-bridge-14/scripts/audit.sh`) and the Apathy protocol (AGENTS.md,
installed 2026-10-11). Standard library only, Python 3.12. Report-only:
**Apathy never modifies the tree it audits.**

## The protocol rules it implements

1. **Never trust the first measurement.** Every token count is produced
   by two independent implementations (below). If they disagree, the
   report flags a `METHOD DISAGREEMENT` discrepancy — it never silently
   picks one.
2. **Strip before you count.** Lean comments (line `--`, block `/- -/`
   *nested*, doc comments `/--` and `/-!`) and string literals are
   blanked before scanning, with newlines preserved so line numbers are
   exact. A "sorry" in a comment is not a sorry.
3. **Parts must sum to the whole.** Per-top-level-directory subtotals
   are shown and must equal the repo total, per token.
4. **Verify refs against the remote.** `git fetch origin` is run with
   errors captured and reported (never silenced), the remote branch
   HEAD is read, and any pinned SHA is checked with
   `git merge-base --is-ancestor`. A local-only commit is reported as
   local-only, not "pushed".
5. **Report-only.** Apathy emits reports; it edits nothing.

## The two counting methods

- **Method A — state machine.** A single pass over the file as one
  character stream, with explicit normal / line-comment / block(depth) /
  string states; hits counted over the full stripped text, line numbers
  derived from offsets.
- **Method B — line-oriented.** Processes the text line by line with its
  own block-depth bookkeeping, locating markers per line with `find`;
  hits counted per line. Structurally different code, same Lean
  semantics — agreement between the two is the evidence.

Tokens counted, always as **whole tokens** (identifier boundaries):
`sorry`, `admit`, `axiom` — each hit reported with file and line.

## Usage

```bash
# full audit (human summary + JSON report); exit 1 on any discrepancy
python3 apathy.py audit <repo> [--manifest manifest.json] [--json]

# counts only
python3 apathy.py count <repo> [--json]

# verify a pinned SHA against origin/<branch> (default branch: main)
python3 apathy.py verify-ref <repo> <sha> [branch]
```

Exit codes: `audit`/`count` exit 0 when both methods agree, parts sum,
and (for `audit`) every manifest claim passes; 1 otherwise. `verify-ref`
exits 0 when the SHA is contained in the remote branch.

### Manifest format

```json
{
  "claims": [
    {"type": "sorry_count", "value": 0},
    {"type": "admit_count", "value": 0},
    {"type": "axiom_count", "value": 2, "scope": "Towers/"},
    {"type": "file_count", "value": 40},
    {"type": "ref", "sha": "f021be9…", "branch": "main"}
  ]
}
```

`scope` restricts a count claim to files under that path prefix. Ref
claims trigger the fetch + ancestry check. Each claim is reported as
claimed-vs-measured with PASS/FAIL.

### MCP server

`apathy_mcp.py` is a dependency-free MCP stdio server (hand-rolled
JSON-RPC, protocol **2025-11-25**) exposing the same core:

- `audit_repo` — `{repo, manifest?}`
- `check_manifest` — `{repo, manifest}`
- `verify_ref` — `{repo, sha, branch?}`

`manifest` may be an inline JSON object or a path to a manifest file.

## Tests

```bash
python3 tests/run_tests.py
```

Builds a fixture tree replaying the October incidents — sorrys that live
only in line comments, nested block comments, doc comments, and string
literals; a known 3-sorry/1-admit file with asserted line numbers; a
false `sorry_count == 0` manifest — and asserts the measurements, the
parts-sum, method agreement, and CLI exit codes.

## Honest limits

- **Token audit only.** Apathy counts tokens in stripped text; it does
  not elaborate Lean, run `lake build`, or evaluate `#print axioms`. A
  zero count says nothing about whether proofs are correct, meaningful,
  or axiom-clean at the kernel level.
- **Macro-obfuscated holes can evade it.** A `sorry` produced by a
  macro, notation, or `eval` trickery that never appears as a literal
  token will not be counted. Each report carries its method and scope so
  the audit is itself auditable.
- **String interpolation:** code inside `s!"…{ … }"` interpolations is
  blanked along with the string by both methods.
- Ref verification is only as fresh as the fetch it performs; it is run
  by `verify-ref` and by ref claims — the `count`/`audit` token paths
  never touch the network.
