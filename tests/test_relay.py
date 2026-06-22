from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.relay import RelayStore
from loveengine_witness.relay_server import (
    parse_client_message,
    verify_relay_profile_binding,
    verify_task_for_node,
)
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
    verify_task_for_node(task, profile)
    task = copy.deepcopy(task)
    task["registry"] = "0x" + "3" * 40
    with pytest.raises(LoveEngineError, match="Registry"):
        verify_task_for_node(task, profile)


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
        sign_challenge=lambda _: "0x",
        sign_typed_data=lambda _: "0x",
    )
    assert isinstance(transport, Transport)
