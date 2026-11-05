#!/usr/bin/env python3
"""Bundle Lock — a folder-scale chain certificate for the trust harness.

The harness's composed flow seals a *claim*: a manifest is re-measured,
the measurement is stamped, and the verdict is written into the Receipts
chain. Bundle Lock seals a *set of files* the same way, generalizing the
oldest certificate in the system — the chain lock that fingerprints a
list of repositories and hashes the list itself into one master
certificate — from repositories to any folder:

  1. Every file in the folder is fingerprinted (SHA-256).
  2. The fingerprints are written into the canonical table, one
     "path:hash" line per file, paths ordered case-insensitively —
     the same canonical order the chain lock uses.
  3. The table itself is hashed into one bundle certificate.
  4. The certificate is stamped with the beacon: beacon_p is the first
     prime at or after the certificate's first eight hex digits, and
     the stamp is the beacon function chunk(beacon_p), unmodified,
     loaded from core/beacon.py.
  5. The lock record carries the full manifest, so verify can name the
     exact files that changed, were added, or were removed — and the
     record must first recompute to its own certificate and stamp, so
     a quietly edited record reports BROKEN before any file is read.

Scope, stated plainly (the lock covers files, not folders):
  - Regular files are covered, recursively, including hidden files.
  - A symlink to a regular file is covered by its target's bytes,
    under the link's path. Dangling symlinks and non-regular files
    are not covered; they are counted in the record's "skipped" list.
  - Empty subfolders are not covered — they contain no files to seal.
  - Comparison is byte-exact: re-saving a file with identical visible
    content but different bytes counts as CHANGED. That strictness is
    the point; the lock cannot be fooled by "close enough".

The lock proves the set is unchanged since locking. It does not prove
the contents are true, who wrote them, or whose hand changed a file —
it names the file, not the hand.

Standard library only. Python 3.12.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import sys
from datetime import datetime
from pathlib import Path

FORMAT = "bundle-lock/1"


def _canonical(paths) -> list[str]:
    """Canonical table order: case-insensitive by path, then exact."""
    return sorted(paths, key=lambda p: (p.lower(), p))


def certificate_from_manifest(manifest: dict) -> str:
    """SHA-256 of the canonical "path:hash" table — the chain lock."""
    table = "".join(
        f"{path}:{manifest[path]}\n" for path in _canonical(manifest))
    return hashlib.sha256(table.encode("utf-8")).hexdigest()


def _load_beacon():
    """Load core/beacon.py by path (repo layout), else import core.beacon."""
    candidate = Path(__file__).resolve().parents[2] / "core" / "beacon.py"
    if candidate.is_file():
        spec = importlib.util.spec_from_file_location(
            "zb_core_beacon", candidate)
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    from core import beacon  # type: ignore

    return beacon


_BEACON = None


def _beacon_module():
    global _BEACON
    if _BEACON is None:
        _BEACON = _load_beacon()
    return _BEACON


def beacon_for(certificate: str) -> tuple[int, str]:
    """(beacon_p, stamp): first prime at/after the certificate's first
    eight hex digits, stamped with the beacon function, unmodified."""
    beacon = _beacon_module()
    p = int(certificate[:8], 16)
    while not beacon.is_prime(p):
        p += 1
    return p, beacon.chunk(p)


def scan_folder(root) -> tuple[dict, dict, list[str]]:
    """Walk a folder: (manifest {relpath: sha256}, sizes {relpath: int},
    skipped [relpath]). Relative paths are POSIX-style."""
    root = Path(root)
    manifest: dict[str, str] = {}
    sizes: dict[str, int] = {}
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort(key=lambda s: (s.lower(), s))
        for name in sorted(filenames, key=lambda s: (s.lower(), s)):
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if not full.is_file():
                skipped.append(rel)
                continue
            digest = hashlib.sha256()
            total = 0
            with open(full, "rb") as handle:
                for block in iter(lambda: handle.read(65536), b""):
                    digest.update(block)
                    total += len(block)
            manifest[rel] = digest.hexdigest()
            sizes[rel] = total
    return manifest, sizes, _canonical(skipped)


def lock_folder(root, label: str) -> dict:
    """Fingerprint a folder and build its lock record."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"folder not found: {root}")
    manifest, sizes, skipped = scan_folder(root)
    certificate = certificate_from_manifest(manifest)
    beacon_p, stamp = beacon_for(certificate)
    return {
        "format": FORMAT,
        "label": label,
        "folder": root.name,
        "certificate": certificate,
        "beacon_p": beacon_p,
        "beacon": stamp,
        "file_count": len(manifest),
        "total_bytes": sum(sizes.values()),
        "manifest": {path: manifest[path] for path in _canonical(manifest)},
        "sizes": {path: sizes[path] for path in _canonical(sizes)},
        "skipped": skipped,
        "locked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def record_self_check(record: dict) -> tuple[bool, str | None]:
    """A lock record must recompute to its own certificate and stamp."""
    try:
        manifest = record["manifest"]
        certificate = record["certificate"]
    except (KeyError, TypeError):
        return False, "record is missing its manifest or certificate"
    if certificate_from_manifest(manifest) != certificate:
        return False, "record manifest does not recompute to its certificate"
    beacon_p, stamp = beacon_for(certificate)
    if record.get("beacon_p") != beacon_p or record.get("beacon") != stamp:
        return False, "record beacon does not recompute from its certificate"
    if record.get("file_count") != len(manifest):
        return False, "record file_count does not match its manifest"
    return True, None


def verify_manifest(current: dict, record: dict) -> dict:
    """Compare a freshly measured manifest against a lock record."""
    ok_record, problem = record_self_check(record)
    stored = record.get("manifest") or {}
    changed = _canonical(
        p for p in stored.keys() & current.keys()
        if stored[p] != current[p])
    added = _canonical(current.keys() - stored.keys())
    removed = _canonical(stored.keys() - current.keys())
    holds = ok_record and not changed and not added and not removed
    return {
        "ok": holds,
        "verdict": "LOCK HOLDS" if holds else "BROKEN",
        "label": record.get("label"),
        "certificate": record.get("certificate"),
        "current_certificate": certificate_from_manifest(current),
        "beacon": record.get("beacon"),
        "record_ok": ok_record,
        "record_problem": problem,
        "changed": changed,
        "added": added,
        "removed": removed,
        "file_count": len(current),
        "locked_file_count": len(stored),
    }


def verify_folder(root, record: dict) -> dict:
    """Recompute a folder's set and verify it against its lock record."""
    root = Path(root)
    if not root.is_dir():
        return {
            "ok": False,
            "verdict": "BROKEN",
            "label": record.get("label") if isinstance(record, dict) else None,
            "record_ok": False,
            "record_problem": None,
            "error": f"folder not found: {root}",
            "changed": [],
            "added": [],
            "removed": [],
        }
    manifest, _sizes, _skipped = scan_folder(root)
    return verify_manifest(manifest, record)


def main(argv: list[str]) -> int:
    import json

    if len(argv) >= 3 and argv[0] == "lock":
        record = lock_folder(argv[1], argv[2] if len(argv) > 2 else argv[1])
        print(json.dumps(record, indent=2))
        return 0
    if len(argv) >= 3 and argv[0] == "verify":
        with open(argv[2], "r", encoding="utf-8") as handle:
            record = json.load(handle)
        report = verify_folder(argv[1], record)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    print("usage: bundle_lock.py lock FOLDER LABEL | "
          "verify FOLDER RECORD.json", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
