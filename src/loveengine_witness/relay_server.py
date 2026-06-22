"""HTTP/WebSocket Relay Hub for the local M3 pilot."""

from __future__ import annotations

import json
import asyncio
import secrets
from time import perf_counter
from typing import Any, Callable

from aiohttp import ClientSession, WSMsgType, web
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_utils import to_checksum_address

from .errors import LoveEngineError
from .network_protocol import (
    build_receipt,
    verify_node_profile,
    verify_receipt,
    verify_task,
)
from .network_typed_data import build_receipt_typed_data
from .relay import RelayStore


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


def verify_task_for_node(
    task: dict[str, Any],
    signed_profile: dict[str, Any],
) -> str:
    return verify_task(
        task,
        expected_chain_id=signed_profile["chain_id"],
        expected_registry=signed_profile["registry"],
        expected_recipient=signed_profile["profile"]["node"],
    )


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
        self.latencies_ms: list[float] = []
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
            node = verify_node_profile(auth["profile"])
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
        try:
            await ws.send_json(
                {"type": "ack", "status": "authenticated", "node": node}
            )
            starts: dict[str, float] = {}
            for message in self.store.pending(node):
                starts[message.task_id] = perf_counter()
                await ws.send_json(
                    {
                        "type": "task",
                        "attempt": message.attempts,
                        "task": json.loads(message.payload),
                    }
                )

            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    value = parse_client_message(message.data)
                except LoveEngineError as exc:
                    self.rejected += 1
                    await ws.send_json(
                        {"type": "error", "code": exc.code, "message": exc.message}
                    )
                    continue
                if value.get("type") == "heartbeat":
                    await ws.send_json({"type": "heartbeat"})
                    continue
                if value.get("type") != "receipt":
                    self.rejected += 1
                    await ws.send_json(
                        {"type": "error", "code": "unsupported_message"}
                    )
                    continue
                try:
                    receipt = value["receipt"]
                    verify_receipt(
                        receipt,
                        self.bootstrap["chain_id"],
                        self.bootstrap["registry"],
                    )
                    self.store.ack(
                        node,
                        receipt["task_id"],
                        json.dumps(receipt, sort_keys=True),
                    )
                    self.receipts.append(receipt)
                    start = starts.get(receipt["task_id"])
                    if start is not None:
                        self.latencies_ms.append((perf_counter() - start) * 1000)
                    await ws.send_json(
                        {
                            "type": "ack",
                            "task_id": receipt["task_id"],
                        }
                    )
                except Exception as exc:
                    self.rejected += 1
                    await ws.send_json(
                        {
                            "type": "error",
                            "code": "invalid_receipt",
                            "message": str(exc),
                        }
                    )
        finally:
            self.connected.discard(node)
        return ws

    def metrics(self) -> dict[str, Any]:
        base = self.store.metrics()
        base.update(
            {
                "connected": len(self.connected),
                "rejected": self.rejected,
                "latency_ms": {
                    "count": len(self.latencies_ms),
                    "max": round(max(self.latencies_ms, default=0.0), 3),
                },
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
) -> dict[str, Any]:
    node = to_checksum_address(node_address)
    completed: set[str] = set()
    receipts: list[dict[str, Any]] = []
    pending_messages: list[dict[str, Any]] = []
    rejected = 0
    async with ClientSession() as session:
        async with session.ws_connect(url) as ws:
            challenge = await ws.receive_json()
            challenge_signature = sign_challenge(challenge["challenge"])
            await ws.send_json(
                {
                    "type": "authenticate",
                    "profile": signed_profile,
                    "challenge_signature": challenge_signature,
                }
            )
            authenticated = await ws.receive_json()
            if authenticated.get("status") != "authenticated":
                raise RuntimeError("relay authentication failed")
            while len(receipts) < expected_tasks:
                message = (
                    pending_messages.pop(0)
                    if pending_messages
                    else await ws.receive_json(timeout=10)
                )
                if message.get("type") != "task":
                    continue
                task = message["task"]
                if task["task_id"] in completed:
                    rejected += 1
                    continue
                verify_task_for_node(task, signed_profile)
                completed.add(task["task_id"])
                receipt = build_receipt(
                    chain_id=task["chain_id"],
                    registry=task["registry"],
                    task_id=task["task_id"],
                    node=node,
                    status="completed",
                    result={
                        "accepted_task_type": task["task_type"],
                        "payload_hash": task["payload_hash"],
                    },
                    nonce=str(len(receipts)),
                    completed_at=str(int(task["deadline"]) - 1),
                )
                receipt["signature"] = sign_typed_data(
                    build_receipt_typed_data(receipt)
                )
                await ws.send_json({"type": "receipt", "receipt": receipt})
                while True:
                    ack = await ws.receive_json(timeout=10)
                    if (
                        ack.get("type") == "ack"
                        and ack.get("task_id") == receipt["task_id"]
                    ):
                        break
                    if ack.get("type") == "task":
                        pending_messages.append(ack)
                        continue
                    raise RuntimeError(
                        "relay did not acknowledge receipt: "
                        + json.dumps(ack, sort_keys=True)
                    )
                receipts.append(receipt)
    return {
        "node": node,
        "receipts": receipts,
        "rejected": rejected,
    }


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
