"""EvidenceBundleV1 construction."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .hashes import keccak256_hex, sha256_prefixed
from .schema import validate_schema
from .secrets import reject_secret_fields


def build_evidence_bundle(session: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(session)
    base = {
        "schema_version": "loveengine.evidence-bundle/1",
        "bundle_id": session["bundle_id"],
        "session_id": session["session_id"],
        "proposal_type": session["proposal_type"],
        "subject": session["subject"],
        "source_refs": session.get("source_refs", []),
        "attachments": session.get("attachments", []),
        "summary": {"kind": "summary", "text": session.get("summary", "")},
        "created_at": session["created_at"],
    }
    payload_hash = keccak256_hex(canonical_json_bytes(base))
    artifact_view = {**base, "payload_hash": payload_hash}
    evidence = {
        **artifact_view,
        "artifact_hash": sha256_prefixed(canonical_json_bytes(artifact_view)),
    }
    validate_schema(evidence, "evidence-bundle-v1.schema.json")
    return evidence
