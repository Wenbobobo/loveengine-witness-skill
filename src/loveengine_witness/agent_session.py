"""One outbound Agent session engine with separate V1 and V2 codecs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import time
from typing import Any, Awaitable, Callable

from aiohttp import ClientSession
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .hashes import keccak256_hex
from .m4_network import build_receipt_v2
from .m4_typed_data import build_receipt_v2_typed_data
from .network_protocol import build_receipt
from .network_typed_data import build_receipt_typed_data
from .observation import ObservationCursorStore, observe_live_session


TaskOperation = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


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


def _review_result(task: dict[str, Any], verdicts: dict[str, str]) -> dict[str, Any]:
    dispute_id = task["payload"]["dispute_id"]
    try:
        verdict = verdicts[dispute_id]
    except KeyError as exc:
        raise LoveEngineError("review_verdict_required", dispute_id) from exc
    if verdict not in {"dismiss", "uphold", "unable_to_determine"}:
        raise LoveEngineError("invalid_verdict", verdict)
    return {
        "dispute_id": dispute_id,
        "bundle_hash": task["payload"]["bundle_hash"],
        "verdict": verdict,
        "reason_hash": keccak256_hex(f"{dispute_id}:{verdict}".encode()),
    }


def _build_signed_receipt(
    task: dict[str, Any],
    node: str,
    result: dict[str, Any],
    sign_typed_data: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    if task["schema_version"] == "loveengine.network-task/1":
        receipt = build_receipt(
            chain_id=task["chain_id"],
            registry=task["registry"],
            task_id=task["task_id"],
            node=node,
            status="completed",
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
        status="completed",
        result=result,
        nonce=task["nonce"],
        completed_at=str(min(int(time()), int(task["deadline"]) - 1)),
    )
    receipt["signature"] = sign_typed_data(build_receipt_v2_typed_data(receipt))
    return receipt


async def run_agent_session(
    *,
    url: str,
    node_address: str,
    signed_profile: dict[str, Any],
    expected_tasks: int,
    expected_issuer: str | None,
    allowed_issuers: list[str] | tuple[str, ...] | None = None,
    expected_manifest_hash: str,
    sign_challenge: Callable[[str], str],
    sign_typed_data: Callable[[dict[str, Any]], str],
    cursor_database: Path | None = None,
    verdicts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Authenticate once, then execute any supported task codec on one session."""

    # Local import avoids coupling the protocol-free session engine to RelayHub.
    from .relay_server import verify_task_for_node

    node = to_checksum_address(node_address)
    pending: list[dict[str, Any]] = []
    completed: set[str] = set()
    receipts: list[dict[str, Any]] = []
    rejected = 0
    cursors = ObservationCursorStore(cursor_database) if cursor_database else None
    async with ClientSession() as session:
        async with session.ws_connect(url) as ws:
            challenge = await ws.receive_json()
            await ws.send_json(
                {
                    "type": "authenticate",
                    "profile": signed_profile,
                    "challenge_signature": sign_challenge(challenge["challenge"]),
                }
            )
            authenticated = await ws.receive_json()
            if authenticated.get("status") != "authenticated":
                raise LoveEngineError("relay_authentication_failed", json.dumps(authenticated))
            while len(receipts) < expected_tasks:
                message = pending.pop(0) if pending else await ws.receive_json(timeout=30)
                if message.get("type") != "task":
                    continue
                task = message["task"]
                if task["task_id"] in completed:
                    rejected += 1
                    continue
                verify_task_for_node(
                    task,
                    signed_profile,
                    expected_issuer=expected_issuer,
                    allowed_issuers=allowed_issuers,
                    expected_manifest_hash=expected_manifest_hash,
                )
                await ws.send_json(
                    {"type": "ack", "task_id": task["task_id"], "status": "accepted"}
                )
                completed.add(task["task_id"])
                if task["schema_version"] == "loveengine.network-task/1":
                    result = _v1_result(task)
                elif task["task_type"] == "observe_live_text":
                    if cursors is None:
                        raise LoveEngineError("observation_cursor_required", task["task_id"])
                    result = await run_with_keepalive(
                        ws,
                        observe_live_session(task["payload"], cursors, node),
                        pending,
                    )
                elif task["task_type"] == "review_dispute":
                    result = _review_result(task, verdicts or {})
                else:
                    raise LoveEngineError("unsupported_task_type", task["task_type"])
                receipt = _build_signed_receipt(task, node, result, sign_typed_data)
                await ws.send_json({"type": "receipt", "receipt": receipt})
                while True:
                    response = await ws.receive_json(timeout=10)
                    if (
                        response.get("type") == "ack"
                        and response.get("task_id") == receipt["task_id"]
                    ):
                        break
                    if response.get("type") == "task":
                        pending.append(response)
                        continue
                    if response.get("type") == "heartbeat":
                        continue
                    raise LoveEngineError(
                        "relay_receipt_not_acknowledged",
                        json.dumps(response, sort_keys=True),
                    )
                receipts.append(receipt)
    return {"node": node, "receipts": receipts, "rejected": rejected}
