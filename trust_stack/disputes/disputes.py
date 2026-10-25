#!/usr/bin/env python3
"""Disputes — rail-aware dispute routing and evidence packets.

Given a purchase that passed the trust harness gate, this module
answers two questions and does nothing else:

  1. If the buyer wants to dispute this purchase, what is the
     correct pathway for the rail it was paid on?
  2. What does the Receipts chain hold that evidences the purchase?

ROUTING AND EVIDENCE ONLY. Nothing here files, submits, opens, or
initiates a dispute or claim with any provider, and no function in
this module performs network I/O. For rails with no provider dispute
API (Amazon Pay, Amazon marketplace) the deliverable is a prepared
claim packet — the pathway description plus the verified evidence —
that a human files through the provider's own channel.

Pathways are encoded as data below, with the facts' sources noted
in comments. Amounts and clocks are the providers' published terms
as of October 2026; they are routing information, not legal advice.

Standard library only (plus the sibling receipts import). Python 3.12.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "receipts"))
import receipts as receipts_mod  # noqa: E402

SCOPE_NOTE = (
    "Routing and evidence only: this result names the dispute pathway "
    "for the rail and assembles the Receipts evidence packet. Nothing "
    "was filed, submitted, or initiated with any provider."
)

# ---------------------------------------------------------------------
# Pathways, one per rail. Sources noted per rail.
# ---------------------------------------------------------------------

PATHWAYS: dict[str, dict] = {
    # PayPal exposes a dispute surface in its APIs (the catalogue's
    # pay_dispute tool wraps it): a buyer opens a dispute in the
    # Resolution Center / via API, the seller responds, and either
    # party can escalate to a claim for PayPal to decide.
    # Source: PayPal Disputes API documentation (developer.paypal.com),
    # checked 2026-10.
    "paypal": {
        "rail": "paypal",
        "channel": "api",
        "provider_dispute_api": True,
        "route_name": "PayPal dispute (Resolution Center / Disputes API)",
        "steps": [
            "Buyer opens a dispute for the transaction (item_not_received "
            "or not_as_described) within the PayPal dispute window.",
            "Seller responds with evidence; parties may resolve between "
            "themselves.",
            "Either party escalates the dispute to a claim; PayPal "
            "reviews the evidence and decides.",
            "Outcome is a refund to the buyer or the payment stands; "
            "the decision is recorded on the transaction.",
        ],
        "limits": {
            "dispute_window": "180 days from the transaction date "
                              "(PayPal buyer protection terms)",
        },
        "notes": [
            "Sandbox and live use the same dispute surface; sandbox "
            "disputes are simulated end to end.",
            "The catalogue pay_dispute tool operates this surface; this "
            "module only routes to it and never calls it.",
        ],
    },
    # Amazon Pay has NO provider dispute API: there is no endpoint to
    # open, answer, or escalate a dispute. Buyer recourse is the
    # Amazon A-to-z Guarantee claim, filed by the buyer in their
    # Amazon account, plus the card-network chargeback as a fallback.
    # Sources: Amazon Pay buyer help pages on the A-to-z Guarantee
    # (pay.amazon.com/help), checked 2026-10: claims cover physical
    # goods up to $2,500; Amazon may take up to 45 business days to
    # investigate and decide.
    "amazon_pay": {
        "rail": "amazon_pay",
        "channel": "buyer_filed_claim",
        "provider_dispute_api": False,
        "route_name": "Amazon A-to-z Guarantee claim (buyer-filed)",
        "steps": [
            "Buyer first contacts the merchant and gives them a chance "
            "to resolve the problem (an A-to-z precondition).",
            "Buyer files an A-to-z Guarantee claim from their Amazon "
            "account order history; no API exists for this step.",
            "Merchant supplies evidence to Amazon through Seller "
            "Central; the packet assembled here is that evidence.",
            "Amazon investigates and decides: refund to the buyer "
            "(charged back to the merchant) or claim denied.",
        ],
        "limits": {
            "coverage_cap": "$2,500 for physical goods",
            "investigation_clock": "up to 45 business days",
        },
        "notes": [
            "There is no Amazon Pay dispute API; any tool claiming to "
            "file this claim programmatically is misdescribing the "
            "rail.",
            "Fallback: the buyer may raise a chargeback with their "
            "card issuer, which runs on card-network rules instead.",
        ],
    },
    # Marketplace orders ride the same A-to-z Guarantee. Sources as
    # for amazon_pay: Amazon A-to-z Guarantee terms (amazon.com/help).
    "amazon": {
        "rail": "amazon",
        "channel": "buyer_filed_claim",
        "provider_dispute_api": False,
        "route_name": "Amazon A-to-z Guarantee claim (buyer-filed)",
        "steps": [
            "Buyer contacts the seller through the order page and "
            "gives them a chance to resolve the problem.",
            "Buyer files an A-to-z Guarantee claim from their Amazon "
            "account; no API exists for this step.",
            "Seller supplies evidence through Seller Central; the "
            "packet assembled here is that evidence.",
            "Amazon investigates and decides under the A-to-z terms.",
        ],
        "limits": {
            "coverage_cap": "$2,500 for physical goods",
            "investigation_clock": "up to 45 business days",
        },
        "notes": [
            "Same guarantee as Amazon Pay; the rail differs, the "
            "buyer's recourse does not.",
            "Fallback: card-issuer chargeback on card-network rules.",
        ],
    },
}

KNOWN_RAILS = tuple(sorted(PATHWAYS))

# Purchase-record keys that identify a purchase across rails.
_ID_KEYS = ("order_id", "capture_id", "charge_id", "session_id",
            "charge_permission_id", "refund_id")


def _refusal(check: str, detail: str, **extra) -> dict:
    item = {"check": check, "detail": detail}
    item.update(extra)
    return {"ok": False, "refusal": {"failed_checks": [item]}}


def build_evidence_packet(purchase: dict | None, bundle: dict | None) -> dict:
    """Assemble the evidence packet for one purchase from a chain bundle.

    purchase: the recorded purchase (identifiers, amounts, the receipt
    dict recorded for it, optionally its gate certificate summary).
    bundle: a Receipts bundle ({public_key, count, head_hash,
    receipts[]}). The chain is verified here, never merely asserted:
    chain_verification reports what receipts.verify_bundle returned.
    """
    packet: dict = {
        "found": purchase is not None,
        "purchase": None,
        "gate_verdicts": {},
        "chain_segment": [],
        "chain_verification": {"verifies": None, "detail": "no bundle supplied"},
        "verification_note": (
            "Chain verification recomputes every receipt hash, checks "
            "prev_hash continuity and every Ed25519 signature, and "
            "compares the declared count and head hash."
        ),
    }
    if bundle is not None:
        ok, detail = receipts_mod.verify_bundle(bundle)
        packet["chain_verification"] = {"verifies": ok is True, "detail": detail}
    if purchase is None:
        packet["note"] = (
            "No recorded purchase matched the supplied identifiers; "
            "the packet carries the pathway only."
        )
        return packet

    identifiers = {key: purchase[key] for key in _ID_KEYS if purchase.get(key)}
    packet["purchase"] = {
        "rail": purchase.get("rail"),
        "identifiers": identifiers,
        "amount": purchase.get("amount"),
        "currency": purchase.get("currency"),
        "environment": purchase.get("environment"),
        "rail_mode": purchase.get("rail_mode"),
        "item": purchase.get("item"),
    }

    verdicts: dict = {}
    certificate = purchase.get("certificate")
    if isinstance(certificate, dict):
        verdicts["certificate"] = {
            "verdict": certificate.get("verdict"),
            "canonical_address": certificate.get("canonical_address"),
            "registry_source": certificate.get("registry_source"),
        }
    receipt = purchase.get("receipt")
    if isinstance(receipt, dict):
        verdicts["payment_receipt_status"] = receipt.get("result_status")
    packet["gate_verdicts"] = verdicts

    if bundle is not None and isinstance(receipt, dict):
        receipts_list = bundle.get("receipts") or []
        target = receipt.get("hash")
        start = next(
            (i for i, entry in enumerate(receipts_list)
             if entry.get("hash") == target),
            None,
        )
        if start is None:
            packet["chain_segment_note"] = (
                "The recorded payment receipt is not present in this "
                "bundle; the segment is empty and the chain head does "
                "not evidence this purchase."
            )
        else:
            packet["chain_segment"] = [
                {
                    "index": i,
                    "receipt_id": entry.get("receipt_id"),
                    "actor": entry.get("actor"),
                    "result_status": entry.get("result_status"),
                    "hash": entry.get("hash"),
                    "prev_hash": entry.get("prev_hash"),
                }
                for i, entry in enumerate(receipts_list)
                if i >= start
            ]
            packet["chain_segment_note"] = (
                f"Segment runs from the payment receipt at index {start} "
                f"to the chain head (index {len(receipts_list) - 1}); "
                "hashes are listed so any holder of the bundle can "
                "re-verify the segment independently."
            )
    return packet


def route_dispute(rail, purchase: dict | None = None,
                  bundle: dict | None = None) -> dict:
    """Route one purchase to its rail's dispute pathway + evidence packet.

    Unknown rails are refused in the harness style: ok False and a
    refusal naming the failed check. Known rails always answer: the
    pathway is static data, and the packet reports honestly whether
    the purchase was found and whether the chain verifies.
    """
    canonical = str(rail).strip().lower() if rail is not None else ""
    pathway = PATHWAYS.get(canonical)
    if pathway is None:
        return _refusal(
            "unknown_rail",
            f"no dispute pathway is encoded for rail {rail!r}; "
            f"known rails: {', '.join(KNOWN_RAILS)}",
            rail=rail,
        )
    return {
        "ok": True,
        "rail": canonical,
        "pathway": pathway,
        "evidence_packet": build_evidence_packet(purchase, bundle),
        "scope": SCOPE_NOTE,
    }
