"""LiveReviewTranscriptV1 hashing and complete offline verification."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .dispute import aggregate_reviews, build_proposal_plan, build_review
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .live_evidence import evidence_bundle_hash
from .live_protocol import ZERO_HASH, verify_live_event
from .m4_network import (
    verify_bootstrap_v2,
    verify_receipt_v2,
    verify_task_v2,
)
from .schema import validate_schema
from .secrets import reject_secret_fields


def live_transcript_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def verify_live_transcript(value: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(value)
    validate_schema(value, "live-review-transcript-v1.schema.json")
    expected_hash = live_transcript_hash(value)
    if value["transcript_hash"] != expected_hash:
        raise LoveEngineError("transcript_hash_mismatch", expected_hash)
    if value["session"]["status"] != "closed":
        raise LoveEngineError("session_not_closed", value["session"]["session_id"])
    previous = ZERO_HASH
    for expected_sequence, event in enumerate(value["events"], start=1):
        if int(event["sequence"]) != expected_sequence:
            raise LoveEngineError("sequence_gap", str(expected_sequence))
        if event["previous_event_hash"] != previous or not verify_live_event(event):
            raise LoveEngineError("hash_chain_broken", event["event_id"])
        previous = event["event_hash"]
    bundle = value["evidence_bundle"]
    if bundle["bundle_hash"] != evidence_bundle_hash(bundle):
        raise LoveEngineError("bundle_hash_mismatch", bundle["bundle_id"])
    registry = value["contracts"]["SkillRegistry"]
    verify_bootstrap_v2(
        value["bootstrap"], value["chain_id"], registry, now=1770000000
    )
    expected_nodes = {
        profile["profile"]["node"] for profile in value["bootstrap"]["directory"]
    }
    tasks_by_id: dict[str, dict[str, Any]] = {}
    issuer_nonces: set[tuple[str, str]] = set()
    if not value["tasks"]:
        raise LoveEngineError("task_quorum_missing", "review tasks are required")
    expected_manifest_hash = value["tasks"][0]["manifest_hash"]
    for task in value["tasks"]:
        verify_task_v2(
            task,
            expected_chain_id=value["chain_id"],
            expected_registry=registry,
            expected_recipient=task["recipient"],
            expected_issuer=value["bootstrap"]["publisher"],
            expected_manifest_hash=expected_manifest_hash,
            now=int(task["deadline"]) - 1,
        )
        if task["task_id"] in tasks_by_id:
            raise LoveEngineError("duplicate_task_id", task["task_id"])
        nonce_key = (task["issuer"], task["nonce"])
        if nonce_key in issuer_nonces:
            raise LoveEngineError("duplicate_task_nonce", task["nonce"])
        issuer_nonces.add(nonce_key)
        tasks_by_id[task["task_id"]] = task
    reviews_by_dispute: dict[str, list[dict[str, Any]]] = {}
    receipt_tasks: set[str] = set()
    for receipt in value["reviews"]:
        signer = verify_receipt_v2(receipt, value["chain_id"], registry)
        if signer not in expected_nodes:
            raise LoveEngineError("unexpected_review_node", signer)
        result = receipt["result"]
        task = tasks_by_id.get(receipt["task_id"])
        if task is None:
            raise LoveEngineError("receipt_without_task", receipt["task_id"])
        if receipt["task_id"] in receipt_tasks:
            raise LoveEngineError("duplicate_task_receipt", receipt["task_id"])
        receipt_tasks.add(receipt["task_id"])
        if signer != task["recipient"]:
            raise LoveEngineError("wrong_recipient", receipt["task_id"])
        if (
            result["dispute_id"] != task["payload"]["dispute_id"]
            or result["bundle_hash"] != task["payload"]["bundle_hash"]
        ):
            raise LoveEngineError("receipt_task_mismatch", receipt["task_id"])
        reviews_by_dispute.setdefault(result["dispute_id"], []).append(
            build_review(
                receipt["task_id"],
                {
                    "dispute_id": result["dispute_id"],
                    "bundle_hash": result["bundle_hash"],
                },
                signer,
                result["verdict"],
                result["reason_hash"],
                receipt["completed_at"],
                receipt["signature"],
            )
        )
    for dispute in value["disputes"]:
        recalculated = aggregate_reviews(
            dispute,
            reviews_by_dispute.get(dispute["dispute_id"], []),
            expected_nodes=expected_nodes,
        )
        if recalculated["status"] != dispute["status"]:
            raise LoveEngineError("dispute_status_mismatch", dispute["dispute_id"])
    accepted_ids = set(value["proposal_gate"]["accepted"]["critical_disputes"])
    ready_disputes = [
        dispute
        for dispute in value["disputes"]
        if dispute["dispute_id"] in accepted_ids
    ]
    plan = build_proposal_plan(
        session=value["session"],
        bundle=bundle,
        disputes=ready_disputes,
        proposal=value["proposal_gate"]["accepted"]["proposal"],
    )
    if plan["proposal_hash"] != value["proposal_gate"]["accepted"]["proposal_hash"]:
        raise LoveEngineError("proposal_hash_mismatch", plan["proposal_hash"])
    return {
        "valid": True,
        "run_id": value["run_id"],
        "event_count": len(value["events"]),
        "node_count": len(expected_nodes),
        "review_count": len(value["reviews"]),
        "accepted_gate": True,
        "blocked_gate": value["proposal_gate"]["blocked"]["ready"] is False,
        "transcript_hash": expected_hash,
    }
