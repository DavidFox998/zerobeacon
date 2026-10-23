#!/usr/bin/env python3
"""Apathy — mechanical verification with zero self-trust.

An audit engine for claims about code. Every count is produced by TWO
independent methods; stripped of comments and strings before counting;
per-directory parts must sum to the whole; refs are verified against the
remote, never from local state alone.

Report-only: Apathy never modifies the tree it audits.

Standard library only. Python 3.12.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

TOKENS = ("sorry", "admit", "axiom")
_IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_'")


# --------------------------------------------------------------------------
# Method A: single-pass character state machine stripper
# --------------------------------------------------------------------------

def strip_state_machine(text: str) -> str:
    """Return `text` with Lean comments and string literals blanked out.

    Blanked characters become spaces; newlines are preserved so line
    numbers are exact. States: normal / line_comment / block(depth) /
    string. Block comments nest (Lean semantics); doc comments (/-- and
    /-!) are block comments. Comment markers inside strings are not
    comments; quotes inside comments are not strings.
    """
    out = []
    i, n = 0, len(text)
    state = "normal"
    depth = 0
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if state == "normal":
            if two == "--":
                out.append("  ")
                i += 2
                state = "line"
            elif two == "/-":
                out.append("  ")
                i += 2
                state = "block"
                depth = 1
            elif c == '"':
                out.append(" ")
                i += 1
                state = "string"
            else:
                out.append(c)
                i += 1
        elif state == "line":
            if c == "\n":
                out.append("\n")
                state = "normal"
            else:
                out.append(" ")
            i += 1
        elif state == "block":
            if two == "/-":
                out.append("  ")
                i += 2
                depth += 1
            elif two == "-/":
                out.append("  ")
                i += 2
                depth -= 1
                if depth == 0:
                    state = "normal"
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
        else:  # string
            if c == "\\" and i + 1 < n:
                # escape: blank both chars (keep a newline if escaped)
                out.append(" ")
                out.append("\n" if text[i + 1] == "\n" else " ")
                i += 2
            elif c == '"':
                out.append(" ")
                i += 1
                state = "normal"
            else:
                out.append("\n" if c == "\n" else " ")
                i += 1
    return "".join(out)


# --------------------------------------------------------------------------
# Method B: line-oriented stripper (own block-depth tracking, search-based)
# --------------------------------------------------------------------------

def strip_line_oriented(text: str) -> str:
    """Independent implementation: process the text one line at a time.

    Within a line, repeatedly locate the nearest special marker with
    str.find (rather than stepping a state machine char-by-char) and
    rewrite the line segment by segment. Block depth and string state are
    tracked across lines by this function's own bookkeeping.
    """
    result_lines = []
    block_depth = 0
    in_string = False
    for raw_line in text.split("\n"):
        line = raw_line
        buf = []
        pos = 0
        L = len(line)
        while pos < L:
            if block_depth > 0:
                # inside a block comment: find the nearest of /- or -/
                nxt_open = line.find("/-", pos)
                nxt_close = line.find("-/", pos)
                if nxt_open == -1 and nxt_close == -1:
                    buf.append(" " * (L - pos))
                    pos = L
                elif nxt_close != -1 and (nxt_open == -1 or nxt_close < nxt_open):
                    buf.append(" " * (nxt_close + 2 - pos))
                    pos = nxt_close + 2
                    block_depth -= 1
                else:
                    buf.append(" " * (nxt_open + 2 - pos))
                    pos = nxt_open + 2
                    block_depth += 1
            elif in_string:
                # find the closing quote, skipping escaped characters
                j = pos
                closed = False
                while j < L:
                    if line[j] == "\\":
                        j += 2
                        continue
                    if line[j] == '"':
                        closed = True
                        break
                    j += 1
                end = (j + 1) if closed else L
                buf.append(" " * (end - pos))
                pos = end
                in_string = not closed
            else:
                # normal code: find nearest of -- , /- , "
                candidates = []
                for marker, kind in (("--", "line"), ("/-", "block"), ('"', "string")):
                    idx = line.find(marker, pos)
                    if idx != -1:
                        candidates.append((idx, kind))
                if not candidates:
                    buf.append(line[pos:])
                    pos = L
                    break
                idx, kind = min(candidates)
                buf.append(line[pos:idx])
                if kind == "line":
                    buf.append(" " * (L - idx))
                    pos = L
                elif kind == "block":
                    buf.append("  ")
                    pos = idx + 2
                    block_depth = 1
                else:  # string opens
                    buf.append(" ")
                    pos = idx + 1
                    in_string = True
        result_lines.append("".join(buf))
    return "\n".join(result_lines)


STRIPPERS = {"A_state_machine": strip_state_machine, "B_line_oriented": strip_line_oriented}


# --------------------------------------------------------------------------
# Counting (whole tokens only), per method
# --------------------------------------------------------------------------

_TOKEN_RES = {t: re.compile(r"(?<![A-Za-z0-9_'])" + t + r"(?![A-Za-z0-9_'])") for t in TOKENS}


def count_tokens_method_a(stripped: str) -> dict:
    """Count whole tokens over the whole text; line numbers from offsets."""
    hits = {t: [] for t in TOKENS}
    for tok, rx in _TOKEN_RES.items():
        for m in rx.finditer(stripped):
            line_no = stripped.count("\n", 0, m.start()) + 1
            hits[tok].append(line_no)
    return hits


def count_tokens_method_b(stripped: str) -> dict:
    """Count whole tokens line by line; line numbers from enumeration."""
    hits = {t: [] for t in TOKENS}
    for line_no, line in enumerate(stripped.split("\n"), start=1):
        for tok, rx in _TOKEN_RES.items():
            for _m in rx.finditer(line):
                hits[tok].append(line_no)
    return hits


COUNTERS = {"A_state_machine": count_tokens_method_a, "B_line_oriented": count_tokens_method_b}


def audit_file(path: str) -> dict:
    """Audit one file with both methods. Returns per-method hits + agreement."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    per_method = {}
    for name in STRIPPERS:
        stripped = STRIPPERS[name](text)
        per_method[name] = COUNTERS[name](stripped)
    agree = all(per_method["A_state_machine"][t] == per_method["B_line_oriented"][t] for t in TOKENS)
    return {"per_method": per_method, "agree": agree}


# --------------------------------------------------------------------------
# Tree audit
# --------------------------------------------------------------------------

def find_lean_files(repo: str) -> list:
    found = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in (".git", ".lake", "lake-packages"))
        for f in sorted(files):
            if f.endswith(".lean"):
                found.append(os.path.join(root, f))
    return sorted(found)


def top_part(repo: str, path: str) -> str:
    rel = os.path.relpath(path, repo)
    head = rel.split(os.sep)[0]
    return "(root)" if head == rel else head


def audit_tree(repo: str) -> dict:
    """Full dual-method audit of every .lean file under `repo`."""
    repo = os.path.abspath(repo)
    files = find_lean_files(repo)
    file_reports = []
    totals = {t: 0 for t in TOKENS}
    parts = {}
    disagreements = []
    for path in files:
        rel = os.path.relpath(path, repo)
        fr = audit_file(path)
        if not fr["agree"]:
            disagreements.append(rel)
        # canonical numbers come from method A; B is the independent check
        hits_a = fr["per_method"]["A_state_machine"]
        entry = {"file": rel, "methods_agree": fr["agree"],
                 "counts": {t: len(hits_a[t]) for t in TOKENS},
                 "hits": {t: hits_a[t] for t in TOKENS}}
        file_reports.append(entry)
        part = top_part(repo, path)
        bucket = parts.setdefault(part, {t: 0 for t in TOKENS})
        for t in TOKENS:
            totals[t] += len(hits_a[t])
            bucket[t] += len(hits_a[t])
    parts_sum = {t: sum(parts[p][t] for p in parts) for t in TOKENS}
    parts_ok = all(parts_sum[t] == totals[t] for t in TOKENS)
    return {
        "repo": repo,
        "file_count": len(files),
        "totals": totals,
        "parts": parts,
        "parts_sum": parts_sum,
        "parts_sum_ok": parts_ok,
        "methods_agree": not disagreements,
        "method_disagreements": disagreements,
        "files": file_reports,
    }


# --------------------------------------------------------------------------
# Git / ref verification
# --------------------------------------------------------------------------

def _git(repo: str, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo] + args,
                          capture_output=True, text=True, timeout=300)


def verify_ref(repo: str, sha: str, branch: str = "main", do_fetch: bool = True) -> dict:
    """Verify a pinned SHA against the remote branch state.

    Runs `git fetch origin` with errors NOT silenced (stderr is captured
    and included in the report), reads the remote branch HEAD, and checks
    ancestry with `git merge-base --is-ancestor`.
    """
    repo = os.path.abspath(repo)
    out = {"repo": repo, "claimed_sha": sha, "branch": branch}
    if do_fetch:
        proc = _git(repo, ["fetch", "origin"])
        out["fetch_exit"] = proc.returncode
        out["fetch_stderr"] = proc.stderr.strip()
        if proc.returncode != 0:
            out["status"] = "fetch_failed"
            return out
    remote_ref = f"refs/remotes/origin/{branch}"
    proc = _git(repo, ["rev-parse", "--verify", remote_ref])
    if proc.returncode != 0:
        out["status"] = "remote_branch_not_found"
        out["detail"] = proc.stderr.strip()
        return out
    remote_head = proc.stdout.strip()
    out["remote_head"] = remote_head
    # does the claimed sha exist as a commit at all (local object store)?
    proc = _git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"])
    exists = proc.returncode == 0
    out["sha_exists_locally"] = exists
    if not exists:
        out["status"] = "sha_not_found"
        return out
    proc = _git(repo, ["merge-base", "--is-ancestor", sha, remote_ref])
    if proc.returncode == 0:
        out["status"] = "ancestor_of_remote"
        out["on_remote"] = True
    else:
        out["on_remote"] = False
        # contained in ANY remote branch?
        proc2 = _git(repo, ["branch", "-r", "--contains", sha])
        containing = [ln.strip() for ln in proc2.stdout.splitlines() if ln.strip()]
        out["remote_branches_containing"] = containing
        out["status"] = "local_only" if not containing else "not_ancestor_of_branch"
    return out


# --------------------------------------------------------------------------
# Manifest checks (claimed vs measured)
# --------------------------------------------------------------------------

def _measured_for_scope(tree: dict, scope: str | None) -> dict:
    """Recompute totals restricted to files whose rel path starts with scope."""
    totals = {t: 0 for t in TOKENS}
    fc = 0
    for entry in tree["files"]:
        if scope and not entry["file"].replace(os.sep, "/").startswith(scope):
            continue
        fc += 1
        for t in TOKENS:
            totals[t] += entry["counts"][t]
    totals["file_count"] = fc
    return totals


def load_manifest(source) -> dict:
    """Accept a manifest as a filesystem path (str), a JSON string, or a dict."""
    if isinstance(source, dict):
        return source
    if isinstance(source, str) and os.path.exists(source):
        with open(source, "r", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(source)


def normalize_claims(manifest: dict) -> list:
    claims = manifest.get("claims")
    if claims is None:
        claims = []
        for key in ("sorry_count", "admit_count", "axiom_count", "file_count"):
            if key in manifest:
                claims.append({"type": key, "value": manifest[key],
                               "scope": manifest.get("scope")})
        for ref in manifest.get("refs", []):
            claims.append({"type": "ref", **ref})
    return claims


def check_manifest(repo: str, manifest: dict, tree: dict | None = None) -> dict:
    """Evaluate every claim in the manifest against fresh measurements."""
    if tree is None:
        tree = audit_tree(repo)
    results = []
    all_pass = tree["methods_agree"] and tree["parts_sum_ok"]
    for claim in normalize_claims(manifest):
        ctype = claim.get("type")
        if ctype in ("sorry_count", "admit_count", "axiom_count", "file_count"):
            scope = claim.get("scope")
            measured_map = _measured_for_scope(tree, scope)
            key = ctype.replace("_count", "") if ctype != "file_count" else "file_count"
            measured = measured_map[key]
            claimed = claim.get("value")
            ok = (measured == claimed)
            results.append({"type": ctype, "scope": scope, "claimed": claimed,
                            "measured": measured, "pass": ok})
            all_pass = all_pass and ok
        elif ctype == "ref":
            sha = claim.get("sha")
            branch = claim.get("branch", "main")
            v = verify_ref(repo, sha, branch, do_fetch=True)
            ok = v.get("on_remote") is True
            results.append({"type": "ref", "claimed": {"sha": sha, "branch": branch},
                            "measured": v, "pass": ok})
            all_pass = all_pass and ok
        else:
            results.append({"type": ctype, "pass": False,
                            "error": f"unknown claim type: {ctype}"})
            all_pass = False
    return {"claims": results, "all_pass": all_pass}


def build_report(repo: str, manifest: dict | None = None) -> dict:
    tree = audit_tree(repo)
    report = {
        "tool": "apathy",
        "version": "1.0.0",
        "repo": tree["repo"],
        "file_count": tree["file_count"],
        "totals": tree["totals"],
        "parts": tree["parts"],
        "parts_sum_ok": tree["parts_sum_ok"],
        "methods_agree": tree["methods_agree"],
        "discrepancies": [],
        "files": tree["files"],
    }
    if not tree["methods_agree"]:
        report["discrepancies"].append(
            {"type": "METHOD DISAGREEMENT", "files": tree["method_disagreements"]})
    if not tree["parts_sum_ok"]:
        report["discrepancies"].append(
            {"type": "PARTS DO NOT SUM", "parts_sum": tree["parts_sum"],
             "totals": tree["totals"]})
    report["ok"] = tree["methods_agree"] and tree["parts_sum_ok"]
    if manifest is not None:
        chk = check_manifest(repo, manifest, tree=tree)
        report["manifest"] = chk
        for r in chk["claims"]:
            if not r["pass"]:
                report["discrepancies"].append(
                    {"type": "CLAIM FAILED", "claim": r})
        report["ok"] = report["ok"] and chk["all_pass"]
    return report


# --------------------------------------------------------------------------
# Human-readable summary + CLI
# --------------------------------------------------------------------------

def human_summary(report: dict) -> str:
    lines = []
    lines.append(f"Apathy audit — {report['repo']}")
    lines.append(f"  .lean files: {report['file_count']}")
    t = report["totals"]
    lines.append(f"  totals: sorry={t['sorry']} admit={t['admit']} axiom={t['axiom']}")
    lines.append("  parts (top-level directory):")
    for part in sorted(report["parts"]):
        p = report["parts"][part]
        lines.append(f"    {part}: sorry={p['sorry']} admit={p['admit']} axiom={p['axiom']}")
    lines.append(f"  parts sum to whole: {'YES' if report['parts_sum_ok'] else 'NO'}")
    lines.append(f"  methods agree (A state machine vs B line-oriented): "
                 f"{'YES' if report['methods_agree'] else 'NO'}")
    for entry in report["files"]:
        hits = {tok: entry["hits"][tok] for tok in TOKENS if entry["hits"][tok]}
        if hits:
            detail = ", ".join(f"{tok} at lines {','.join(map(str, ls))}"
                               for tok, ls in hits.items())
            lines.append(f"  {entry['file']}: {detail}")
    if "manifest" in report:
        lines.append("  manifest claims (claimed vs measured):")
        for r in report["manifest"]["claims"]:
            mark = "PASS" if r["pass"] else "FAIL"
            if r["type"] == "ref":
                st = r["measured"].get("status")
                lines.append(f"    [{mark}] ref {r['claimed']['sha'][:12]}… on "
                             f"{r['claimed']['branch']}: {st}")
            else:
                scope = f" scope={r['scope']}" if r.get("scope") else ""
                lines.append(f"    [{mark}] {r['type']}{scope}: claimed "
                             f"{r['claimed']}, measured {r['measured']}")
    if report["discrepancies"]:
        lines.append("  DISCREPANCIES:")
        for d in report["discrepancies"]:
            lines.append(f"    ! {json.dumps(d)}")
    lines.append(f"  verdict: {'OK' if report['ok'] else 'DISCREPANCIES FOUND'}")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="apathy", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_audit = sub.add_parser("audit", help="full audit, optionally against a manifest")
    p_audit.add_argument("repo")
    p_audit.add_argument("--manifest", default=None)
    p_audit.add_argument("--json", action="store_true", help="print JSON report only")

    p_count = sub.add_parser("count", help="dual-method token count of a repo")
    p_count.add_argument("repo")
    p_count.add_argument("--json", action="store_true")

    p_ref = sub.add_parser("verify-ref", help="verify a pinned SHA against the remote")
    p_ref.add_argument("repo")
    p_ref.add_argument("sha")
    p_ref.add_argument("branch", nargs="?", default="main")
    p_ref.add_argument("--no-fetch", action="store_true")

    args = parser.parse_args(argv)

    if args.command == "verify-ref":
        result = verify_ref(args.repo, args.sha, args.branch,
                            do_fetch=not args.no_fetch)
        print(json.dumps(result, indent=2))
        return 0 if result.get("on_remote") else 1

    manifest = None
    if args.command == "audit" and args.manifest:
        manifest = load_manifest(args.manifest)
    report = build_report(args.repo, manifest)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(human_summary(report))
        print("\n--- JSON report ---")
        print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
