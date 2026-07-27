"""One outbound Agent session engine with separate V1 and V2 codecs."""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from pathlib import Path
from time import time
from typing import Any, Awaitable, Callable

from aiohttp import ClientError, ClientSession
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .hashes import keccak256_hex
from .m4_network import build_receipt_v2
from .m4_typed_data import build_receipt_v2_typed_data
from .network_protocol import build_receipt
from .network_typed_data import build_receipt_typed_data
from .observation import ObservationCursorStore, observe_live_session
from .review_evidence import verify_review_evidence


TaskOperation = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
RELAY_CHALLENGE_SCHEMA = "loveengine.relay-challenge/1"
_RELAY_CHALLENGE_KEYS = {
    "type",
    "schema_version",
    "chain_id",
    "registry",
    "nonce",
}
_RELAY_NONCE = re.compile(r"^[0-9a-f]{64}$")


class AgentTaskJournal:
    """Durable task/nonce replay protection and receipt resend state."""

    def __init__(self, path: Path | None) -> None:
        self.path = Path(path) if path is not None else None
        self._tasks: dict[str, tuple[str, dict[str, Any] | None]] = {}
        self._nonces: dict[tuple[str, str], str] = {}
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.path) as database:
                database.execute(
                    """
                    CREATE TABLE IF NOT EXISTS agent_task_journal (
                        task_id TEXT PRIMARY KEY,
                        issuer TEXT NOT NULL,
                        nonce TEXT NOT NULL,
                        task_json TEXT NOT NULL,
                        receipt_json TEXT,
                        acked INTEGER NOT NULL DEFAULT 0,
                        UNIQUE (issuer, nonce)
                    )
                    """
                )

    @staticmethod
    def _serialized(task: dict[str, Any]) -> str:
        return json.dumps(
            task,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @staticmethod
    def _nonce_key(task: dict[str, Any]) -> tuple[str, str]:
        return to_checksum_address(task["issuer"]).lower(), str(task["nonce"])

    def claim(self, task: dict[str, Any]) -> dict[str, Any] | None:
        serialized = self._serialized(task)
        task_id = str(task["task_id"])
        nonce_key = self._nonce_key(task)
        if self.path is None:
            existing = self._tasks.get(task_id)
            if existing is not None:
                if existing[0] != serialized:
                    raise LoveEngineError(
                        "task_replay_conflict",
                        "task ID was reused with different signed content",
                    )
                return existing[1]
            existing_task_id = self._nonces.get(nonce_key)
            if existing_task_id is not None:
                raise LoveEngineError(
                    "task_nonce_replay",
                    f"issuer nonce already belongs to {existing_task_id}",
                )
            self._tasks[task_id] = (serialized, None)
            self._nonces[nonce_key] = task_id
            return None
        with sqlite3.connect(self.path) as database:
            existing = database.execute(
                "SELECT task_json, receipt_json FROM agent_task_journal WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != serialized:
                    raise LoveEngineError(
                        "task_replay_conflict",
                        "task ID was reused with different signed content",
                    )
                return json.loads(existing[1]) if existing[1] else None
            nonce_owner = database.execute(
                """
                SELECT task_id FROM agent_task_journal
                WHERE issuer = ? AND nonce = ?
                """,
                nonce_key,
            ).fetchone()
            if nonce_owner is not None:
                raise LoveEngineError(
                    "task_nonce_replay",
                    f"issuer nonce already belongs to {nonce_owner[0]}",
                )
            database.execute(
                """
                INSERT INTO agent_task_journal(task_id, issuer, nonce, task_json)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, *nonce_key, serialized),
            )
        return None

    def save_receipt(self, task_id: str, receipt: dict[str, Any]) -> None:
        if self.path is None:
            serialized, _ = self._tasks[task_id]
            self._tasks[task_id] = (serialized, receipt)
            return
        with sqlite3.connect(self.path) as database:
            cursor = database.execute(
                """
                UPDATE agent_task_journal SET receipt_json = ?
                WHERE task_id = ?
                """,
                (self._serialized(receipt), task_id),
            )
            if cursor.rowcount != 1:
                raise LoveEngineError("task_journal_missing", task_id)

    def mark_acked(self, task_id: str) -> None:
        if self.path is None:
            return
        with sqlite3.connect(self.path) as database:
            database.execute(
                "UPDATE agent_task_journal SET acked = 1 WHERE task_id = ?",
                (task_id,),
            )

    def confirm_stored_receipt(self, receipt: dict[str, Any]) -> None:
        task_id = str(receipt.get("task_id", ""))
        serialized_receipt = self._serialized(receipt)
        if self.path is None:
            existing = self._tasks.get(task_id)
            if (
                existing is None
                or existing[1] is None
                or self._serialized(existing[1]) != serialized_receipt
            ):
                raise LoveEngineError(
                    "receipt_state_conflict",
                    "Relay receipt state does not match the local task journal",
                )
            return
        with sqlite3.connect(self.path) as database:
            existing = database.execute(
                """
                SELECT receipt_json FROM agent_task_journal
                WHERE task_id = ?
                """,
                (task_id,),
            ).fetchone()
            if (
                existing is None
                or existing[0] is None
                or str(existing[0]) != serialized_receipt
            ):
                raise LoveEngineError(
                    "receipt_state_conflict",
                    "Relay receipt state does not match the local task journal",
                )
            database.execute(
                """
                UPDATE agent_task_journal SET acked = 1
                WHERE task_id = ?
                """,
                (task_id,),
            )


def build_relay_challenge(
    *, chain_id: str, registry: str, nonce: str
) -> dict[str, str]:
    value = {
        "type": "challenge",
        "schema_version": RELAY_CHALLENGE_SCHEMA,
        "chain_id": str(chain_id),
        "registry": to_checksum_address(registry),
        "nonce": nonce,
    }
    verify_relay_challenge(
        value,
        expected_chain_id=chain_id,
        expected_registry=registry,
    )
    return value


def verify_relay_challenge(
    value: dict[str, Any],
    *,
    expected_chain_id: str,
    expected_registry: str,
) -> None:
    """Reject arbitrary signer prompts before a Relay can reach the RPC signer."""

    if not isinstance(value, dict) or set(value) != _RELAY_CHALLENGE_KEYS:
        raise LoveEngineError(
            "invalid_relay_challenge",
            "Relay challenge must use the fixed authentication schema",
        )
    if (
        value.get("type") != "challenge"
        or value.get("schema_version") != RELAY_CHALLENGE_SCHEMA
        or value.get("chain_id") != str(expected_chain_id)
    ):
        raise LoveEngineError(
            "invalid_relay_challenge",
            "Relay challenge domain does not match the signed node profile",
        )
    try:
        registry = to_checksum_address(value.get("registry"))
        expected = to_checksum_address(expected_registry)
    except (TypeError, ValueError) as exc:
        raise LoveEngineError(
            "invalid_relay_challenge", "Relay challenge Registry is invalid"
        ) from exc
    if registry != expected or not _RELAY_NONCE.fullmatch(str(value.get("nonce", ""))):
        raise LoveEngineError(
            "invalid_relay_challenge",
            "Relay challenge Registry or nonce is invalid",
        )


def relay_challenge_signing_text(
    value: dict[str, Any],
    *,
    node: str,
) -> str:
    verify_relay_challenge(
        value,
        expected_chain_id=value.get("chain_id"),
        expected_registry=value.get("registry"),
    )
    return "\n".join(
        (
            "LoveEngine Relay Authentication",
            f"schema={RELAY_CHALLENGE_SCHEMA}",
            f"chain_id={value['chain_id']}",
            f"registry={to_checksum_address(value['registry'])}",
            f"node={to_checksum_address(node)}",
            f"nonce={value['nonce']}",
        )
    )


async def run_with_keepalive(
    ws: Any,
    operation: Awaitable[dict[str, Any]],
    pending: list[dict[str, Any]],
    *,
    interval: float = 1.0,
) -> dict[str, Any]:
    task = asyncio.create_task(operation)
    try:
        while not task.done():
            try:
                message = await ws.receive_json(timeout=interval)
            except TimeoutError:
                await ws.send_json({"type": "heartbeat"})
                continue
            if message.get("type") == "task":
                pending.append(message)
        return await task
    finally:
        if not task.done():
            task.cancel()


def _v1_result(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "accepted_task_type": task["task_type"],
        "payload_hash": task["payload_hash"],
    }


async def _review_result(
    task: dict[str, Any],
    verdicts: dict[str, str],
    *,
    allowed_origin: str,
) -> dict[str, Any]:
    dispute_id = task["payload"]["dispute_id"]
    try:
        verdict = verdicts[dispute_id]
    except KeyError as exc:
        raise LoveEngineError("review_verdict_required", dispute_id) from exc
    if verdict not in {"dismiss", "uphold", "unable_to_determine"}:
        raise LoveEngineError("invalid_verdict", verdict)
    verified = await verify_review_evidence(
        task["payload"],
        allowed_origin=allowed_origin,
    )
    return {
        "dispute_id": dispute_id,
        "bundle_hash": task["payload"]["bundle_hash"],
        "verdict": verdict,
        "reason_hash": keccak256_hex(f"{dispute_id}:{verdict}".encode()),
        **verified,
    }


def _build_signed_receipt(
    task: dict[str, Any],
    node: str,
    result: dict[str, Any],
    sign_typed_data: Callable[[dict[str, Any]], str],
    *,
    status: str = "completed",
) -> dict[str, Any]:
    if task["schema_version"] == "loveengine.network-task/1":
        receipt = build_receipt(
            chain_id=task["chain_id"],
            registry=task["registry"],
            task_id=task["task_id"],
            node=node,
            status=status,
            result=result,
            nonce=task["nonce"],
            completed_at=str(int(task["deadline"]) - 1),
        )
        receipt["signature"] = sign_typed_data(build_receipt_typed_data(receipt))
        return receipt
    receipt = build_receipt_v2(
        chain_id=task["chain_id"],
        registry=task["registry"],
        task_id=task["task_id"],
        node=node,
        status=status,
        result=result,
        nonce=task["nonce"],
        completed_at=str(min(int(time()), int(task["deadline"]) - 1)),
    )
    receipt["signature"] = sign_typed_data(build_receipt_v2_typed_data(receipt))
    return receipt


def _record_receipt(
    receipts: list[dict[str, Any]],
    receipt: dict[str, Any],
) -> int:
    for existing in receipts:
        if existing["task_id"] != receipt["task_id"]:
            continue
        if AgentTaskJournal._serialized(existing) != AgentTaskJournal._serialized(
            receipt
        ):
            raise LoveEngineError(
                "receipt_state_conflict",
                "the same task has conflicting signed receipts",
            )
        return 0
    receipts.append(receipt)
    return int(receipt["status"] == "rejected")


async def _confirm_relay_receipt(
    ws: Any,
    *,
    receipt: dict[str, Any],
    pending: list[dict[str, Any]],
    idle_timeout_seconds: float,
) -> None:
    await ws.send_json(
        {
            "type": "receipt_ack",
            "task_id": receipt["task_id"],
        }
    )
    while True:
        response = await ws.receive_json(timeout=idle_timeout_seconds)
        if (
            response.get("type") == "ack"
            and response.get("status") == "receipt_confirmed"
            and response.get("task_id") == receipt["task_id"]
        ):
            return
        if response.get("type") in {"task", "receipt_state"}:
            pending.append(response)
            continue
        if response.get("type") == "heartbeat":
            continue
        raise LoveEngineError(
            "relay_receipt_confirmation_failed",
            json.dumps(response, sort_keys=True),
        )


async def _run_authenticated_session(
    *,
    url: str,
    node: str,
    signed_profile: dict[str, Any],
    expected_tasks: int,
    expected_issuer: str | None,
    allowed_issuers: list[str] | tuple[str, ...] | None,
    expected_manifest_hash: str,
    sign_challenge: Callable[[dict[str, Any]], str],
    sign_typed_data: Callable[[dict[str, Any]], str],
    cursors: ObservationCursorStore | None,
    journal: AgentTaskJournal,
    verdicts: dict[str, str] | None,
    allowed_http_origin: str | None,
    receipts: list[dict[str, Any]],
    idle_timeout_seconds: float,
) -> int:
    from .relay_server import verify_task_for_node

    pending: list[dict[str, Any]] = []
    rejected = 0
    async with ClientSession() as session:
        async with session.ws_connect(url) as ws:
            challenge = await ws.receive_json(timeout=idle_timeout_seconds)
            verify_relay_challenge(
                challenge,
                expected_chain_id=signed_profile["chain_id"],
                expected_registry=signed_profile["registry"],
            )
            await ws.send_json(
                {
                    "type": "authenticate",
                    "profile": signed_profile,
                    "challenge_signature": sign_challenge(challenge),
                }
            )
            authenticated = await ws.receive_json(
                timeout=idle_timeout_seconds
            )
            if authenticated.get("status") != "authenticated":
                raise LoveEngineError("relay_authentication_failed", json.dumps(authenticated))
            receipt_state_count = authenticated.get("receipt_state_count", 0)
            if (
                isinstance(receipt_state_count, bool)
                or not isinstance(receipt_state_count, int)
                or receipt_state_count < 0
                or receipt_state_count > 1_000
            ):
                raise LoveEngineError(
                    "invalid_receipt_state_count",
                    str(receipt_state_count),
                )
            for _ in range(receipt_state_count):
                message = (
                    pending.pop(0)
                    if pending
                    else await ws.receive_json(timeout=idle_timeout_seconds)
                )
                if message.get("type") != "receipt_state":
                    raise LoveEngineError(
                        "invalid_receipt_state",
                        "Relay did not send the declared receipt state",
                    )
                receipt = message.get("receipt")
                if (
                    not isinstance(receipt, dict)
                    or receipt.get("task_id") != message.get("task_id")
                ):
                    raise LoveEngineError(
                        "invalid_receipt_state",
                        "Relay receipt state is malformed",
                    )
                journal.confirm_stored_receipt(receipt)
                rejected += _record_receipt(receipts, receipt)
                await _confirm_relay_receipt(
                    ws,
                    receipt=receipt,
                    pending=pending,
                    idle_timeout_seconds=idle_timeout_seconds,
                )
            while len(receipts) < expected_tasks:
                message = (
                    pending.pop(0)
                    if pending
                    else await ws.receive_json(timeout=idle_timeout_seconds)
                )
                if message.get("type") == "receipt_state":
                    receipt = message.get("receipt")
                    if (
                        not isinstance(receipt, dict)
                        or receipt.get("task_id") != message.get("task_id")
                    ):
                        raise LoveEngineError(
                            "invalid_receipt_state",
                            "Relay receipt state is malformed",
                        )
                    journal.confirm_stored_receipt(receipt)
                    rejected += _record_receipt(receipts, receipt)
                    await _confirm_relay_receipt(
                        ws,
                        receipt=receipt,
                        pending=pending,
                        idle_timeout_seconds=idle_timeout_seconds,
                    )
                    continue
                if message.get("type") != "task":
                    continue
                task = message["task"]
                verify_task_for_node(
                    task,
                    signed_profile,
                    expected_issuer=expected_issuer,
                    allowed_issuers=allowed_issuers,
                    expected_manifest_hash=expected_manifest_hash,
                )
                stored_receipt = journal.claim(task)
                await ws.send_json(
                    {"type": "ack", "task_id": task["task_id"], "status": "accepted"}
                )
                if stored_receipt is not None:
                    receipt = stored_receipt
                else:
                    try:
                        if task["schema_version"] == "loveengine.network-task/1":
                            result = _v1_result(task)
                        elif task["task_type"] == "observe_live_text":
                            if cursors is None:
                                raise LoveEngineError(
                                    "observation_cursor_required",
                                    task["task_id"],
                                )
                            if allowed_http_origin is None:
                                raise LoveEngineError(
                                    "observation_origin_required",
                                    task["task_id"],
                                )
                            result = await run_with_keepalive(
                                ws,
                                observe_live_session(
                                    task["payload"],
                                    cursors,
                                    node,
                                    allowed_origin=allowed_http_origin,
                                ),
                                pending,
                            )
                        elif task["task_type"] == "review_dispute":
                            if allowed_http_origin is None:
                                raise LoveEngineError(
                                    "review_origin_required",
                                    task["task_id"],
                                )
                            result = await run_with_keepalive(
                                ws,
                                _review_result(
                                    task,
                                    verdicts or {},
                                    allowed_origin=allowed_http_origin,
                                ),
                                pending,
                            )
                        else:
                            raise LoveEngineError(
                                "unsupported_task_type", task["task_type"]
                            )
                        receipt = _build_signed_receipt(
                            task, node, result, sign_typed_data
                        )
                    except LoveEngineError as exc:
                        receipt = _build_signed_receipt(
                            task,
                            node,
                            {
                                "error_code": exc.code,
                                "task_type": task["task_type"],
                            },
                            sign_typed_data,
                            status="rejected",
                        )
                journal.save_receipt(task["task_id"], receipt)
                await ws.send_json({"type": "receipt", "receipt": receipt})
                while True:
                    response = await ws.receive_json(
                        timeout=idle_timeout_seconds
                    )
                    if (
                        response.get("type") == "ack"
                        and response.get("task_id") == receipt["task_id"]
                    ):
                        journal.mark_acked(task["task_id"])
                        rejected += _record_receipt(receipts, receipt)
                        await _confirm_relay_receipt(
                            ws,
                            receipt=receipt,
                            pending=pending,
                            idle_timeout_seconds=idle_timeout_seconds,
                        )
                        break
                    if response.get("type") == "receipt_state":
                        stored = response.get("receipt")
                        if not isinstance(stored, dict):
                            raise LoveEngineError(
                                "invalid_receipt_state",
                                "Relay receipt state is malformed",
                            )
                        journal.confirm_stored_receipt(stored)
                        rejected += _record_receipt(receipts, stored)
                        await _confirm_relay_receipt(
                            ws,
                            receipt=stored,
                            pending=pending,
                            idle_timeout_seconds=idle_timeout_seconds,
                        )
                        continue
                    if response.get("type") == "task":
                        pending.append(response)
                        continue
                    if response.get("type") == "heartbeat":
                        continue
                    raise LoveEngineError(
                        "relay_receipt_not_acknowledged",
                        json.dumps(response, sort_keys=True),
                    )
    return rejected


async def run_agent_session(
    *,
    url: str,
    node_address: str,
    signed_profile: dict[str, Any],
    expected_tasks: int,
    expected_issuer: str | None,
    allowed_issuers: list[str] | tuple[str, ...] | None = None,
    expected_manifest_hash: str,
    sign_challenge: Callable[[dict[str, Any]], str],
    sign_typed_data: Callable[[dict[str, Any]], str],
    cursor_database: Path | None = None,
    verdicts: dict[str, str] | None = None,
    allowed_http_origin: str | None = None,
    reconnect_attempts: int = 0,
    idle_timeout_seconds: float = 30,
    before_connect: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Execute a bounded session and resume signed receipts after reconnect."""

    if reconnect_attempts < 0 or reconnect_attempts > 10:
        raise LoveEngineError(
            "invalid_reconnect_attempts", str(reconnect_attempts)
        )
    if idle_timeout_seconds < 1 or idle_timeout_seconds > 900:
        raise LoveEngineError(
            "invalid_idle_timeout", str(idle_timeout_seconds)
        )
    node = to_checksum_address(node_address)
    receipts: list[dict[str, Any]] = []
    rejected = 0
    cursors = ObservationCursorStore(cursor_database) if cursor_database else None
    journal = AgentTaskJournal(cursor_database)
    connection_attempts = 0
    while True:
        connection_attempts += 1
        try:
            if before_connect is not None:
                before_connect()
            rejected += await _run_authenticated_session(
                url=url,
                node=node,
                signed_profile=signed_profile,
                expected_tasks=expected_tasks,
                expected_issuer=expected_issuer,
                allowed_issuers=allowed_issuers,
                expected_manifest_hash=expected_manifest_hash,
                sign_challenge=sign_challenge,
                sign_typed_data=sign_typed_data,
                cursors=cursors,
                journal=journal,
                verdicts=verdicts,
                allowed_http_origin=allowed_http_origin,
                receipts=receipts,
                idle_timeout_seconds=idle_timeout_seconds,
            )
            break
        except (ClientError, TimeoutError, OSError) as exc:
            if connection_attempts > reconnect_attempts:
                raise LoveEngineError(
                    "relay_session_unavailable", type(exc).__name__, 4
                ) from exc
        except LoveEngineError as exc:
            if (
                exc.code not in {"rpc_unavailable", "registry_query_failed"}
                or connection_attempts > reconnect_attempts
            ):
                raise
        await asyncio.sleep(min(0.25 * connection_attempts, 1.0))
    return {
        "node": node,
        "receipts": receipts,
        "rejected": rejected,
        "connection_attempts": connection_attempts,
        "reconnects": connection_attempts - 1,
    }
