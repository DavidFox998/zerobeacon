#!/usr/bin/env python3
"""Amazon Pay rail adapter — fixture default; sandbox signs and sends.

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

  sandbox: when the merchant credential environment variables
  (AMAZON_PAY_MERCHANT_ID, AMAZON_PAY_PUBLIC_KEY_ID, and a private
  key supplied either as a file named by AMAZON_PAY_PRIVATE_KEY_PATH
  or as PEM text in AMAZON_PAY_PRIVATE_KEY) are set, each call is
  signed with Amazon Pay Signature V2 (AMZN-PAY-RSASSA-PSS-V2:
  RSASSA-PSS with SHA-256 / MGF1-SHA-256, salt length 20) and sent
  to the Amazon Pay API v2 sandbox endpoints at
  pay-api.amazon.com/sandbox. Without usable credentials — variables
  missing, or a private key that cannot be loaded as RSA — it
  raises NotConfiguredError and sends nothing. There is no
  production (live-money) transport in this module.

HONESTY CONTRACT: every response produced here carries
"rail": "amazon_pay" and "rail_mode": <mode>; fixture responses
additionally carry "mode_note" with the text in FIXTURE_NOTE, and
sandbox responses are Amazon's own response bodies. Nothing in
this module claims a production Amazon integration, because the
live endpoints are not built.

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

import base64
import hashlib
import json
import os
import secrets
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

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

# Alternative key source: the PEM text itself, for hosts where the
# key arrives as a secret value rather than a file (e.g. Fly
# secrets). The file path wins when both are set, and either source
# satisfies the key requirement in missing_credentials().
SANDBOX_KEY_PEM_ENV_VAR = "AMAZON_PAY_PRIVATE_KEY"


class NotConfiguredError(Exception):
    """The sandbox transport cannot run: credentials absent, or the
    private key cannot be loaded as an RSA key. Nothing is sent
    to Amazon when this is raised."""


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
# Sandbox transport (live: signs with Signature V2 and sends)
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

_SIGNING_ALGORITHM = "AMZN-PAY-RSASSA-PSS-V2"
_SANDBOX_HOST = "pay-api.amazon.com"
_SANDBOX_REGION = "na"
# RSASSA-PSS parameters for Amazon Pay Signature V2, pinned from the
# EN "Signing requests" doc (developer.amazon.com/docs/amazon-pay-
# api-v2): SHA-256, MGF1-SHA-256, salt length 20. (A translated doc
# page says 32; the EN page and its worked example use 20.)
PSS_SALT_LENGTH = 20
_RSA_ENCRYPTION_OID = bytes.fromhex("2a864886f70d010101")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# -- minimal ASN.1 DER reading (stdlib only) ---------------------------

class _DerReader:
    """Just enough DER to walk a PKCS#1 / PKCS#8 RSA private key."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def _read_length(self) -> int:
        first = self._data[self._pos]
        self._pos += 1
        if first < 0x80:
            return first
        count = first & 0x7F
        if count == 0 or count > 4:
            raise ValueError("bad DER length")
        length = int.from_bytes(
            self._data[self._pos:self._pos + count], "big")
        self._pos += count
        return length

    def read(self, tag: int) -> bytes:
        if self._pos >= len(self._data) or self._data[self._pos] != tag:
            raise ValueError("unexpected DER tag")
        self._pos += 1
        length = self._read_length()
        end = self._pos + length
        if end > len(self._data):
            raise ValueError("DER value overruns input")
        content = self._data[self._pos:end]
        self._pos = end
        return content

    def sequence(self) -> "_DerReader":
        return _DerReader(self.read(0x30))

    def integer(self) -> int:
        return int.from_bytes(self.read(0x02), "big")

    def octet_string(self) -> bytes:
        return self.read(0x04)

    def oid(self) -> bytes:
        return self.read(0x06)


def _parse_pkcs1(der: bytes) -> tuple[int, int, int]:
    seq = _DerReader(der).sequence()
    seq.integer()  # version
    modulus = seq.integer()
    public_exponent = seq.integer()
    private_exponent = seq.integer()
    if modulus <= 0 or private_exponent <= 0:
        raise ValueError("bad RSA parameters")
    return modulus, public_exponent, private_exponent


def _parse_rsa_private_key_text(text: str,
                                source: str) -> tuple[int, int, int]:
    """(modulus, public exponent, private exponent) from PEM text.

    Supports PKCS#8 ("BEGIN PRIVATE KEY") wrapping a PKCS#1 key and
    a bare PKCS#1 key ("BEGIN RSA PRIVATE KEY"). Any failure — not
    PEM, not RSA — raises NotConfiguredError naming `source` (a
    variable description, never key material): without a usable key
    the transport refuses and sends nothing.
    """
    def _fail(reason: str) -> NotConfiguredError:
        return NotConfiguredError(
            "Amazon Pay sandbox transport is not configured: "
            f"{reason}; no call was sent to Amazon")

    try:
        if "-----BEGIN RSA PRIVATE KEY-----" in text:
            body = text.split("-----BEGIN RSA PRIVATE KEY-----")[1]
            body = body.split("-----END RSA PRIVATE KEY-----")[0]
            return _parse_pkcs1(base64.b64decode(body))
        if "-----BEGIN PRIVATE KEY-----" in text:
            body = text.split("-----BEGIN PRIVATE KEY-----")[1]
            body = body.split("-----END PRIVATE KEY-----")[0]
            outer = _DerReader(base64.b64decode(body)).sequence()
            outer.integer()  # version
            algorithm = outer.sequence()
            if algorithm.oid() != _RSA_ENCRYPTION_OID:
                raise ValueError("private key is not RSA")
            return _parse_pkcs1(outer.octet_string())
        raise ValueError("no supported PEM block")
    except NotConfiguredError:
        raise
    except Exception:
        raise _fail("could not parse an RSA private key from "
                    f"{source}") from None


def _load_rsa_private_key(path: str) -> tuple[int, int, int]:
    """(modulus, public exponent, private exponent) from a PEM file."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise NotConfiguredError(
            "Amazon Pay sandbox transport is not configured: "
            "could not read the private key file named by "
            "AMAZON_PAY_PRIVATE_KEY_PATH; no call was sent to "
            "Amazon") from None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        raise NotConfiguredError(
            "Amazon Pay sandbox transport is not configured: "
            "could not parse an RSA private key from the file named "
            "by AMAZON_PAY_PRIVATE_KEY_PATH; no call was sent to "
            "Amazon") from None
    return _parse_rsa_private_key_text(
        text, "the file named by AMAZON_PAY_PRIVATE_KEY_PATH")


def _load_configured_key(env) -> tuple[int, int, int]:
    """Load the sandbox RSA key from the configured source.

    The file named by AMAZON_PAY_PRIVATE_KEY_PATH wins when set;
    otherwise AMAZON_PAY_PRIVATE_KEY supplies the PEM text itself
    (secret stores often flatten its newlines to literal backslash-n
    sequences, which are restored before parsing). Callers have
    already established that at least one source is set.
    """
    path = str(env.get("AMAZON_PAY_PRIVATE_KEY_PATH", "")).strip()
    if path:
        return _load_rsa_private_key(path)
    pem = str(env.get(SANDBOX_KEY_PEM_ENV_VAR, ""))
    if "\\n" in pem:
        pem = pem.replace("\\n", "\n")
    return _parse_rsa_private_key_text(
        pem, "the value of AMAZON_PAY_PRIVATE_KEY")



# -- RSASSA-PSS signing (RFC 8017, SHA-256 / MGF1-SHA-256) -------------

def _mgf1(seed: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out += hashlib.sha256(
            seed + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:length])


def _pss_sign(message_hash: bytes, modulus: int,
              private_exponent: int,
              salt: bytes | None = None) -> bytes:
    """EMSA-PSS-ENCODE + RSASP1 over an already-computed SHA-256."""
    h_len = hashlib.sha256().digest_size
    if salt is None:
        salt = secrets.token_bytes(PSS_SALT_LENGTH)
    em_bits = modulus.bit_length() - 1
    em_len = (em_bits + 7) // 8
    if em_len < h_len + len(salt) + 2:
        raise RailError("RSA key too small for RSASSA-PSS/SHA-256")
    digest = hashlib.sha256(
        b"\x00" * 8 + message_hash + salt).digest()
    data_block = b"\x00" * (em_len - len(salt) - h_len - 2) \
        + b"\x01" + salt
    masked = bytearray(
        a ^ b for a, b in
        zip(data_block, _mgf1(digest, em_len - h_len - 1)))
    masked[0] &= 0xFF >> (8 * em_len - em_bits)
    encoded = bytes(masked) + digest + b"\xbc"
    signature = pow(int.from_bytes(encoded, "big"),
                    private_exponent, modulus)
    return signature.to_bytes((modulus.bit_length() + 7) // 8, "big")


# -- canonical request (Signature V2, "Signing requests" Step 1) -------

def _canonical_uri(path: str) -> str:
    return "/".join(urllib.parse.quote(segment, safe="-_.~")
                    for segment in path.split("/"))


def _canonical_request(method: str, path: str, signed_headers: dict,
                       body: bytes) -> tuple[str, str]:
    """(canonical request, signed-headers list) for a request."""
    names = sorted(signed_headers)
    canonical_headers = "".join(
        f"{name}:{' '.join(str(signed_headers[name]).split())}\n"
        for name in names)
    canonical = "\n".join([
        method.upper(),
        _canonical_uri(path),
        "",  # no operation uses query parameters
        canonical_headers,
        ";".join(names),
        _sha256_hex(body),
    ])
    return canonical, ";".join(names)


# -- default network opener ---------------------------------------------

def _default_opener(method: str, url: str, headers: dict,
                    body: bytes) -> tuple[int, bytes]:
    """One HTTPS call via urllib; HTTP errors are answers, not
    exceptions. Network failures become RailError with a plain
    message (no headers, no key material)."""
    request = urllib.request.Request(
        url, data=body if body else None, headers=dict(headers),
        method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RailError(
            "Amazon Pay sandbox request failed before any response: "
            f"{reason}") from exc


class SandboxTransport:
    """Signs and sends Amazon Pay sandbox calls.

    Raises NotConfiguredError unless the credential variables are
    set (merchant id, public key id, and a private key from the
    file named by AMAZON_PAY_PRIVATE_KEY_PATH or the PEM text in
    AMAZON_PAY_PRIVATE_KEY) and the key loads as an RSA key; in
    either failure nothing is sent. Otherwise each
    request is signed with Amazon Pay Signature V2 and sent once
    through the opener (urllib HTTPS by default; tests inject a fake
    opener and a fixed clock). build_request() returns the fully
    signed request for inspection without any network I/O.
    """

    mode = "sandbox"

    def __init__(self, env: dict | None = None, opener=None,
                 clock=None):
        self._env = env if env is not None else os.environ
        self._opener = opener if opener is not None else _default_opener
        self._clock = clock

    def missing_credentials(self) -> list[str]:
        missing = [name for name in SANDBOX_ENV_VARS
                   if not str(self._env.get(name, "")).strip()]
        if str(self._env.get(SANDBOX_KEY_PEM_ENV_VAR, "")).strip():
            # The PEM-text key source satisfies the key requirement.
            missing = [name for name in missing
                       if name != "AMAZON_PAY_PRIVATE_KEY_PATH"]
        return missing

    @property
    def configured(self) -> bool:
        return not self.missing_credentials()

    def _now(self) -> datetime:
        moment = self._clock() if self._clock is not None \
            else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    def _signed_request(self, operation: str, path_params: dict,
                        payload: dict,
                        idempotency_key: str | None) -> dict:
        missing = self.missing_credentials()
        if missing:
            raise NotConfiguredError(
                "Amazon Pay sandbox transport is not configured: "
                f"missing {', '.join(missing)}; no call was sent to "
                "Amazon")
        method, template = _OPERATION_HTTP[operation]
        path = "/sandbox" + template.format(**(path_params or {}))
        has_body = method not in ("GET", "DELETE")
        # Sign exactly the bytes that will be sent.
        body = json.dumps(payload or {}).encode("utf-8") \
            if has_body else b""
        headers = {"accept": "application/json"}
        if has_body:
            headers["content-type"] = "application/json"
        headers["x-amz-pay-date"] = \
            self._now().strftime("%Y%m%dT%H%M%SZ")
        headers["x-amz-pay-host"] = _SANDBOX_HOST
        if idempotency_key:
            headers["x-amz-pay-idempotency-key"] = idempotency_key
        headers["x-amz-pay-region"] = _SANDBOX_REGION
        canonical, signed_names = _canonical_request(
            method, path, headers, body)
        string_to_sign = (
            _SIGNING_ALGORITHM + "\n"
            + _sha256_hex(canonical.encode("utf-8")))
        modulus, _public_exponent, private_exponent = \
            _load_configured_key(self._env)
        signature = _pss_sign(
            hashlib.sha256(string_to_sign.encode("utf-8")).digest(),
            modulus, private_exponent)
        headers["authorization"] = (
            f"{_SIGNING_ALGORITHM} "
            f"PublicKeyId="
            f"{self._env.get('AMAZON_PAY_PUBLIC_KEY_ID', '')}, "
            f"SignedHeaders={signed_names}, "
            f"Signature="
            f"{base64.b64encode(signature).decode('ascii')}")
        return {
            "method": method,
            "url": f"https://{_SANDBOX_HOST}{path}",
            "headers": headers,
            "body": body,
        }

    def build_request(self, operation: str, path_params: dict,
                      payload: dict, idempotency_key: str | None) -> dict:
        """The fully signed request, for inspection. No network I/O;
        reading the private key file is the only I/O."""
        signed = self._signed_request(operation, path_params, payload,
                                      idempotency_key)
        return {
            "method": signed["method"],
            "url": signed["url"],
            "headers": dict(signed["headers"]),
            "body": signed["body"].decode("utf-8"),
        }

    def request(self, operation: str, path_params: dict, payload: dict,
                idempotency_key: str | None) -> dict:
        signed = self._signed_request(operation, path_params, payload,
                                      idempotency_key)
        try:
            status, response_body = self._opener(
                signed["method"], signed["url"], signed["headers"],
                signed["body"])
        except OSError as exc:
            raise RailError(
                "Amazon Pay sandbox request failed before any "
                f"response: {exc}") from exc
        if 200 <= status < 300:
            try:
                parsed = json.loads(response_body.decode("utf-8")) \
                    if response_body else {}
            except (ValueError, UnicodeDecodeError):
                raise RailError(
                    f"Amazon Pay sandbox returned HTTP {status} "
                    "with an unparseable body") from None
            if not isinstance(parsed, dict):
                raise RailError(
                    f"Amazon Pay sandbox returned HTTP {status} "
                    "with a non-object body")
            return {"rail": RAIL, "rail_mode": self.mode, **parsed}
        detail = ""
        try:
            error = json.loads(response_body.decode("utf-8"))
            if isinstance(error, dict):
                parts = [str(error.get(name, "")).strip()
                         for name in ("reasonCode", "message")]
                detail = ": ".join(part for part in parts if part)
        except (ValueError, UnicodeDecodeError):
            detail = ""
        suffix = f": {detail}" if detail else ""
        raise RailError(
            f"Amazon Pay sandbox request failed with HTTP "
            f"{status}{suffix}")


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
