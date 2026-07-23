"""HTTP/WebSocket Relay Hub for the local M3 pilot."""

from __future__ import annotations

import json
import asyncio
import secrets
import math
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, Callable, TypeVar

from aiohttp import WSMsgType, web
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import to_checksum_address

from .agent_session import run_agent_session, run_with_keepalive
from .canonical import canonical_json_bytes
from .errors import LoveEngineError
from .network_protocol import (
    verify_node_profile,
    verify_receipt,
    verify_task,
)
from .m4_network import (
    verify_node_profile_v2,
    verify_receipt_v2,
    verify_task_v2,
)
from .relay import RelayStore


T = TypeVar("T")


async def _run_with_relay_keepalive(
    ws: Any,
    operation: Awaitable[T],
    pending_messages: list[dict[str, Any]],
    *,
    interval: float = 3.0,
) -> T:
    """Compatibility wrapper around the shared session keepalive."""

    return await run_with_keepalive(
        ws, operation, pending_messages, interval=interval
    )


def parse_client_message(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LoveEngineError(
            "malformed_message",
            "WebSocket message must be valid JSON",
        ) from exc
    if not isinstance(parsed, dict):
        raise LoveEngineError(
            "malformed_message",
            "WebSocket message must be a JSON object",
        )
    return parsed


def verify_relay_profile_binding(
    signed_profile: dict[str, Any],
    bootstrap: dict[str, Any],
) -> None:
    if signed_profile.get("chain_id") != bootstrap.get("chain_id"):
        raise LoveEngineError("wrong_chain_id", "profile chainId mismatch")
    if to_checksum_address(signed_profile["registry"]) != to_checksum_address(
        bootstrap["registry"]
    ):
        raise LoveEngineError("wrong_registry", "profile Registry mismatch")
    encoded = canonical_json_bytes(signed_profile)
    if not any(
        canonical_json_bytes(member) == encoded
        for member in bootstrap.get("directory", [])
    ):
        raise LoveEngineError(
            "profile_not_in_directory",
            "profile is not a member of the signed bootstrap directory",
        )


def verify_task_for_node(
    task: dict[str, Any],
    signed_profile: dict[str, Any],
    *,
    expected_issuer: str | None,
    allowed_issuers: list[str] | tuple[str, ...] | None = None,
    expected_manifest_hash: str,
) -> str:
    schema_version = task.get("schema_version")
    verifier = {
        "loveengine.network-task/1": verify_task,
        "loveengine.network-task/2": verify_task_v2,
    }.get(schema_version)
    if verifier is None:
        raise LoveEngineError(
            "unsupported_schema_version", str(schema_version)
        )
    expected_task_schema = {
        "loveengine.signed-agent-node-profile/1": "loveengine.network-task/1",
        "loveengine.signed-agent-node-profile/2": "loveengine.network-task/2",
    }.get(signed_profile.get("schema_version"))
    if schema_version != expected_task_schema:
        raise LoveEngineError(
            "task_profile_schema_mismatch",
            "task codec does not match the authenticated profile codec",
        )
    signer = verifier(
        task,
        expected_chain_id=signed_profile["chain_id"],
        expected_registry=signed_profile["registry"],
        expected_recipient=signed_profile["profile"]["node"],
        expected_issuer=expected_issuer,
        expected_manifest_hash=expected_manifest_hash,
    )
    if allowed_issuers is not None and signer.lower() not in {
        to_checksum_address(issuer).lower() for issuer in allowed_issuers
    }:
        raise LoveEngineError("wrong_issuer", "task issuer is not allowed by policy")
    if task["task_type"] not in signed_profile["profile"]["capabilities"]:
        raise LoveEngineError(
            "capability_not_declared",
            f"node did not declare {task['task_type']}",
        )
    return signer


def verify_profile(signed_profile: dict[str, Any]) -> str:
    schema_version = signed_profile.get("schema_version")
    if schema_version == "loveengine.signed-agent-node-profile/2":
        return verify_node_profile_v2(signed_profile)
    if schema_version == "loveengine.signed-agent-node-profile/1":
        return verify_node_profile(signed_profile)
    raise LoveEngineError("unsupported_schema_version", str(schema_version))


def verify_relay_receipt(
    receipt: dict[str, Any],
    chain_id: str,
    registry: str,
    *,
    expected_node: str | None = None,
) -> str:
    schema_version = receipt.get("schema_version")
    if schema_version == "loveengine.task-receipt/2":
        signer = verify_receipt_v2(receipt, chain_id, registry)
    elif schema_version == "loveengine.task-receipt/1":
        signer = verify_receipt(receipt, chain_id, registry)
    else:
        raise LoveEngineError(
            "unsupported_schema_version", str(schema_version)
        )
    if expected_node is not None and signer != to_checksum_address(expected_node):
        raise LoveEngineError(
            "wrong_receipt_node",
            "receipt signer does not match the authenticated node",
        )
    return signer


def _signature_hex(value: bytes) -> str:
    result = value.hex()
    return result if result.startswith("0x") else "0x" + result


class RelayHub:
    def __init__(
        self,
        store: RelayStore,
        bootstrap: dict[str, Any],
        releases: dict[str, dict[str, Any]],
        artifacts: dict[str, bytes],
    ) -> None:
        self.store = store
        self.bootstrap = bootstrap
        self.releases = releases
        self.artifacts = artifacts
        self.connected: set[str] = set()
        self.receipts: list[dict[str, Any]] = []
        self.rejected = 0
        self.acceptance_latencies_ms: list[float] = []
        self.completion_latencies_ms: list[float] = []
        self.runner: web.AppRunner | None = None

    def app(self) -> web.Application:
        app = web.Application()
        app.add_routes(
            [
                web.get("/v1/health", self.health),
                web.get("/v1/bootstrap", self.get_bootstrap),
                web.get(
                    "/v1/releases/{publisher}/{skill_id}/{version}",
                    self.get_release,
                ),
                web.get("/v1/artifacts/{package_hash}", self.get_artifact),
                web.get("/v1/ws", self.websocket),
            ]
        )
        return app

    async def start(self, host: str, port: int) -> None:
        self.runner = web.AppRunner(self.app())
        await self.runner.setup()
        await web.TCPSite(self.runner, host, port).start()

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "connected": len(self.connected),
                **self.store.metrics(),
            }
        )

    async def get_bootstrap(self, request: web.Request) -> web.Response:
        return web.json_response(self.bootstrap)

    async def get_release(self, request: web.Request) -> web.Response:
        key = "/".join(
            (
                request.match_info["publisher"].lower(),
                request.match_info["skill_id"],
                request.match_info["version"],
            )
        )
        release = self.releases.get(key)
        if release is None:
            raise web.HTTPNotFound()
        return web.json_response(release)

    async def get_artifact(self, request: web.Request) -> web.Response:
        value = self.artifacts.get(request.match_info["package_hash"].lower())
        if value is None:
            raise web.HTTPNotFound()
        return web.Response(body=value, content_type="application/octet-stream")

    async def websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=10)
        await ws.prepare(request)
        challenge = secrets.token_hex(32)
        await ws.send_json({"type": "challenge", "challenge": challenge})
        auth = await ws.receive_json()
        if auth.get("type") != "authenticate":
            self.rejected += 1
            await ws.send_json({"type": "error", "code": "authentication_required"})
            await ws.close()
            return ws
        try:
            verify_relay_profile_binding(auth["profile"], self.bootstrap)
            node = verify_profile(auth["profile"])
            recovered = Account.recover_message(
                encode_defunct(text=challenge),
                signature=auth["challenge_signature"],
            )
            if recovered != node:
                raise ValueError("challenge signer mismatch")
        except Exception:
            self.rejected += 1
            await ws.send_json({"type": "error", "code": "authentication_failed"})
            await ws.close()
            return ws

        self.connected.add(node)
        delivery_task: asyncio.Task[None] | None = None
        try:
            send_lock = asyncio.Lock()

            async def send_json(value: dict[str, Any]) -> None:
                async with send_lock:
                    await ws.send_json(value)

            await send_json(
                {"type": "ack", "status": "authenticated", "node": node}
            )
            starts: dict[str, float] = {}

            async def deliver_pending() -> None:
                while not ws.closed:
                    for queued in self.store.pending(node, set(starts)):
                        starts[queued.task_id] = perf_counter()
                        await send_json(
                            {
                                "type": "task",
                                "attempt": queued.attempts,
                                "task": json.loads(queued.payload),
                            }
                        )
                    await asyncio.sleep(0.05)

            delivery_task = asyncio.create_task(deliver_pending())

            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    value = parse_client_message(message.data)
                except LoveEngineError as exc:
                    self.rejected += 1
                    await send_json(
                        {"type": "error", "code": exc.code, "message": exc.message}
                    )
                    continue
                if value.get("type") == "heartbeat":
                    await send_json({"type": "heartbeat"})
                    continue
                if value.get("type") == "ack":
                    task_id = value.get("task_id")
                    if (
                        value.get("status") != "accepted"
                        or not isinstance(task_id, str)
                        or task_id not in starts
                    ):
                        self.rejected += 1
                        await send_json(
                            {"type": "error", "code": "invalid_task_ack"}
                        )
                        continue
                    if self.store.accept(node, task_id):
                        self.acceptance_latencies_ms.append(
                            (perf_counter() - starts[task_id]) * 1000
                        )
                    continue
                if value.get("type") != "receipt":
                    self.rejected += 1
                    await send_json(
                        {"type": "error", "code": "unsupported_message"}
                    )
                    continue
                try:
                    receipt = value["receipt"]
                    task_id = receipt.get("task_id")
                    if not isinstance(task_id, str) or task_id not in starts:
                        raise LoveEngineError(
                            "unassigned_receipt",
                            "receipt does not match a task delivered to this connection",
                        )
                    state = self.store.task_state(node, task_id)
                    if state is None:
                        raise LoveEngineError(
                            "unassigned_receipt",
                            "receipt task is not assigned to the authenticated node",
                        )
                    if state["acked"] or state["receipt"] is not None:
                        raise LoveEngineError(
                            "duplicate_receipt", "task already has a receipt"
                        )
                    if not state["accepted"]:
                        raise LoveEngineError(
                            "task_not_accepted",
                            "task must be accepted before submitting a receipt",
                        )
                    queued_task = json.loads(str(state["payload"]))
                    if queued_task.get("task_id") != task_id:
                        raise LoveEngineError(
                            "queued_task_mismatch",
                            "queued task ID does not match its Relay index",
                        )
                    if queued_task.get("chain_id") != self.bootstrap["chain_id"]:
                        raise LoveEngineError(
                            "wrong_chain_id", "queued task chainId mismatch"
                        )
                    if to_checksum_address(
                        queued_task["registry"]
                    ) != to_checksum_address(self.bootstrap["registry"]):
                        raise LoveEngineError(
                            "wrong_registry", "queued task Registry mismatch"
                        )
                    if to_checksum_address(
                        queued_task["recipient"]
                    ) != to_checksum_address(node):
                        raise LoveEngineError(
                            "wrong_recipient",
                            "queued task recipient does not match authenticated node",
                        )
                    expected_receipt_schema = {
                        "loveengine.network-task/1": "loveengine.task-receipt/1",
                        "loveengine.network-task/2": "loveengine.task-receipt/2",
                    }.get(queued_task.get("schema_version"))
                    if receipt.get("schema_version") != expected_receipt_schema:
                        raise LoveEngineError(
                            "receipt_schema_mismatch",
                            "receipt codec does not match the assigned task codec",
                        )
                    verify_relay_receipt(
                        receipt,
                        self.bootstrap["chain_id"],
                        self.bootstrap["registry"],
                        expected_node=node,
                    )
                    if receipt["nonce"] != queued_task["nonce"]:
                        raise LoveEngineError(
                            "receipt_nonce_mismatch",
                            "receipt nonce does not match the assigned task",
                        )
                    if int(receipt["completed_at"]) > int(
                        queued_task["deadline"]
                    ):
                        raise LoveEngineError(
                            "receipt_after_deadline",
                            "receipt completed after the assigned task deadline",
                        )
                    if not self.store.ack(
                        node,
                        task_id,
                        json.dumps(receipt, sort_keys=True),
                    ):
                        raise LoveEngineError(
                            "duplicate_receipt",
                            "task receipt was already recorded",
                        )
                    self.receipts.append(receipt)
                    start = starts.get(task_id)
                    if start is not None:
                        self.completion_latencies_ms.append(
                            (perf_counter() - start) * 1000
                        )
                    await send_json(
                        {
                            "type": "ack",
                            "task_id": task_id,
                        }
                    )
                except Exception as exc:
                    self.rejected += 1
                    code = (
                        exc.code if isinstance(exc, LoveEngineError) else "invalid_receipt"
                    )
                    message_text = (
                        exc.message if isinstance(exc, LoveEngineError) else str(exc)
                    )
                    await send_json(
                        {
                            "type": "error",
                            "code": code,
                            "message": message_text,
                        }
                    )
        finally:
            if delivery_task is not None:
                delivery_task.cancel()
                await asyncio.gather(delivery_task, return_exceptions=True)
            self.connected.discard(node)
        return ws

    def metrics(self) -> dict[str, Any]:
        base = self.store.metrics()
        def latency(values: list[float]) -> dict[str, float | int]:
            ordered = sorted(values)
            p95_index = max(
                0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)
            )
            return {
                "count": len(values),
                "max": round(max(values, default=0.0), 3),
                "p95": round(ordered[p95_index], 3) if ordered else 0.0,
            }

        base.update(
            {
                "connected": len(self.connected),
                "rejected": self.rejected,
                "latency_ms": latency(self.acceptance_latencies_ms),
                "completion_latency_ms": latency(
                    self.completion_latencies_ms
                ),
                "queue_depth": max(0, base["queued"] - base["acked"]),
            }
        )
        return base


async def run_node_client(
    url: str,
    node_address: str,
    signed_profile: dict[str, Any],
    expected_tasks: int,
    sign_challenge: Callable[[str], str],
    sign_typed_data: Callable[[dict[str, Any]], str],
    *,
    expected_issuer: str,
    expected_manifest_hash: str,
) -> dict[str, Any]:
    return await run_agent_session(
        url=url,
        node_address=node_address,
        signed_profile=signed_profile,
        expected_tasks=expected_tasks,
        expected_issuer=expected_issuer,
        expected_manifest_hash=expected_manifest_hash,
        sign_challenge=sign_challenge,
        sign_typed_data=sign_typed_data,
    )


async def run_v2_review_client(
    url: str,
    node_address: str,
    signed_profile: dict[str, Any],
    expected_tasks: int,
    verdicts: dict[str, str],
    sign_challenge: Callable[[str], str],
    sign_typed_data: Callable[[dict[str, Any]], str],
    *,
    expected_issuer: str,
    expected_manifest_hash: str,
) -> dict[str, Any]:
    return await run_agent_session(
        url=url,
        node_address=node_address,
        signed_profile=signed_profile,
        expected_tasks=expected_tasks,
        expected_issuer=expected_issuer,
        expected_manifest_hash=expected_manifest_hash,
        sign_challenge=sign_challenge,
        sign_typed_data=sign_typed_data,
        verdicts=verdicts,
    )


async def run_v2_observation_client(
    url: str,
    node_address: str,
    signed_profile: dict[str, Any],
    expected_tasks: int,
    cursor_database: str,
    sign_challenge: Callable[[str], str],
    sign_typed_data: Callable[[dict[str, Any]], str],
    *,
    expected_issuer: str,
    expected_manifest_hash: str,
) -> dict[str, Any]:
    return await run_agent_session(
        url=url,
        node_address=node_address,
        signed_profile=signed_profile,
        expected_tasks=expected_tasks,
        expected_issuer=expected_issuer,
        expected_manifest_hash=expected_manifest_hash,
        sign_challenge=sign_challenge,
        sign_typed_data=sign_typed_data,
        cursor_database=Path(cursor_database),
    )


async def serve_forever(
    store: RelayStore,
    bootstrap: dict[str, Any],
    host: str,
    port: int,
) -> None:
    hub = RelayHub(store, bootstrap, {}, {})
    await hub.start(host, port)
    try:
        await asyncio.Event().wait()
    finally:
        await hub.stop()
