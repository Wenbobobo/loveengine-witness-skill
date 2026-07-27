from __future__ import annotations

import copy
import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data

from loveengine_witness.agent_session import (
    AgentTaskJournal,
    build_relay_challenge,
    relay_challenge_signing_text,
    run_agent_session,
    verify_relay_challenge,
)
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.m4_network import (
    build_node_profile_v2,
    build_task_v2,
    verify_receipt_v2,
)
from loveengine_witness.m4_typed_data import (
    build_node_profile_v2_typed_data,
    build_task_v2_typed_data,
)
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
    verify_relay_bootstrap,
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
        encode_defunct(
            text=relay_challenge_signing_text(
                challenge,
                node=account.address,
            )
        ),
        account.key,
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
        "receipt_confirmed": 0,
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


def test_agent_task_journal_persists_task_and_issuer_nonce_replay_guards(
    tmp_path: Path,
) -> None:
    publisher = Account.create()
    node = Account.create()
    task = _signed_task(publisher, node.address, "durable-task")
    path = tmp_path / "node.cursor.sqlite"

    first = AgentTaskJournal(path)
    assert first.claim(task) is None
    receipt = _signed_receipt(node, task["task_id"])
    first.save_receipt(task["task_id"], receipt)

    reopened = AgentTaskJournal(path)
    assert reopened.claim(task) == receipt

    nonce_replay = _signed_task(publisher, node.address, "different-task")
    with pytest.raises(LoveEngineError) as error:
        reopened.claim(nonce_replay)
    assert error.value.code == "task_nonce_replay"

    task_id_replay = copy.deepcopy(task)
    task_id_replay["payload"] = {"package_hash": "sha256:" + "9" * 64}
    with pytest.raises(LoveEngineError) as error:
        reopened.claim(task_id_replay)
    assert error.value.code == "task_replay_conflict"


def test_agent_reconnects_and_recovers_a_receipt_when_relay_ack_is_lost(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        publisher = Account.create()
        node = Account.create()
        profile = _signed_profile(node)
        task = _signed_task(publisher, node.address, "ack-loss-task")
        state: dict[str, object] = {
            "connections": 0,
            "receipt": None,
            "confirmed": False,
        }

        async def websocket(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            state["connections"] = int(state["connections"]) + 1
            challenge = build_relay_challenge(
                chain_id=CHAIN_ID,
                registry=REGISTRY,
                nonce=f"{int(state['connections']):064x}",
            )
            await ws.send_json(challenge)
            await ws.receive_json()
            await ws.send_json(
                {
                    "type": "ack",
                    "status": "authenticated",
                    "node": node.address,
                }
            )
            if state["connections"] == 1:
                await ws.send_json(
                    {"type": "task", "attempt": 1, "task": task}
                )
                assert (await ws.receive_json())["status"] == "accepted"
                state["receipt"] = (await ws.receive_json())["receipt"]
                await asyncio.sleep(1.2)
            else:
                await ws.send_json(
                    {
                        "type": "receipt_state",
                        "task_id": task["task_id"],
                        "receipt": state["receipt"],
                    }
                )
                confirmation = await ws.receive_json()
                state["confirmed"] = (
                    confirmation
                    == {
                        "type": "receipt_ack",
                        "task_id": task["task_id"],
                    }
                )
                await ws.send_json(
                    {
                        "type": "ack",
                        "status": "receipt_confirmed",
                        "task_id": task["task_id"],
                    }
                )
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/v1/ws", websocket)
        server = TestServer(app)
        await server.start_server()
        release_checks = 0
        try:
            def before_connect() -> None:
                nonlocal release_checks
                release_checks += 1

            result = await run_agent_session(
                url=str(server.make_url("/v1/ws")),
                node_address=node.address,
                signed_profile=profile,
                expected_tasks=1,
                expected_issuer=publisher.address,
                expected_manifest_hash=MANIFEST_HASH,
                sign_challenge=lambda challenge: (
                    "0x"
                    + Account.sign_message(
                        encode_defunct(
                            text=relay_challenge_signing_text(
                                challenge,
                                node=node.address,
                            )
                        ),
                        node.key,
                    ).signature.hex()
                ),
                sign_typed_data=lambda typed: _sign_typed(node, typed),
                cursor_database=tmp_path / "node.cursor.sqlite",
                reconnect_attempts=1,
                idle_timeout_seconds=1,
                before_connect=before_connect,
            )
        finally:
            await server.close()

        assert result["connection_attempts"] == 2
        assert result["reconnects"] == 1
        assert len(result["receipts"]) == 1
        assert result["receipts"][0] == state["receipt"]
        assert state["confirmed"] is True
        assert release_checks == 2

    asyncio.run(scenario())


def test_agent_returns_a_signed_rejected_receipt_for_execution_failure(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        publisher = Account.create()
        node = Account.create()
        profile_value = build_node_profile_v2(
            node.address,
            ["review_dispute"],
            "1",
            "4102444800",
        )
        profile = {
            "schema_version": "loveengine.signed-agent-node-profile/2",
            "chain_id": CHAIN_ID,
            "registry": REGISTRY,
            "profile": profile_value,
            "signature": _sign_typed(
                node,
                build_node_profile_v2_typed_data(
                    CHAIN_ID,
                    REGISTRY,
                    profile_value,
                ),
            ),
        }
        task = build_task_v2(
            chain_id=CHAIN_ID,
            registry=REGISTRY,
            task_id="rejected-review",
            task_type="review_dispute",
            issuer=publisher.address,
            recipient=node.address,
            manifest_hash=MANIFEST_HASH,
            payload={
                "dispute_id": "dispute-1",
                "bundle_hash": "0x" + "4" * 64,
            },
            nonce="9",
            deadline="4102444800",
        )
        task["signature"] = _sign_typed(
            publisher,
            build_task_v2_typed_data(task),
        )
        captured: dict[str, object] = {}

        async def websocket(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json(
                build_relay_challenge(
                    chain_id=CHAIN_ID,
                    registry=REGISTRY,
                    nonce="9" * 64,
                )
            )
            await ws.receive_json()
            await ws.send_json(
                {
                    "type": "ack",
                    "status": "authenticated",
                    "node": node.address,
                    "receipt_state_count": 0,
                }
            )
            await ws.send_json({"type": "task", "attempt": 1, "task": task})
            assert (await ws.receive_json())["status"] == "accepted"
            captured["receipt"] = (await ws.receive_json())["receipt"]
            await ws.send_json(
                {
                    "type": "ack",
                    "task_id": task["task_id"],
                }
            )
            assert (await ws.receive_json()) == {
                "type": "receipt_ack",
                "task_id": task["task_id"],
            }
            await ws.send_json(
                {
                    "type": "ack",
                    "status": "receipt_confirmed",
                    "task_id": task["task_id"],
                }
            )
            await ws.close()
            return ws

        app = web.Application()
        app.router.add_get("/v1/ws", websocket)
        server = TestServer(app)
        await server.start_server()
        try:
            result = await run_agent_session(
                url=str(server.make_url("/v1/ws")),
                node_address=node.address,
                signed_profile=profile,
                expected_tasks=1,
                expected_issuer=publisher.address,
                expected_manifest_hash=MANIFEST_HASH,
                sign_challenge=lambda challenge: (
                    "0x"
                    + Account.sign_message(
                        encode_defunct(
                            text=relay_challenge_signing_text(
                                challenge,
                                node=node.address,
                            )
                        ),
                        node.key,
                    ).signature.hex()
                ),
                sign_typed_data=lambda typed: _sign_typed(node, typed),
                cursor_database=tmp_path / "node.cursor.sqlite",
                verdicts={"dispute-1": "dismiss"},
            )
        finally:
            await server.close()

        receipt = captured["receipt"]
        assert isinstance(receipt, dict)
        assert receipt["status"] == "rejected"
        assert receipt["result"] == {
            "error_code": "review_origin_required",
            "task_type": "review_dispute",
        }
        assert result["rejected"] == 1
        assert verify_receipt_v2(receipt, CHAIN_ID, REGISTRY) == node.address

    asyncio.run(scenario())


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


def test_relay_revalidates_bootstrap_signature_and_expiry() -> None:
    publisher = Account.create()
    node = Account.create()
    bootstrap = _signed_bootstrap(publisher, _signed_profile(node))
    verify_relay_bootstrap(bootstrap)

    tampered = copy.deepcopy(bootstrap)
    tampered["directory_hash"] = "0x" + "ff" * 32
    with pytest.raises(LoveEngineError):
        verify_relay_bootstrap(tampered)

    expired = copy.deepcopy(bootstrap)
    expired["valid_until"] = "1"
    with pytest.raises(LoveEngineError) as error:
        verify_relay_bootstrap(expired)
    assert error.value.code == "bootstrap_expired"


def test_malformed_websocket_json_has_stable_error() -> None:
    with pytest.raises(LoveEngineError) as error:
        parse_client_message("{")
    assert error.value.code == "malformed_message"


@pytest.mark.parametrize(
    "challenge",
    [
        {"type": "challenge", "challenge": "attacker-selected text"},
        {
            "type": "challenge",
            "schema_version": "loveengine.relay-challenge/1",
            "chain_id": CHAIN_ID,
            "registry": REGISTRY,
            "nonce": "not-a-random-256-bit-hex-value",
        },
        {
            "type": "challenge",
            "schema_version": "loveengine.relay-challenge/1",
            "chain_id": "1",
            "registry": REGISTRY,
            "nonce": "a" * 64,
        },
    ],
)
def test_relay_challenge_rejects_arbitrary_signer_prompts(challenge: dict) -> None:
    with pytest.raises(LoveEngineError) as error:
        verify_relay_challenge(
            challenge,
            expected_chain_id=CHAIN_ID,
            expected_registry=REGISTRY,
        )
    assert error.value.code == "invalid_relay_challenge"


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
                    await ws.send_json(
                        {
                            "type": "receipt_ack",
                            "task_id": task["task_id"],
                        }
                    )
                    confirmation = await ws.receive_json()
                    assert confirmation == {
                        "type": "ack",
                        "status": "receipt_confirmed",
                        "task_id": task["task_id"],
                    }
                    assert store.stored_receipts(node.address) == []
                    await ws.send_json({"type": "receipt", "receipt": receipt})
                    assert (await ws.receive_json())["code"] == "duplicate_receipt"
                    assert len(hub.receipts) == 1
        finally:
            await server.close()

    asyncio.run(scenario())
