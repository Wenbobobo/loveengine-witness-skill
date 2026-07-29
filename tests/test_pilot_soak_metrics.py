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


def _successful_demo(output: Path, **_: object) -> dict[str, object]:
    (output / "operator.token").write_text("test-write-token", encoding="utf-8")
    return {
        "transcript": {
            "events": [{"sequence": 1}],
            "metrics": {
                "latency_ms": {"p95": 10, "max": 20},
                "completion_latency_ms": {"count": 6},
            },
        },
        "transcript_path": str(output / "witness-core.fixture.json"),
        "observation_receipts": 3,
        "read_only_observers": 10,
        "faults": {
            "server_restarts": 1,
            "anvil_restarts": 1,
            "agent_disconnects": 3,
            "recovery_seconds": 1.0,
        },
    }


def test_pilot_soak_reports_runtime_tree_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sampler = _install_memory_stubs(monkeypatch)
    monkeypatch.setattr(pilot_soak, "run_pilot_demo", _successful_demo)
    monkeypatch.setattr(
        pilot_soak, "verify_core_transcript", lambda transcript: {"valid": True}
    )

    report = pilot_soak.run_pilot_soak(
        tmp_path, duration_seconds=1, event_count=1, observers=10
    )

    assert sampler.started is True
    assert sampler.stopped is True
    assert report["passed"] is True
    assert set(report["checks"]) == SOAK_SUCCESS_CHECK_KEYS
    assert report["peak_rss_bytes"] == 123
    assert report["peak_rss_bytes_scope"] == "root_process_os_peak"
    assert report["memory"] == {
        "root_process_peak_rss_bytes": 123,
        "root_process_metric": "peak_working_set"
        if pilot_soak.os.name == "nt"
        else "vm_hwm",
        "runtime_tree_sampled_peak_rss_bytes": 456,
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
    written = json.loads(
        (tmp_path / "pilot-soak-report.json").read_text(encoding="utf-8")
    )
    assert written == report


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
        pilot_soak, "verify_core_transcript", lambda transcript: {"valid": True}
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
    def __init__(self, pid: int, rss_values: list[int]) -> None:
        self.pid = pid
        self._rss_values = iter(rss_values)
        self._children: list[_FakeProcess] = []

    def children(self, *, recursive: bool) -> list[_FakeProcess]:
        assert recursive is True
        return list(self._children)

    def memory_info(self) -> SimpleNamespace:
        return SimpleNamespace(rss=next(self._rss_values))


def test_runtime_tree_sampler_peaks_current_tree_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _FakeProcess(1, [100, 400])
    child = _FakeProcess(2, [200, 100])
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
    assert metrics["sample_count"] == 2
    assert metrics["max_process_count"] == 2
    assert metrics["sample_error_count"] == 0
    memory = pilot_soak._memory_report(metrics, 123)
    assert pilot_soak._memory_checks(memory)[
        "runtime_tree_sampling_observed"
    ] is False


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
    assert memory["runtime_tree_sample_error_count"] == 1
    checks = pilot_soak._memory_checks(memory)
    assert checks["memory_under_512mb"] is True
    assert checks["runtime_tree_memory_under_512mb"] is False
    assert checks["runtime_tree_sampling_observed"] is False
