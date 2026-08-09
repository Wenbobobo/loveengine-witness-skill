from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import loveengine_witness.pilot_phases as pilot_phases
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_phases import (
    _carry_restart_metrics,
    _new_pilot_client_session,
    _participant_attestation_window,
    _reviews_from_receipts,
    _stop_processes,
    _wait_for_relay_task_acceptance,
    _wait_for_relay_node_connections,
    run_pilot_phases,
)


def test_participant_attestation_window_is_anchor_bound_and_bounded() -> None:
    issued_at, valid_until = _participant_attestation_window("100")

    assert issued_at == "100"
    assert valid_until == "3700"


def test_restart_carries_full_process_local_metrics() -> None:
    previous_hub = SimpleNamespace(
        receipts=[{"task_id": "before-restart"}],
        rejected=2,
        acceptance_latencies_ms=[10.0, 20.0],
        completion_latencies_ms=[30.0],
        receipt_ack_drops=["before-restart"],
        drop_receipt_ack_once_for={"pending-after-restart"},
    )
    previous_metrics = SimpleNamespace(
        started_at=100.0,
        accepted_requests=17,
        rejected_requests=3,
        recoveries=0,
        sse_clients=1,
    )
    next_hub = SimpleNamespace(
        receipts=[],
        rejected=0,
        acceptance_latencies_ms=[],
        completion_latencies_ms=[],
        receipt_ack_drops=[],
        drop_receipt_ack_once_for=set(),
    )
    next_metrics = SimpleNamespace(
        started_at=200.0,
        accepted_requests=0,
        rejected_requests=0,
        recoveries=0,
        sse_clients=0,
    )

    _carry_restart_metrics(
        previous_hub, previous_metrics, next_hub, next_metrics
    )

    assert next_hub.receipts == [{"task_id": "before-restart"}]
    assert next_hub.rejected == 2
    assert next_hub.acceptance_latencies_ms == [10.0, 20.0]
    assert next_hub.completion_latencies_ms == [30.0]
    assert next_hub.receipt_ack_drops == ["before-restart"]
    assert next_hub.drop_receipt_ack_once_for == {"pending-after-restart"}
    assert next_metrics.started_at == 100.0
    assert next_metrics.accepted_requests == 17
    assert next_metrics.rejected_requests == 3
    assert next_metrics.recoveries == 1
    assert next_metrics.sse_clients == 0


def test_prepare_failure_stops_started_dual_surface(tmp_path, monkeypatch) -> None:
    accounts = [f"0x{index:040x}" for index in range(1, 8)]
    w3 = SimpleNamespace(
        eth=SimpleNamespace(chain_id=31337, accounts=accounts)
    )
    deployment = {
        "deployer": accounts[0],
        "corporate_admin": accounts[1],
        "witnesses": accounts[2:5],
        "relayer": accounts[5],
    }
    archive = tmp_path / "release.zip"
    archive.write_bytes(b"release")
    runtime = SimpleNamespace(
        info={"release_transactions": []},
        package=SimpleNamespace(
            archive=archive,
            keccak256="0x" + "11" * 32,
        ),
        bootstrap={},
        release_key="publisher/skill/0.7",
        release={"version": "0.7.0-invited-public-pilot"},
        invite={"server_url": "http://127.0.0.1:8780"},
        config=SimpleNamespace(
            run_id="prepare-cleanup",
            host="127.0.0.1",
            port=8780,
            database=tmp_path / "pilot.sqlite",
            relay_database=tmp_path / "relay.sqlite",
            artifact_root=tmp_path / "artifacts",
            audit_log=tmp_path / "audit.jsonl",
            token_file=tmp_path / "operator.token",
            bootstrap_file=tmp_path / "bootstrap.json",
            release_file=tmp_path / "release.json",
            package_archive=archive,
            rpc_url="http://127.0.0.1:8545",
            chain_id="31337",
            write_token="test-token",
        ),
    )
    lifecycle: list[str] = []

    class SurfaceServer:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def start(self) -> None:
            lifecycle.append("started")

        async def stop(self) -> None:
            lifecycle.append("stopped")

    monkeypatch.setattr(
        pilot_phases,
        "_contract",
        lambda *_args: SimpleNamespace(address="0x" + "aa" * 20),
    )
    monkeypatch.setattr(
        pilot_phases,
        "create_pilot_surfaces",
        lambda *_args, **_kwargs: SimpleNamespace(
            admin=object(), relay=object(), metrics=object()
        ),
    )
    monkeypatch.setattr(pilot_phases, "PilotSurfaceServer", SurfaceServer)
    monkeypatch.setattr(
        pilot_phases, "build_pilot_invite_v2", lambda **_kwargs: {}
    )
    monkeypatch.setattr(
        pilot_phases,
        "write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        asyncio.run(
            pilot_phases._prepare_environment(
                tmp_path,
                w3,
                deployment,
                runtime,
                stage="core",
                core_transcript_version=2,
                participant_port=8781,
            )
        )

    assert lifecycle == ["started", "stopped"]


def test_run_pilot_phases_routes_acceptance_profile_to_acceptance_only(
    tmp_path, monkeypatch
) -> None:
    environment = SimpleNamespace(output=tmp_path, all_processes=[])
    observation = SimpleNamespace()
    evidence = SimpleNamespace()
    expected_profile = {"duration_seconds": 900}
    seen: dict[str, object] = {}

    async def prepare(*_args, **_kwargs):
        return environment

    async def observe(*_args, **kwargs):
        assert "acceptance_profile" not in kwargs
        return observation

    async def build_evidence(*_args, **_kwargs):
        return evidence

    def accept(*_args, **kwargs):
        seen["acceptance_profile"] = kwargs["acceptance_profile"]
        return {"passed": True}

    async def stop(*_args, **_kwargs):
        return None

    async def restore(*_args, **_kwargs):
        seen["snapshot_restore"] = True

    monkeypatch.setattr(pilot_phases, "_prepare_environment", prepare)
    monkeypatch.setattr(pilot_phases, "_run_observation_phase", observe)
    monkeypatch.setattr(pilot_phases, "_run_evidence_phase", build_evidence)
    monkeypatch.setattr(pilot_phases, "_run_acceptance_phase", accept)
    monkeypatch.setattr(pilot_phases, "_exercise_system_snapshot_restore", restore)
    monkeypatch.setattr(pilot_phases, "_stop_processes", stop)
    monkeypatch.setattr(pilot_phases, "_stop_pilot_server", stop)

    result = asyncio.run(
        run_pilot_phases(
            tmp_path,
            SimpleNamespace(),
            {},
            SimpleNamespace(),
            event_count=30,
            observer_count=10,
            event_interval=30,
            restart_chain=stop,
            simulate_faults=True,
            core_transcript_version=2,
            acceptance_profile=expected_profile,
        )
    )

    assert result == {"passed": True}
    assert seen["acceptance_profile"] is expected_profile
    assert seen["snapshot_restore"] is True


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
