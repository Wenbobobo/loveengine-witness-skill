"""Independent live-text observation and three-node attestation aggregation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientSession, ClientTimeout
from eth_utils import to_checksum_address

from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .hashes import keccak256_hex, sha256_prefixed
from .live_evidence import evidence_bundle_hash
from .live_protocol import verify_live_event
from .m4_network import verify_receipt_v2
from .schema import validate_schema
from .secrets import reject_secret_fields


MAX_SSE_RESPONSE_BYTES = 1024 * 1024
MAX_JSON_RESPONSE_BYTES = 1024 * 1024
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_OBSERVATION_EVENTS = 10_000
MAX_OBSERVATION_ARTIFACT_BYTES = 64 * 1024 * 1024


def _normalized_http_origin(value: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError) as exc:
        raise LoveEngineError("invalid_observation_url", str(value)) from exc
    return parsed.scheme, parsed.hostname.lower(), port


def validate_observation_urls(
    payload: dict[str, Any],
    *,
    allowed_origin: str,
) -> None:
    validate_schema(payload, "observe-live-text-payload-v1.schema.json")
    expected = _normalized_http_origin(allowed_origin)
    for field in ("stream_url", "session_url", "artifact_base_url"):
        if _normalized_http_origin(payload[field]) != expected:
            raise LoveEngineError(
                "observation_origin_mismatch",
                f"{field} must match the invite server origin",
            )


async def _read_limited(
    response: Any,
    *,
    limit: int,
    error_code: str,
) -> bytes:
    # StreamReader.read(n) may return an available buffer before EOF. Keep the
    # bounded read loop so a valid multi-buffer response is never parsed as a
    # truncated document.
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = await response.content.read(min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    body = b"".join(chunks)
    if len(body) > limit:
        raise LoveEngineError(error_code, f"response exceeds {limit} bytes")
    return body


def observation_timeout_seconds(payload: dict[str, Any]) -> float:
    validate_schema(payload, "observe-live-text-payload-v1.schema.json")
    return float(payload.get("max_duration_seconds", 30))


class ObservationCursorStore:
    """Durable per-node cursor with monotonic update enforcement."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as database:
            database.execute(
                """
                CREATE TABLE IF NOT EXISTS observation_cursors (
                    consumer TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    head_event_hash TEXT NOT NULL,
                    event_count INTEGER NOT NULL,
                    PRIMARY KEY (consumer, session_id)
                )
                """
            )

    def get(self, consumer: str, session_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.path) as database:
            row = database.execute(
                """
                SELECT sequence, head_event_hash, event_count
                FROM observation_cursors
                WHERE consumer = ? AND session_id = ?
                """,
                (consumer, session_id),
            ).fetchone()
        if row is None:
            return None
        return {"sequence": int(row[0]), "head_event_hash": row[1], "event_count": int(row[2])}

    def save(
        self,
        consumer: str,
        session_id: str,
        sequence: int,
        head_event_hash: str,
        event_count: int,
    ) -> None:
        current = self.get(consumer, session_id)
        if current is not None and sequence < current["sequence"]:
            raise LoveEngineError("cursor_rollback", f"{sequence} < {current['sequence']}")
        if (
            current is not None
            and sequence == current["sequence"]
            and head_event_hash != current["head_event_hash"]
        ):
            raise LoveEngineError("cursor_conflict", session_id)
        with sqlite3.connect(self.path) as database:
            database.execute(
                """
                INSERT INTO observation_cursors(
                    consumer, session_id, sequence, head_event_hash, event_count
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(consumer, session_id) DO UPDATE SET
                    sequence = excluded.sequence,
                    head_event_hash = excluded.head_event_hash,
                    event_count = excluded.event_count
                """,
                (consumer, session_id, sequence, head_event_hash, event_count),
            )


def _sse_events(body: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data = [line[6:] for line in block.splitlines() if line.startswith("data: ")]
        if data:
            values.append(json.loads("\n".join(data)))
    return values


async def observe_live_session(
    payload: dict[str, Any],
    cursors: ObservationCursorStore,
    consumer: str,
    *,
    poll_interval: float = 0.05,
    timeout_seconds: float | None = None,
    allowed_origin: str,
) -> dict[str, Any]:
    """Observe a live SSE feed, verify every artifact, and resume from disk."""

    reject_secret_fields(payload)
    validate_observation_urls(payload, allowed_origin=allowed_origin)
    configured_timeout = observation_timeout_seconds(payload)
    session_id = payload["session_id"]
    start = int(payload["start_cursor"])
    stored = cursors.get(consumer, session_id)
    recovered = stored is not None
    if stored is not None and stored["sequence"] < start:
        raise LoveEngineError("cursor_rollback", "stored cursor precedes task cursor")
    cursor = stored["sequence"] if stored else start
    head = stored["head_event_hash"] if stored else payload["initial_head_hash"].lower()
    event_count = stored["event_count"] if stored else start
    observed_from = cursor
    artifact_count = 0
    artifact_bytes = 0
    processed_events = 0
    deadline = asyncio.get_running_loop().time() + (
        configured_timeout if timeout_seconds is None else timeout_seconds
    )

    try:
        async with ClientSession(timeout=ClientTimeout(total=5)) as session:
            while True:
                if asyncio.get_running_loop().time() > deadline:
                    raise LoveEngineError("observation_timeout", session_id, 4)
                async with session.get(
                    payload["stream_url"],
                    params={"after": str(cursor)},
                    headers={"Last-Event-ID": str(cursor)},
                    allow_redirects=False,
                ) as response:
                    if response.status != 200:
                        raise LoveEngineError(
                            "live_stream_unavailable", str(response.status), 4
                        )
                    stream_body = await _read_limited(
                        response,
                        limit=MAX_SSE_RESPONSE_BYTES,
                        error_code="live_stream_too_large",
                    )
                    events = _sse_events(stream_body.decode("utf-8"))
                for event in events:
                    processed_events += 1
                    if processed_events > MAX_OBSERVATION_EVENTS:
                        raise LoveEngineError(
                            "observation_event_limit_exceeded",
                            str(MAX_OBSERVATION_EVENTS),
                        )
                    validate_schema(event, "live-event-v1.schema.json")
                    if event["session_id"] != session_id:
                        raise LoveEngineError("wrong_session", event["session_id"])
                    if int(event["sequence"]) != cursor + 1:
                        raise LoveEngineError("sequence_gap", event["sequence"])
                    if event["previous_event_hash"].lower() != head.lower():
                        raise LoveEngineError("hash_chain_broken", event["event_id"])
                    if not verify_live_event(event):
                        raise LoveEngineError("event_hash_mismatch", event["event_id"])
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
                        remaining_artifact_bytes = (
                            MAX_OBSERVATION_ARTIFACT_BYTES - artifact_bytes
                        )
                        if remaining_artifact_bytes <= 0:
                            raise LoveEngineError(
                                "observation_artifact_budget_exceeded",
                                str(MAX_OBSERVATION_ARTIFACT_BYTES),
                            )
                        artifact = await _read_limited(
                            artifact_response,
                            limit=min(
                                MAX_ARTIFACT_BYTES,
                                remaining_artifact_bytes,
                            ),
                            error_code=(
                                "artifact_too_large"
                                if remaining_artifact_bytes
                                >= MAX_ARTIFACT_BYTES
                                else "observation_artifact_budget_exceeded"
                            ),
                        )
                    artifact_bytes += len(artifact)
                    if (
                        sha256_prefixed(artifact) != event["artifact_hash"]
                        or artifact != event["content"].encode("utf-8")
                    ):
                        raise LoveEngineError(
                            "artifact_hash_mismatch", event["artifact_hash"]
                        )
                    cursor = int(event["sequence"])
                    event_count = cursor
                    artifact_count += 1
                    head = event["event_hash"]
                    cursors.save(consumer, session_id, cursor, head, event_count)

                async with session.get(
                    payload["session_url"], allow_redirects=False
                ) as response:
                    state = json.loads(
                        (
                            await _read_limited(
                                response,
                                limit=MAX_JSON_RESPONSE_BYTES,
                                error_code="session_response_too_large",
                            )
                        ).decode("utf-8")
                    )
                validate_schema(state, "live-session-v1.schema.json")
                if state["status"] == "closed":
                    if int(state["next_sequence"]) - 1 != cursor:
                        raise LoveEngineError("sequence_gap", state["next_sequence"])
                    if state["head_event_hash"].lower() != head.lower():
                        raise LoveEngineError("hash_chain_broken", session_id)
                    async with session.get(
                        payload["session_url"] + "/evidence",
                        allow_redirects=False,
                    ) as response:
                        if response.status in {400, 404, 409}:
                            await asyncio.sleep(poll_interval)
                            continue
                        if response.status != 200:
                            raise LoveEngineError(
                                "evidence_unavailable", str(response.status), 4
                            )
                        bundle = json.loads(
                            (
                                await _read_limited(
                                    response,
                                    limit=MAX_JSON_RESPONSE_BYTES,
                                    error_code="evidence_response_too_large",
                                )
                                ).decode("utf-8")
                            )
                    validate_schema(bundle, "evidence-bundle-v2.schema.json")
                    if bundle["bundle_hash"] != evidence_bundle_hash(bundle):
                        raise LoveEngineError("bundle_hash_mismatch", session_id)
                    if (
                        bundle["head_event_hash"].lower() != head.lower()
                        or int(bundle["event_count"]) != event_count
                    ):
                        raise LoveEngineError("observation_bundle_mismatch", session_id)
                    result = {
                        "schema_version": "loveengine.live-observation-receipt/1",
                        "session_id": session_id,
                        "observed_from": str(observed_from),
                        "last_sequence": str(cursor),
                        "event_count": str(event_count),
                        "head_event_hash": head,
                        "bundle_hash": bundle["bundle_hash"],
                        "artifact_count": str(
                            event_count if recovered and artifact_count == 0 else artifact_count
                        ),
                        "recovered_from_cursor": recovered,
                    }
                    validate_schema(result, "live-observation-receipt-v1.schema.json")
                    return result
                await asyncio.sleep(poll_interval)
    except (ClientError, TimeoutError, OSError, UnicodeError, ValueError) as exc:
        raise LoveEngineError("live_stream_unavailable", str(exc), 4) from exc


def observation_set_hash(value: dict[str, Any]) -> str:
    view = dict(value)
    view.pop("observation_set_hash", None)
    return keccak256_hex(canonical_json_bytes(view))


def aggregate_observations(
    receipts: list[dict[str, Any]],
    *,
    expected_nodes: set[str],
    expected_chain_id: str,
    expected_registry: str,
) -> dict[str, Any]:
    if len(receipts) != 3:
        raise LoveEngineError("observation_quorum_missing", "three receipts required")
    nodes: set[str] = set()
    canonical_result: dict[str, Any] | None = None
    for receipt in receipts:
        node = verify_receipt_v2(receipt, expected_chain_id, expected_registry)
        node = to_checksum_address(node)
        if node not in {to_checksum_address(value) for value in expected_nodes}:
            raise LoveEngineError("unexpected_observer", node)
        if node in nodes:
            raise LoveEngineError("duplicate_observer", node)
        nodes.add(node)
        if receipt.get("status") != "completed":
            result = receipt.get("result")
            error_code = (
                result.get("error_code", "unknown")
                if isinstance(result, dict)
                else "unknown"
            )
            raise LoveEngineError(
                "observation_receipt_rejected",
                f"{receipt.get('task_id', 'unknown')}:{error_code}",
                4,
            )
        validate_schema(receipt["result"], "live-observation-receipt-v1.schema.json")
        comparable = {
            key: receipt["result"][key]
            for key in (
                "session_id",
                "last_sequence",
                "event_count",
                "head_event_hash",
                "bundle_hash",
            )
        }
        if canonical_result is None:
            canonical_result = comparable
        elif comparable != canonical_result:
            raise LoveEngineError("observation_mismatch", receipt["task_id"])
    assert canonical_result is not None
    value = {
        "schema_version": "loveengine.observation-set/1",
        "session_id": canonical_result["session_id"],
        "event_count": canonical_result["event_count"],
        "head_event_hash": canonical_result["head_event_hash"],
        "bundle_hash": canonical_result["bundle_hash"],
        "node_count": "3",
        "receipts": sorted(receipts, key=lambda item: item["node"].lower()),
    }
    value["observation_set_hash"] = observation_set_hash(value)
    validate_schema(value, "observation-set-v1.schema.json")
    return value
