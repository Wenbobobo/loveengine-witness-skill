"""Independent evidence verification before a dispute verdict is signed."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

from .errors import LoveEngineError
from .hashes import sha256_prefixed
from .live_evidence import evidence_bundle_hash
from .live_protocol import ZERO_HASH, verify_live_event
from .observation import (
    MAX_ARTIFACT_BYTES,
    MAX_JSON_RESPONSE_BYTES,
    _normalized_http_origin,
    _read_limited,
)
from .schema import validate_schema
from .secrets import reject_secret_fields


MAX_REVIEW_EVENTS = 1_000
MAX_REVIEW_ARTIFACT_BYTES = 32 * 1024 * 1024
CURRENT_REVIEW_PAYLOAD_SCHEMA = "loveengine.review-dispute-payload/1"
REVIEW_RESULT_BINDING_FIELDS = (
    "dispute_id",
    "bundle_hash",
    "session_id",
    "revision",
    "event_count",
    "head_event_hash",
)
REVIEW_RESULT_HASH_FIELDS = {"bundle_hash", "head_event_hash"}


def is_current_review_payload(payload: object) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == CURRENT_REVIEW_PAYLOAD_SCHEMA
    )


def require_verified_review_result(
    payload: object,
    result: object,
    *,
    task_id: str,
) -> dict[str, Any]:
    """Require the executable review binding recorded by a current node."""

    if not is_current_review_payload(payload):
        raise LoveEngineError("review_evidence_payload_required", task_id)
    validate_schema(payload, "review-dispute-payload-v1.schema.json")
    if not isinstance(result, dict):
        raise LoveEngineError("review_evidence_result_invalid", task_id)
    if result.get("evidence_verified") is not True:
        raise LoveEngineError("review_evidence_not_verified", task_id)
    for field in REVIEW_RESULT_BINDING_FIELDS:
        expected = payload[field]
        actual = result.get(field)
        if field in REVIEW_RESULT_HASH_FIELDS:
            matches = str(actual).lower() == str(expected).lower()
        else:
            matches = str(actual) == str(expected)
        if not matches:
            raise LoveEngineError(
                "review_evidence_binding_mismatch", f"{task_id}:{field}"
            )
    return result


def validate_review_urls(
    payload: dict[str, Any],
    *,
    allowed_origin: str,
) -> None:
    validate_schema(payload, "review-dispute-payload-v1.schema.json")
    expected = _normalized_http_origin(allowed_origin)
    for field in ("evidence_url", "events_url", "artifact_base_url"):
        if _normalized_http_origin(payload[field]) != expected:
            raise LoveEngineError(
                "review_origin_mismatch",
                f"{field} must match the invite server origin",
            )


async def _json_response(
    session: ClientSession,
    url: str,
    *,
    error_code: str,
) -> Any:
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200:
            raise LoveEngineError(error_code, str(response.status), 4)
        return json.loads(
            (
                await _read_limited(
                    response,
                    limit=MAX_JSON_RESPONSE_BYTES,
                    error_code=f"{error_code}_too_large",
                )
            ).decode("utf-8")
        )


async def verify_review_evidence(
    payload: dict[str, Any],
    *,
    allowed_origin: str,
) -> dict[str, Any]:
    """Fetch and independently verify the finalized bundle, events and artifacts."""

    reject_secret_fields(payload)
    validate_review_urls(payload, allowed_origin=allowed_origin)
    expected_count = int(payload["event_count"])
    if expected_count > MAX_REVIEW_EVENTS:
        raise LoveEngineError(
            "review_event_limit_exceeded",
            f"{expected_count} > {MAX_REVIEW_EVENTS}",
        )
    try:
        async with ClientSession(timeout=ClientTimeout(total=30)) as session:
            bundle = await _json_response(
                session,
                payload["evidence_url"],
                error_code="review_evidence_unavailable",
            )
            validate_schema(bundle, "evidence-bundle-v2.schema.json")
            if bundle["bundle_hash"] != evidence_bundle_hash(bundle):
                raise LoveEngineError(
                    "bundle_hash_mismatch", payload["session_id"]
                )
            for field in (
                "bundle_hash",
                "session_id",
                "revision",
                "event_count",
                "head_event_hash",
            ):
                if str(bundle[field]).lower() != str(payload[field]).lower():
                    raise LoveEngineError(
                        "review_bundle_mismatch",
                        f"{field} does not match the signed task",
                    )

            events_value = await _json_response(
                session,
                payload["events_url"],
                error_code="review_events_unavailable",
            )
            if not isinstance(events_value, dict) or set(events_value) != {"events"}:
                raise LoveEngineError(
                    "review_events_invalid", "expected an events object"
                )
            events = events_value["events"]
            if not isinstance(events, list) or len(events) != expected_count:
                raise LoveEngineError(
                    "review_event_count_mismatch", payload["session_id"]
                )

            previous = ZERO_HASH
            references: list[dict[str, Any]] = []
            category_counts = {"source": 0, "summary": 0, "derived": 0}
            total_artifact_bytes = 0
            for expected_sequence, event in enumerate(events, start=1):
                validate_schema(event, "live-event-v1.schema.json")
                if event["session_id"] != payload["session_id"]:
                    raise LoveEngineError(
                        "wrong_session", event["session_id"]
                    )
                if (
                    int(event["sequence"]) != expected_sequence
                    or event["previous_event_hash"].lower() != previous.lower()
                ):
                    raise LoveEngineError(
                        "hash_chain_broken", event["event_id"]
                    )
                if not verify_live_event(event):
                    raise LoveEngineError(
                        "event_hash_mismatch", event["event_id"]
                    )
                async with session.get(
                    payload["artifact_base_url"].rstrip("/")
                    + "/"
                    + event["artifact_hash"],
                    allow_redirects=False,
                ) as artifact_response:
                    if artifact_response.status != 200:
                        raise LoveEngineError(
                            "artifact_missing", event["artifact_hash"]
                        )
                    artifact = await _read_limited(
                        artifact_response,
                        limit=MAX_ARTIFACT_BYTES,
                        error_code="artifact_too_large",
                    )
                total_artifact_bytes += len(artifact)
                if total_artifact_bytes > MAX_REVIEW_ARTIFACT_BYTES:
                    raise LoveEngineError(
                        "review_artifact_budget_exceeded",
                        str(MAX_REVIEW_ARTIFACT_BYTES),
                    )
                if (
                    sha256_prefixed(artifact) != event["artifact_hash"]
                    or artifact != event["content"].encode("utf-8")
                ):
                    raise LoveEngineError(
                        "artifact_hash_mismatch", event["artifact_hash"]
                    )
                references.append(
                    {
                        "event_id": event["event_id"],
                        "sequence": event["sequence"],
                        "category": event["category"],
                        "event_hash": event["event_hash"],
                        "artifact_hash": event["artifact_hash"],
                    }
                )
                category_counts[event["category"]] += 1
                previous = event["event_hash"]

            if previous.lower() != payload["head_event_hash"].lower():
                raise LoveEngineError(
                    "hash_chain_broken", payload["session_id"]
                )
            if bundle["events"] != references:
                raise LoveEngineError(
                    "review_bundle_event_mismatch", payload["session_id"]
                )
            expected_categories = {
                key: str(value) for key, value in category_counts.items()
            }
            if bundle["category_counts"] != expected_categories:
                raise LoveEngineError(
                    "review_category_count_mismatch", payload["session_id"]
                )
            return {
                "evidence_verified": True,
                "session_id": payload["session_id"],
                "revision": payload["revision"],
                "event_count": payload["event_count"],
                "head_event_hash": payload["head_event_hash"],
            }
    except (ClientError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
        raise LoveEngineError(
            "review_evidence_unavailable", type(exc).__name__, 4
        ) from exc
