"""M4 live session and append-only event protocol."""

from __future__ import annotations

from typing import Any

from .canonical import canonical_json_bytes
from .hashes import keccak256_hex, sha256_prefixed
from .secrets import reject_secret_fields


ZERO_HASH = "0x" + "00" * 32
CATEGORIES = {"source", "summary", "derived"}


def build_live_session(
    session_id: str,
    source_type: str,
    created_at: str,
) -> dict[str, Any]:
    value = {
        "schema_version": "loveengine.live-session/1",
        "session_id": session_id,
        "source_type": source_type,
        "status": "live",
        "created_at": str(created_at),
        "closed_at": None,
        "next_sequence": "1",
        "head_event_hash": ZERO_HASH,
    }
    reject_secret_fields(value)
    return value


def live_event_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("event_hash", None)
    return keccak256_hex(canonical_json_bytes(view))


def build_live_event(
    *,
    event_id: str,
    session_id: str,
    sequence: str,
    occurred_at: str,
    category: str,
    source_type: str,
    content: str,
    artifact_hash: str,
    previous_event_hash: str,
    source_uri: str | None = None,
    media_url: str | None = None,
    media_hash: str | None = None,
) -> dict[str, Any]:
    if category not in CATEGORIES:
        raise ValueError(f"unsupported event category: {category}")
    value = {
        "schema_version": "loveengine.live-event/1",
        "event_id": event_id,
        "session_id": session_id,
        "sequence": str(sequence),
        "occurred_at": str(occurred_at),
        "category": category,
        "source_type": source_type,
        "source_uri": source_uri,
        "content": content,
        "content_hash": sha256_prefixed(content.encode("utf-8")),
        "artifact_hash": artifact_hash,
        "media_url": media_url,
        "media_hash": media_hash,
        "previous_event_hash": previous_event_hash.lower(),
    }
    value["event_hash"] = live_event_hash(value)
    reject_secret_fields(value)
    return value


def verify_live_event(value: dict[str, Any]) -> bool:
    reject_secret_fields(value)
    if value["content_hash"] != sha256_prefixed(value["content"].encode("utf-8")):
        return False
    return value["event_hash"] == live_event_hash(value)
