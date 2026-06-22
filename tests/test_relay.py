from __future__ import annotations

from loveengine_witness.relay import RelayStore
from loveengine_witness.transport import RelayTransport, Transport


def test_relay_store_redelivers_until_ack_and_restores_cursor(tmp_path) -> None:
    store = RelayStore(tmp_path / "relay.sqlite")
    store.enqueue("node-a", "task-1", '{"task_id":"task-1"}')

    first = store.pending("node-a")
    assert [item.task_id for item in first] == ["task-1"]
    assert first[0].attempts == 1

    redelivery = store.pending("node-a")
    assert [item.task_id for item in redelivery] == ["task-1"]
    assert redelivery[0].attempts == 2

    store.ack("node-a", "task-1", '{"status":"completed"}')
    assert store.pending("node-a") == []
    assert store.metrics() == {
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


def test_relay_transport_implements_transport_boundary() -> None:
    transport = RelayTransport(
        url="ws://127.0.0.1:1/v1/ws",
        node_address="0x" + "1" * 40,
        signed_profile={},
        expected_tasks=0,
        sign_challenge=lambda _: "0x",
        sign_typed_data=lambda _: "0x",
    )
    assert isinstance(transport, Transport)
