#!/usr/bin/env python3
"""Amazon Pay rail adapter — STAGED, fixture mode only.

This adapter sits behind the same gate contract as the PayPal rail:
a purchase proposal runs the trust harness flow first, and only a
passing flow may create a checkout session. The harness owns the
gate; this module owns the rail's operation shapes and transport.

Transport modes:

  fixture (DEFAULT, and the only mode that can run without a
  merchant account): deterministic, locally generated responses that
  mimic the Amazon Pay sandbox object shapes and state machines
  (CheckoutSession Open -> Completed/Canceled; Charge Authorized ->
  CaptureInitiated -> Completed). No network I/O of any kind.

  sandbox: builds signed-request-ready calls for the Amazon Pay API
  v2 sandbox endpoints at pay-api.amazon.com/sandbox, but raises
  NotConfiguredError unless the merchant credential environment
  variables are present — and, in this staged build, also when they
  are present: the live transport is deliberately not wired to the
  network until the merchant account exists and a human enables it.
  No call is ever sent to Amazon by this module as shipped.

HONESTY CONTRACT: every response produced here carries
"rail": "amazon_pay", "rail_mode": <mode>, and, in fixture mode,
"mode_note" with the text in FIXTURE_NOTE. Nothing in this module
claims a live Amazon integration, because there isn't one yet.

Amazon Pay API v2 operation shapes (developer.amazon.com, Amazon Pay
API v2 reference, checked 2026-10):

  Create Checkout Session    POST   /v2/checkoutSessions
  Get Checkout Session       GET    /v2/checkoutSessions/{id}
  Update Checkout Session    PATCH  /v2/checkoutSessions/{id}
  Complete Checkout Session  POST   /v2/checkoutSessions/{id}/complete
  Create Charge              POST   /v2/charges
  Capture Charge             POST   /v2/charges/{id}/capture
  Cancel Charge              DELETE /v2/charges/{id}
  Create Refund              POST   /v2/refunds
  Get Refund                 GET    /v2/refunds/{id}

Create Checkout Session, Create Charge, Capture Charge, and Create
Refund require an idempotency key (the x-amz-pay-idempotency-key
header); the client refuses to call the transport without one.

Standard library only. Python 3.12.
"""

from __future__ import annotations

import hashlib
import json
import os

RAIL = "amazon_pay"
SANDBOX_BASE = "https://pay-api.amazon.com/sandbox"

FIXTURE_NOTE = (
    "Amazon Pay merchant account not connected; fixture responses "
    "only, no Amazon system was called."
)

# Merchant credential environment variables the sandbox transport
# will require before it even builds a request. Values are read at
# call time and never logged, echoed, or written anywhere.
SANDBOX_ENV_VARS = (
    "AMAZON_PAY_MERCHANT_ID",
    "AMAZON_PAY_PUBLIC_KEY_ID",
    "AMAZON_PAY_PRIVATE_KEY_PATH",
)


class NotConfiguredError(Exception):
    """The sandbox transport cannot run: credentials absent (or the
    staged build's live transport not yet enabled)."""


class RailError(Exception):
    """The rail refused the operation (bad state, unknown id, ...)."""


def _deterministic_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256(":".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:24]}"


# ---------------------------------------------------------------------
# Fixture transport
# ---------------------------------------------------------------------

class FixtureTransport:
    """Deterministic in-memory mimic of the Amazon Pay sandbox.

    Ids derive from the operation + idempotency key, so a repeated
    create with the same key returns the same object (idempotent),
    exactly as the real API promises. `calls` records every operation
    name invoked, in order, so tests can prove the rail was — or was
    not — called.
    """

    mode = "fixture"

    def __init__(self):
        self.calls: list[str] = []
        self.sessions: dict[str, dict] = {}
        self.charges: dict[str, dict] = {}
        self.refunds: dict[str, dict] = {}

    # -- envelope ----------------------------------------------------
    def _envelope(self, obj: dict) -> dict:
        return {
            "rail": RAIL,
            "rail_mode": self.mode,
            "mode_note": FIXTURE_NOTE,
            **obj,
        }

    # -- operations --------------------------------------------------
    def request(self, operation: str, path_params: dict, payload: dict,
                idempotency_key: str | None) -> dict:
        handler = getattr(self, f"_op_{operation}", None)
        if handler is None:
            raise RailError(f"unknown operation: {operation}")
        self.calls.append(operation)
        return handler(path_params or {}, payload or {}, idempotency_key)

    def _op_create_checkout_session(self, _params, payload, idem):
        session_id = _deterministic_id("fixture-session", "create", idem or "")
        existing = self.sessions.get(session_id)
        if existing is not None:
            return self._envelope(existing)
        session = {
            "checkoutSessionId": session_id,
            "statusDetails": {"state": "Open"},
            "chargePermissionId": _deterministic_id(
                "fixture-permission", "session", session_id),
            "chargeAmount": payload.get("chargeAmount"),
            "webCheckoutDetails": payload.get("webCheckoutDetails"),
            "merchantMetadata": payload.get("merchantMetadata"),
        }
        self.sessions[session_id] = session
        return self._envelope(session)

    def _op_get_checkout_session(self, params, _payload, _idem):
        session = self.sessions.get(params.get("session_id", ""))
        if session is None:
            raise RailError("checkout session not found")
        return self._envelope(session)

    def _op_update_checkout_session(self, params, payload, _idem):
        session = self.sessions.get(params.get("session_id", ""))
        if session is None:
            raise RailError("checkout session not found")
        if session["statusDetails"]["state"] != "Open":
            raise RailError(
                f"cannot update a session in state "
                f"{session['statusDetails']['state']}")
        for key, value in payload.items():
            session[key] = value
        return self._envelope(session)

    def _op_complete_checkout_session(self, params, payload, idem):
        session = self.sessions.get(params.get("session_id", ""))
        if session is None:
            raise RailError("checkout session not found")
        state = session["statusDetails"]["state"]
        if state == "Canceled":
            raise RailError("cannot complete a canceled session")
        if state == "Completed":
            return self._envelope(session)
        charge_id = _deterministic_id("fixture-charge", "complete",
                                      session["checkoutSessionId"])
        charge = {
            "chargeId": charge_id,
            "chargePermissionId": session["chargePermissionId"],
            "statusDetails": {"state": "Authorized"},
            "chargeAmount": payload.get("chargeAmount")
                            or session.get("chargeAmount"),
            "capturedAmount": None,
        }
        self.charges[charge_id] = charge
        session["statusDetails"] = {"state": "Completed"}
        session["chargeId"] = charge_id
        return self._envelope(session)

    def _op_create_charge(self, _params, payload, idem):
        charge_id = _deterministic_id("fixture-charge", "create", idem or "")
        existing = self.charges.get(charge_id)
        if existing is not None:
            return self._envelope(existing)
        permission = payload.get("chargePermissionId")
        if not permission:
            raise RailError("create_charge requires chargePermissionId")
        capture_now = bool(payload.get("captureNow"))
        charge = {
            "chargeId": charge_id,
            "chargePermissionId": permission,
            "statusDetails": {
                "state": "Completed" if capture_now else "Authorized"},
            "chargeAmount": payload.get("chargeAmount"),
            "capturedAmount": payload.get("chargeAmount")
                              if capture_now else None,
        }
        self.charges[charge_id] = charge
        return self._envelope(charge)

    def _op_capture_charge(self, params, payload, idem):
        charge = self.charges.get(params.get("charge_id", ""))
        if charge is None:
            raise RailError("charge not found")
        state = charge["statusDetails"]["state"]
        if state == "Completed":
            return self._envelope(charge)  # idempotent replay
        if state != "Authorized":
            raise RailError(f"cannot capture a charge in state {state}")
        amount = payload.get("captureAmount") or charge.get("chargeAmount")
        charge["statusDetails"] = {"state": "Completed"}
        charge["state_history"] = ["Authorized", "CaptureInitiated",
                                   "Completed"]
        charge["capturedAmount"] = amount
        return self._envelope(charge)

    def _op_cancel_charge(self, params, _payload, _idem):
        charge = self.charges.get(params.get("charge_id", ""))
        if charge is None:
            raise RailError("charge not found")
        state = charge["statusDetails"]["state"]
        if state == "Canceled":
            return self._envelope(charge)
        if state != "Authorized":
            raise RailError(f"cannot cancel a charge in state {state}")
        charge["statusDetails"] = {"state": "Canceled"}
        return self._envelope(charge)

    def _op_create_refund(self, _params, payload, idem):
        refund_id = _deterministic_id("fixture-refund", "create", idem or "")
        existing = self.refunds.get(refund_id)
        if existing is not None:
            return self._envelope(existing)
        charge = self.charges.get(payload.get("chargeId", ""))
        if charge is None:
            raise RailError("refund requires an existing chargeId")
        if charge["statusDetails"]["state"] != "Completed":
            raise RailError("refunds require a Completed charge")
        refund = {
            "refundId": refund_id,
            "chargeId": charge["chargeId"],
            "statusDetails": {"state": "Refunded"},
            "state_history": ["RefundInitiated", "Refunded"],
            "refundAmount": payload.get("refundAmount"),
        }
        self.refunds[refund_id] = refund
        return self._envelope(refund)

    def _op_get_refund(self, params, _payload, _idem):
        refund = self.refunds.get(params.get("refund_id", ""))
        if refund is None:
            raise RailError("refund not found")
        return self._envelope(refund)


# ---------------------------------------------------------------------
# Sandbox transport (staged: builds requests, never sends them)
# ---------------------------------------------------------------------

_OPERATION_HTTP = {
    "create_checkout_session": ("POST", "/v2/checkoutSessions"),
    "get_checkout_session": ("GET", "/v2/checkoutSessions/{session_id}"),
    "update_checkout_session": ("PATCH", "/v2/checkoutSessions/{session_id}"),
    "complete_checkout_session": ("POST",
                                  "/v2/checkoutSessions/{session_id}/complete"),
    "create_charge": ("POST", "/v2/charges"),
    "capture_charge": ("POST", "/v2/charges/{charge_id}/capture"),
    "cancel_charge": ("DELETE", "/v2/charges/{charge_id}"),
    "create_refund": ("POST", "/v2/refunds"),
    "get_refund": ("GET", "/v2/refunds/{refund_id}"),
}


class SandboxTransport:
    """Builds signed-request-ready Amazon Pay sandbox calls.

    Raises NotConfiguredError unless every variable in SANDBOX_ENV_VARS
    is set. With them set it still sends nothing in this staged build:
    request() raises NotConfiguredError saying the live transport is
    not enabled, and build_request() returns the would-be request so
    a human can inspect exactly what would be signed and sent.
    """

    mode = "sandbox"

    def __init__(self, env: dict | None = None):
        self._env = env if env is not None else os.environ

    def missing_credentials(self) -> list[str]:
        return [name for name in SANDBOX_ENV_VARS
                if not str(self._env.get(name, "")).strip()]

    @property
    def configured(self) -> bool:
        return not self.missing_credentials()

    def build_request(self, operation: str, path_params: dict,
                      payload: dict, idempotency_key: str | None) -> dict:
        """The request that would be signed and sent. Pure; no I/O."""
        method, template = _OPERATION_HTTP[operation]
        path = template.format(**(path_params or {}))
        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["x-amz-pay-idempotency-key"] = idempotency_key
        return {
            "method": method,
            "url": SANDBOX_BASE + path,
            "headers": headers,
            "body": payload or {},
            "signing": (
                "Amazon Pay Signature Version 2 over the canonical "
                "request, using the merchant key pair named by "
                "AMAZON_PAY_PUBLIC_KEY_ID / AMAZON_PAY_PRIVATE_KEY_PATH"
            ),
        }

    def request(self, operation: str, path_params: dict, payload: dict,
                idempotency_key: str | None) -> dict:
        missing = self.missing_credentials()
        if missing:
            raise NotConfiguredError(
                "Amazon Pay sandbox transport is not configured: "
                f"missing {', '.join(missing)}; no call was sent to "
                "Amazon")
        raise NotConfiguredError(
            "Amazon Pay sandbox credentials are present, but the live "
            "transport is disabled in this staged build; no call was "
            "sent to Amazon")


# ---------------------------------------------------------------------
# Client: typed operations over a transport
# ---------------------------------------------------------------------

_IDEMPOTENCY_REQUIRED = frozenset({
    "create_checkout_session", "create_charge", "capture_charge",
    "create_refund",
})


class AmazonPayClient:
    """The Amazon Pay API v2 operations, as typed functions."""

    def __init__(self, transport):
        self.transport = transport

    @property
    def mode(self) -> str:
        return self.transport.mode

    def _call(self, operation: str, path_params: dict | None = None,
              payload: dict | None = None,
              idempotency_key: str | None = None) -> dict:
        if operation in _IDEMPOTENCY_REQUIRED and not idempotency_key:
            raise RailError(
                f"{operation} requires an idempotency key "
                "(x-amz-pay-idempotency-key)")
        result = self.transport.request(operation, path_params or {},
                                        payload or {}, idempotency_key)
        return result

    # Checkout Sessions
    def create_checkout_session(self, payload: dict,
                                idempotency_key: str) -> dict:
        return self._call("create_checkout_session", payload=payload,
                          idempotency_key=idempotency_key)

    def get_checkout_session(self, session_id: str) -> dict:
        return self._call("get_checkout_session",
                          {"session_id": session_id})

    def update_checkout_session(self, session_id: str,
                                payload: dict) -> dict:
        return self._call("update_checkout_session",
                          {"session_id": session_id}, payload)

    def complete_checkout_session(self, session_id: str,
                                  payload: dict | None = None,
                                  idempotency_key: str | None = None) -> dict:
        return self._call("complete_checkout_session",
                          {"session_id": session_id}, payload or {},
                          idempotency_key)

    # Charges
    def create_charge(self, payload: dict, idempotency_key: str) -> dict:
        return self._call("create_charge", payload=payload,
                          idempotency_key=idempotency_key)

    def capture_charge(self, charge_id: str, payload: dict | None = None,
                       idempotency_key: str | None = None) -> dict:
        return self._call("capture_charge", {"charge_id": charge_id},
                          payload or {}, idempotency_key)

    def cancel_charge(self, charge_id: str) -> dict:
        return self._call("cancel_charge", {"charge_id": charge_id})

    # Refunds
    def create_refund(self, payload: dict, idempotency_key: str) -> dict:
        return self._call("create_refund", payload=payload,
                          idempotency_key=idempotency_key)

    def get_refund(self, refund_id: str) -> dict:
        return self._call("get_refund", {"refund_id": refund_id})


def client_from_env(env: dict | None = None) -> AmazonPayClient:
    """Build the client for AMAZON_PAY_MODE (default fixture)."""
    source = env if env is not None else os.environ
    mode = str(source.get("AMAZON_PAY_MODE", "")).strip().lower() or "fixture"
    if mode == "sandbox":
        return AmazonPayClient(SandboxTransport(source))
    return AmazonPayClient(FixtureTransport())
