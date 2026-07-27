"""Verification for the release-to-ProposalGate Witness core transcript."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .pilot_transcript import (
    verify_release_anchor_rpc,
    verify_transcript_trust_policy,
    verify_witness_evidence_stages,
)
from .schema import validate_schema
from .secrets import reject_secret_fields
from .trust_policy import load_node_trust_policy, validate_node_trust_policy


TrustPolicyInput = dict[str, Any] | str | Path


def core_transcript_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def _load_policy(value: TrustPolicyInput | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return validate_node_trust_policy(value)
    return load_node_trust_policy(Path(value))


def verify_core_transcript(
    value: dict[str, Any],
    rpc_url: str | None = None,
    trust_policy: TrustPolicyInput | None = None,
) -> dict[str, Any]:
    """Verify core integrity, optional chain consistency, and optional trust binding."""

    reject_secret_fields(value)
    validate_schema(value, "witness-core-transcript-v1.schema.json")
    if value["transcript_hash"] != core_transcript_hash(value):
        raise LoveEngineError(
            "transcript_hash_mismatch", "WitnessCoreTranscriptV1"
        )
    policy = _load_policy(trust_policy)
    verify_witness_evidence_stages(
        value,
        allowed_issuers=(policy["allowed_issuers"] if policy is not None else None),
    )
    if policy is not None:
        verify_transcript_trust_policy(value, policy)
    if rpc_url:
        verify_release_anchor_rpc(value, rpc_url)

    trust_bound = bool(rpc_url and policy is not None)
    verification_level = (
        "chain_verified"
        if trust_bound
        else "chain_consistency"
        if rpc_url
        else "offline_integrity"
    )
    return {
        "valid": True,
        "verification_level": verification_level,
        "chain_verified": bool(rpc_url),
        "trust_bound": trust_bound,
        "run_id": value["run_id"],
        "environment": value["environment"],
        "actors_simulated": value["actors_simulated"],
        "event_count": len(value["events"]),
        "observation_receipts": len(value["observation_receipts"]),
        "review_receipts": len(value["review_receipts"]),
        "gate_ready": bool(value["proposal_gate"].get("ready")),
    }
