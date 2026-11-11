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

Scope, stated plainly. Two record formats exist, and verify
dispatches on the record's own format:

  bundle-lock/1 (the original format; records still verify under
  exactly these rules — the lock covers files, not folders):
  - Regular files are covered, recursively, including hidden files.
  - A symlink to a regular file is covered by its target's bytes,
    under the link's path. Dangling symlinks and non-regular files
    are not covered; they are counted in the record's "skipped" list.
  - Empty subfolders are not covered — they contain no files to seal.

  bundle-lock/2 (every new lock; the folder itself is sealed):
  - Regular files are covered as above, recursively.
  - Every directory — empty ones included — is sealed by presence:
    adding or removing a directory breaks the lock, by name.
  - Every symlink is sealed by its target string (os.readlink),
    dangling or not: retargeting a link reads CHANGED, adding or
    removing one reads ADDED / REMOVED. Under /1 a dangling
    symlink is never recorded or named at all — planting one is
    invisible, and a file swapped for one surfaces only as a
    bare file removal; under /2 the link itself is named.
  - Non-regular, non-link files are counted in "skipped", as in /1.

  Both formats: comparison is byte-exact: re-saving a file with
  identical visible content but different bytes counts as CHANGED.
  That strictness is the point; the lock cannot be fooled by
  "close enough".

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
FORMAT_V2 = "bundle-lock/2"


def _canonical(paths) -> list[str]:
    """Canonical table order: case-insensitive by path, then exact."""
    return sorted(paths, key=lambda p: (p.lower(), p))


def certificate_from_manifest(manifest: dict) -> str:
    """SHA-256 of the canonical "path:hash" table — the chain lock."""
    table = "".join(
        f"{path}:{manifest[path]}\n" for path in _canonical(manifest))
    return hashlib.sha256(table.encode("utf-8")).hexdigest()


def _esc(text: str) -> str:
    """Escape a path or link target for one canonical table line."""
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace(
        "\r", "\\r")


def certificate_from_entries(manifest: dict, directories, symlinks) -> str:
    """SHA-256 of the canonical bundle-lock/2 table: one typed line
    per entry — "file:<path>:<sha256>" for files, "dir:<path>" for
    directories, "link:<path>:<target>" for symlinks — each class in
    the canonical case-insensitive path order, the table hashed into
    one certificate exactly as the /1 chain lock hashes its table."""
    lines = []
    for path in _canonical(manifest):
        lines.append(f"file:{_esc(path)}:{manifest[path]}")
    for path in _canonical(directories):
        lines.append(f"dir:{_esc(path)}")
    for path in _canonical(symlinks):
        lines.append(f"link:{_esc(path)}:{_esc(symlinks[path])}")
    table = "".join(line + "\n" for line in lines)
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


def scan_folder_v2(root) -> tuple[dict, dict, list[str], dict, list[str]]:
    """Walk a folder for a bundle-lock/2 record: (manifest
    {relpath: sha256}, sizes {relpath: int}, directories [relpath],
    symlinks {relpath: target}, skipped [relpath]). Relative paths
    are POSIX-style. Every directory — empty ones included — is
    listed; every symlink is recorded by its target string, dangling
    or not, and is never followed."""
    root = Path(root)
    manifest: dict[str, str] = {}
    sizes: dict[str, int] = {}
    directories: list[str] = []
    symlinks: dict[str, str] = {}
    skipped: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort(key=lambda s: (s.lower(), s))
        base = Path(dirpath)
        for name in dirnames:
            full = base / name
            rel = full.relative_to(root).as_posix()
            if os.path.islink(full):
                symlinks[rel] = os.readlink(full)
            else:
                directories.append(rel)
        for name in sorted(filenames, key=lambda s: (s.lower(), s)):
            full = base / name
            rel = full.relative_to(root).as_posix()
            if os.path.islink(full):
                symlinks[rel] = os.readlink(full)
                continue
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
    return (
        manifest,
        sizes,
        _canonical(directories),
        {path: symlinks[path] for path in _canonical(symlinks)},
        _canonical(skipped),
    )


def lock_folder(root, label: str) -> dict:
    """Fingerprint a folder and build its lock record
    (bundle-lock/2: files, directories and symlinks are sealed)."""
    root = Path(root)
    if not root.is_dir():
        raise ValueError(f"folder not found: {root}")
    manifest, sizes, directories, symlinks, skipped = scan_folder_v2(root)
    certificate = certificate_from_entries(manifest, directories, symlinks)
    beacon_p, stamp = beacon_for(certificate)
    return {
        "format": FORMAT_V2,
        "label": label,
        "folder": root.name,
        "certificate": certificate,
        "beacon_p": beacon_p,
        "beacon": stamp,
        "file_count": len(manifest),
        "total_bytes": sum(sizes.values()),
        "directory_count": len(directories),
        "symlink_count": len(symlinks),
        "manifest": {path: manifest[path] for path in _canonical(manifest)},
        "sizes": {path: sizes[path] for path in _canonical(sizes)},
        "directories": directories,
        "symlinks": symlinks,
        "skipped": skipped,
        "locked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _record_self_check_v1(record: dict) -> tuple[bool, str | None]:
    """A bundle-lock/1 record must recompute to its own certificate
    and stamp, under the /1 rules exactly as first shipped."""
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


def _record_self_check_v2(record: dict) -> tuple[bool, str | None]:
    """A bundle-lock/2 record must recompute to its own certificate
    and stamp from its file, directory and symlink entries."""
    try:
        manifest = record["manifest"]
        directories = record["directories"]
        symlinks = record["symlinks"]
        certificate = record["certificate"]
    except (KeyError, TypeError):
        return False, ("record is missing its manifest, directories, "
                       "symlinks or certificate")
    if not isinstance(directories, list) or not isinstance(symlinks, dict):
        return False, "record directories or symlinks are malformed"
    if certificate_from_entries(manifest, directories, symlinks) \
            != certificate:
        return False, "record entries do not recompute to its certificate"
    beacon_p, stamp = beacon_for(certificate)
    if record.get("beacon_p") != beacon_p or record.get("beacon") != stamp:
        return False, "record beacon does not recompute from its certificate"
    if record.get("file_count") != len(manifest):
        return False, "record file_count does not match its manifest"
    if record.get("directory_count") != len(directories):
        return False, "record directory_count does not match its directories"
    if record.get("symlink_count") != len(symlinks):
        return False, "record symlink_count does not match its symlinks"
    return True, None


def record_self_check(record: dict) -> tuple[bool, str | None]:
    """A lock record must recompute to its own certificate and stamp.
    Dispatches on the record's format: bundle-lock/2 records check
    under the /2 rules; everything else under the /1 rules."""
    if isinstance(record, dict) and record.get("format") == FORMAT_V2:
        return _record_self_check_v2(record)
    return _record_self_check_v1(record)


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


def verify_entries(current: dict, current_directories,
                     current_symlinks, record: dict) -> dict:
    """Compare a freshly measured bundle-lock/2 scan against a /2
    lock record: files, directories and symlinks, each named as
    changed, added or removed."""
    ok_record, problem = record_self_check(record)
    stored = record.get("manifest") or {}
    changed = _canonical(
        p for p in stored.keys() & current.keys()
        if stored[p] != current[p])
    added = _canonical(current.keys() - stored.keys())
    removed = _canonical(stored.keys() - current.keys())
    stored_dirs = set(record.get("directories") or [])
    current_dirs = set(current_directories)
    dirs_added = _canonical(current_dirs - stored_dirs)
    dirs_removed = _canonical(stored_dirs - current_dirs)
    stored_links = record.get("symlinks") or {}
    links_changed = _canonical(
        p for p in stored_links.keys() & current_symlinks.keys()
        if stored_links[p] != current_symlinks[p])
    links_added = _canonical(current_symlinks.keys() - stored_links.keys())
    links_removed = _canonical(stored_links.keys() - current_symlinks.keys())
    holds = ok_record and not (
        changed or added or removed or dirs_added or dirs_removed
        or links_changed or links_added or links_removed)
    return {
        "ok": holds,
        "verdict": "LOCK HOLDS" if holds else "BROKEN",
        "label": record.get("label"),
        "certificate": record.get("certificate"),
        "current_certificate": certificate_from_entries(
            current, current_directories, current_symlinks),
        "beacon": record.get("beacon"),
        "record_ok": ok_record,
        "record_problem": problem,
        "changed": changed,
        "added": added,
        "removed": removed,
        "dirs_added": dirs_added,
        "dirs_removed": dirs_removed,
        "links_changed": links_changed,
        "links_added": links_added,
        "links_removed": links_removed,
        "file_count": len(current),
        "locked_file_count": len(stored),
        "directory_count": len(current_dirs),
        "locked_directory_count": len(stored_dirs),
        "symlink_count": len(current_symlinks),
        "locked_symlink_count": len(stored_links),
    }


def verify_folder(root, record: dict) -> dict:
    """Recompute a folder's set and verify it against its lock record.
    Dispatches on the record's format: bundle-lock/2 records verify
    files, directories and symlinks; bundle-lock/1 records verify
    under the /1 rules exactly as first shipped."""
    root = Path(root)
    is_v2 = isinstance(record, dict) and record.get("format") == FORMAT_V2
    if not root.is_dir():
        report = {
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
        if is_v2:
            report.update({
                "dirs_added": [],
                "dirs_removed": [],
                "links_changed": [],
                "links_added": [],
                "links_removed": [],
            })
        return report
    if is_v2:
        manifest, _sizes, directories, symlinks, _skipped = \
            scan_folder_v2(root)
        return verify_entries(manifest, directories, symlinks, record)
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
