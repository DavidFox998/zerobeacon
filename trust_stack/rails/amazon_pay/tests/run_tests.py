#!/usr/bin/env python3
"""Amazon Pay adapter test suite (standalone).

Covers the library surface only: fixture state machines, idempotent
replays, idempotency-key enforcement, the fixture labelling on every
response, and the sandbox transport — NotConfiguredError without
credentials or with an unloadable key, and, with a throwaway test
key, a real signed send through a fake opener whose signature is
verified in this suite (RSASSA-PSS verify, MGF1-SHA256). The hosted
gate wiring is covered by the repo pytest file
test_disputes_amazonpay.py. No network I/O anywhere in this suite:
the sandbox transport is only ever driven through fake openers.

Run:  python3 tests/run_tests.py
Exit: 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[3]

RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool) -> None:
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


amazon_pay = _load(
    "amazon_pay",
    _ROOT / "trust_stack" / "rails" / "amazon_pay" / "amazon_pay.py")

# TEST-ONLY throwaway RSA key pair (2048-bit), generated for this
# suite. It protects nothing, signs nothing real, and must never be
# used anywhere else. Both PEM encodings of the SAME key are embedded
# so the suite covers the PKCS#8 and the PKCS#1 parse paths.
TEST_KEY_PKCS8 = """-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDCuW+KBjtc6rGm
bN9Z252hF9+2/dP1KqPYL9lStauARxVeIJlZf9xKtWdExFKwcAtgIxSXCoTgZwh/
7HcT1vzd/9x+B68hY2u4/zO9UnTL0Kd1025BpHoM7ELafeX3ovEWrLRlEdfzwpJW
MRjlXqn0myDropVirLtBK3OL4zN5moVSZZc/F9/9UnAJZeQgICV9abVK+2dEVxxj
PfHBEZBXC/eujMWBS43UvXfptU+fno0HeTgEKgE/sSHf/IjcIYw7rXREhnjAfXgC
exvcFLoLBuj7fSwl51HKXIQ68jBmNxF9qw8d8mKF61z6RS7ThbX/2cIzS1N3K8J1
7LVaDrRLAgMBAAECggEAX9pRThOwQBZleaOJNJ33U5fBb742Cq7BNYekRmMYN46G
Na39XsGiaoIugfrUK3cUGnBaT9mFPENruzDnEvSWyB8NZlqY+moK2meBjBXdWYDu
FgGMejKhQ+A/Lnef7s0eOUetiz0D3UGvsE79ztbQ+ZAtXeDMAWU+zh86AyfFrHsn
1vkzFG+7Fq/HFko9rZMrjC1LKmVv+qNz3PxbqAp7sE/gma3x8XLi3GixWps+EUY9
be56mtwB4DrNonO596336IpRS7bQnwfPeP9GgU4Wi95hEtEw+ToghwQbJNgebYWS
mS8JPQDx8d2YQjM88pRMdeo1RYtkTUDiZxUbzCwlwQKBgQDx2gtUCXh35pR0rzX+
Jp9J5tgC0mE0WyEAuSGik2KCa9+5wrSw0a9i0wXkkpVFKjEXZC2or7P0YVaUk7Ck
2iRxfvz4Vz4bgN7+KbDuZUgRUdg56zrle1pzYRGRIGW3DEaD1wYEX8prH68QDKzr
C0/HJbiRLVpypgPVcaGuVbBQCwKBgQDOHZ1sYpsjHGvD0+fWU9VOKicpY7cuENln
GVHK8pyQJOgDlW5gVsZKZZVsaCllMysjRmVpwtRrj1DR/B5GTuxDy3WXwpLOu+FJ
vXA83LBzLPXf8KdwLUwO8GlojEvw8dPDQq9zKheMaGjXRofjjxDe0yg1eRQNll99
ADvpUxSUwQKBgQCY9LXFaFfwPLwXL9iN7A9BxeVHo5YmbpvowS0sjNn4chEEgseU
LIknjx5i+PG+1HhHuuvFi5ncLNN9FArbt7rkrNOAnGyDKHXfeK6VlyiFC6Ycu1B8
Yg9KeVGK31p08iaGqy77BToXleo51EpoPLcv1ANujahuaeBy1XLTtw3TEQKBgQCH
H3sYlvxFZFhxpPYT2Bm6iH/r7mckV/9uS3cHqU1iYg+IaepWVgzgsP/oddDNe2II
WhoZ35QKf4XoY3Qle5AAUd3hg7litmDXSs3N2JAEsbDvAoGCVO/Ly2gPFgqjBqZh
F9KVmT6e2cQBfUeQszXIHn35sEqpLa+j7wdXyxNvAQKBgHiAaY1ib5UXLRzqIjUR
JGw6D4yf1aBE0UQYWazac5bMU5Ws5MHx7M0SYyEeBu/LZ6HPJqxEybxswR4bIpEl
268oWE5RIFxCsyX0YTrs5BPY/AA8zYFNQIvL1XPmEstJ6+Ze4tK9Hl1hKgz0mkcQ
WbHsN/aYJpoJt//Ks/IVzEZY
-----END PRIVATE KEY-----
"""

TEST_KEY_PKCS1 = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAwrlvigY7XOqxpmzfWdudoRfftv3T9Sqj2C/ZUrWrgEcVXiCZ
WX/cSrVnRMRSsHALYCMUlwqE4GcIf+x3E9b83f/cfgevIWNruP8zvVJ0y9CnddNu
QaR6DOxC2n3l96LxFqy0ZRHX88KSVjEY5V6p9Jsg66KVYqy7QStzi+MzeZqFUmWX
Pxff/VJwCWXkICAlfWm1SvtnRFccYz3xwRGQVwv3rozFgUuN1L136bVPn56NB3k4
BCoBP7Eh3/yI3CGMO610RIZ4wH14Ansb3BS6Cwbo+30sJedRylyEOvIwZjcRfasP
HfJihetc+kUu04W1/9nCM0tTdyvCdey1Wg60SwIDAQABAoIBAF/aUU4TsEAWZXmj
iTSd91OXwW++NgquwTWHpEZjGDeOhjWt/V7BomqCLoH61Ct3FBpwWk/ZhTxDa7sw
5xL0lsgfDWZamPpqCtpngYwV3VmA7hYBjHoyoUPgPy53n+7NHjlHrYs9A91Br7BO
/c7W0PmQLV3gzAFlPs4fOgMnxax7J9b5MxRvuxavxxZKPa2TK4wtSyplb/qjc9z8
W6gKe7BP4Jmt8fFy4txosVqbPhFGPW3ueprcAeA6zaJzufet9+iKUUu20J8Hz3j/
RoFOFoveYRLRMPk6IIcEGyTYHm2FkpkvCT0A8fHdmEIzPPKUTHXqNUWLZE1A4mcV
G8wsJcECgYEA8doLVAl4d+aUdK81/iafSebYAtJhNFshALkhopNigmvfucK0sNGv
YtMF5JKVRSoxF2QtqK+z9GFWlJOwpNokcX78+Fc+G4De/imw7mVIEVHYOes65Xta
c2ERkSBltwxGg9cGBF/Kax+vEAys6wtPxyW4kS1acqYD1XGhrlWwUAsCgYEAzh2d
bGKbIxxrw9Pn1lPVTionKWO3LhDZZxlRyvKckCToA5VuYFbGSmWVbGgpZTMrI0Zl
acLUa49Q0fweRk7sQ8t1l8KSzrvhSb1wPNywcyz13/CncC1MDvBpaIxL8PHTw0Kv
cyoXjGho10aH448Q3tMoNXkUDZZffQA76VMUlMECgYEAmPS1xWhX8Dy8Fy/YjewP
QcXlR6OWJm6b6MEtLIzZ+HIRBILHlCyJJ48eYvjxvtR4R7rrxYuZ3CzTfRQK27e6
5KzTgJxsgyh133iulZcohQumHLtQfGIPSnlRit9adPImhqsu+wU6F5XqOdRKaDy3
L9QDbo2obmngctVy07cN0xECgYEAhx97GJb8RWRYcaT2E9gZuoh/6+5nJFf/bkt3
B6lNYmIPiGnqVlYM4LD/6HXQzXtiCFoaGd+UCn+F6GN0JXuQAFHd4YO5YrZg10rN
zdiQBLGw7wKBglTvy8toDxYKowamYRfSlZk+ntnEAX1HkLM1yB59+bBKqS2vo+8H
V8sTbwECgYB4gGmNYm+VFy0c6iI1ESRsOg+Mn9WgRNFEGFms2nOWzFOVrOTB8ezN
EmMhHgbvy2ehzyasRMm8bMEeGyKRJduvKFhOUSBcQrMl9GE67OQT2PwAPM2BTUCL
y9Vz5hLLSevmXuLSvR5dYSoM9JpHEFmx7Df2mCaaCbf/yrPyFcxGWA==
-----END RSA PRIVATE KEY-----
"""

_FIXED_NOW = datetime(2026, 10, 26, 13, 37, 58, tzinfo=timezone.utc)


def _fixed_clock() -> datetime:
    return _FIXED_NOW


def _write_key(pem: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".pem", delete=False, encoding="utf-8")
    handle.write(pem)
    handle.close()
    return handle.name


def _sandbox_env(key_path: str) -> dict:
    return {
        "AMAZON_PAY_MERCHANT_ID": "MERCHANT-TEST",
        "AMAZON_PAY_PUBLIC_KEY_ID": "PUBKEY-TEST",
        "AMAZON_PAY_PRIVATE_KEY_PATH": key_path,
    }


def _pss_verify(authorization: str, method: str, path: str,
                headers: dict, body: bytes, modulus: int,
                exponent: int) -> bool:
    """EMSA-PSS-VERIFY (RFC 8017) over the reconstructed signing
    input: canonical request -> string to sign -> SHA-256."""
    scheme, _, rest = authorization.partition(" ")
    if scheme != "AMZN-PAY-RSASSA-PSS-V2":
        return False
    fields = dict(part.split("=", 1) for part in rest.split(", "))
    names = fields["SignedHeaders"].split(";")
    signed = {name: headers[name] for name in names}
    canonical, _ = amazon_pay._canonical_request(method, path,
                                                 signed, body)
    string_to_sign = ("AMZN-PAY-RSASSA-PSS-V2\n"
                      + hashlib.sha256(
                          canonical.encode("utf-8")).hexdigest())
    mhash = hashlib.sha256(string_to_sign.encode("utf-8")).digest()
    signature = base64.b64decode(fields["Signature"])
    em_bits = modulus.bit_length() - 1
    em_len = (em_bits + 7) // 8
    encoded = pow(int.from_bytes(signature, "big"),
                  exponent, modulus).to_bytes(em_len, "big")
    if encoded[-1] != 0xBC:
        return False
    digest = encoded[em_len - 33:em_len - 1]
    masked = encoded[:em_len - 33]
    data_block = bytearray(
        a ^ b for a, b in
        zip(masked, amazon_pay._mgf1(digest, em_len - 33)))
    data_block[0] &= 0xFF >> (8 * em_len - em_bits)
    try:
        separator = data_block.index(1)
    except ValueError:
        return False
    if any(data_block[:separator]):
        return False
    salt = bytes(data_block[separator + 1:])
    expect = hashlib.sha256(b"\x00" * 8 + mhash + salt).digest()
    return expect == digest


def main() -> int:
    client = amazon_pay.AmazonPayClient(amazon_pay.FixtureTransport())

    session = client.create_checkout_session(
        {"chargeAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="s1")
    check("A1. fixture session opens, labelled",
          session["statusDetails"]["state"] == "Open"
          and session["rail_mode"] == "fixture"
          and session["mode_note"] == amazon_pay.FIXTURE_NOTE)
    replay = client.create_checkout_session(
        {"chargeAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="s1")
    check("A2. create with the same idempotency key replays",
          replay["checkoutSessionId"] == session["checkoutSessionId"])

    completed = client.complete_checkout_session(
        session["checkoutSessionId"], {}, idempotency_key="s1-complete")
    check("A3. complete moves Open -> Completed and authorizes a charge",
          completed["statusDetails"]["state"] == "Completed"
          and completed["chargeId"] in client.transport.charges)
    captured = client.capture_charge(
        completed["chargeId"], {}, idempotency_key="s1-capture")
    check("A4. capture walks Authorized -> CaptureInitiated -> Completed",
          captured["state_history"] == ["Authorized", "CaptureInitiated",
                                         "Completed"])
    refund = client.create_refund(
        {"chargeId": completed["chargeId"],
         "refundAmount": {"amount": "10.00", "currencyCode": "USD"}},
        idempotency_key="s1-refund")
    check("A5. refund on a completed charge settles",
          refund["statusDetails"]["state"] == "Refunded"
          and client.get_refund(refund["refundId"])["refundId"]
          == refund["refundId"])

    try:
        client.create_charge({"chargePermissionId": "p"}, None)
        check("A6. idempotency key is enforced", False)
    except amazon_pay.RailError:
        check("A6. idempotency key is enforced", True)

    sandbox = amazon_pay.SandboxTransport({})
    try:
        sandbox.request("create_checkout_session", {}, {}, "k")
        check("A7. sandbox without credentials raises NotConfigured", False)
    except amazon_pay.NotConfiguredError as exc:
        check("A7. sandbox without credentials raises NotConfigured",
              "no call was sent to Amazon" in str(exc))

    # -- sandbox: a real signed send through a fake opener ------------
    key_path = _write_key(TEST_KEY_PKCS8)
    env = _sandbox_env(key_path)
    calls: list[dict] = []

    def ok_opener(method, url, headers, body):
        calls.append({"method": method, "url": url,
                      "headers": dict(headers), "body": body})
        return 200, (b'{"checkoutSessionId":"S01-TEST",'
                     b'"statusDetails":{"state":"Open"}}')

    transport = amazon_pay.SandboxTransport(
        env, opener=ok_opener, clock=_fixed_clock)
    payload = {"chargeAmount": {"amount": "25.00",
                                 "currencyCode": "USD"}}
    result = amazon_pay.AmazonPayClient(transport) \
        .create_checkout_session(payload, idempotency_key="sb-1")
    call = calls[0] if calls else {}
    signed_headers_expect = (
        "accept;content-type;x-amz-pay-date;x-amz-pay-host;"
        "x-amz-pay-idempotency-key;x-amz-pay-region")
    check("A8. sandbox signs and sends with full credentials",
          len(calls) == 1
          and result["rail_mode"] == "sandbox"
          and result["checkoutSessionId"] == "S01-TEST"
          and call["method"] == "POST"
          and call["url"] == "https://pay-api.amazon.com/sandbox"
                             "/v2/checkoutSessions"
          and call["headers"]["authorization"].startswith(
              "AMZN-PAY-RSASSA-PSS-V2 PublicKeyId=PUBKEY-TEST, "
              f"SignedHeaders={signed_headers_expect}, Signature=")
          and call["headers"]["x-amz-pay-region"] == "na"
          and call["headers"]["x-amz-pay-date"] == "20261026T133758Z"
          and call["headers"]["x-amz-pay-idempotency-key"] == "sb-1")

    modulus, exponent, _private = amazon_pay._load_rsa_private_key(
        key_path)
    check("A9. the sandbox signature verifies (PSS, PKCS#8 key)",
          _pss_verify(call["headers"]["authorization"], "POST",
                      "/sandbox/v2/checkoutSessions",
                      call["headers"], call["body"],
                      modulus, exponent))

    body_literal = ('{"chargeAmount": {"amount": "25.00", '
                    '"currencyCode": "USD"}}')
    canonical_expect = (
        "POST\n"
        "/sandbox/v2/checkoutSessions\n"
        "\n"
        "accept:application/json\n"
        "content-type:application/json\n"
        "x-amz-pay-date:20261026T133758Z\n"
        "x-amz-pay-host:pay-api.amazon.com\n"
        "x-amz-pay-idempotency-key:sb-1\n"
        "x-amz-pay-region:na\n"
        "\n"
        f"{signed_headers_expect}\n"
        + hashlib.sha256(body_literal.encode("utf-8")).hexdigest())
    signed_subset = {name: call["headers"][name]
                     for name in signed_headers_expect.split(";")}
    canonical_actual, _ = amazon_pay._canonical_request(
        "POST", "/sandbox/v2/checkoutSessions", signed_subset,
        call["body"])
    check("A10. canonical request matches the doc's Step 1 shape",
          canonical_actual == canonical_expect
          and call["body"] == body_literal.encode("utf-8"))

    get_calls: list[dict] = []

    def get_opener(method, url, headers, body):
        get_calls.append({"method": method, "url": url,
                          "headers": dict(headers), "body": body})
        return 200, b"{}"

    amazon_pay.SandboxTransport(env, opener=get_opener,
                                clock=_fixed_clock) \
        .request("get_checkout_session", {"session_id": "S01-TEST"},
                 {}, None)
    get_call = get_calls[0] if get_calls else {"headers": {}, "body": b"x"}
    check("A11. GET signs no content-type, sends no body",
          get_call["body"] == b""
          and "content-type" not in get_call["headers"]
          and get_call["headers"]["authorization"].split(
              "SignedHeaders=")[1].split(",")[0]
          == "accept;x-amz-pay-date;x-amz-pay-host;x-amz-pay-region"
          and _pss_verify(get_call["headers"]["authorization"], "GET",
                          "/sandbox/v2/checkoutSessions/S01-TEST",
                          get_call["headers"], get_call["body"],
                          modulus, exponent))

    def error_opener(method, url, headers, body):
        return 403, (b'{"reasonCode":"InvalidPublicKeyId",'
                     b'"message":"The public key id is not valid"}')

    try:
        amazon_pay.SandboxTransport(env, opener=error_opener,
                                    clock=_fixed_clock) \
            .request("create_checkout_session", {}, {}, "sb-err")
        check("A12. sandbox maps Amazon's error body to RailError",
              False)
    except amazon_pay.RailError as exc:
        check("A12. sandbox maps Amazon's error body to RailError",
              "403" in str(exc) and "InvalidPublicKeyId" in str(exc))

    def boom_opener(method, url, headers, body):
        raise OSError("connection refused (test)")

    try:
        amazon_pay.SandboxTransport(env, opener=boom_opener,
                                    clock=_fixed_clock) \
            .request("create_checkout_session", {}, {}, "sb-err")
        check("A13. opener network failure becomes RailError", False)
    except amazon_pay.RailError:
        check("A13. opener network failure becomes RailError", True)

    guarded_calls: list = []

    def guarded_opener(method, url, headers, body):
        guarded_calls.append((method, url))
        return 200, b"{}"

    dead_env = _sandbox_env("/nonexistent/amazon-pay-test-key.pem")
    try:
        amazon_pay.SandboxTransport(dead_env, opener=guarded_opener,
                                    clock=_fixed_clock) \
            .request("create_checkout_session", {}, {}, "sb-dead")
        check("A14. unreadable key file refuses before any call",
              False)
    except amazon_pay.NotConfiguredError as exc:
        check("A14. unreadable key file refuses before any call",
              "no call was sent to Amazon" in str(exc)
              and guarded_calls == [])

    built = amazon_pay.SandboxTransport(
        env, opener=guarded_opener, clock=_fixed_clock) \
        .build_request("capture_charge", {"charge_id": "c1"}, {},
                       "idem-1")
    check("A15. build_request returns the signed request, sends none",
          built["method"] == "POST"
          and built["url"] == "https://pay-api.amazon.com/sandbox"
                              "/v2/charges/c1/capture"
          and built["headers"]["x-amz-pay-idempotency-key"] == "idem-1"
          and built["headers"]["authorization"].startswith(
              "AMZN-PAY-RSASSA-PSS-V2 PublicKeyId=PUBKEY-TEST, ")
          and built["body"] == "{}"
          and guarded_calls == [])

    pkcs1_path = _write_key(TEST_KEY_PKCS1)
    pkcs1_calls: list[dict] = []

    def pkcs1_opener(method, url, headers, body):
        pkcs1_calls.append({"method": method, "url": url,
                            "headers": dict(headers), "body": body})
        return 200, b"{}"

    amazon_pay.SandboxTransport(_sandbox_env(pkcs1_path),
                                opener=pkcs1_opener,
                                clock=_fixed_clock) \
        .request("create_checkout_session", {}, {}, "sb-pkcs1")
    pkcs1_call = pkcs1_calls[0] if pkcs1_calls else None
    check("A16. PKCS#1 key signs and the signature verifies",
          pkcs1_call is not None
          and _pss_verify(pkcs1_call["headers"]["authorization"],
                          "POST", "/sandbox/v2/checkoutSessions",
                          pkcs1_call["headers"], pkcs1_call["body"],
                          modulus, exponent))

    # A17: the PEM-text key source (no file path) signs + verifies.
    pem_env = {
        "AMAZON_PAY_MERCHANT_ID": "MERCHANT-TEST",
        "AMAZON_PAY_PUBLIC_KEY_ID": "PUBKEY-TEST",
        "AMAZON_PAY_PRIVATE_KEY": TEST_KEY_PKCS8,
    }
    pem_calls: list[dict] = []

    def pem_opener(method, url, headers, body):
        pem_calls.append({"method": method, "url": url,
                          "headers": dict(headers), "body": body})
        return 200, b"{}"

    pem_transport = amazon_pay.SandboxTransport(
        pem_env, opener=pem_opener, clock=_fixed_clock)
    pem_transport.request("create_checkout_session", {}, {},
                          "sb-pem")
    pem_call = pem_calls[0] if pem_calls else None
    check("A17. PEM-text key source signs and the signature verifies",
          pem_transport.configured
          and pem_call is not None
          and _pss_verify(pem_call["headers"]["authorization"],
                          "POST", "/sandbox/v2/checkoutSessions",
                          pem_call["headers"], pem_call["body"],
                          modulus, exponent))

    # A18: PEM text with flattened (literal backslash-n) newlines.
    flat_env = dict(pem_env)
    flat_env["AMAZON_PAY_PRIVATE_KEY"] = \
        TEST_KEY_PKCS8.replace("\n", "\\n")
    flat_calls: list[dict] = []

    def flat_opener(method, url, headers, body):
        flat_calls.append({"method": method, "url": url,
                           "headers": dict(headers), "body": body})
        return 200, b"{}"

    amazon_pay.SandboxTransport(flat_env, opener=flat_opener,
                                clock=_fixed_clock) \
        .request("create_checkout_session", {}, {}, "sb-flat")
    check("A18. flattened-newline PEM text still loads and signs",
          len(flat_calls) == 1
          and flat_calls[0]["headers"]["authorization"].startswith(
              "AMZN-PAY-RSASSA-PSS-V2 PublicKeyId=PUBKEY-TEST, "))

    # A19: neither key source set -> key reported missing, no send.
    bare_env = {
        "AMAZON_PAY_MERCHANT_ID": "MERCHANT-TEST",
        "AMAZON_PAY_PUBLIC_KEY_ID": "PUBKEY-TEST",
    }
    bare = amazon_pay.SandboxTransport(bare_env, opener=flat_opener,
                                       clock=_fixed_clock)
    try:
        bare.request("create_checkout_session", {}, {}, "sb-bare")
        check("A19. no key source refuses before any call", False)
    except amazon_pay.NotConfiguredError:
        check("A19. no key source refuses before any call",
              "AMAZON_PAY_PRIVATE_KEY_PATH"
              in bare.missing_credentials()
              and len(flat_calls) == 1)

    passed = sum(1 for _, ok in RESULTS if ok)
    print()
    print(f"{passed}/{len(RESULTS)} checks passed")
    if passed != len(RESULTS):
        return 1
    print("ALL TESTS PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
