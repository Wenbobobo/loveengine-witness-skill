from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import loveengine_witness.pilot_soak as pilot_soak
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_soak import SOAK_SUCCESS_CHECK_KEYS


class _FakeSampler:
    def __init__(self, metrics: dict[str, object]) -> None:
        self._metrics = metrics
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> dict[str, object]:
        self.stopped = True
        return dict(self._metrics)


def _runtime_tree_metrics() -> dict[str, object]:
    return {
        "available": True,
        "sampled_peak_rss_bytes": 456,
        "sampled_peak_processes": [
            {"pid": 101, "name": "python", "rss_bytes": 456}
        ],
        "sample_count": 4,
        "max_process_count": 5,
        "sample_interval_seconds": 0.5,
        "sample_error_count": 0,
    }


def _install_memory_stubs(
    monkeypatch: pytest.MonkeyPatch,
) -> _FakeSampler:
    sampler = _FakeSampler(_runtime_tree_metrics())
    monkeypatch.setattr(
        pilot_soak, "_runtime_tree_metrics", lambda root_pid: sampler
    )
    monkeypatch.setattr(
        pilot_soak, "_root_process_peak_rss_bytes", lambda: 123
    )
    return sampler


def _successful_demo(output: Path, **kwargs: object) -> dict[str, object]:
    (output / "operator.token").write_text("test-write-token", encoding="utf-8")
    run_id = str(kwargs.get("run_id") or "lan-pilot-e2e-001")
    event_count = int(kwargs.get("event_count") or 1)
    result: dict[str, object] = {
        "transcript": {
            "run_id": run_id,
            "events": [
                {"sequence": sequence}
                for sequence in range(1, event_count + 1)
            ],
            "metrics": {
                "acked": 6,
                "receipt_confirmed": 6,
                "latency_ms": {"p95": 10, "max": 20},
                "completion_latency_ms": {"count": 6},
            },
        },
        "transcript_path": str(output / "witness-core.fixture.json"),
        "observation_receipts": 3,
        "review_receipts": 3,
        "gate_ready": True,
        "read_only_observers": 10,
        "faults": {
            "server_restarts": 1,
            "anvil_restarts": 1,
            "agent_disconnects": 3,
            "agent_disconnect_proofs": [
                {
                    "node": f"0x{index:040x}",
                    "task_id": f"observe:pilot:{index}",
                    "accepted": True,
                    "connection_closed": True,
                    "reconnected": True,
                }
                for index in range(1, 4)
            ],
            "receipt_ack_loss_proofs": [
                {
                    "node": f"0x{index:040x}",
                    "task_id": f"observe:pilot:{index}",
                    "receipt_stored": True,
                    "confirmation_ack_dropped": True,
                    "receipt_state_recovered": True,
                    "receipt_confirmed": True,
                }
                for index in range(1, 4)
            ],
            "recovery_seconds": 1.0,
        },
        "snapshot_restore_verified": True,
    }
    if kwargs.get("core_transcript_version") == 2:
        result["transcript"]["acceptance"] = {
            "duration_seconds": 900,
            "event_count": 30,
            "observer_count": 10,
            "restart_verified": True,
            "reconnect_verified": True,
            "cleanup_verified": False,
            "secret_findings": 0,
            "stderr_empty": False,
        }
        result.update(
            {
                "acceptance_verified": False,
                "offline_verification": "standalone_unverified",
                "integrity_verification": "offline_integrity",
                "acceptance_evidence": {
                    "verification_scope": "standalone_process",
                    "reason": "external_process_evidence_required",
                    "pilot_write_token_scan_completed": True,
                    "pilot_write_token_findings": 0,
                    "chain_process_exited": True,
                    "complete_process_tree_cleanup_verified": False,
                    "outer_process_stderr_observed": False,
                },
            }
        )
    return result


def _valid_core_verification(transcript: dict[str, object]) -> dict[str, object]:
    return {
        "valid": True,
        "verification_level": "offline_integrity",
        "chain_verified": False,
        "trust_bound": False,
        "run_id": transcript["run_id"],
        "event_count": len(transcript["events"]),
        "observation_receipts": 3,
        "review_receipts": 3,
        "gate_ready": True,
        "participant_claims_verified": True,
        "nodes": 3,
        "declared_operator_groups": 2,
        "declared_network_groups": 2,
    }


def test_pilot_soak_reports_runtime_tree_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sampler = _install_memory_stubs(monkeypatch)
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", _successful_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", _valid_core_verification
    )
    report = pilot_soak.run_pilot_soak(
        tmp_path,
        duration_seconds=1,
        event_count=1,
        observers=10,
        run_id="background-run-123",
    )

    assert sampler.started is True
    assert sampler.stopped is True
    assert report["passed"] is True
    assert report["run_id"] == "background-run-123"
    assert set(report["checks"]) == SOAK_SUCCESS_CHECK_KEYS
    assert report["peak_rss_bytes"] == 123
    assert report["peak_rss_bytes_scope"] == "root_process_os_peak"
    assert report["memory"] == {
        "root_process_peak_rss_bytes": 123,
        "root_process_metric": "peak_working_set"
        if pilot_soak.os.name == "nt"
        else "vm_hwm",
        "runtime_tree_sampled_peak_rss_bytes": 456,
        "runtime_tree_peak_processes": [
            {"pid": 101, "name": "python", "rss_bytes": 456}
        ],
        "runtime_tree_sample_count": 4,
        "runtime_tree_max_process_count": 5,
        "runtime_tree_required_min_process_count": 4,
        "runtime_tree_sample_interval_seconds": 0.5,
        "runtime_tree_sample_error_count": 0,
        "runtime_tree_available": True,
    }
    assert report["checks"]["root_process_memory_under_512mb"] is True
    assert report["checks"]["memory_under_512mb"] is True
    assert report["checks"]["runtime_tree_memory_under_512mb"] is True
    assert report["checks"]["runtime_tree_sampling_observed"] is True
    assert report["checks"]["offline_transcript_valid"] is True
    assert report["checks"]["transcript_run_id_bound"] is True
    assert report["checks"]["workflow_evidence_complete"] is True
    written = json.loads(
        (tmp_path / "pilot-soak-report.json").read_text(encoding="utf-8")
    )
    assert written == report


def test_v2_standalone_evidence_requires_explicit_abstention() -> None:
    acceptance = {
        "duration_seconds": 900,
        "event_count": 30,
        "observer_count": 10,
        "restart_verified": True,
        "reconnect_verified": True,
        "cleanup_verified": False,
        "secret_findings": 0,
        "stderr_empty": False,
    }
    transcript = {"acceptance": acceptance}
    result = {
        "acceptance_verified": False,
        "offline_verification": "standalone_unverified",
        "integrity_verification": "offline_integrity",
        "acceptance_evidence": {
            "verification_scope": "standalone_process",
            "reason": "external_process_evidence_required",
            "pilot_write_token_scan_completed": True,
            "pilot_write_token_findings": 0,
            "chain_process_exited": True,
            "complete_process_tree_cleanup_verified": False,
            "outer_process_stderr_observed": False,
        },
    }

    assert pilot_soak._v2_standalone_evidence_is_honest(
        transcript, result, []
    ) is True

    acceptance["cleanup_verified"] = True
    assert pilot_soak._v2_standalone_evidence_is_honest(
        transcript, result, []
    ) is False
    acceptance["cleanup_verified"] = False

    acceptance["stderr_empty"] = True
    assert pilot_soak._v2_standalone_evidence_is_honest(
        transcript, result, []
    ) is False


def test_v2_standalone_evidence_binds_actual_secret_findings() -> None:
    transcript = {
        "acceptance": {
            "duration_seconds": 900,
            "event_count": 30,
            "observer_count": 10,
            "restart_verified": True,
            "reconnect_verified": True,
            "cleanup_verified": False,
            "secret_findings": 0,
            "stderr_empty": False,
        }
    }
    result = {
        "acceptance_verified": False,
        "offline_verification": "standalone_unverified",
        "integrity_verification": "offline_integrity",
        "acceptance_evidence": {
            "verification_scope": "standalone_process",
            "reason": "external_process_evidence_required",
            "pilot_write_token_scan_completed": True,
            "pilot_write_token_findings": 0,
            "complete_process_tree_cleanup_verified": False,
            "outer_process_stderr_observed": False,
        },
    }

    assert pilot_soak._v2_standalone_evidence_is_honest(
        transcript, result, ["leak.txt"]
    ) is False


def test_v2_soak_accepts_honest_standalone_abstention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_memory_stubs(monkeypatch)
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", _successful_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", _valid_core_verification
    )
    monotonic_values = iter((100.0, 1_000.0))
    monkeypatch.setattr(
        pilot_soak.time, "monotonic", lambda: next(monotonic_values)
    )

    report = pilot_soak.run_pilot_soak(
        tmp_path,
        duration_seconds=900,
        event_count=30,
        observers=10,
        run_id="v2-standalone-test",
        core_transcript_version=2,
    )

    assert report["passed"] is True
    assert report["checks"]["v2_participant_claims"] is True
    assert report["checks"]["v2_standalone_evidence_honest"] is True
    assert report["checks"]["v2_wall_clock_duration_met"] is True
    assert report["acceptance_evidence"][
        "complete_process_tree_cleanup_verified"
    ] is False
    assert report["acceptance_evidence"]["outer_process_stderr_observed"] is False


def test_v2_soak_rejects_short_wall_clock_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_memory_stubs(monkeypatch)
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", _successful_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", _valid_core_verification
    )
    monotonic_values = iter((100.0, 999.0))
    monkeypatch.setattr(
        pilot_soak.time, "monotonic", lambda: next(monotonic_values)
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_soak.run_pilot_soak(
            tmp_path,
            duration_seconds=900,
            event_count=30,
            observers=10,
            run_id="v2-short-wall-clock-test",
            core_transcript_version=2,
        )

    assert error.value.code == "pilot_soak_failed"
    report = json.loads(
        (tmp_path / "pilot-soak-report.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    assert report["checks"]["v2_wall_clock_duration_met"] is False


def test_pilot_soak_failure_writes_machine_readable_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sampler = _install_memory_stubs(monkeypatch)

    def fail_demo(output: Path, **_: object) -> dict[str, object]:
        raise LoveEngineError(
            "review_receipt_rejected", "test-write-token", 4
        )

    monkeypatch.setattr(pilot_soak, "run_pilot_demo", fail_demo)

    with pytest.raises(LoveEngineError) as error:
        pilot_soak.run_pilot_soak(
            tmp_path, duration_seconds=1, event_count=1, observers=10
        )

    assert error.value.code == "review_receipt_rejected"
    assert sampler.started is True
    assert sampler.stopped is True
    report = json.loads(
        (tmp_path / "pilot-soak-report.json").read_text(encoding="utf-8")
    )
    assert report["schema_version"] == "loveengine.pilot-soak-report/1"
    assert report["passed"] is False
    assert report["failure"]["code"] == "review_receipt_rejected"
    assert "message" not in report["failure"]
    assert "test-write-token" not in json.dumps(report)
    assert report["checks"]["run_completed"] is False
    assert report["checks"]["memory_under_512mb"] is True
    assert report["checks"]["runtime_tree_memory_under_512mb"] is True
    assert report["memory"]["runtime_tree_sampled_peak_rss_bytes"] == 456
    assert report["transcript_path"] is None


def test_completed_but_failed_gate_has_a_stable_failure_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sampler = _install_memory_stubs(monkeypatch)
    sampler._metrics["sampled_peak_rss_bytes"] = 512 * 1024 * 1024
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", _successful_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", _valid_core_verification
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_soak.run_pilot_soak(
            tmp_path, duration_seconds=1, event_count=1, observers=10
        )

    assert error.value.code == "pilot_soak_failed"
    report = json.loads(
        (tmp_path / "pilot-soak-report.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    assert report["failure"] == {
        "code": "pilot_soak_failed",
        "exception_type": "LoveEngineError",
    }
    assert report["checks"]["runtime_tree_memory_under_512mb"] is False


def test_pilot_soak_rejects_transcript_from_another_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_memory_stubs(monkeypatch)

    def wrong_run_demo(output: Path, **kwargs: object) -> dict[str, object]:
        result = _successful_demo(output, **kwargs)
        result["transcript"]["run_id"] = "different-run"
        return result

    monkeypatch.setattr(pilot_soak, "run_pilot_demo", wrong_run_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", _valid_core_verification
    )
    with pytest.raises(LoveEngineError) as error:
        pilot_soak.run_pilot_soak(
            tmp_path,
            duration_seconds=1,
            event_count=1,
            observers=10,
            run_id="background-run-123",
        )

    assert error.value.code == "pilot_soak_run_id_mismatch"


def test_pilot_soak_rejects_verifier_run_id_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_memory_stubs(monkeypatch)
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", _successful_demo)
    def wrong_verification(transcript: dict[str, object]) -> dict[str, object]:
        value = _valid_core_verification(transcript)
        value["run_id"] = "different-run"
        return value

    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", wrong_verification
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_soak.run_pilot_soak(
            tmp_path,
            duration_seconds=1,
            event_count=1,
            observers=10,
            run_id="background-run-123",
        )

    assert error.value.code == "pilot_soak_run_id_mismatch"


def test_foreground_soak_generates_and_binds_a_unique_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_memory_stubs(monkeypatch)
    captured: dict[str, object] = {}

    def capture_demo(output: Path, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return _successful_demo(output, **kwargs)

    monkeypatch.setenv("LOVEENGINE_PILOT_SOAK_RUN_ID", "stale-shell-run")
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", capture_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", _valid_core_verification
    )

    report = pilot_soak.run_pilot_soak(
        tmp_path, duration_seconds=1, event_count=1, observers=10
    )

    assert isinstance(report["run_id"], str)
    assert report["run_id"]
    assert report["run_id"] != "stale-shell-run"
    assert captured["run_id"] == report["run_id"]
    assert report["checks"]["transcript_run_id_bound"] is True


def test_failure_report_survives_diagnostic_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BrokenSampler:
        def start(self) -> None:
            pass

        def stop(self) -> dict[str, object]:
            raise RuntimeError("sampler unavailable")

    def fail_demo(output: Path, **_: object) -> dict[str, object]:
        raise LoveEngineError("review_receipt_rejected", "test-write-token", 4)

    monkeypatch.setattr(
        pilot_soak, "_runtime_tree_metrics", lambda root_pid: BrokenSampler()
    )
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", fail_demo)
    monkeypatch.setattr(
        pilot_soak,
        "_root_process_peak_rss_bytes",
        lambda: (_ for _ in ()).throw(RuntimeError("root metric unavailable")),
    )
    monkeypatch.setattr(
        pilot_soak,
        "_disk_bytes",
        lambda output: (_ for _ in ()).throw(RuntimeError("disk unavailable")),
    )
    monkeypatch.setattr(
        pilot_soak,
        "_scan_token_leak",
        lambda output, token: (_ for _ in ()).throw(RuntimeError("scan unavailable")),
    )

    with pytest.raises(LoveEngineError) as error:
        pilot_soak.run_pilot_soak(
            tmp_path, duration_seconds=1, event_count=1, observers=10
        )

    assert error.value.code == "review_receipt_rejected"
    report = json.loads(
        (tmp_path / "pilot-soak-report.json").read_text(encoding="utf-8")
    )
    assert report["passed"] is False
    assert report["failure"]["code"] == "review_receipt_rejected"
    assert report["checks"]["run_completed"] is False
    assert report["checks"]["runtime_tree_memory_under_512mb"] is False
    assert report["checks"]["secret_scan_zero"] is False
    assert "test-write-token" not in json.dumps(report)


class _FakePsutilError(Exception):
    pass


class _FakeNoSuchProcess(_FakePsutilError):
    pass


class _FakeProcess:
    def __init__(
        self,
        pid: int,
        rss_values: list[int],
        *,
        name: str | None = None,
        name_error: BaseException | None = None,
    ) -> None:
        self.pid = pid
        self._rss_values = iter(rss_values)
        self._name = name
        self._name_error = name_error
        self._children: list[_FakeProcess] = []

    def children(self, *, recursive: bool) -> list[_FakeProcess]:
        assert recursive is True
        return list(self._children)

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=next(self._rss_values))

    def name(self) -> str | None:
        if self._name_error is not None:
            raise self._name_error
        return self._name


def test_runtime_tree_sampler_peaks_current_tree_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _FakeProcess(1, [100, 400], name="loveengine")
    child = _FakeProcess(2, [200, 100], name="anvil")
    # The duplicate models a process API returning the same descendant twice.
    root._children = [child, child]
    monkeypatch.setattr(
        pilot_soak,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: root,
            Error=_FakePsutilError,
            NoSuchProcess=_FakeNoSuchProcess,
        ),
    )
    sampler = pilot_soak._RuntimeTreeSampler(1, interval_seconds=0.5)

    sampler._sample()
    sampler._sample()

    metrics = sampler.metrics()
    assert metrics["available"] is True
    assert metrics["sampled_peak_rss_bytes"] == 500
    assert metrics["sampled_peak_processes"] == [
        {"pid": 1, "name": "loveengine", "rss_bytes": 400},
        {"pid": 2, "name": "anvil", "rss_bytes": 100},
    ]
    assert metrics["sample_count"] == 2
    assert metrics["max_process_count"] == 2
    assert metrics["sample_error_count"] == 0
    memory = pilot_soak._memory_report(metrics, 123)
    assert pilot_soak._memory_checks(memory)[
        "runtime_tree_sampling_observed"
    ] is False


def test_runtime_tree_sampler_keeps_first_peak_composition_and_handles_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _FakeProcess(1, [100, 50], name=None)
    child = _FakeProcess(
        2,
        [300, 350],
        name_error=RuntimeError("name unavailable"),
    )
    root._children = [child]
    monkeypatch.setattr(
        pilot_soak,
        "psutil",
        SimpleNamespace(
            Process=lambda pid: root,
            Error=_FakePsutilError,
            NoSuchProcess=_FakeNoSuchProcess,
        ),
    )
    sampler = pilot_soak._RuntimeTreeSampler(1, interval_seconds=0.5)

    sampler._sample()
    sampler._sample()

    metrics = sampler.metrics()
    assert metrics["sampled_peak_rss_bytes"] == 400
    assert metrics["sampled_peak_processes"] == [
        {"pid": 2, "name": None, "rss_bytes": 300},
        {"pid": 1, "name": None, "rss_bytes": 100},
    ]
    # Name lookup is diagnostic only; the existing fail-closed RSS sampling
    # behavior is unchanged when the capacity measurement succeeded.
    assert metrics["sample_error_count"] == 0


def test_runtime_tree_memory_check_fails_closed_when_sampling_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable_process(pid: int) -> _FakeProcess:
        raise _FakePsutilError()

    monkeypatch.setattr(
        pilot_soak,
        "psutil",
        SimpleNamespace(
            Process=unavailable_process,
            Error=_FakePsutilError,
            NoSuchProcess=_FakeNoSuchProcess,
        ),
    )
    sampler = pilot_soak._RuntimeTreeSampler(1, interval_seconds=0.5)

    sampler._sample()

    memory = pilot_soak._memory_report(sampler.metrics(), 123)
    assert memory["runtime_tree_available"] is False
    assert memory["runtime_tree_sampled_peak_rss_bytes"] is None
    assert memory["runtime_tree_peak_processes"] is None
    assert memory["runtime_tree_sample_error_count"] == 1
    checks = pilot_soak._memory_checks(memory)
    assert checks["memory_under_512mb"] is True
    assert checks["runtime_tree_memory_under_512mb"] is False
    assert checks["runtime_tree_sampling_observed"] is False
