#!/usr/bin/env python3
"""Anchor — reference implementation of the PUBLIC layer of the Anchor
collision-anchored addressing standard (see SPEC.md).

What this module does — and only this:

  * parse / validate addresses against the published grammar
    (/api/<router>/<block-nn>/<tool>) and canonicalize them;
  * load a registry document (a data file of already-issued addresses),
    verify its Ed25519 signature and its internal consistency;
  * resolve an address against a verified registry to a precise
    ISSUED (tool + status) or NOT ISSUED answer.

What this module deliberately does NOT do: it contains no issuance
machinery of any kind. How an address comes to be issued — how a tool
receives its block and number — is an issuer-internal matter that the
standard does not specify (SPEC.md section 8) and that this reference
implementation does not implement. A registry here is data produced
elsewhere; Anchor formats, signs, and verifies — nothing more.

Signing reuses the package's single Ed25519 implementation
(../receipts/receipts.py, RFC 8032) — one crypto implementation across
the whole Agent Trust Stack.

CLI:
  anchor.py parse          <address>
  anchor.py validate       <address>
  anchor.py sign-registry  <source.json> --key <keyfile> --out <registry.json>
  anchor.py verify-registry <registry.json>
  anchor.py verify-address <registry.json> <address>

Standard library only (plus the sibling receipts import). Python 3.12.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "receipts"))
import receipts as receipts_mod  # noqa: E402

# =====================================================================
# Address grammar (SPEC.md section 2)
#
#   address  = "/" "api" "/" block "/" tool
#   block    = router "/" block-nn
#   router   = 2 lower-alpha characters
#   block-nn = exactly 2 digits, zero-padded
#   tool     = slug: [a-z0-9_]+ after canonicalization
# =====================================================================

R_ROOT = "R-ROOT"          # must begin with the literal /api/ prefix
R_SEGMENTS = "R-SEGMENTS"  # exactly 4 path segments after the leading /
R_EMPTY = "R-EMPTY"        # no empty segments
R_TRAILING = "R-TRAILING"  # no trailing slash
R_ROUTER = "R-ROUTER"      # router is exactly two letters a-z
R_BLOCKNN = "R-BLOCKNN"    # block number is exactly two digits
R_TOOL = "R-TOOL"          # tool is a slug over [a-z0-9_]
R_FORM = "R-FORM"          # address must be a bare path (no whitespace)

_ROUTER_RE = re.compile(r"^[a-z]{2}$")
_BLOCKNN_RE = re.compile(r"^[0-9]{2}$")
_TOOL_RE = re.compile(r"^[a-z0-9_]+$")
_TOOL_RAW_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")

LEGAL_STATUSES = ("active", "retired")


class AddressError(ValueError):
    """A grammar failure: carries the SPEC rule id that was violated."""

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


def canonicalize(address: str) -> str:
    """The canonical form of a grammar-valid address (SPEC.md section 3).

    Canonicalization normalizes letter case only. Structural defects
    are grammar failures, never repaired here.
    """
    return address.lower()


def parse_address(address: str) -> dict:
    """Parse an address into its components.

    Returns {canonical, router, block, block_number, tool}.
    Raises AddressError (with the violated rule id) if the address is
    not grammar-valid. Letter case is normalized per canonicalization;
    everything else must already conform.
    """
    if not isinstance(address, str) or not address:
        raise AddressError(R_FORM, "address must be a non-empty string")
    if any(ch.isspace() for ch in address):
        raise AddressError(R_FORM, "address must not contain whitespace")
    if address.endswith("/"):
        raise AddressError(R_TRAILING, "address must not end with a slash")
    parts = address.split("/")
    if len(parts) < 2 or parts[0] != "" or parts[1].lower() != "api":
        raise AddressError(R_ROOT, "address must begin with /api/")
    segments = parts[1:]
    if any(seg == "" for seg in segments):
        raise AddressError(R_EMPTY, "address must not contain empty segments")
    if len(segments) != 4:
        raise AddressError(
            R_SEGMENTS,
            f"address must have exactly 4 segments "
            f"(api/<router>/<block-nn>/<tool>), found {len(segments)}")
    router_raw, blocknn_raw, tool_raw = segments[1], segments[2], segments[3]
    router = router_raw.lower()
    if not _ROUTER_RE.match(router):
        raise AddressError(
            R_ROUTER,
            f"router segment {router_raw!r} must be exactly two letters a-z")
    if not _BLOCKNN_RE.match(blocknn_raw):
        raise AddressError(
            R_BLOCKNN,
            f"block number {blocknn_raw!r} must be exactly two digits, "
            f"zero-padded (e.g. 03)")
    for ch in tool_raw:
        if ch not in _TOOL_RAW_CHARS:
            raise AddressError(
                R_TOOL,
                f"tool segment contains illegal character {ch!r}; "
                f"tool slugs use letters, digits, and underscore only")
    tool = tool_raw.lower()
    if not _TOOL_RE.match(tool):
        raise AddressError(R_TOOL, "tool segment must be a non-empty slug")
    canonical = canonicalize(address)
    return {
        "canonical": canonical,
        "router": router,
        "block": f"{router}/{blocknn_raw}",
        "block_number": int(blocknn_raw),
        "tool": tool,
    }


def validate_address(address: str) -> dict:
    """Validate an address; always returns a report dict (never raises)."""
    report = {"input": address, "valid": False, "canonical": None,
              "input_is_canonical": False, "rule": None, "message": None}
    try:
        parsed = parse_address(address)
    except AddressError as exc:
        report["rule"] = exc.rule
        report["message"] = exc.message
        return report
    report["valid"] = True
    report["canonical"] = parsed["canonical"]
    report["input_is_canonical"] = (address == parsed["canonical"])
    report["components"] = parsed
    report["message"] = "grammar-valid"
    return report


# =====================================================================
# Registry documents (SPEC.md sections 5-6)
#
# A registry is DATA: {registry_id, issued_at, entries, public_key,
# signature}. Entries arrive inside the document; this module loads,
# signs, and verifies them. It never constructs an entry.
# =====================================================================

def registry_body(registry: dict) -> dict:
    """The signed portion of a registry document."""
    return {
        "registry_id": registry["registry_id"],
        "issued_at": registry["issued_at"],
        "entries": registry["entries"],
    }


def registry_digest(registry: dict) -> str:
    """SHA-256 over the canonical JSON of the registry body."""
    return hashlib.sha256(receipts_mod._canonical(registry_body(registry))
                          ).hexdigest()


def sign_registry(source: dict, keypair: "receipts_mod.Keypair") -> dict:
    """Sign a registry body (loaded from a source data file).

    The entries in `source` are carried through untouched — signing
    attests to data issued elsewhere; it does not create it.
    """
    doc = {
        "registry_id": source["registry_id"],
        "issued_at": source["issued_at"],
        "entries": source["entries"],
        "public_key": keypair.public_hex,
    }
    digest = registry_digest(doc)
    signature = receipts_mod.ed25519_sign(
        bytes.fromhex(digest), keypair.secret, keypair.public)
    doc["signature"] = signature.hex()
    return doc


def load_registry(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _check(name: str, ok: bool, detail: str) -> dict:
    return {"check": name, "ok": bool(ok), "detail": detail}


def verify_registry(registry: dict) -> dict:
    """Verify a registry document: signature + internal consistency.

    Returns {ok, checks: [...]}; every check is reported individually.
    """
    checks: list[dict] = []

    # -- structure -----------------------------------------------------
    structure_ok = (
        isinstance(registry, dict)
        and isinstance(registry.get("registry_id"), str)
        and isinstance(registry.get("issued_at"), str)
        and isinstance(registry.get("entries"), list)
        and isinstance(registry.get("public_key"), str)
        and isinstance(registry.get("signature"), str))
    checks.append(_check(
        "structure", structure_ok,
        "registry_id, issued_at, entries, public_key, signature present "
        "with the right types" if structure_ok else
        "registry document is missing required fields or has wrong types"))
    if not structure_ok:
        return {"ok": False, "checks": checks}

    # -- signature ------------------------------------------------------
    try:
        public = bytes.fromhex(registry["public_key"])
        signature = bytes.fromhex(registry["signature"])
        digest = registry_digest(registry)
        sig_ok = receipts_mod.ed25519_verify(
            signature, bytes.fromhex(digest), public)
        sig_detail = ("Ed25519 signature over the registry body verifies "
                      "under the embedded public key" if sig_ok else
                      "signature does not verify — the registry body was "
                      "altered after signing, or was signed by another key")
    except Exception as exc:
        sig_ok, sig_detail = False, f"signature check errored: {exc}"
    checks.append(_check("signature", sig_ok, sig_detail))

    # -- per-entry checks ------------------------------------------------
    entries = registry["entries"]
    canon_of: list[str | None] = []
    parse_ok, parse_detail = True, "every entry address is grammar-valid"
    canon_ok, canon_detail = True, "every entry is stored in canonical form"
    status_ok, status_detail = True, "every status is active or retired"
    tool_ok, tool_detail = True, "every entry tool matches its address"
    for entry in entries:
        addr = entry.get("address") if isinstance(entry, dict) else None
        try:
            parsed = parse_address(addr)
        except AddressError as exc:
            parse_ok = False
            parse_detail = (f"entry address {addr!r} is not grammar-valid: "
                            f"{exc.rule}: {exc.message}")
            canon_of.append(None)
            continue
        canon_of.append(parsed["canonical"])
        if addr != parsed["canonical"]:
            canon_ok = False
            canon_detail = (f"entry address {addr!r} is not canonical; "
                            f"canonical form is {parsed['canonical']!r}")
        if entry.get("status") not in LEGAL_STATUSES:
            status_ok = False
            status_detail = (f"entry {addr!r} has illegal status "
                             f"{entry.get('status')!r}")
        if entry.get("tool") != parsed["tool"]:
            tool_ok = False
            tool_detail = (f"entry {addr!r} declares tool "
                           f"{entry.get('tool')!r} but its address names "
                           f"{parsed['tool']!r}")
    checks.append(_check("entries_grammar_valid", parse_ok, parse_detail))
    checks.append(_check("entries_canonical", canon_ok, canon_detail))
    checks.append(_check("statuses_legal", status_ok, status_detail))
    checks.append(_check("tool_field_matches", tool_ok, tool_detail))

    # -- duplicates (on canonical forms) ---------------------------------
    seen: dict[str, int] = {}
    duplicate = None
    for canon in canon_of:
        if canon is None:
            continue
        seen[canon] = seen.get(canon, 0) + 1
        if seen[canon] == 2:
            duplicate = canon
    checks.append(_check(
        "no_duplicate_addresses", duplicate is None,
        "no two entries share a canonical address" if duplicate is None
        else f"duplicate canonical address: {duplicate} appears "
             f"{seen[duplicate]} times"))

    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def verify_address(registry: dict, address: str) -> dict:
    """Resolve an address against a registry (SPEC.md section 7).

    The registry is verified first; an unverifiable registry resolves
    nothing. A grammar-valid address yields exactly one verdict:
    ISSUED (with tool + status) or NOT ISSUED.
    """
    reg_report = verify_registry(registry)
    if not reg_report["ok"]:
        failed = [c["check"] for c in reg_report["checks"] if not c["ok"]]
        return {"verdict": "REGISTRY_INVALID",
                "message": "registry does not verify; refusing to resolve",
                "failed_checks": failed}
    try:
        parsed = parse_address(address)
    except AddressError as exc:
        return {"verdict": "INVALID_ADDRESS", "rule": exc.rule,
                "message": exc.message}
    canonical = parsed["canonical"]
    for entry in registry["entries"]:
        if entry.get("address") == canonical:
            return {"verdict": "ISSUED", "canonical_address": canonical,
                    "tool": entry["tool"], "status": entry["status"]}
    return {"verdict": "NOT ISSUED", "canonical_address": canonical,
            "message": "no entry in this registry carries this address"}


# =====================================================================
# CLI
# =====================================================================

def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2))


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.split("CLI:")[1].split("Standard library")[0],
              file=sys.stderr)
        return 2
    cmd = argv[1]

    if cmd == "parse" and len(argv) == 3:
        try:
            parsed = parse_address(argv[2])
        except AddressError as exc:
            _print_json({"valid": False, "rule": exc.rule,
                         "message": exc.message})
            return 1
        _print_json({"valid": True, **parsed})
        return 0

    if cmd == "validate" and len(argv) == 3:
        report = validate_address(argv[2])
        _print_json(report)
        return 0 if report["valid"] else 1

    if cmd == "sign-registry" and len(argv) >= 4:
        # anchor.py sign-registry <source.json> --key <keyfile> --out <out>
        args = argv[2:]
        source_path = args[0]
        key_path = out_path = None
        i = 1
        while i < len(args):
            if args[i] == "--key" and i + 1 < len(args):
                key_path = args[i + 1]
                i += 2
            elif args[i] == "--out" and i + 1 < len(args):
                out_path = args[i + 1]
                i += 2
            else:
                print(f"unrecognized argument: {args[i]}", file=sys.stderr)
                return 2
        if not key_path or not out_path:
            print("sign-registry requires --key and --out", file=sys.stderr)
            return 2
        with open(source_path, "r", encoding="utf-8") as fh:
            source = json.load(fh)
        keypair = receipts_mod.Keypair.load_or_create(key_path)
        doc = sign_registry(source, keypair)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=2)
        print(f"signed registry {doc['registry_id']!r} "
              f"({len(doc['entries'])} entries) -> {out_path}")
        return 0

    if cmd == "verify-registry" and len(argv) == 3:
        registry = load_registry(argv[2])
        report = verify_registry(registry)
        for c in report["checks"]:
            print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['check']}: "
                  f"{c['detail']}")
        print(f"registry verification: "
              f"{'PASS' if report['ok'] else 'FAIL'}")
        return 0 if report["ok"] else 1

    if cmd == "verify-address" and len(argv) == 4:
        registry = load_registry(argv[2])
        result = verify_address(registry, argv[3])
        _print_json(result)
        if result["verdict"] == "ISSUED":
            return 0
        if result["verdict"] == "NOT ISSUED":
            return 0  # a precise not-issued is a successful resolution
        return 1

    print(f"unknown or malformed command: {' '.join(argv[1:])}",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
