"""Trust harness — Apathy, Proven, Anchor, Receipts, and the composed flow.

Mounted outside ROUTERS. The catalogue MCP list and /health counts stay
on the existing surface. This router is the judges' surface.

Anchor here only resolves an address against the shipped signed registry.
It does not create registry entries. Receipts has no MCP server in the
staged tree, so record is REST only.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

_ROOT = Path(__file__).resolve().parents[1] / "trust_stack"
_REGISTRY = _ROOT / "anchor" / "registry.json"

_LOCK = threading.Lock()
_CHAIN = None
_BUNDLE_PATH: str | None = None


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

_DISPATCH = {
    "audit_repo": apathy_mcp.call_tool,
    "check_manifest": apathy_mcp.call_tool,
    "verify_ref": apathy_mcp.call_tool,
    "probe_server": proven_mcp.call_tool,
    "get_facts": proven_mcp.call_tool,
    "check_drift": proven_mcp.call_tool,
    "parse_address": anchor_mcp.call_tool,
    "verify_address": anchor_mcp.call_tool,
    "registry_info": anchor_mcp.call_tool,
}

HARNESS_TOOL_NAMES = tuple(_DISPATCH)

# Receipts ships receipts.py and no MCP server. These names are REST only.
RECEIPTS_MCP_GAP = (
    "receipts has no MCP server in the staged tree; "
    "record is POST /api/trust/receipts/append and "
    "POST /api/trust/receipts/verify"
)

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
    return body


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
      <tr><td>Receipts, record</td><td><code>POST /api/trust/receipts/append</code><br><code>POST /api/trust/receipts/verify</code></td><td>none — receipts ships no MCP server</td></tr>
    </tbody>
  </table>
  <p>MCP transport for the nine tools above is <code>POST /api/trust/mcp</code>
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
                "receipts_mcp": None,
                "receipts_mcp_gap": RECEIPTS_MCP_GAP,
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        tools = list(apathy_mcp.TOOLS) + list(proven_mcp.TOOLS) + list(anchor_mcp.TOOLS)
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "tools": tools,
                "receipts_mcp_gap": RECEIPTS_MCP_GAP,
            },
        }
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
                    "message": f"method not found: {name}. {RECEIPTS_MCP_GAP}",
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
