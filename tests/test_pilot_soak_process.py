from __future__ import annotations

import json
from pathlib import Path

import pytest

from loveengine_witness.errors import LoveEngineError
from loveengine_witness.pilot_soak_process import (
    background_soak_status,
    start_background_soak,
)


class FakeProcess:
    pid = 43210


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
    assert state_path.is_file()
    assert "--background" not in captured["command"]
    assert "--duration-seconds" in captured["command"]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["pid"] == FakeProcess.pid
    assert state["duration_seconds"] == 3600
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
    report = {
        "schema_version": "loveengine.pilot-soak-report/1",
        "passed": True,
    }
    (tmp_path / "pilot-soak-report.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    status = background_soak_status(Path(started["state_path"]))

    assert status["status"] == "passed"
    assert status["report"]["passed"] is True
    assert status["progress_percent"] == 100


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
    assert status["report"] is None
    assert status["stderr_path"].endswith("pilot-soak.stderr.log")
