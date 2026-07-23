"""Independent live-text observation and three-node attestation aggregation."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

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
) -> dict[str, Any]:
    """Observe a live SSE feed, verify every artifact, and resume from disk."""

    reject_secret_fields(payload)
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
                ) as response:
                    if response.status != 200:
                        raise LoveEngineError(
                            "live_stream_unavailable", str(response.status), 4
                        )
                    events = _sse_events(await response.text())
                for event in events:
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
                        + event["artifact_hash"]
                    ) as artifact_response:
                        if artifact_response.status != 200:
                            raise LoveEngineError(
                                "artifact_missing", event["artifact_hash"]
                            )
                        artifact = await artifact_response.read()
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

                async with session.get(payload["session_url"]) as response:
                    state = await response.json()
                if state["status"] == "closed":
                    if int(state["next_sequence"]) - 1 != cursor:
                        raise LoveEngineError("sequence_gap", state["next_sequence"])
                    if state["head_event_hash"].lower() != head.lower():
                        raise LoveEngineError("hash_chain_broken", session_id)
                    async with session.get(payload["session_url"] + "/evidence") as response:
                        if response.status in {400, 404, 409}:
                            await asyncio.sleep(poll_interval)
                            continue
                        if response.status != 200:
                            raise LoveEngineError(
                                "evidence_unavailable", str(response.status), 4
                            )
                        bundle = await response.json()
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
    except (ClientError, TimeoutError, OSError) as exc:
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
