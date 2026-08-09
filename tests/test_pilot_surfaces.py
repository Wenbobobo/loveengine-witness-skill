from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from loveengine_witness.agent_session import relay_challenge_signing_text
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.m4_network import build_node_profile_v2, build_task_v2
from loveengine_witness.m4_typed_data import (
    build_bootstrap_v2_typed_data,
    build_node_profile_v2_typed_data,
    build_receipt_v2_typed_data,
    build_task_v2_typed_data,
)
from loveengine_witness.m4_network import (
    build_bootstrap_v2,
    build_receipt_v2,
)
from loveengine_witness.pilot_config import (
    PilotAdminSurfaceConfig,
    PilotConfigV2,
    PilotParticipantSurfaceConfig,
)
from loveengine_witness.pilot_server import pilot_status
from loveengine_witness.pilot_surfaces import (
    PARTICIPANT_CONFIG_KEY,
    PARTICIPANT_ROUTE_ALLOWLIST,
    PilotSurfaceServer,
    create_pilot_surfaces,
)


CHAIN_ID = "11155111"
REGISTRY = "0x" + "1" * 40
TOKEN = "dual-surface-test-token"
MANIFEST_HASH = "0x" + "2" * 64
PUBLIC_ORIGIN = "https://witness.example-tailnet.ts.net"


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _config(tmp_path: Path) -> PilotConfigV2:
    token_file = tmp_path / "operator.token"
    token_file.write_text(TOKEN, encoding="utf-8")
    return PilotConfigV2(
        schema_version="loveengine.pilot-config/2",
        run_id="dual-surface-test",
        admin=PilotAdminSurfaceConfig(
            host="127.0.0.1",
            port=8780,
            allowed_origins=("http://127.0.0.1:8780",),
        ),
        participant=PilotParticipantSurfaceConfig(
            host="127.0.0.1",
            port=8781,
            public_base_url=PUBLIC_ORIGIN,
        ),
        database=tmp_path / "pilot.sqlite",
        relay_database=tmp_path / "relay.sqlite",
        artifact_root=tmp_path / "artifacts",
        audit_log=tmp_path / "audit.jsonl",
        token_file=token_file,
        bootstrap_file=tmp_path / "bootstrap.json",
        release_file=tmp_path / "release.json",
        package_archive=tmp_path / "release.zip",
        rpc_url="http://127.0.0.1:1",
        rpc_url_file=None,
        chain_id=CHAIN_ID,
        write_token=TOKEN,
    )


def _sign(account: object, typed_data: dict) -> str:
    value = Account.sign_message(
        encode_typed_data(full_message=typed_data), account.key
    ).signature.hex()
    return value if value.startswith("0x") else "0x" + value


def _network_context() -> tuple[object, object, dict, dict]:
    publisher = Account.create()
    node = Account.create()
    profile_value = build_node_profile_v2(
        node=node.address,
        capabilities=["review_dispute"],
        sequence="1",
        valid_until="4102444800",
    )
    profile = {
        "schema_version": "loveengine.signed-agent-node-profile/2",
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "profile": profile_value,
        "signature": _sign(
            node,
            build_node_profile_v2_typed_data(CHAIN_ID, REGISTRY, profile_value),
        ),
    }
    bootstrap = build_bootstrap_v2(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        publisher=publisher.address,
        nodes=[profile],
        sequence="1",
        valid_until="4102444800",
    )
    bootstrap["signature"] = _sign(
        publisher, build_bootstrap_v2_typed_data(bootstrap)
    )
    return publisher, node, profile, bootstrap


def test_participant_surface_has_allowlisted_gets_and_no_http_authority(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        _, _, _, bootstrap = _network_context()
        surfaces = create_pilot_surfaces(
            _config(tmp_path), bootstrap=bootstrap, readiness=lambda: (True, {})
        )
        admin = TestClient(TestServer(surfaces.admin))
        participant = TestClient(TestServer(surfaces.participant))
        await admin.start_server()
        await participant.start_server()
        try:
            body = {
                "session_id": "dual-surface-session",
                "source_type": "operator",
                "created_at": "1780000000",
            }
            missing = await admin.post("/v1/live/sessions", json=body)
            assert missing.status == 401
            missing_origin = await admin.post(
                "/v1/live/sessions",
                json=body,
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert missing_origin.status == 403
            assert (await missing_origin.json())["error"]["code"] == (
                "origin_rejected"
            )
            created = await admin.post(
                "/v1/live/sessions",
                json=body,
                headers={
                    "Authorization": f"Bearer {TOKEN}",
                    "Origin": "http://127.0.0.1:8780",
                },
            )
            assert created.status == 201

            visible = await participant.get("/v1/live/sessions/dual-surface-session")
            assert visible.status == 200
            admin_metrics = await admin.get("/v1/metrics")
            assert admin_metrics.status == 200
            metrics_value = await admin_metrics.json()
            assert {"last_chain_block", "disk_bytes"}.issubset(metrics_value)
            admin_health = await admin.get("/healthz")
            participant_health = await participant.get("/healthz")
            assert (await admin_health.json())["surface"] == "admin"
            assert (await participant_health.json())["surface"] == "participant"
            participant_ready = await participant.get("/readyz")
            assert (await participant_ready.json())["surface"] == "participant"
            with pytest.raises(LoveEngineError) as mismatch:
                await pilot_status(
                    str(participant.make_url("/")),
                    expected_surface="admin",
                    include_metrics=False,
                )
            assert mismatch.value.code == "pilot_surface_mismatch"
            wrong_origin = await participant.get(
                "/v1/live/sessions/dual-surface-session",
                headers={"Origin": "https://untrusted.example"},
            )
            assert wrong_origin.status == 403

            for path in (
                "/v1/live/sessions",
                "/v1/relay/tasks",
                "/v1/live/sessions/dual-surface-session/evidence/finalize",
            ):
                denied = await participant.post(
                    path,
                    json={},
                    headers={"Authorization": f"Bearer {TOKEN}"},
                )
                assert denied.status == 405
                assert (await denied.json())["error"]["code"] == (
                    "participant_http_write_forbidden"
                )

            for path in ("/operator/", "/v1/metrics", "/v1/snapshot/restore"):
                denied = await participant.get(path)
                assert denied.status == 404
                assert (await denied.json())["error"]["code"] == (
                    "participant_route_forbidden"
                )

            participant_config = surfaces.participant[PARTICIPANT_CONFIG_KEY]
            assert not hasattr(participant_config, "write_token")
            routes = {
                (route.method, route.resource.canonical)
                for route in surfaces.participant.router.routes()
            }
            assert set(PARTICIPANT_ROUTE_ALLOWLIST) == {
                route for route in routes if route[0] == "GET"
            }
            assert all(method in {"GET", "HEAD"} for method, _ in routes)
            assert ("GET", "/v1/ws") in routes
            assert all(path != "/operator/" for _, path in routes)
            assert all(path != "/v1/relay/tasks" for _, path in routes)
            assert all(path != "/v1/metrics" for _, path in routes)
        finally:
            await participant.close()
            await admin.close()

    asyncio.run(scenario())


def test_participant_websocket_keeps_authenticated_receipt_flow(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        publisher, node, profile, bootstrap = _network_context()
        surfaces = create_pilot_surfaces(
            _config(tmp_path), bootstrap=bootstrap, readiness=lambda: (True, {})
        )
        server = TestServer(surfaces.participant)
        await server.start_server()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(server.make_url("/v1/ws")) as ws:
                    challenge = await ws.receive_json()
                    challenge_signature = Account.sign_message(
                        encode_defunct(
                            text=relay_challenge_signing_text(
                                challenge, node=node.address
                            )
                        ),
                        node.key,
                    ).signature.hex()
                    await ws.send_json(
                        {
                            "type": "authenticate",
                            "profile": profile,
                            "challenge_signature": challenge_signature,
                        }
                    )
                    authenticated = await ws.receive_json()
                    assert authenticated["status"] == "authenticated"

                    task = build_task_v2(
                        chain_id=CHAIN_ID,
                        registry=REGISTRY,
                        task_id="dual-surface-task",
                        task_type="review_dispute",
                        issuer=publisher.address,
                        recipient=node.address,
                        manifest_hash=MANIFEST_HASH,
                        payload={"dispute_id": "d1", "bundle_hash": "0x" + "3" * 64},
                        nonce="7",
                        deadline="4102444800",
                    )
                    task["signature"] = _sign(
                        publisher, build_task_v2_typed_data(task)
                    )
                    assert surfaces.relay.store.enqueue(
                        node.address,
                        task["task_id"],
                        json.dumps(task),
                        task["issuer"],
                        task["nonce"],
                    )
                    delivered = await ws.receive_json(timeout=1)
                    assert delivered["task"]["task_id"] == task["task_id"]
                    await ws.send_json(
                        {
                            "type": "ack",
                            "task_id": task["task_id"],
                            "status": "accepted",
                        }
                    )
                    receipt = build_receipt_v2(
                        chain_id=CHAIN_ID,
                        registry=REGISTRY,
                        task_id=task["task_id"],
                        node=node.address,
                        status="completed",
                        result={"verdict": "dismiss"},
                        nonce=task["nonce"],
                        completed_at="1780000010",
                    )
                    receipt["signature"] = _sign(
                        node, build_receipt_v2_typed_data(receipt)
                    )
                    await ws.send_json({"type": "receipt", "receipt": receipt})
                    receipt_ack = await ws.receive_json()
                    assert receipt_ack["task_id"] == task["task_id"]
                    assert surfaces.relay.store.metrics()["acked"] == 1
        finally:
            await server.close()

    asyncio.run(scenario())


def test_surface_server_starts_and_stops_both_loopback_listeners(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        config = _config(tmp_path)
        admin_port = _free_port()
        participant_port = _free_port()
        while participant_port == admin_port:
            participant_port = _free_port()
        config = replace(
            config,
            admin=replace(config.admin, port=admin_port),
            participant=replace(config.participant, port=participant_port),
        )
        surfaces = create_pilot_surfaces(
            config, readiness=lambda: (True, {"listeners": True})
        )
        server = PilotSurfaceServer(config, surfaces)
        await server.start()
        try:
            async with ClientSession() as session:
                admin = await session.get(f"http://127.0.0.1:{admin_port}/healthz")
                participant = await session.get(
                    f"http://127.0.0.1:{participant_port}/healthz"
                )
                assert admin.status == 200
                assert participant.status == 200
        finally:
            await server.stop()

    asyncio.run(scenario())
