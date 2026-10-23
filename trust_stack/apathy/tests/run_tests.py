#!/usr/bin/env python3
"""Apathy v1 test suite.

Builds a fixture Lean tree full of the October traps (sorrys that live only
in comments and strings, nested block comments, a "SORRY: 0" claim that is
false) and asserts the dual-method audit measures the truth.

Run:  python3 tests/run_tests.py
Exit: 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import apathy  # noqa: E402

RESULTS = []


def check(name: str, condition: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(condition)))
    mark = "PASS" if condition else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{mark}] {name}{suffix}")


# ---------------------------------------------------------------------------
# Fixture tree
# ---------------------------------------------------------------------------

COMMENTS_LEAN = "\n".join([
    "-- sorry admit axiom all in a line comment",
    "/- block: sorry",
    "   /- nested: sorry -/",
    "   outer again: sorry -/",
    "/-- doc comment: sorry -/",
    "/-! module doc: sorry -/",
    'def msg : String := "sorry admit axiom in a string with -- and /- markers and an escaped \\" quote: sorry"',
    "theorem fine : True := trivial",
    'def quote : String := "he said \\"sorry\\" loudly"',
]) + "\n"

REAL_LEAN_LINES = [
    "import Mathlib",                                   # 1
    "",                                                 # 2
    "theorem a : True := by",                           # 3
    "  sorry",                                          # 4  sorry
    "theorem b : True := by",                           # 5
    "  trivial",                                        # 6
    "-- a sorry in a comment does not count",           # 7
    "theorem c : True := by",                           # 8
    "  sorry",                                          # 9  sorry
    "theorem d : Nat := by",                            # 10
    "  admit",                                          # 11 admit
    'def s : String := "sorry inside string"',          # 12
    "theorem e : True := by",                           # 13
    "  sorry",                                          # 14 sorry
]
REAL_LEAN = "\n".join(REAL_LEAN_LINES) + "\n"

NESTED_LEAN_LINES = [
    "/- outer comment begins",                          # 1
    "   sorry inside outer (before inner)",             # 2
    "   /- inner comment",                              # 3
    "      sorry inside inner",                         # 4
    "   -/ inner closes",                               # 5
    "   sorry inside outer (after inner closed)",      # 6
    "-/ outer closes",                                  # 7
    "theorem real : True := by",                        # 8
    "  sorry",                                          # 9  sorry
]
NESTED_LEAN = "\n".join(NESTED_LEAN_LINES) + "\n"

AXIOMS_LEAN = "\n".join([
    "axiom my_axiom : True",                            # 1 axiom
    "-- axiom in a comment does not count",
    'def t : String := "axiom in a string"',
]) + "\n"


def build_fixture() -> str:
    root = tempfile.mkdtemp(prefix="apathy_fixture_")
    files = {
        os.path.join("src", "Comments.lean"): COMMENTS_LEAN,
        os.path.join("src", "Real.lean"): REAL_LEAN,
        os.path.join("deep", "Nested.lean"): NESTED_LEAN,
        os.path.join("deep", "Axioms.lean"): AXIOMS_LEAN,
    }
    for rel, content in files.items():
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
    return root


def file_entry(report: dict, suffix: str) -> dict:
    for entry in report["files"]:
        if entry["file"].replace(os.sep, "/").endswith(suffix):
            return entry
    raise AssertionError(f"file not in report: {suffix}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_a_comments_only(root: str, report: dict) -> None:
    entry = file_entry(report, "src/Comments.lean")
    naive = COMMENTS_LEAN.count("sorry")
    check("a1: comments/strings-only file measures 0 true sorrys",
          entry["hits"]["sorry"] == [] and entry["counts"]["sorry"] == 0,
          f"measured={entry['counts']['sorry']}")
    check("a2: naive grep counts the comment sorrys (the trap is real)",
          naive >= 5 and naive != entry["counts"]["sorry"],
          f"naive substring count={naive}")
    check("a3: comments-only file measures 0 admit and 0 axiom",
          entry["counts"]["admit"] == 0 and entry["counts"]["axiom"] == 0)


def test_b_real_counts(root: str, report: dict) -> None:
    entry = file_entry(report, "src/Real.lean")
    check("b1: exactly 3 real sorrys", entry["counts"]["sorry"] == 3,
          f"measured={entry['counts']['sorry']}")
    check("b2: sorry line numbers are exactly [4, 9, 14]",
          entry["hits"]["sorry"] == [4, 9, 14],
          f"lines={entry['hits']['sorry']}")
    check("b3: exactly 1 admit, at line 11",
          entry["counts"]["admit"] == 1 and entry["hits"]["admit"] == [11],
          f"admit lines={entry['hits']['admit']}")


def test_c_nested(root: str, report: dict) -> None:
    entry = file_entry(report, "deep/Nested.lean")
    check("c1: sorrys inside the outer block (incl. after the inner closed) are NOT counted",
          entry["hits"]["sorry"] == [9],
          f"lines={entry['hits']['sorry']}")
    check("c2: the sorry after the nested block structure closes IS counted",
          entry["counts"]["sorry"] == 1)


def test_d_manifest(root: str, report: dict) -> None:
    bad = {"claims": [{"type": "sorry_count", "value": 0}]}
    rep_bad = apathy.build_report(root, bad)
    claim0 = rep_bad["manifest"]["claims"][0]
    check("d1: manifest claiming sorry_count 0 FAILS", claim0["pass"] is False
          and rep_bad["ok"] is False)
    check("d2: the failed claim reports the measured count (4)",
          claim0["measured"] == 4, f"measured={claim0['measured']}")
    good = {"claims": [
        {"type": "sorry_count", "value": 4},
        {"type": "admit_count", "value": 1},
        {"type": "axiom_count", "value": 1},
        {"type": "file_count", "value": 4},
        {"type": "sorry_count", "value": 3, "scope": "src/"},
        {"type": "sorry_count", "value": 1, "scope": "deep/"},
    ]}
    rep_good = apathy.build_report(root, good)
    check("d3: a correct manifest PASSES in full",
          rep_good["manifest"]["all_pass"] is True and rep_good["ok"] is True,
          f"claims={[(c['type'], c.get('scope'), c['pass']) for c in rep_good['manifest']['claims']]}")


def test_e_parts_sum(root: str, report: dict) -> None:
    check("e1: parts_sum_ok is true", report["parts_sum_ok"] is True)
    sums = {t: sum(report["parts"][p][t] for p in report["parts"]) for t in apathy.TOKENS}
    check("e2: per-directory parts sum to the whole, per token",
          sums == report["totals"], f"parts sum={sums}, totals={report['totals']}")
    check("e3: fixture totals are sorry=4 admit=1 axiom=1 over 4 files",
          report["totals"] == {"sorry": 4, "admit": 1, "axiom": 1}
          and report["file_count"] == 4,
          f"totals={report['totals']}, files={report['file_count']}")


def test_f_methods_agree(root: str, report: dict) -> None:
    check("f1: report-level methods_agree is true", report["methods_agree"] is True)
    for entry in report["files"]:
        check(f"f2: methods agree on {entry['file']}", entry["methods_agree"] is True)
    # direct per-file comparison of the two strippers' counts
    for rel in ("src/Comments.lean", "src/Real.lean", "deep/Nested.lean", "deep/Axioms.lean"):
        fr = apathy.audit_file(os.path.join(root, rel))
        a = fr["per_method"]["A_state_machine"]
        b = fr["per_method"]["B_line_oriented"]
        check(f"f3: A and B hit-lists identical for {rel}", a == b)


def test_g_cli_exit_codes(root: str) -> None:
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cli = os.path.join(here, "apathy.py")
    ok = subprocess.run([sys.executable, cli, "count", root],
                        capture_output=True, text=True)
    check("g1: CLI `count` exits 0 on the clean fixture", ok.returncode == 0,
          f"exit={ok.returncode}")
    import json as _json
    manifest_path = os.path.join(root, "bad_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        _json.dump({"claims": [{"type": "sorry_count", "value": 0}]}, fh)
    bad = subprocess.run([sys.executable, cli, "audit", root,
                          "--manifest", manifest_path],
                         capture_output=True, text=True)
    check("g2: CLI `audit` exits 1 when a claim fails", bad.returncode == 1,
          f"exit={bad.returncode}")


def main() -> int:
    root = build_fixture()
    print(f"fixture: {root}\n")
    report = apathy.build_report(root)
    for fn in (test_a_comments_only, test_b_real_counts, test_c_nested,
               test_d_manifest, test_e_parts_sum, test_f_methods_agree):
        try:
            fn(root, report)
        except Exception as exc:
            check(f"{fn.__name__} raised", False, repr(exc))
    try:
        test_g_cli_exit_codes(root)
    except Exception as exc:
        check("test_g_cli_exit_codes raised", False, repr(exc))
    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:", "; ".join(failed))
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
