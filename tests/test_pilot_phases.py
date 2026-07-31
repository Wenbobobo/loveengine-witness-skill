from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import loveengine_witness.pilot_phases as pilot_phases
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_phases import (
    _new_pilot_client_session,
    _reviews_from_receipts,
    _stop_processes,
    _wait_for_relay_task_acceptance,
    _wait_for_relay_node_connections,
)


def test_pilot_http_sessions_do_not_reuse_connections_across_restart() -> None:
    async def verify() -> None:
        session = _new_pilot_client_session()
        try:
            assert session.connector is not None
            assert session.connector.force_close is True
        finally:
            await session.close()

    asyncio.run(verify())


def test_stopped_pilot_node_processes_are_reaped_with_pipe_drain(
    tmp_path,
) -> None:
    class Process:
        pid = 4321

        def __init__(self) -> None:
            self.running = True
            self.killed = False
            self.communicate_timeouts: list[int] = []

        def poll(self) -> int | None:
            return None if self.running else 0

        def kill(self) -> None:
            self.killed = True
            self.running = False

        def communicate(self, *, timeout: int) -> tuple[str, str]:
            self.communicate_timeouts.append(timeout)
            return ("", "")

    process = Process()
    asyncio.run(_stop_processes([(process, tmp_path / "result.json")]))

    assert process.killed is True
    assert process.communicate_timeouts == [10]


def test_wait_for_relay_task_acceptance_requires_durable_acceptance() -> None:
    states = iter(
        [
            {"accepted": False},
            {"accepted": True},
        ]
    )

    class Store:
        def task_state(self, node: str, task_id: str) -> dict[str, bool]:
            assert node == "0x0000000000000000000000000000000000000001"
            assert task_id == "observe:pilot:3"
            return next(states)

    asyncio.run(
        _wait_for_relay_task_acceptance(
            SimpleNamespace(store=Store()),
            node="0x0000000000000000000000000000000000000001",
            task_id="observe:pilot:3",
        )
    )


def test_wait_for_relay_task_acceptance_times_out_without_ack() -> None:
    class Store:
        def task_state(self, node: str, task_id: str) -> None:
            del node, task_id
            return None

    with pytest.raises(LoveEngineError) as error:
        asyncio.run(
            _wait_for_relay_task_acceptance(
                SimpleNamespace(store=Store()),
                node="0x0000000000000000000000000000000000000001",
                task_id="observe:pilot:3",
                timeout_seconds=0.0,
            )
        )

    assert error.value.code == "pilot_task_acceptance_timeout"


def test_wait_for_relay_node_connections_requires_authenticated_membership() -> None:
    hub = SimpleNamespace(connected=set())

    async def connect_later() -> None:
        await asyncio.sleep(0)
        hub.connected.add("0x0000000000000000000000000000000000000001")

    async def wait_for_connection() -> None:
        waiter = asyncio.create_task(
            _wait_for_relay_node_connections(
                hub,
                nodes=["0x0000000000000000000000000000000000000001"],
                connected=True,
            )
        )
        await connect_later()
        await waiter

    asyncio.run(wait_for_connection())


def test_wait_for_relay_node_connections_times_out_without_disconnect() -> None:
    hub = SimpleNamespace(
        connected={"0x0000000000000000000000000000000000000001"}
    )

    with pytest.raises(LoveEngineError) as error:
        asyncio.run(
            _wait_for_relay_node_connections(
                hub,
                nodes=["0x0000000000000000000000000000000000000001"],
                connected=False,
                timeout_seconds=0.0,
            )
        )

    assert error.value.code == "pilot_relay_disconnect_timeout"


def test_pilot_enqueues_task_only_after_authenticated_relay_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def wait_for_connection(
        hub: object,
        *,
        nodes: list[str],
        connected: bool,
    ) -> None:
        assert hub is not None
        assert nodes == ["node-1"]
        assert connected is True
        calls.append("connected")

    async def enqueue(
        environment: object, client: object, task: dict[str, object]
    ) -> None:
        assert environment is not None
        assert client is not None
        assert task["task_id"] == "task-1"
        calls.append("enqueued")

    async def wait_for_acceptance(
        hub: object, *, node: str, task_id: str
    ) -> None:
        assert hub is not None
        assert node == "node-1"
        assert task_id == "task-1"
        calls.append("accepted")

    monkeypatch.setattr(
        pilot_phases, "_wait_for_relay_node_connections", wait_for_connection
    )
    monkeypatch.setattr(
        pilot_phases, "_enqueue_task_through_operator_api", enqueue
    )
    monkeypatch.setattr(
        pilot_phases, "_wait_for_relay_task_acceptance", wait_for_acceptance
    )

    asyncio.run(
        pilot_phases._enqueue_task_after_relay_connection(
            object(),
            object(),
            object(),
            task={"task_id": "task-1"},
            node="node-1",
        )
    )

    assert calls == ["connected", "enqueued", "accepted"]


def test_pilot_does_not_enqueue_task_when_relay_connection_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def wait_for_connection(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise LoveEngineError("pilot_relay_connection_timeout", "node-1", 4)

    async def enqueue(*args: object, **kwargs: object) -> None:
        del args, kwargs
        calls.append("enqueued")

    monkeypatch.setattr(
        pilot_phases, "_wait_for_relay_node_connections", wait_for_connection
    )
    monkeypatch.setattr(
        pilot_phases, "_enqueue_task_through_operator_api", enqueue
    )

    with pytest.raises(LoveEngineError) as error:
        asyncio.run(
            pilot_phases._enqueue_task_after_relay_connection(
                object(),
                object(),
                object(),
                task={"task_id": "task-1"},
                node="node-1",
            )
        )

    assert error.value.code == "pilot_relay_connection_timeout"
    assert calls == []


def test_rejected_review_receipt_has_a_stable_pilot_error() -> None:
    receipt = {
        "task_id": "review-1",
        "status": "rejected",
        "result": {"error_code": "review_evidence_unavailable"},
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt], {})

    assert error.value.code == "review_receipt_rejected"
    assert error.value.message == "review-1:review_evidence_unavailable"


def test_completed_review_receipt_without_verdict_is_invalid() -> None:
    receipt = {
        "task_id": "review-1",
        "status": "completed",
        "result": {"reason_hash": "0x" + "11" * 32},
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt], {})

    assert error.value.code == "review_receipt_invalid"


def test_completed_review_receipt_requires_verified_evidence() -> None:
    task_id = "review-1"
    task = {
        "payload": {
            "schema_version": "loveengine.review-dispute-payload/1",
            "dispute_id": "dispute-1",
            "bundle_hash": "0x" + "11" * 32,
            "session_id": "session-1",
            "evidence_url": "http://127.0.0.1:8780/evidence",
            "events_url": "http://127.0.0.1:8780/events",
            "artifact_base_url": "http://127.0.0.1:8780/artifacts",
            "revision": "1",
            "event_count": "1",
            "head_event_hash": "0x" + "22" * 32,
        }
    }
    receipt = {
        "task_id": task_id,
        "status": "completed",
        "result": {
            "dispute_id": "dispute-1",
            "bundle_hash": "0x" + "11" * 32,
            "session_id": "session-1",
            "revision": "1",
            "event_count": "1",
            "head_event_hash": "0x" + "22" * 32,
            "evidence_verified": False,
            "verdict": "dismiss",
            "reason_hash": "0x" + "33" * 32,
        },
    }

    with pytest.raises(LoveEngineError) as error:
        _reviews_from_receipts({}, [receipt], {task_id: task})

    assert error.value.code == "review_evidence_not_verified"
