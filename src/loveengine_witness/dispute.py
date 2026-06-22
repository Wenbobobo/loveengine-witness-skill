"""Critical dispute review aggregation and read-only ProposalGate."""

from __future__ import annotations

from collections import Counter
from typing import Any

from eth_utils import to_checksum_address

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import keccak256_hex


VERDICTS = {"uphold", "dismiss", "unable_to_determine"}


def build_dispute(
    dispute_id: str,
    bundle_hash: str,
    severity: str,
    reason_hash: str,
    deadline: str,
) -> dict[str, Any]:
    if severity not in {"critical", "noncritical"}:
        raise LoveEngineError("invalid_severity", severity)
    return {
        "schema_version": "loveengine.dispute-case/1",
        "dispute_id": dispute_id,
        "bundle_hash": bundle_hash,
        "severity": severity,
        "reason_hash": reason_hash,
        "deadline": str(deadline),
        "status": "open",
    }


def build_review(
    review_id: str,
    dispute: dict[str, Any],
    node: str,
    verdict: str,
    reason_hash: str,
    completed_at: str,
    signature: str = "0x",
) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise LoveEngineError("invalid_verdict", verdict)
    return {
        "schema_version": "loveengine.dispute-review/1",
        "review_id": review_id,
        "dispute_id": dispute["dispute_id"],
        "bundle_hash": dispute["bundle_hash"],
        "node": to_checksum_address(node),
        "verdict": verdict,
        "reason_hash": reason_hash,
        "completed_at": str(completed_at),
        "signature": signature,
    }


def aggregate_reviews(
    dispute: dict[str, Any],
    reviews: list[dict[str, Any]],
    *,
    expected_nodes: set[str],
) -> dict[str, Any]:
    normalized_expected = {to_checksum_address(node) for node in expected_nodes}
    nodes: set[str] = set()
    for review in reviews:
        node = to_checksum_address(review["node"])
        if node not in normalized_expected:
            raise LoveEngineError("unexpected_review_node", node)
        if node in nodes:
            raise LoveEngineError("duplicate_review_node", node)
        if review["dispute_id"] != dispute["dispute_id"]:
            raise LoveEngineError("wrong_dispute", review["review_id"])
        if review["bundle_hash"] != dispute["bundle_hash"]:
            raise LoveEngineError("wrong_bundle", review["review_id"])
        nodes.add(node)
    status = "unresolved"
    if len(normalized_expected) == 3 and nodes == normalized_expected:
        counts = Counter(review["verdict"] for review in reviews)
        if counts["uphold"] >= 2:
            status = "upheld"
        elif counts["dismiss"] >= 2:
            status = "dismissed"
    value = dict(dispute)
    value["status"] = status
    value["valid_review_count"] = str(len(nodes))
    value["review_ids"] = [review["review_id"] for review in reviews]
    return value


def build_proposal_plan(
    *,
    session: dict[str, Any],
    bundle: dict[str, Any],
    disputes: list[dict[str, Any]],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    reasons: list[str] = []
    if session.get("status") != "closed":
        reasons.append("session_not_closed")
    if bundle.get("status") != "finalized":
        reasons.append("bundle_not_finalized")
    if bundle.get("session_id") != session.get("session_id"):
        reasons.append("bundle_session_mismatch")
    if any(
        item.get("bundle_hash") is not None
        and item.get("bundle_hash") != bundle.get("bundle_hash")
        for item in disputes
    ):
        reasons.append("dispute_bundle_mismatch")
    blocked = [
        item["dispute_id"]
        for item in disputes
        if item.get("severity") == "critical" and item.get("status") != "dismissed"
    ]
    if blocked:
        reasons.append("critical_dispute_blocked:" + ",".join(sorted(blocked)))
    if reasons:
        raise LoveEngineError("proposal_gate_blocked", ";".join(reasons))
    plan = {
        "schema_version": "loveengine.proposal-plan/1",
        "ready": True,
        "session_id": session["session_id"],
        "bundle_hash": bundle["bundle_hash"],
        "proposal": proposal,
        "critical_disputes": sorted(
            item["dispute_id"]
            for item in disputes
            if item.get("severity") == "critical"
        ),
    }
    plan["proposal_hash"] = keccak256_hex(canonical_json_bytes(plan))
    return plan
