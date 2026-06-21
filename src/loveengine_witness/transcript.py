"""LocalLoopTranscriptV1 construction and verification."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .schema import validate_schema
from .secrets import reject_secret_fields


def transcript_hash(transcript: dict[str, Any]) -> str:
    view = dict(transcript)
    view.pop("transcript_hash", None)
    return sha256_prefixed(canonical_json_bytes(view))


def verify_transcript(transcript: dict[str, Any]) -> dict[str, Any]:
    reject_secret_fields(transcript)
    validate_schema(transcript, "local-loop-transcript-v1.schema.json")
    expected = transcript_hash(transcript)
    if transcript.get("transcript_hash") != expected:
        raise LoveEngineError("transcript_hash_mismatch", expected)
    return {"valid": True, "run_id": transcript["run_id"], "transcript_hash": expected}
