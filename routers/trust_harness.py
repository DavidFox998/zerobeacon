"""Trust harness — Apathy, Proven, Anchor, Receipts, and the composed flow.

Mounted outside ROUTERS. The catalogue MCP list and /health counts stay
on the existing surface. This router is the judges' surface.

Anchor reads resolve against the shipped signed demo registry. When
ANCHOR_SIGNING_SECRET is set, a passing flow also records the certified
address in a live registry beside the receipts chain and re-signs it;
an address already in the live registry is not recorded twice. Nothing
here decides which address a tool gets: the live registry only records
addresses the demo registry already issued. With no secret, behaviour
is demo resolution and responses say live issuance is unconfigured.
Receipts is exposed both ways: REST append/verify and the
append_receipt/verify_receipts MCP tools, which write to the same
persisted chain.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import string
import sys
import threading
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

_ROOT = Path(__file__).resolve().parents[1] / "trust_stack"
_REGISTRY = _ROOT / "anchor" / "registry.json"

_LOCK = threading.Lock()
_CHAIN = None
_BUNDLE_PATH: str | None = None

_LIVE_LOCK = threading.Lock()
LIVE_REGISTRY_ID = "anchor-live-registry"
LIVE_REGISTRY_FILE = "anchor-live-registry.json"
LIVE_SECRET_ENV = "ANCHOR_SIGNING_SECRET"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


receipts = _load("receipts", _ROOT / "receipts" / "receipts.py")
anchor = _load("anchor", _ROOT / "anchor" / "anchor.py")
apathy = _load("apathy", _ROOT / "apathy" / "apathy.py")
proven = _load("proven", _ROOT / "proven" / "proven.py")
apathy_mcp = _load("apathy_mcp", _ROOT / "apathy" / "apathy_mcp.py")
proven_mcp = _load("proven_mcp", _ROOT / "proven" / "proven_mcp.py")
anchor_mcp = _load("anchor_mcp", _ROOT / "anchor" / "anchor_mcp.py")
receipts_mcp = _load("receipts_mcp", _ROOT / "receipts" / "receipts_mcp.py")
disputes = _load("disputes", _ROOT / "disputes" / "disputes.py")
disputes_mcp = _load("disputes_mcp", _ROOT / "disputes" / "disputes_mcp.py")
amazon_pay = _load(
    "amazon_pay", _ROOT / "rails" / "amazon_pay" / "amazon_pay.py")


def _receipts_call(name: str, arguments: dict) -> dict:
    # Same chain as REST: route MCP append through record_receipt.
    return receipts_mcp.call_tool(name, arguments, recorder=record_receipt)


def _registry_info_call(name: str, arguments: dict) -> dict:
    # Demo result exactly as anchor_mcp reports it, labelled, plus live state.
    result = anchor_mcp.call_tool(name, arguments)
    try:
        payload = json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    payload["registry_source"] = "demo"
    payload["live"] = live_state()
    result["content"][0]["text"] = json.dumps(payload, indent=2)
    return result


def _purchase_record_for(rail: str, arguments: dict) -> dict | None:
    """The recorded purchase behind a dispute route, read from the
    captured-order stores. None when nothing matches."""
    if rail == "paypal":
        store = _captured_orders()
        record = None
        if arguments.get("order_id"):
            record = store.get(arguments["order_id"])
        if record is None and arguments.get("capture_id"):
            record = next(
                (entry for entry in store.values()
                 if entry.get("paypal", {}).get("capture_id")
                 == arguments["capture_id"]),
                None,
            )
        if record is None:
            return None
        return {"rail": "paypal", **record["paypal"],
                "receipt": record.get("receipt")}
    if rail == "amazon_pay":
        store = _amazon_captured()
        record = None
        if arguments.get("session_id"):
            record = store.get(arguments["session_id"])
        if record is None and arguments.get("charge_id"):
            record = next(
                (entry for entry in store.values()
                 if entry.get("amazonpay", {}).get("charge_id")
                 == arguments["charge_id"]),
                None,
            )
        if record is None:
            return None
        return {"rail": "amazon_pay", **record["amazonpay"],
                "receipt": record.get("receipt"),
                "certificate": record.get("certificate"),
                "item": record.get("item")}
    return None


def _dispute_evidence(rail: str, arguments: dict):
    """(purchase, bundle) for a dispute route: the store record plus
    the live chain bundle, which the disputes module verifies."""
    canonical = str(rail).strip().lower()
    return _purchase_record_for(canonical, arguments), _chain().to_bundle()


def _disputes_call(name: str, arguments: dict) -> dict:
    return disputes_mcp.call_tool(
        name, arguments, evidence_provider=_dispute_evidence)


_DISPATCH = {
    "audit_repo": apathy_mcp.call_tool,
    "check_manifest": apathy_mcp.call_tool,
    "verify_ref": apathy_mcp.call_tool,
    "probe_server": proven_mcp.call_tool,
    "get_facts": proven_mcp.call_tool,
    "check_drift": proven_mcp.call_tool,
    "parse_address": anchor_mcp.call_tool,
    "verify_address": anchor_mcp.call_tool,
    "registry_info": _registry_info_call,
    "append_receipt": _receipts_call,
    "verify_receipts": _receipts_call,
    "dispute_route": _disputes_call,
}

HARNESS_TOOL_NAMES = tuple(_DISPATCH)

public_router = APIRouter(tags=["Trust harness"])
router = APIRouter(prefix="/api/trust", tags=["Trust harness"])


def _data_dir() -> str:
    configured = os.environ.get("TRUST_DATA_DIR", "").strip()
    if configured:
        return configured
    app_data = Path("/app/data")
    if app_data.is_dir() and os.access(app_data, os.W_OK):
        return str(app_data / "trust")
    return "/tmp/zerobeacon-trust"


def _read_json(request_body: bytes) -> tuple[dict | None, JSONResponse | None]:
    try:
        body = json.loads(request_body.decode("utf-8") if request_body else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, JSONResponse({"ok": False, "error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return None, JSONResponse({"ok": False, "error": "JSON body must be an object"}, status_code=400)
    return body, None


def _chain():
    global _CHAIN, _BUNDLE_PATH
    if _CHAIN is not None:
        return _CHAIN
    directory = _data_dir()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    key_path = os.path.join(directory, "receipts-key.json")
    bundle_path = os.path.join(directory, "receipts-bundle.json")
    keypair = receipts.Keypair.load_or_create(key_path)
    if os.path.exists(bundle_path):
        with open(bundle_path, "r", encoding="utf-8") as handle:
            bundle = json.load(handle)
        _CHAIN = receipts.ReceiptChain.from_bundle(keypair, bundle)
    else:
        _CHAIN = receipts.ReceiptChain(keypair)
    _BUNDLE_PATH = bundle_path
    return _CHAIN


def record_receipt(actor: str, action: dict, result_status) -> dict:
    """Append one signed receipt and persist the verifiable bundle."""
    with _LOCK:
        chain = _chain()
        receipt = chain.append(actor, action, result_status)
        bundle = chain.to_bundle()
        path = _BUNDLE_PATH
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(bundle, handle)
        os.chmod(path, 0o600)
        ok, detail = receipts.verify_bundle(bundle)
    return {"receipt": receipt, "bundle": bundle, "bundle_ok": ok, "bundle_detail": detail}


def _shipped_registry() -> dict:
    return anchor.load_registry(str(_REGISTRY))


# -- live registry ------------------------------------------------------
# Same document schema as the demo registry, signed with anchor.sign_registry
# under a keypair derived from ANCHOR_SIGNING_SECRET (hex 32-byte seed). The
# secret is read at call time and never logged, echoed, or written.


def _live_path() -> str:
    return os.path.join(_data_dir(), LIVE_REGISTRY_FILE)


def _live_keypair():
    """Return (keypair, None) or (None, reason). Never echoes the value."""
    raw = os.environ.get(LIVE_SECRET_ENV, "").strip()
    if not raw:
        return None, f"{LIVE_SECRET_ENV} is not set; live issuance is unconfigured"
    if len(raw) != 64 or any(ch not in string.hexdigits for ch in raw):
        return None, (
            f"{LIVE_SECRET_ENV} is malformed (expected 64 hex characters); "
            "live issuance is unconfigured"
        )
    return receipts.Keypair(bytes.fromhex(raw)), None


def _load_live() -> dict | None:
    path = _live_path()
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_live(doc: dict) -> None:
    path = _live_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(doc, handle, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def live_state(include_checks: bool = True) -> dict:
    """Live registry state: configured or not, count, digest, key, verifies."""
    keypair, reason = _live_keypair()
    state = {
        "registry_source": "live",
        "registry_id": LIVE_REGISTRY_ID,
        "configured": keypair is not None,
    }
    if keypair is None:
        state["unconfigured_reason"] = reason
    try:
        doc = _load_live()
    except (OSError, json.JSONDecodeError) as exc:
        state.update({"exists": True, "verifies": False,
                      "error": f"live registry unreadable: {type(exc).__name__}"})
        return state
    if doc is None:
        state.update({"exists": False, "entries": 0, "digest": None,
                      "public_key": keypair.public_hex if keypair else None,
                      "verifies": None})
        return state
    report = anchor.verify_registry(doc)
    state.update({
        "exists": True,
        "issued_at": doc.get("issued_at"),
        "entries": len(doc.get("entries") or []),
        "digest": anchor.registry_digest(doc) if report["checks"][0]["ok"] else None,
        "public_key": doc.get("public_key"),
        "verifies": report["ok"],
    })
    if keypair is not None:
        state["key_matches_configured"] = doc.get("public_key") == keypair.public_hex
    if include_checks:
        state["checks"] = report["checks"]
    return state


def issue_live(certificate: dict) -> dict:
    """Record a certified address in the live registry, at most once.

    Returns {"live_issuance": issued | already_issued | unconfigured |
    blocked, ...}. Only "issued" writes; every other outcome leaves the
    live registry byte-unchanged.
    """
    keypair, reason = _live_keypair()
    if keypair is None:
        return {"live_issuance": "unconfigured", "detail": reason}
    address = certificate["canonical_address"]
    with _LIVE_LOCK:
        try:
            existing = _load_live()
        except (OSError, json.JSONDecodeError) as exc:
            return {"live_issuance": "blocked",
                    "detail": f"live registry unreadable ({type(exc).__name__}); not extended"}
        entries: list = []
        if existing is not None:
            report = anchor.verify_registry(existing)
            if not report["ok"]:
                failed = [c["check"] for c in report["checks"] if not c["ok"]]
                return {"live_issuance": "blocked",
                        "detail": "live registry does not verify; not extended",
                        "failed_checks": failed}
            if existing.get("public_key") != keypair.public_hex:
                return {"live_issuance": "blocked",
                        "detail": "live registry is signed by a different key "
                                  "than the configured one; not extended"}
            entries = list(existing["entries"])
            if any(entry.get("address") == address for entry in entries):
                return {"live_issuance": "already_issued"}
        record = {
            "address": address,
            "tool": certificate["tool"],
            "status": certificate["status"],
        }
        doc = anchor.sign_registry({
            "registry_id": LIVE_REGISTRY_ID,
            "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "entries": entries + [record],
        }, keypair)
        report = anchor.verify_registry(doc)
        if not report["ok"]:
            failed = [c["check"] for c in report["checks"] if not c["ok"]]
            return {"live_issuance": "blocked",
                    "detail": "re-signed live registry would not verify; not written",
                    "failed_checks": failed}
        _write_live(doc)
    return {"live_issuance": "issued"}


def _audit_failures(report: dict) -> list[dict]:
    failed = []
    for claim in report.get("claims") or []:
        if claim.get("pass"):
            continue
        failed.append({
            "check": claim.get("type") or "unknown_claim",
            "claimed": claim.get("claimed"),
            "measured": claim.get("measured"),
            "scope": claim.get("scope"),
            "error": claim.get("error"),
        })
    if not report.get("all_pass") and not failed:
        failed.append({
            "check": "audit_consistency",
            "detail": "methods disagree or parts do not sum",
        })
    return failed


def _verify_failure(facts: dict) -> dict | None:
    if facts.get("verdict") == "MATCH":
        return None
    return {
        "check": "proven_verdict",
        "verdict": facts.get("verdict"),
        "claimed_tools": facts.get("claimed_tools"),
        "measured_tools": facts.get("measured_tools"),
        "name": facts.get("name"),
    }


def run_flow(proposal: dict, host: str) -> dict:
    """Proposal in, audit + verify, certificate or a named refusal, receipt always."""
    missing = [key for key in ("repo", "manifest", "server", "address") if key not in proposal]
    action = {"method": "POST", "host": host, "path": "/api/trust/flow"}
    if missing or not isinstance(proposal.get("manifest"), dict):
        detail = "proposal requires repo, manifest object, server, and address"
        if missing:
            detail = "proposal missing " + ", ".join(missing)
        elif not isinstance(proposal.get("manifest"), dict):
            detail = "manifest must be a JSON object"
        recorded = record_receipt("trust-flow", action, "refusal:proposal_shape")
        return {
            "ok": False,
            "refusal": {"failed_checks": [{"check": "proposal_shape", "detail": detail}]},
            "certificate": None,
            "receipt": recorded["receipt"],
            "receipt_verifies": recorded["bundle_ok"],
            **_refusal_live_fields(),
        }

    audit = apathy.check_manifest(proposal["repo"], proposal["manifest"])
    try:
        facts = proven.get_facts(proposal["server"])
    except Exception as exc:
        facts = {"name": proposal["server"], "verdict": "UNPROBED", "error": str(exc)}

    failed = _audit_failures(audit)
    verify_failed = _verify_failure(facts)
    if verify_failed is not None:
        failed.append(verify_failed)

    certificate = None
    live = None
    if not failed:
        registry = _shipped_registry()
        resolution = anchor.verify_address(registry, proposal["address"])
        if resolution.get("verdict") == "ISSUED":
            certificate = {
                "verdict": "ISSUED",
                "canonical_address": resolution["canonical_address"],
                "tool": resolution["tool"],
                "status": resolution["status"],
                "registry_id": registry.get("registry_id"),
            }
            live = issue_live(certificate)
            on_live = live["live_issuance"] in ("issued", "already_issued")
            certificate["registry_source"] = "live" if on_live else "demo"
            if on_live:
                certificate["live_registry_id"] = LIVE_REGISTRY_ID
            status = "certificate"
        else:
            failed.append({
                "check": "anchor_verdict",
                "verdict": resolution.get("verdict"),
                "message": resolution.get("message"),
                "failed_checks": resolution.get("failed_checks"),
            })
            status = "refusal:" + ",".join(item["check"] for item in failed)
    else:
        status = "refusal:" + ",".join(item["check"] for item in failed)

    recorded = record_receipt("trust-flow", action, status)
    body = {
        "ok": certificate is not None,
        "audit": audit,
        "verify": facts,
        "certificate": certificate,
        "receipt": recorded["receipt"],
        "receipt_verifies": recorded["bundle_ok"],
    }
    if certificate is None:
        body["refusal"] = {"failed_checks": failed}
        body.update(_refusal_live_fields())
    else:
        body["registry_source"] = certificate["registry_source"]
        body["live_issuance"] = live["live_issuance"]
        if "detail" in live:
            body["live_issuance_detail"] = live["detail"]
        if "failed_checks" in live:
            body["live_issuance_failed_checks"] = live["failed_checks"]
        body["live_registry"] = live_state(include_checks=False)
    return body


def _refusal_live_fields() -> dict:
    """A refusal never touches the live registry; say so, and whether it is configured."""
    keypair, reason = _live_keypair()
    fields = {
        "registry_source": None,
        "live_issuance": "not_issued" if keypair is not None else "unconfigured",
        "live_registry": live_state(include_checks=False),
    }
    if keypair is None:
        fields["live_issuance_detail"] = reason
    return fields


_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ZeroBeacon trust harness</title>
<style>
  :root { color-scheme: light dark; }
  body { margin: 0; font: 16px/1.45 system-ui, sans-serif; }
  main { max-width: 42rem; margin: 0 auto; padding: 1.5rem 1rem 3rem; }
  h1 { font-size: 1.6rem; line-height: 1.2; margin-bottom: 0.3rem; }
  h2 { font-size: 1.05rem; margin-top: 1.6rem; }
  p, li { max-width: 40rem; }
  code { font-size: 0.92em; }
  table { width: 100%; border-collapse: collapse; font-size: 0.92rem; }
  th, td { text-align: left; vertical-align: top; padding: 0.4rem 0.5rem 0.4rem 0; border-bottom: 1px solid #8884; }
  .flow { padding: 0.8rem 1rem; border: 1px solid #8886; border-radius: 6px; }
</style>
</head>
<body>
<main>
  <h1>ZeroBeacon trust harness</h1>
  <p>Four hosted modules and one composed flow. Every call except this page
  requires an enterprise <code>X-API-Key</code>.</p>

  <h2>Endpoints</h2>
  <table>
    <thead><tr><th>Module</th><th>REST</th><th>MCP tool</th></tr></thead>
    <tbody>
      <tr><td>Apathy, audit</td><td><code>POST /api/trust/apathy/audit</code><br><code>POST /api/trust/apathy/check</code><br><code>POST /api/trust/apathy/verify-ref</code></td><td><code>audit_repo</code><br><code>check_manifest</code><br><code>verify_ref</code></td></tr>
      <tr><td>Proven, verify</td><td><code>POST /api/trust/proven/facts</code><br><code>POST /api/trust/proven/probe</code><br><code>POST /api/trust/proven/drift</code></td><td><code>get_facts</code><br><code>probe_server</code><br><code>check_drift</code></td></tr>
      <tr><td>Anchor, certificate</td><td><code>POST /api/trust/anchor/parse</code><br><code>POST /api/trust/anchor/verify</code><br><code>POST /api/trust/anchor/registry</code></td><td><code>parse_address</code><br><code>verify_address</code><br><code>registry_info</code></td></tr>
      <tr><td>Receipts, record</td><td><code>POST /api/trust/receipts/append</code><br><code>POST /api/trust/receipts/verify</code></td><td><code>append_receipt</code><br><code>verify_receipts</code></td></tr>
      <tr><td>Disputes, routing</td><td><code>POST /api/trust/disputes/route</code></td><td><code>dispute_route</code></td></tr>
      <tr><td>Amazon Pay rail (sandbox-verified)</td><td><code>POST /api/trust/amazonpay/propose</code><br><code>POST /api/trust/amazonpay/capture</code></td><td>—</td></tr>
    </tbody>
  </table>
  <p>MCP transport for the twelve tools above is <code>POST /api/trust/mcp</code>
  (JSON-RPC, protocol 2025-11-25). The catalogue <code>POST /mcp</code> list
  is unchanged.</p>

  <h2>Flow</h2>
  <div class="flow">
    <p><code>POST /api/trust/flow</code></p>
    <p>Proposal in (<code>repo</code>, <code>manifest</code>, <code>server</code>,
    <code>address</code>) → Apathy checks the manifest and Proven reads the
    measured verdict → an Anchor certificate when the address is already
    ISSUED in the signed registry, or a refusal that names the failed check.
    A receipt is recorded on both paths. No certificate is returned when a
    check fails.</p>
    <p>When the server holds an Anchor signing secret, a passing flow also
    records the certified address once in a separate live registry and
    re-signs it (<code>registry_source: "live"</code>); a refusal never
    writes to it. Without the secret, the certificate comes from the demo
    registry (<code>registry_source: "demo"</code>) and the response says
    live issuance is unconfigured. <code>registry_info</code> and
    <code>POST /api/trust/anchor/registry</code> report both registries,
    labelled.</p>
  </div>
</main>
</body>
</html>
"""


@public_router.get("/trust", response_class=HTMLResponse)
def showroom_page():
    """Static status page naming the four endpoints and the composed flow."""
    return HTMLResponse(_PAGE)


@router.post("/apathy/audit")
async def apathy_audit(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("repo"), str):
        return JSONResponse({"ok": False, "error": "repo is required"}, status_code=400)
    manifest = None
    if body.get("manifest") is not None:
        manifest = apathy.load_manifest(body["manifest"])
    report = apathy.build_report(body["repo"], manifest)
    return {"ok": bool(report.get("ok")), "report": report}


@router.post("/apathy/check")
async def apathy_check(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("repo"), str) or body.get("manifest") is None:
        return JSONResponse({"ok": False, "error": "repo and manifest are required"}, status_code=400)
    report = apathy.check_manifest(body["repo"], apathy.load_manifest(body["manifest"]))
    return {"ok": bool(report.get("all_pass")), "report": report}


@router.post("/apathy/verify-ref")
async def apathy_verify_ref(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("repo"), str) or not isinstance(body.get("sha"), str):
        return JSONResponse({"ok": False, "error": "repo and sha are required"}, status_code=400)
    report = apathy.verify_ref(body["repo"], body["sha"], body.get("branch", "main"))
    return {"ok": report.get("on_remote") is True, "report": report}


@router.post("/proven/facts")
async def proven_facts(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("name"), str):
        return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
    facts = proven.get_facts(body["name"])
    return {"ok": True, "facts": facts}


@router.post("/proven/probe")
async def proven_probe(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("name"), str):
        return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
    record = proven.probe_server(body["name"])
    return {"ok": bool(record.get("reachable")), "probe": record}


@router.post("/proven/drift")
async def proven_drift(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("name"), str):
        return JSONResponse({"ok": False, "error": "name is required"}, status_code=400)
    drift = proven.compute_drift(body["name"])
    return {"ok": "error" not in drift, "drift": drift}


@router.post("/anchor/parse")
async def anchor_parse(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("address"), str):
        return JSONResponse({"ok": False, "error": "address is required"}, status_code=400)
    report = anchor.validate_address(body["address"])
    return {"ok": bool(report.get("valid")), "report": report}


@router.post("/anchor/verify")
async def anchor_verify(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("address"), str):
        return JSONResponse({"ok": False, "error": "address is required"}, status_code=400)
    path = body.get("registry") or str(_REGISTRY)
    registry = anchor.load_registry(path)
    resolution = anchor.verify_address(registry, body["address"])
    return {
        "ok": resolution.get("verdict") == "ISSUED",
        "resolution": resolution,
        "registry_id": registry.get("registry_id"),
    }


@router.post("/anchor/registry")
async def anchor_registry(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    path = body.get("registry") or str(_REGISTRY)
    registry = anchor.load_registry(path)
    report = anchor.verify_registry(registry)
    entries = registry.get("entries", [])
    return {
        "ok": bool(report.get("ok")),
        "registry_id": registry.get("registry_id"),
        "issued_at": registry.get("issued_at"),
        "entries": len(entries),
        "active": sum(1 for entry in entries if entry.get("status") == "active"),
        "retired": sum(1 for entry in entries if entry.get("status") == "retired"),
        "public_key": registry.get("public_key"),
        "verifies": report["ok"],
        "checks": report["checks"],
        "registry_source": "demo",
        "live": live_state(),
    }


@router.post("/receipts/append")
async def receipts_append(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    if not isinstance(body.get("actor"), str) or not isinstance(body.get("action"), dict):
        return JSONResponse(
            {"ok": False, "error": "actor and action object are required"},
            status_code=400,
        )
    if "result_status" not in body:
        return JSONResponse({"ok": False, "error": "result_status is required"}, status_code=400)
    recorded = record_receipt(body["actor"], body["action"], body["result_status"])
    return {
        "ok": recorded["bundle_ok"] is True,
        "receipt": recorded["receipt"],
        "bundle": recorded["bundle"],
        "bundle_detail": recorded["bundle_detail"],
    }


@router.post("/receipts/verify")
async def receipts_verify(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    bundle = body.get("bundle", body)
    ok, detail = receipts.verify_bundle(bundle)
    return {"ok": ok is True, "detail": detail}


@router.post("/disputes/route")
async def dispute_route_endpoint(request: Request):
    """Rail-aware dispute routing + the Receipts evidence packet.

    Routing and evidence only: this endpoint never files, submits,
    or initiates a dispute or claim with any provider.
    """
    body, err = _read_json(await request.body())
    if err:
        return err
    rail = body.get("rail")
    if not isinstance(rail, str) or not rail.strip():
        return JSONResponse(
            {"ok": False, "error": "rail is required"}, status_code=400)
    purchase, bundle = _dispute_evidence(rail, body)
    return disputes.route_dispute(rail, purchase, bundle)


@router.post("/flow")
async def trust_flow(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    host = request.url.hostname or "zerobeacon"
    return run_flow(body, host)


@router.post("/mcp")
async def trust_mcp(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    method = body.get("method")
    mid = body.get("id")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "zerobeacon-trust-harness", "version": "1.0.0"},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        tools = (
            list(apathy_mcp.TOOLS)
            + list(proven_mcp.TOOLS)
            + list(anchor_mcp.TOOLS)
            + list(receipts_mcp.TOOLS)
            + list(disputes_mcp.TOOLS)
        )
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}
    if method == "tools/call":
        params = body.get("params") or {}
        name = params.get("name")
        call = _DISPATCH.get(name)
        if call is None:
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "error": {
                    "code": -32601,
                    "message": f"method not found: {name}",
                },
            }
        result = call(name, params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if isinstance(method, str) and method.startswith("notifications/"):
        return JSONResponse(status_code=202, content=None)
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


# -- PayPal gate --------------------------------------------------------
# The payment is the gated action. A purchase proposal runs the composed
# flow first (audit + verify + certificate); only a passing flow may
# create a PayPal order, and capture records the executed payment in
# Receipts. A refused flow never reaches PayPal: no order is created.
# Credentials are read from the environment at call time and are never
# logged, echoed, or written anywhere.

PAYPAL_SANDBOX_BASE = "https://api-m.sandbox.paypal.com"
PAYPAL_LIVE_BASE = "https://api-m.paypal.com"
PAYPAL_CAPTURED_FILE = "paypal-captured-orders.json"


def _paypal_config() -> dict:
    client_id = os.environ.get("PAYPAL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PAYPAL_CLIENT_SECRET", "").strip()
    env = os.environ.get("PAYPAL_ENV", "").strip().lower() or "sandbox"
    return {
        "configured": bool(client_id and client_secret),
        "environment": env,
        "base": PAYPAL_LIVE_BASE if env == "live" else PAYPAL_SANDBOX_BASE,
        "client_id": client_id,
        "client_secret": client_secret,
    }


def _paypal_http(method: str, url: str, headers: dict, body: bytes | None) -> tuple[int, dict]:
    """One HTTP round trip to PayPal. Module-level so tests can replace it."""
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw}
        return exc.code, parsed


def _paypal_access_token(cfg: dict) -> tuple[str | None, dict | None]:
    basic = base64.b64encode(
        f"{cfg['client_id']}:{cfg['client_secret']}".encode("utf-8")
    ).decode("ascii")
    status, payload = _paypal_http(
        "POST",
        cfg["base"] + "/v1/oauth2/token",
        {
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        b"grant_type=client_credentials",
    )
    if status == 200 and payload.get("access_token"):
        return payload["access_token"], None
    return None, {"http_status": status, "paypal": payload}


def _paypal_create_order(cfg: dict, token: str, purchase: dict, amount: str,
                         certificate: dict) -> tuple[int, dict]:
    currency = str(purchase.get("currency", "USD")).strip().upper() or "USD"
    item = str(purchase.get("item", "")).strip() or "ZeroBeacon certified service"
    cert_ref = (
        certificate.get("canonical_address")
        or certificate.get("registry_id")
        or "certified"
    )
    order = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "description": f"{item} - ZeroBeacon certificate {cert_ref}"[:127],
                "custom_id": str(cert_ref)[:127],
                "amount": {"currency_code": currency, "value": amount},
            }
        ],
    }
    return _paypal_http(
        "POST",
        cfg["base"] + "/v2/checkout/orders",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json.dumps(order).encode("utf-8"),
    )


def _captured_orders() -> dict:
    path = os.path.join(_data_dir(), PAYPAL_CAPTURED_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_captured(order_id: str, record: dict) -> None:
    path = os.path.join(_data_dir(), PAYPAL_CAPTURED_FILE)
    with _LOCK:
        captured = _captured_orders()
        captured[order_id] = record
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(captured, handle)


def _amount_ok(raw) -> tuple[bool, str]:
    """A positive amount with at most two decimal places, canonicalized."""
    try:
        value = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError):
        return False, ""
    if value <= 0:
        return False, ""
    quantized = value.quantize(Decimal("0.01"))
    if value != quantized:
        return False, ""
    return True, str(quantized)


@router.get("/paypal/status")
async def paypal_status(request: Request):
    cfg = _paypal_config()
    return {
        "ok": True,
        "configured": cfg["configured"],
        "environment": cfg["environment"],
        "gate": "live",
    }


@router.post("/paypal/propose")
async def paypal_propose(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    proposal = body.get("proposal")
    purchase = body.get("purchase")
    if not isinstance(proposal, dict) or not isinstance(purchase, dict):
        return JSONResponse(
            {"ok": False, "error": "proposal and purchase objects are required"},
            status_code=400,
        )
    amount_ok, amount = _amount_ok(purchase.get("amount_usd", ""))
    if not amount_ok:
        return JSONResponse(
            {
                "ok": False,
                "error": "purchase.amount_usd must be a positive amount with at most 2 decimal places",
            },
            status_code=400,
        )
    host = request.url.hostname or "zerobeacon"
    gate = run_flow(proposal, host)
    certificate = gate.get("certificate")
    if certificate is None:
        return {
            "ok": False,
            "gate": "refused",
            "refusal": gate.get("refusal"),
            "certificate": None,
            "paypal": None,
            "receipt": gate.get("receipt"),
            "receipt_verifies": gate.get("receipt_verifies"),
        }
    cfg = _paypal_config()
    if not cfg["configured"]:
        return {
            "ok": True,
            "gate": "passed",
            "certificate": certificate,
            "paypal": {"status": "unconfigured", "environment": cfg["environment"]},
            "receipt": gate.get("receipt"),
            "receipt_verifies": gate.get("receipt_verifies"),
        }
    token, error = _paypal_access_token(cfg)
    if token is None:
        return JSONResponse(
            {
                "ok": False,
                "gate": "passed",
                "certificate": certificate,
                "paypal": {"status": "auth_failed", **(error or {})},
            },
            status_code=502,
        )
    status, payload = _paypal_create_order(cfg, token, purchase, amount, certificate)
    if status not in (200, 201) or "id" not in payload:
        return JSONResponse(
            {
                "ok": False,
                "gate": "passed",
                "certificate": certificate,
                "paypal": {
                    "status": "order_failed",
                    "http_status": status,
                    "paypal": payload,
                },
            },
            status_code=502,
        )
    approve_url = next(
        (link.get("href") for link in payload.get("links", []) if link.get("rel") == "approve"),
        None,
    )
    return {
        "ok": True,
        "gate": "passed",
        "certificate": certificate,
        "paypal": {
            "status": "created",
            "order_id": payload["id"],
            "approve_url": approve_url,
            "environment": cfg["environment"],
        },
        "receipt": gate.get("receipt"),
        "receipt_verifies": gate.get("receipt_verifies"),
    }


@router.post("/paypal/capture")
async def paypal_capture(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    order_id = body.get("paypal_order_id")
    if not isinstance(order_id, str) or not order_id.strip():
        return JSONResponse(
            {"ok": False, "error": "paypal_order_id is required"},
            status_code=400,
        )
    order_id = order_id.strip()
    existing = _captured_orders().get(order_id)
    if existing is not None:
        return {
            "ok": True,
            "paypal": {"status": "already_captured", **existing["paypal"]},
            "receipt": existing.get("receipt"),
            "recorded": False,
        }
    cfg = _paypal_config()
    if not cfg["configured"]:
        return {
            "ok": False,
            "paypal": {"status": "unconfigured", "environment": cfg["environment"]},
        }
    token, error = _paypal_access_token(cfg)
    if token is None:
        return JSONResponse(
            {"ok": False, "paypal": {"status": "auth_failed", **(error or {})}},
            status_code=502,
        )
    status, payload = _paypal_http(
        "POST",
        f"{cfg['base']}/v2/checkout/orders/{order_id}/capture",
        {"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        b"{}",
    )
    if status not in (200, 201) or payload.get("status") != "COMPLETED":
        return JSONResponse(
            {
                "ok": False,
                "paypal": {
                    "status": "capture_failed",
                    "http_status": status,
                    "paypal": payload,
                },
            },
            status_code=502,
        )
    capture_id = amount = currency = None
    try:
        capture = payload["purchase_units"][0]["payments"]["captures"][0]
        capture_id = capture.get("id")
        amount = capture.get("amount", {}).get("value")
        currency = capture.get("amount", {}).get("currency_code")
    except (KeyError, IndexError, TypeError):
        pass
    paypal_part = {
        "order_id": order_id,
        "capture_id": capture_id,
        "amount": amount,
        "currency": currency,
        "environment": cfg["environment"],
    }
    recorded = record_receipt(
        "trust-paypal",
        {"method": "POST", "path": "/api/trust/paypal/capture", **paypal_part},
        "paypal_payment_executed",
    )
    _save_captured(
        order_id, {"paypal": paypal_part, "receipt": recorded["receipt"]}
    )
    return {
        "ok": recorded["bundle_ok"] is True,
        "paypal": {"status": "captured", **paypal_part},
        "receipt": recorded["receipt"],
        "receipt_verifies": recorded["bundle_ok"],
        "recorded": True,
    }


# -- Amazon Pay gate -----------------------------------------------------
# Same gate contract as PayPal: a purchase proposal runs the composed
# flow first; only a passing flow may create a checkout session, and
# capture records the executed payment in Receipts exactly once.
# The rail adapter is live: its transport is selected by configuration.
# Without merchant credentials it answers in the deterministic fixture
# mode — labelled rail_mode "fixture" with the adapter's mode note on
# every response. With credentials it signs and sends (Amazon Pay
# Signature V2); the sandbox transport was verified against Amazon's
# own sandbox on 2026-10-26, and live mode awaits Amazon's account
# verification (tax identity and bank account on file).

AMAZON_CAPTURED_FILE = "amazon-pay-captured.json"
_AMAZON_CLIENT = None
_AMAZON_PENDING: dict[str, dict] = {}


def _amazon_client():
    global _AMAZON_CLIENT
    if _AMAZON_CLIENT is None:
        _AMAZON_CLIENT = amazon_pay.client_from_env()
    return _AMAZON_CLIENT


def _amazon_captured() -> dict:
    path = os.path.join(_data_dir(), AMAZON_CAPTURED_FILE)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_amazon_captured(session_id: str, record: dict) -> None:
    path = os.path.join(_data_dir(), AMAZON_CAPTURED_FILE)
    with _LOCK:
        captured = _amazon_captured()
        captured[session_id] = record
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(captured, handle)


@router.get("/amazonpay/status")
async def amazonpay_status(request: Request):
    client = _amazon_client()
    body = {
        "ok": True,
        "rail": "amazon_pay",
        "rail_mode": client.mode,
        "gate": "live",
    }
    if client.mode == "fixture":
        body["configured"] = False
        body["mode_note"] = amazon_pay.FIXTURE_NOTE
    else:
        body["configured"] = client.transport.configured
    return body


@router.post("/amazonpay/propose")
async def amazonpay_propose(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    proposal = body.get("proposal")
    purchase = body.get("purchase")
    if not isinstance(proposal, dict) or not isinstance(purchase, dict):
        return JSONResponse(
            {"ok": False, "error": "proposal and purchase objects are required"},
            status_code=400,
        )
    amount_ok, amount = _amount_ok(purchase.get("amount_usd", ""))
    if not amount_ok:
        return JSONResponse(
            {
                "ok": False,
                "error": "purchase.amount_usd must be a positive amount with at most 2 decimal places",
            },
            status_code=400,
        )
    host = request.url.hostname or "zerobeacon"
    gate = run_flow(proposal, host)
    certificate = gate.get("certificate")
    if certificate is None:
        return {
            "ok": False,
            "gate": "refused",
            "refusal": gate.get("refusal"),
            "certificate": None,
            "amazonpay": None,
            "receipt": gate.get("receipt"),
            "receipt_verifies": gate.get("receipt_verifies"),
        }
    client = _amazon_client()
    currency = str(purchase.get("currency", "USD")).strip().upper() or "USD"
    item = str(purchase.get("item", "")).strip() or "ZeroBeacon certified service"
    payload = {
        "webCheckoutDetails": {
            "checkoutResultReturnUrl": "https://zerobeacon.ai/trust",
        },
        "chargeAmount": {"amount": amount, "currencyCode": currency},
        "merchantMetadata": {
            "merchantReferenceId": str(
                certificate.get("canonical_address") or "certified")[:64],
            "noteToBuyer": item[:64],
        },
    }
    try:
        session = client.create_checkout_session(
            payload, idempotency_key=gate["receipt"]["hash"])
    except amazon_pay.NotConfiguredError as exc:
        return {
            "ok": True,
            "gate": "passed",
            "certificate": certificate,
            "amazonpay": {
                "status": "unconfigured",
                "rail": "amazon_pay",
                "rail_mode": client.mode,
                "detail": str(exc),
            },
            "receipt": gate.get("receipt"),
            "receipt_verifies": gate.get("receipt_verifies"),
        }
    session_id = session["checkoutSessionId"]
    _AMAZON_PENDING[session_id] = {
        "certificate": {
            "verdict": certificate.get("verdict"),
            "canonical_address": certificate.get("canonical_address"),
            "registry_source": certificate.get("registry_source"),
        },
        "item": item,
        "amount": amount,
        "currency": currency,
    }
    return {
        "ok": True,
        "gate": "passed",
        "certificate": certificate,
        "amazonpay": {"status": "session_created", **session},
        "receipt": gate.get("receipt"),
        "receipt_verifies": gate.get("receipt_verifies"),
    }


@router.post("/amazonpay/capture")
async def amazonpay_capture(request: Request):
    body, err = _read_json(await request.body())
    if err:
        return err
    session_id = body.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        return JSONResponse(
            {"ok": False, "error": "session_id is required"},
            status_code=400,
        )
    session_id = session_id.strip()
    existing = _amazon_captured().get(session_id)
    if existing is not None:
        return {
            "ok": True,
            "amazonpay": {"status": "already_captured", **existing["amazonpay"]},
            "receipt": existing.get("receipt"),
            "recorded": False,
        }
    client = _amazon_client()
    try:
        completed = client.complete_checkout_session(
            session_id, {}, idempotency_key=f"{session_id}:complete")
        charge_id = completed.get("chargeId")
        if not charge_id:
            return JSONResponse(
                {
                    "ok": False,
                    "amazonpay": {
                        "status": "complete_failed",
                        "rail": "amazon_pay",
                        "rail_mode": client.mode,
                        "session": completed,
                    },
                },
                status_code=502,
            )
        captured = client.capture_charge(
            charge_id, {}, idempotency_key=f"{session_id}:capture")
    except amazon_pay.NotConfiguredError as exc:
        return {
            "ok": False,
            "amazonpay": {
                "status": "unconfigured",
                "rail": "amazon_pay",
                "rail_mode": client.mode,
                "detail": str(exc),
            },
        }
    except amazon_pay.RailError as exc:
        return JSONResponse(
            {
                "ok": False,
                "amazonpay": {
                    "status": "rail_error",
                    "rail": "amazon_pay",
                    "rail_mode": client.mode,
                    "detail": str(exc),
                },
            },
            status_code=502,
        )
    pending = _AMAZON_PENDING.pop(session_id, {})
    captured_amount = captured.get("capturedAmount") or {}
    amazon_part = {
        "rail": "amazon_pay",
        "rail_mode": client.mode,
        "session_id": session_id,
        "charge_id": charge_id,
        "charge_permission_id": completed.get("chargePermissionId"),
        "amount": captured_amount.get("amount"),
        "currency": captured_amount.get("currencyCode"),
        "charge_state": captured.get("statusDetails", {}).get("state"),
    }
    if client.mode == "fixture":
        amazon_part["mode_note"] = amazon_pay.FIXTURE_NOTE
    recorded = record_receipt(
        "trust-amazonpay",
        {"method": "POST", "path": "/api/trust/amazonpay/capture",
         **amazon_part},
        "amazonpay_payment_executed",
    )
    _save_amazon_captured(session_id, {
        "amazonpay": amazon_part,
        "receipt": recorded["receipt"],
        "certificate": pending.get("certificate"),
        "item": pending.get("item"),
    })
    return {
        "ok": recorded["bundle_ok"] is True,
        "amazonpay": {"status": "captured", **amazon_part},
        "receipt": recorded["receipt"],
        "receipt_verifies": recorded["bundle_ok"],
        "recorded": True,
    }
