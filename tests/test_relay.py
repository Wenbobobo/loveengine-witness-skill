from __future__ import annotations

import copy
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.network_protocol import (
    build_bootstrap,
    build_node_profile,
    build_receipt,
    build_task,
)
from loveengine_witness.network_typed_data import (
    build_bootstrap_typed_data,
    build_node_profile_typed_data,
    build_receipt_typed_data,
    build_task_typed_data,
)
from loveengine_witness.relay import RelayStore
from loveengine_witness.relay_server import (
    _run_with_relay_keepalive,
    RelayHub,
    parse_client_message,
    verify_relay_profile_binding,
    verify_task_for_node,
)
from loveengine_witness.transport import RelayTransport, Transport


CHAIN_ID = "31337"
REGISTRY = "0x" + "1" * 40
MANIFEST_HASH = "0x" + "2" * 64


def _sign_typed(account: object, value: dict) -> str:
    signature = Account.sign_message(
        encode_typed_data(full_message=value), account.key
    ).signature.hex()
    return signature if signature.startswith("0x") else "0x" + signature


def _signed_profile(account: object) -> dict:
    profile = build_node_profile(
        account.address,
        ["propagate_skill"],
        "1",
        "4102444800",
    )
    return {
        "schema_version": "loveengine.signed-agent-node-profile/1",
        "chain_id": CHAIN_ID,
        "registry": REGISTRY,
        "profile": profile,
        "signature": _sign_typed(
            account,
            build_node_profile_typed_data(CHAIN_ID, REGISTRY, profile),
        ),
    }


def _signed_bootstrap(publisher: object, profile: dict) -> dict:
    bootstrap = build_bootstrap(
        publisher.address,
        [profile],
        "1",
        "4102444800",
    )
    bootstrap["signature"] = _sign_typed(
        publisher,
        build_bootstrap_typed_data(CHAIN_ID, REGISTRY, bootstrap),
    )
    return bootstrap


def _signed_task(publisher: object, node: str, task_id: str = "task-1") -> dict:
    task = build_task(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        task_id=task_id,
        task_type="propagate_skill",
        issuer=publisher.address,
        recipient=node,
        manifest_hash=MANIFEST_HASH,
        payload={"package_hash": "sha256:" + "3" * 64},
        nonce="1",
        deadline="4102444800",
    )
    task["signature"] = _sign_typed(publisher, build_task_typed_data(task))
    return task


def _signed_receipt(
    account: object,
    task_id: str,
    *,
    nonce: str = "1",
    completed_at: str = "2000000000",
) -> dict:
    receipt = build_receipt(
        chain_id=CHAIN_ID,
        registry=REGISTRY,
        task_id=task_id,
        node=account.address,
        status="completed",
        result={"accepted": True},
        nonce=nonce,
        completed_at=completed_at,
    )
    receipt["signature"] = _sign_typed(
        account, build_receipt_typed_data(receipt)
    )
    return receipt


async def _authenticate(ws: object, account: object, profile: dict) -> dict:
    challenge = await ws.receive_json()
    signature = Account.sign_message(
        encode_defunct(text=challenge["challenge"]), account.key
    ).signature.hex()
    await ws.send_json(
        {
            "type": "authenticate",
            "profile": profile,
            "challenge_signature": (
                signature if signature.startswith("0x") else "0x" + signature
            ),
        }
    )
    return await ws.receive_json()


def test_relay_store_redelivers_until_ack_and_restores_cursor(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    store.enqueue("node-a", "task-1", '{"task_id":"task-1"}')

    first = store.pending("node-a")
    assert [item.task_id for item in first] == ["task-1"]
    assert first[0].attempts == 1

    redelivery = store.pending("node-a")
    assert [item.task_id for item in redelivery] == ["task-1"]
    assert redelivery[0].attempts == 2

    assert store.accept("node-a", "task-1") is True
    assert store.accept("node-a", "task-1") is False
    assert store.ack("node-a", "task-1", '{"status":"completed"}') is True
    assert store.ack("node-a", "task-1", '{"status":"completed"}') is False
    assert store.pending("node-a") == []
    assert store.metrics() == {
        "accepted": 1,
        "acked": 1,
        "delivered": 2,
        "queued": 1,
    }

    reopened = RelayStore(tmp_path / "relay.sqlite")
    assert reopened.pending("node-a") == []
    assert reopened.receipt("node-a", "task-1") == '{"status":"completed"}'


def test_relay_store_deduplicates_task_id_and_issuer_nonce(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    assert store.enqueue("node-a", "task-1", "payload", "issuer", "1") is True
    assert store.enqueue("node-a", "task-1", "payload", "issuer", "1") is False
    assert store.enqueue("node-a", "task-2", "payload", "issuer", "1") is False
    assert store.metrics()["queued"] == 1


def test_relay_store_defaults_do_not_collapse_distinct_tasks(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")

    assert store.enqueue("node-a", "task-1", "first") is True
    assert store.enqueue("node-a", "task-2", "second") is True
    assert [item.task_id for item in store.pending("node-a")] == [
        "task-1",
        "task-2",
    ]


def test_relay_rejects_profile_and_task_outside_bootstrap_trust_root() -> None:
    fixture = json.loads(
        (
            Path(__file__).parents[1]
            / "examples"
            / "transcripts"
            / "network-pilot.fixture.json"
        ).read_text(encoding="utf-8")
    )
    bootstrap = fixture["bootstrap"]
    profile = bootstrap["directory"][0]
    verify_relay_profile_binding(profile, bootstrap)

    wrong_profile = copy.deepcopy(profile)
    wrong_profile["chain_id"] = "1"
    with pytest.raises(LoveEngineError, match="chain"):
        verify_relay_profile_binding(wrong_profile, bootstrap)

    task = next(
        item
        for item in fixture["tasks"]
        if item["recipient"] == profile["profile"]["node"]
    )
    verify_task_for_node(
        task,
        profile,
        expected_issuer=task["issuer"],
        expected_manifest_hash=task["manifest_hash"],
    )
    task = copy.deepcopy(task)
    task["registry"] = "0x" + "3" * 40
    with pytest.raises(LoveEngineError, match="Registry"):
        verify_task_for_node(
            task,
            profile,
            expected_issuer=task["issuer"],
            expected_manifest_hash=task["manifest_hash"],
        )


def test_malformed_websocket_json_has_stable_error() -> None:
    with pytest.raises(LoveEngineError) as error:
        parse_client_message("{")
    assert error.value.code == "malformed_message"


def test_relay_transport_implements_transport_boundary() -> None:
    transport = RelayTransport(
        url="ws://127.0.0.1:1/v1/ws",
        node_address="0x" + "1" * 40,
        signed_profile={},
        expected_tasks=0,
        expected_issuer="0x" + "2" * 40,
        expected_manifest_hash=MANIFEST_HASH,
        sign_challenge=lambda _: "0x",
        sign_typed_data=lambda _: "0x",
    )
    assert isinstance(transport, Transport)


def test_long_running_relay_task_keeps_websocket_active() -> None:
    class QuietWebSocket:
        def __init__(self) -> None:
            self.sent: list[dict] = []

        async def receive_json(self, timeout: float) -> dict:
            await asyncio.sleep(timeout)
            raise TimeoutError

        async def send_json(self, value: dict) -> None:
            self.sent.append(value)

    async def scenario() -> None:
        socket = QuietWebSocket()
        pending: list[dict] = []

        async def operation() -> str:
            await asyncio.sleep(0.04)
            return "complete"

        result = await _run_with_relay_keepalive(
            socket,
            operation(),
            pending,
            interval=0.01,
        )

        assert result == "complete"
        assert socket.sent
        assert all(item == {"type": "heartbeat"} for item in socket.sent)
        assert pending == []

    asyncio.run(scenario())


def test_relay_rejects_valid_profile_outside_bootstrap_directory(tmp_path) -> None:
    async def scenario() -> None:
        publisher = Account.create()
        member = Account.create()
        outsider = Account.create()
        bootstrap = _signed_bootstrap(publisher, _signed_profile(member))
        hub = RelayHub(RelayStore(tmp_path / "relay.sqlite"), bootstrap, {}, {})
        server = TestServer(hub.app())
        await server.start_server()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(server.make_url("/v1/ws")) as ws:
                    response = await _authenticate(
                        ws, outsider, _signed_profile(outsider)
                    )
                    assert response["code"] == "authentication_failed"
        finally:
            await server.close()

    asyncio.run(scenario())


def test_relay_pushes_later_tasks_and_binds_receipts_to_connection(
    tmp_path,
) -> None:
    async def scenario() -> None:
        publisher = Account.create()
        node = Account.create()
        outsider = Account.create()
        profile = _signed_profile(node)
        bootstrap = _signed_bootstrap(publisher, profile)
        store = RelayStore(tmp_path / "relay.sqlite")
        hub = RelayHub(store, bootstrap, {}, {})
        server = TestServer(hub.app())
        await server.start_server()
        try:
            async with ClientSession() as session:
                async with session.ws_connect(server.make_url("/v1/ws")) as ws:
                    authenticated = await _authenticate(ws, node, profile)
                    assert authenticated["status"] == "authenticated"

                    task = _signed_task(publisher, node.address)
                    assert store.enqueue(
                        node.address,
                        task["task_id"],
                        json.dumps(task),
                        task["issuer"],
                        task["nonce"],
                    )
                    delivered = await ws.receive_json(timeout=1)
                    assert delivered["task"]["task_id"] == task["task_id"]

                    receipt = _signed_receipt(node, task["task_id"])
                    await ws.send_json({"type": "receipt", "receipt": receipt})
                    assert (await ws.receive_json())["code"] == "task_not_accepted"

                    await ws.send_json(
                        {
                            "type": "ack",
                            "task_id": task["task_id"],
                            "status": "accepted",
                        }
                    )
                    wrong_node = _signed_receipt(outsider, task["task_id"])
                    await ws.send_json(
                        {"type": "receipt", "receipt": wrong_node}
                    )
                    assert (await ws.receive_json())["code"] == "wrong_receipt_node"

                    unassigned = _signed_receipt(node, "not-assigned")
                    await ws.send_json(
                        {"type": "receipt", "receipt": unassigned}
                    )
                    assert (await ws.receive_json())["code"] == "unassigned_receipt"

                    wrong_nonce = _signed_receipt(
                        node, task["task_id"], nonce="2"
                    )
                    await ws.send_json(
                        {"type": "receipt", "receipt": wrong_nonce}
                    )
                    assert (await ws.receive_json())["code"] == "receipt_nonce_mismatch"

                    late = _signed_receipt(
                        node,
                        task["task_id"],
                        completed_at=str(int(task["deadline"]) + 1),
                    )
                    await ws.send_json({"type": "receipt", "receipt": late})
                    assert (await ws.receive_json())["code"] == "receipt_after_deadline"

                    await ws.send_json({"type": "receipt", "receipt": receipt})
                    assert (await ws.receive_json())["task_id"] == task["task_id"]
                    await ws.send_json({"type": "receipt", "receipt": receipt})
                    assert (await ws.receive_json())["code"] == "duplicate_receipt"
                    assert len(hub.receipts) == 1
        finally:
            await server.close()

    asyncio.run(scenario())
