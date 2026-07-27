"""EvidenceBundleV2 finalization."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import keccak256_hex, sha256_prefixed
from .live_protocol import ZERO_HASH, verify_live_event
from .live_store import ArtifactStore, LiveMetadataStore


def evidence_bundle_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("bundle_hash", None)
    return keccak256_hex(canonical_json_bytes(view))


def finalize_evidence_bundle(
    metadata: LiveMetadataStore,
    artifacts: ArtifactStore,
    session_id: str,
    *,
    revision: str,
    finalized_at: str,
) -> dict[str, Any]:
    session = metadata.get_session(session_id)
    if session["status"] != "closed":
        raise LoveEngineError("session_not_closed", session_id)
    events = metadata.list_events(session_id)
    previous = ZERO_HASH
    counts = {"source": 0, "summary": 0, "derived": 0}
    references: list[dict[str, Any]] = []
    for expected, event in enumerate(events, start=1):
        if int(event["sequence"]) != expected:
            raise LoveEngineError("sequence_gap", str(expected))
        if event["previous_event_hash"] != previous or not verify_live_event(event):
            raise LoveEngineError("hash_chain_broken", event["event_id"])
        artifact = artifacts.get(event["artifact_hash"])
        if sha256_prefixed(artifact) != event["artifact_hash"]:
            raise LoveEngineError(
                "artifact_hash_mismatch", event["artifact_hash"]
            )
        if event["artifact_hash"] != sha256_prefixed(
            event["content"].encode("utf-8")
        ):
            raise LoveEngineError(
                "artifact_content_mismatch", event["event_id"]
            )
        counts[event["category"]] += 1
        references.append(
            {
                "event_id": event["event_id"],
                "sequence": event["sequence"],
                "category": event["category"],
                "event_hash": event["event_hash"],
                "artifact_hash": event["artifact_hash"],
            }
        )
        previous = event["event_hash"]
    if previous != session["head_event_hash"]:
        raise LoveEngineError("hash_chain_broken", session_id)
    value = {
        "schema_version": "loveengine.evidence-bundle/2",
        "bundle_id": f"{session_id}:r{revision}",
        "session_id": session_id,
        "revision": str(revision),
        "status": "finalized",
        "finalized_at": str(finalized_at),
        "event_count": str(len(references)),
        "head_event_hash": previous,
        "category_counts": {key: str(value) for key, value in counts.items()},
        "events": references,
    }
    value["bundle_hash"] = evidence_bundle_hash(value)
    return metadata.save_bundle(value)
