from __future__ import annotations

import json
from pathlib import Path

import pytest

import loveengine_witness.pilot_soak_process as pilot_soak_process
from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_soak import SOAK_SUCCESS_CHECK_KEYS
from loveengine_witness.pilot_soak_process import (
    background_soak_status,
    start_background_soak,
)


class FakeProcess:
    pid = 43210


def _passing_checks() -> dict[str, bool]:
    return {key: True for key in SOAK_SUCCESS_CHECK_KEYS}


def _passing_report(started: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "loveengine.pilot-soak-report/1",
        "passed": True,
        "stage": "core",
        "event_count": 12,
        "observer_count": 10,
        "requested_duration_seconds": 60,
        "elapsed_seconds": 60,
        "run_id": started["run_id"],
        "checks": _passing_checks(),
        "failure": None,
        "secret_leaks": [],
        "transcript_path": str(
            Path(str(started["output"])) / "witness-core.fixture.json"
        ),
    }


def _install_valid_core_transcript(
    tmp_path: Path,
    started: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    *,
    run_id: str | None = None,
) -> None:
    transcript_path = tmp_path / "witness-core.fixture.json"
    transcript_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        pilot_soak_process,
        "verify_core_transcript",
        lambda transcript: {
            "valid": True,
            "verification_level": "offline_integrity",
            "chain_verified": False,
            "trust_bound": False,
            "run_id": run_id or started["run_id"],
            "event_count": 12,
            "observation_receipts": 3,
            "review_receipts": 3,
            "gate_ready": True,
        },
    )


def _mark_planned_duration_elapsed(
    started: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        pilot_soak_process.time,
        "time",
        lambda: float(started["planned_end_epoch"]) + 1.0,
    )

def test_background_soak_writes_queryable_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen", fake_popen
    )
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process._process_alive",
        lambda pid: pid == FakeProcess.pid,
    )

    started = start_background_soak(
        tmp_path,
        duration_seconds=3600,
        event_count=60,
        observers=10,
    )

    state_path = Path(started["state_path"])
    assert started["status"] == "running"
    assert started["process_alive"] is True
    assert state_path.is_file()
    assert "--background" not in captured["command"]
    assert "--duration-seconds" in captured["command"]
    worker_run_id = captured["command"].index("--worker-run-id")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pid"] == FakeProcess.pid
    assert state["duration_seconds"] == 3600
    assert state["run_id"] == captured["command"][worker_run_id + 1]
    assert "env" not in captured["kwargs"]
    assert "token" not in state_path.read_text(encoding="utf-8").lower()

    status = background_soak_status(state_path)
    assert status["status"] == "running"
    assert 0 <= status["progress_percent"] <= 100


def test_background_soak_status_uses_completed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    _install_valid_core_transcript(tmp_path, started, monkeypatch)
    _mark_planned_duration_elapsed(started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "passed"
    assert status["process_alive"] is False
    assert "failure_reason" not in status
    assert status["report"]["passed"] is True
    assert status["progress_percent"] == 100


def test_background_soak_rejects_early_passing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["elapsed_seconds"] = 1
    _install_valid_core_transcript(tmp_path, started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["report_validation_error"] == "elapsed_duration_too_short"


def test_background_soak_rejects_report_before_planned_wall_clock_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    _install_valid_core_transcript(tmp_path, started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(_passing_report(started)), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["report_validation_error"] == "wall_clock_duration_too_short"


def test_background_soak_rejects_incomplete_passing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["checks"]["zero_event_loss"] = False
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"
    assert status["report_validation_error"] == "checks_not_all_true"


def test_background_soak_rejects_minimal_passing_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["checks"] = {"complete": True}
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"
    assert status["report_validation_error"] == "checks_incomplete"


def test_background_soak_rejects_report_for_a_different_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["stage"] = "governance"
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report),
        encoding="utf-8",
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"
    assert status["report_validation_error"] == "stage_mismatch"


def test_background_soak_status_requires_matching_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["run_id"] = "different-run"
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"
    assert status["report_validation_error"] == "run_id_mismatch"


@pytest.mark.parametrize(
    ("state_has_run_id", "state_run_id"),
    [
        pytest.param(False, None, id="missing"),
        pytest.param(True, None, id="null"),
        pytest.param(True, "", id="empty"),
        pytest.param(True, "   ", id="blank"),
        pytest.param(True, 123, id="wrong-type"),
    ],
)
def test_background_soak_status_requires_a_valid_state_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_has_run_id: bool,
    state_run_id: object,
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    _install_valid_core_transcript(tmp_path, started, monkeypatch)
    _mark_planned_duration_elapsed(started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(_passing_report(started)), encoding="utf-8"
    )
    state_path = Path(started["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state_has_run_id:
        state["run_id"] = state_run_id
    else:
        state.pop("run_id")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    status = background_soak_status(state_path)

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"
    assert status["report_validation_error"] == "state_run_id_invalid"


@pytest.mark.parametrize("report_run_id", [None, "", "   ", 123])
def test_background_soak_status_requires_a_valid_report_run_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_run_id: object,
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    _install_valid_core_transcript(tmp_path, started, monkeypatch)
    _mark_planned_duration_elapsed(started, monkeypatch)
    report = _passing_report(started)
    report["run_id"] = report_run_id
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"
    assert status["report_validation_error"] == "report_run_id_invalid"


def test_background_soak_status_waits_for_child_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process._process_alive", lambda pid: True
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    _install_valid_core_transcript(tmp_path, started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(_passing_report(started)), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "running"
    assert status["process_alive"] is True
    assert status["progress_percent"] < 100


def test_background_soak_rejects_passing_report_with_secret_findings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["secret_leaks"] = ["unexpected-token.txt"]
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["report_validation_error"] == "secret_leaks_detected"


def test_background_soak_rejects_missing_secret_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report.pop("secret_leaks")
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["report_validation_error"] == "secret_leaks_missing"


def test_background_soak_rejects_missing_or_mismatched_transcript(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report.pop("transcript_path")
    _mark_planned_duration_elapsed(started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    missing = background_soak_status(Path(started["state_path"]))

    assert missing["status"] == "failed"
    assert missing["report_validation_error"] == "transcript_path_missing"

    report = _passing_report(started)
    _install_valid_core_transcript(
        tmp_path, started, monkeypatch, run_id="different-run"
    )
    _mark_planned_duration_elapsed(started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    mismatched = background_soak_status(Path(started["state_path"]))

    assert mismatched["status"] == "failed"
    assert mismatched["report_validation_error"] == "transcript_run_id_mismatch"


def test_background_soak_rejects_transcript_outside_its_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    report = _passing_report(started)
    report["transcript_path"] = str(tmp_path.parent / "foreign-transcript.json")
    _mark_planned_duration_elapsed(started, monkeypatch)
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["report_validation_error"] == "transcript_path_outside_output"


def test_background_soak_rejects_orphaned_report_before_launch(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "pilot-soak-report.json"
    report_path.write_text("{}", encoding="utf-8")

    with pytest.raises(LoveEngineError) as error:
        start_background_soak(
            tmp_path,
            duration_seconds=60,
            event_count=12,
            observers=10,
        )

    assert error.value.code == "pilot_soak_report_exists"


def test_background_soak_rejects_second_live_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process._process_alive", lambda pid: True
    )
    start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )

    with pytest.raises(LoveEngineError) as error:
        start_background_soak(
            tmp_path,
            duration_seconds=60,
            event_count=12,
            observers=10,
        )

    assert error.value.code == "pilot_soak_already_running"


def test_background_soak_status_marks_dead_process_without_report_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process._process_alive", lambda pid: False
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["process_alive"] is False
    assert status["failure_reason"] == "process_exited_without_report"
    assert status["report"] is None
    assert status["stderr_path"].endswith("pilot-soak.stderr.log")


def test_background_soak_status_names_failed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "loveengine_witness.pilot_soak_process.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    started = start_background_soak(
        tmp_path,
        duration_seconds=60,
        event_count=12,
        observers=10,
    )
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps({"passed": False}), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "failed"
    assert status["failure_reason"] == "soak_report_failed"


def test_background_soak_status_names_launch_failure(tmp_path: Path) -> None:
    state_path = tmp_path / "pilot-soak-run.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": "loveengine.pilot-soak-run/1",
                "status": "failed",
                "pid": None,
                "launch_error": "OSError",
                "started_at_epoch": 0,
                "duration_seconds": 60,
                "output": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )

    status = background_soak_status(state_path)

    assert status["status"] == "failed"
    assert status["process_alive"] is False
    assert status["failure_reason"] == "launch_failed"
