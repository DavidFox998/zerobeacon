#!/usr/bin/env python3
"""Resolution Layer — decisions with reasons, in the payments dialect.

The harness's checkpoints already decided: a payment packet that no
longer matches its sealed record is declined, a transaction over its
tier limit is declined. What they returned was a verdict a person had
to interpret. The Resolution Layer makes the decision itself the
answer — a decision record a counterparty can act on without a human
in the loop:

  - Every decision, APPROVED or DECLINED, is a case: it carries a
    case_id, the checkpoint that decided it, the checkpoint's
    version, who decided, when, the subject, and the evidence the
    decision rests on — the evidence envelope embedded in the record,
    not referenced from somewhere else.
  - A decline is an answer, not an error. It carries one primary
    reason (reason == reasons[0]) and every reason behind it, in a
    published, deterministic precedence order.
  - Every reason carries its limit: limit_name, limit_type, the
    operator of the comparison, limit_value, assessed_value, and —
    whenever those values are numeric — their unit. And it carries
    steps_to_resolve: what to change or provide so the case closes
    the other way, in the Resolution Center's sense.
  - No current validation, no decision. Where a checkpoint declares
    requires_validation, the check behind the decision must show a
    current validation record. A missing or expired validation
    declines the case on its own — short-circuit, never mixed with
    subject reasons, because no limit was honestly evaluated — and
    carries typed instants (required_valid_at,
    validation_expires_at), not a numeric pair.
  - Every decision is written to the Receipts chain by the hosted
    harness and retrievable by case_id. A decision you cannot audit
    later is just an opinion again.

Reserved, not emitted in v1: DETAIL_WITHHELD_BY_LAW — a reason code
for the cases where a duty (anti-money-laundering reporting, for
example) forbids explaining a detail. It is named here so the shape
has somewhere honest to put it; emitting it is a separate ruling.

Standard library only. Python 3.12.
"""

from __future__ import annotations

import uuid
from datetime import datetime

FORMAT = "resolution-decision/1"

OPERATORS = ("lt", "lte", "gt", "gte", "eq", "neq")

LIMIT_TYPES = (
    "verification",
    "hard_policy_limit",
    "restricted_category",
    "eligibility_limit",
    "document",
    "validation",
)

REASON_CODES = (
    "LIMIT_NOT_MET",
    "POLICY_LIMIT_EXCEEDED",
    "RESTRICTED_CATEGORY",
    "VERIFICATION_MISMATCH",
    "DOCUMENT_NOT_ACCEPTED",
    "VALIDATION_MISSING",
    "VALIDATION_EXPIRED",
)

RESERVED_REASON_CODES = ("DETAIL_WITHHELD_BY_LAW",)

VALIDATION_REASON_CODES = ("VALIDATION_MISSING", "VALIDATION_EXPIRED")

# Published precedence (contract rule 2): a validation reason
# outranks everything, because it means no limit was honestly
# evaluated; then verification of the artefact itself; then the
# hard policy limit; then restricted categories; then eligibility
# limits; then document acceptance.
PRECEDENCE = (
    "VALIDATION_MISSING",
    "VALIDATION_EXPIRED",
    "VERIFICATION_MISMATCH",
    "POLICY_LIMIT_EXCEEDED",
    "RESTRICTED_CATEGORY",
    "LIMIT_NOT_MET",
    "DOCUMENT_NOT_ACCEPTED",
)

_LIMIT_TYPE_TO_CODE = {
    "verification": "VERIFICATION_MISMATCH",
    "hard_policy_limit": "POLICY_LIMIT_EXCEEDED",
    "restricted_category": "RESTRICTED_CATEGORY",
    "eligibility_limit": "LIMIT_NOT_MET",
    "document": "DOCUMENT_NOT_ACCEPTED",
}


class ResolutionRequestError(ValueError):
    """The evaluation request itself violates the contract."""


class UnknownCheckpointError(ResolutionRequestError):
    """The named checkpoint does not exist."""


CHECKPOINTS = {
    "paypal_packet_gate": {
        "checkpoint_id": "paypal_packet_gate",
        "description": "A payment's evidence packet is verified "
                       "against its Bundle Lock record before the "
                       "payment is proposed. The check is a "
                       "recompute — the record must recompute to "
                       "its own certificate before any file is "
                       "read — so it is self-validating and this "
                       "checkpoint declares requires_validation "
                       "false; no external validation record exists "
                       "for it in v1.",
        "gate_version": "2.0.0",
        "decided_by": "paypal_packet_gate",
        "requires_validation": False,
        "limits": [
            {
                "limit_name": "bundle_certificate_match",
                "limit_type": "verification",
                "operator": "eq",
                "limit_value": 1,
                "unit": "match",
                "check_id": "bundle-verify-v1",
            },
        ],
    },
    "paypal_transaction_review": {
        "checkpoint_id": "paypal_transaction_review",
        "description": "A proposed transaction is assessed against "
                       "the limits the caller declares for the "
                       "account (its tier limit, its exposure "
                       "limits). The checks behind this checkpoint "
                       "rest on validation records, so it declares "
                       "requires_validation true: no current "
                       "validation, no decision.",
        "gate_version": "1.0.0",
        "decided_by": "paypal_transaction_review",
        "requires_validation": True,
        "limits": [],
    },
    "paypal_document_review": {
        "checkpoint_id": "paypal_document_review",
        "description": "Documents supplied for a check are assessed "
                       "for acceptance: type, category, and the "
                       "validation of the check that reads them. "
                       "Declares requires_validation true.",
        "gate_version": "1.0.0",
        "decided_by": "paypal_document_review",
        "requires_validation": True,
        "limits": [],
    },
}


def describe_checkpoint(checkpoint_id: str) -> dict:
    """The checkpoint's published description: its limits with their
    operators, whether it requires validation, and the precedence
    order its reasons are returned in."""
    checkpoint = CHECKPOINTS.get(checkpoint_id)
    if checkpoint is None:
        raise UnknownCheckpointError(
            f"unknown checkpoint: {checkpoint_id!r}")
    described = dict(checkpoint)
    described["precedence"] = list(PRECEDENCE)
    described["reason_codes"] = list(REASON_CODES)
    return described


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_instant(raw, field: str) -> datetime:
    if isinstance(raw, datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ResolutionRequestError(
                f"{field} is not an ISO 8601 instant: {raw!r}")
    else:
        raise ResolutionRequestError(
            f"{field} must be an ISO 8601 instant string")
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _satisfies(operator: str, assessed, limit) -> bool:
    if operator == "lt":
        return assessed < limit
    if operator == "lte":
        return assessed <= limit
    if operator == "gt":
        return assessed > limit
    if operator == "gte":
        return assessed >= limit
    if operator == "eq":
        return assessed == limit
    if operator == "neq":
        return assessed != limit
    raise ResolutionRequestError(f"unknown operator: {operator!r}")


def _templated_steps(limit_name: str, operator: str, limit_value,
                     unit) -> str:
    """Steps generated from the comparison itself, so the text can
    never disagree with the operator it ships beside."""
    suffix = f" {unit}" if unit else ""
    return (f"Resolve {limit_name}: provide an assessed value that "
            f"satisfies {operator} {limit_value}{suffix}.")


def _validation_context(evidence: dict, limits: list[dict]) -> dict:
    """The validation papers behind the decision: the evidence
    envelope's, else the first limit's that carries any."""
    sources = [evidence] + list(limits)
    for source in sources:
        if source.get("validation_id") is not None \
                or source.get("validation_expires_at") is not None:
            return {
                "check_id": source.get("check_id"),
                "validation_id": source.get("validation_id"),
                "validation_expires_at":
                    source.get("validation_expires_at"),
            }
    first = sources[0] if sources else {}
    return {
        "check_id": first.get("check_id"),
        "validation_id": None,
        "validation_expires_at": None,
    }


def _validation_reason(code: str, context: dict, evaluated_at: datetime,
                       evaluated_text: str) -> dict:
    """A validation reason: typed instants, never a numeric pair
    (contract rule 7), naming the check and, where a record exists,
    its validation_id (rule 8)."""
    check_id = context.get("check_id")
    if code == "VALIDATION_MISSING":
        steps = (f"Provide a current validation record for check "
                 f"{check_id}: no decision is made on an "
                 f"unvalidated check.")
    else:
        steps = (f"Revalidate check {check_id}: validation "
                 f"{context.get('validation_id')} expired at "
                 f"{context.get('validation_expires_at')}; a "
                 f"current record must show validation_expires_at "
                 f"gt {evaluated_text} (the evaluation instant).")
    return {
        "reason_code": code,
        "limit_type": "validation",
        "limit_name": "current_validation",
        "operator": "gt",
        "steps_to_resolve": steps,
        "check_id": check_id,
        "validation_id": context.get("validation_id"),
        "required_valid_at": evaluated_text,
        "validation_expires_at": context.get("validation_expires_at"),
    }


def _subject_reason(limit: dict, assessed) -> dict:
    reason = {
        "reason_code": _LIMIT_TYPE_TO_CODE[limit["limit_type"]],
        "limit_type": limit["limit_type"],
        "limit_name": limit["limit_name"],
        "operator": limit["operator"],
        "limit_value": limit["limit_value"],
        "assessed_value": assessed,
        "steps_to_resolve": limit.get("steps_to_resolve") or
        _templated_steps(limit["limit_name"], limit["operator"],
                         limit["limit_value"], limit.get("unit")),
        "check_id": limit.get("check_id"),
        "validation_id": limit.get("validation_id"),
    }
    if _is_number(limit["limit_value"]) or _is_number(assessed):
        reason["unit"] = limit.get("unit")
    return reason


def _validate_limit(limit, evidence: dict) -> object:
    """Validate one declared limit against contract rules 4 and 5
    and return the assessed value it will be judged on."""
    if not isinstance(limit, dict):
        raise ResolutionRequestError("each limit must be an object")
    if not isinstance(limit.get("limit_name"), str) \
            or not limit["limit_name"].strip():
        raise ResolutionRequestError("limit_name is required")
    if limit.get("limit_type") not in _LIMIT_TYPE_TO_CODE:
        raise ResolutionRequestError(
            f"limit_type must be one of "
            f"{sorted(_LIMIT_TYPE_TO_CODE)}: {limit.get('limit_type')!r}")
    operator = limit.get("operator")
    if operator not in OPERATORS:
        raise ResolutionRequestError(
            f"operator is required on every limit and must be one "
            f"of {list(OPERATORS)} (rule 5): {operator!r}")
    if "limit_value" not in limit:
        raise ResolutionRequestError(
            f"limit_value is required for {limit['limit_name']}")
    assessed = limit.get("assessed_value", evidence.get("value"))
    if assessed is None:
        raise ResolutionRequestError(
            f"assessed_value is required for {limit['limit_name']} "
            f"(on the limit, or as the evidence value)")
    if _is_number(limit["limit_value"]) or _is_number(assessed):
        if not isinstance(limit.get("unit"), str) \
                or not limit["unit"].strip():
            raise ResolutionRequestError(
                f"unit is required whenever limit and assessed "
                f"values are numeric (rule 4): {limit['limit_name']}")
    return assessed


def evaluate(request: dict) -> dict:
    """Assess a subject against a checkpoint's declared limits and
    return the decision record. Pure: persistence (the Receipts
    chain, the case store) is the hosted harness's work, done with
    the record this returns."""
    if not isinstance(request, dict):
        raise ResolutionRequestError("evaluation request must be an object")
    checkpoint_id = request.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id.strip():
        raise ResolutionRequestError("checkpoint_id is required")
    checkpoint = CHECKPOINTS.get(checkpoint_id)
    if checkpoint is None:
        raise UnknownCheckpointError(
            f"unknown checkpoint: {checkpoint_id!r}")
    subject = request.get("subject")
    if not isinstance(subject, dict) \
            or not isinstance(subject.get("kind"), str) \
            or not isinstance(subject.get("ref"), str):
        raise ResolutionRequestError(
            "subject object with kind and ref is required")
    evidence_in = request.get("evidence") or {}
    if not isinstance(evidence_in, dict):
        raise ResolutionRequestError("evidence must be an object")
    limits_in = request.get("limits")
    if limits_in is None:
        limits = [dict(limit) for limit in checkpoint["limits"]]
    elif isinstance(limits_in, list):
        limits = limits_in
    else:
        raise ResolutionRequestError("limits must be a list")

    evaluated_at = _now()
    evaluated_text = evaluated_at.isoformat(timespec="seconds")

    assessed_by_limit = [_validate_limit(limit, evidence_in)
                         for limit in limits]

    evidence = {
        "evidence_id": evidence_in.get("evidence_id")
        or f"ev_{uuid.uuid4().hex[:16]}",
        "property": evidence_in.get("property"),
        "value": evidence_in.get("value"),
        "unit": evidence_in.get("unit"),
        "check_id": evidence_in.get("check_id"),
        "validation_id": evidence_in.get("validation_id"),
    }
    if evidence["property"] is None and limits:
        evidence["property"] = limits[0].get("limit_name")
    if evidence["value"] is None and assessed_by_limit:
        evidence["value"] = assessed_by_limit[0]
    if evidence["check_id"] is None and limits:
        evidence["check_id"] = limits[0].get("check_id")
    if evidence["validation_id"] is None and limits:
        evidence["validation_id"] = limits[0].get("validation_id")

    requires_validation = bool(checkpoint["requires_validation"]) or \
        any(limit.get("requires_validation") for limit in limits)
    reasons: list[dict] = []
    if requires_validation:
        context = _validation_context(evidence_in, limits)
        if context["validation_id"] is None:
            reasons = [_validation_reason(
                "VALIDATION_MISSING", context, evaluated_at,
                evaluated_text)]
        elif context["validation_expires_at"] is not None:
            expires = _parse_instant(
                context["validation_expires_at"],
                "validation_expires_at")
            if expires <= evaluated_at:
                reasons = [_validation_reason(
                    "VALIDATION_EXPIRED", context, evaluated_at,
                    evaluated_text)]
    if not reasons:
        # No validation reason: subject limits were honestly
        # evaluated. (Rule 3: the two kinds never mix.)
        for limit, assessed in zip(limits, assessed_by_limit):
            if not _satisfies(limit["operator"], assessed,
                              limit["limit_value"]):
                reasons.append(_subject_reason(limit, assessed))
        rank = {code: i for i, code in enumerate(PRECEDENCE)}
        reasons.sort(key=lambda r: rank[r["reason_code"]])

    record = {
        "format": FORMAT,
        "decision": "DECLINED" if reasons else "APPROVED",
        "checkpoint_id": checkpoint_id,
        "gate_version": checkpoint["gate_version"],
        "decided_by": checkpoint["decided_by"],
        "case_id": f"case_{uuid.uuid4().hex}",
        "evaluated_at": evaluated_text,
        "subject": {"kind": subject["kind"], "ref": subject["ref"]},
        "evidence": evidence,
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
    }
    if record["decision"] == "APPROVED" and requires_validation:
        # Rule 6, stated as the invariant it is: an APPROVED record
        # under requires_validation always rests on a validation_id
        # (a missing or expired one declined above, short-circuit).
        assert record["evidence"]["validation_id"] is not None or \
            _validation_context(evidence_in,
                                limits)["validation_id"] is not None
    return record
