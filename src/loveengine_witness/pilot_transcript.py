"""Offline verification for the package-to-PublicSink pilot transcript."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .live_evidence import evidence_bundle_hash
from .live_protocol import ZERO_HASH, verify_live_event
from .observation import observation_set_hash
from .schema import validate_schema
from .secrets import reject_secret_fields


def pilot_transcript_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def _same(left: Any, right: Any, label: str) -> None:
    if left != right:
        raise LoveEngineError("pilot_cross_reference_mismatch", label)


def verify_pilot_transcript(value: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(value)
    validate_schema(value, "pilot-transcript-v1.schema.json")
    if value["transcript_hash"] != pilot_transcript_hash(value):
        raise LoveEngineError("transcript_hash_mismatch", "PilotTranscriptV1")
    session = value["session"]
    bundle = value["evidence_bundle"]
    observations = value["observation_set"]
    _same(session["session_id"], bundle["session_id"], "session/bundle")
    _same(session["session_id"], observations["session_id"], "session/observations")
    _same(
        session["head_event_hash"],
        bundle["head_event_hash"],
        "session/bundle head",
    )
    _same(
        session["head_event_hash"],
        observations["head_event_hash"],
        "session/observation head",
    )
    _same(
        bundle["bundle_hash"],
        observations["bundle_hash"],
        "bundle/observation hash",
    )
    if bundle.get("schema_version") == "loveengine.evidence-bundle/2":
        _same(bundle["bundle_hash"], evidence_bundle_hash(bundle), "bundle hash")
    if observations.get("schema_version") == "loveengine.observation-set/1":
        _same(
            observations["observation_set_hash"],
            observation_set_hash(observations),
            "observation set hash",
        )
    previous = ZERO_HASH
    for expected, event in enumerate(value["events"], start=1):
        if int(event["sequence"]) != expected or event["previous_event_hash"] != previous:
            raise LoveEngineError("pilot_cross_reference_mismatch", "event chain")
        if not verify_live_event(event):
            raise LoveEngineError("event_hash_mismatch", event["event_id"])
        previous = event["event_hash"]
    if value["events"]:
        _same(previous, session["head_event_hash"], "event/session head")
    proposal = value["proposal"]
    for approval in value["vote_approvals"]:
        _same(approval["proposal_id"], proposal["proposal_id"], "vote proposal")
        _same(approval["payload_hash"], proposal["payload_hash"], "vote payload")
    if not value["proposal_gate"].get("ready"):
        raise LoveEngineError("pilot_cross_reference_mismatch", "gate not ready")
    if not value["final_state"].get("proposal_executed"):
        raise LoveEngineError("pilot_cross_reference_mismatch", "proposal not executed")
    return {
        "valid": True,
        "run_id": value["run_id"],
        "event_count": len(value["events"]),
        "observation_receipts": len(observations.get("receipts", [])),
        "vote_approvals": len(value["vote_approvals"]),
        "total_uto": value["final_state"]["total_uto"],
    }
