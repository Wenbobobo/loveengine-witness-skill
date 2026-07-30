"""Detached pilot soak lifecycle and observable status."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from math import isfinite
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .core_transcript import verify_core_transcript
from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .pilot_soak import SOAK_SUCCESS_CHECK_KEYS, validate_pilot_soak_args
from .pilot_transcript import verify_pilot_transcript


STATE_FILENAME = "pilot-soak-run.json"
STDOUT_FILENAME = "pilot-soak.stdout.jsonl"
STDERR_FILENAME = "pilot-soak.stderr.log"
REPORT_FILENAME = "pilot-soak-report.json"
REPORT_SCHEMA_VERSION = "loveengine.pilot-soak-report/1"
REPORT_DURATION_TOLERANCE_SECONDS = 1.0


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(
            process_query_limited_information, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _verified_report_transcript(
    report: dict[str, Any], state: dict[str, Any], output: Path
) -> str | None:
    """Verify the report-owned transcript without using RPC or a trust policy."""

    raw_path = report.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path:
        return "transcript_path_missing"
    try:
        transcript_path = Path(raw_path)
        if not transcript_path.is_absolute():
            return "transcript_path_not_absolute"
        transcript_path = transcript_path.resolve()
        transcript_path.relative_to(output)
    except (OSError, RuntimeError, ValueError):
        return "transcript_path_outside_output"
    if not transcript_path.is_file():
        return "transcript_missing"
    try:
        transcript = read_json(transcript_path)
        if not isinstance(transcript, dict):
            return "transcript_not_object"
        verification = (
            verify_core_transcript(transcript)
            if state.get("stage") == "core"
            else verify_pilot_transcript(transcript)
        )
    except (
        KeyError,
        LoveEngineError,
        OSError,
        RuntimeError,
        TypeError,
        UnicodeError,
        ValueError,
    ):
        return "transcript_invalid"
    if not isinstance(verification, dict):
        return "transcript_not_offline_valid"
    if (
        verification.get("valid") is not True
        or verification.get("verification_level") != "offline_integrity"
        or verification.get("chain_verified") is not False
        or verification.get("trust_bound") is not False
    ):
        return "transcript_not_offline_valid"
    if verification.get("run_id") != report.get("run_id"):
        return "transcript_run_id_mismatch"
    if verification.get("event_count") != report.get("event_count"):
        return "transcript_event_count_mismatch"
    if state.get("stage") == "core" and (
        verification.get("observation_receipts") != 3
        or verification.get("review_receipts") != 3
        or verification.get("gate_ready") is not True
    ):
        return "transcript_core_evidence_incomplete"
    return None


def _passed_report_issue(
    report: dict[str, Any], state: dict[str, Any], output: Path
) -> str | None:
    """Return a stable reason when a report cannot prove a completed soak."""

    if report.get("schema_version") != REPORT_SCHEMA_VERSION:
        return "schema_version"
    if state.get("stage") not in {"core", "governance"}:
        return "stage_invalid"
    if report.get("stage") != state.get("stage"):
        return "stage_mismatch"
    if report.get("event_count") != state.get("event_count"):
        return "event_count_mismatch"
    if report.get("observer_count") != state.get("observer_count"):
        return "observer_count_mismatch"
    run_id = state.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        return "state_run_id_invalid"
    report_run_id = report.get("run_id")
    if not isinstance(report_run_id, str) or not report_run_id.strip():
        return "report_run_id_invalid"
    if report_run_id != run_id:
        return "run_id_mismatch"
    requested_duration = report.get("requested_duration_seconds")
    if (
        isinstance(requested_duration, bool)
        or not isinstance(requested_duration, (int, float))
        or float(requested_duration) != float(state["duration_seconds"])
    ):
        return "duration_mismatch"
    checks = report.get("checks")
    if not isinstance(checks, dict) or not checks:
        return "checks_missing"
    if not SOAK_SUCCESS_CHECK_KEYS.issubset(checks):
        return "checks_incomplete"
    if any(value is not True for value in checks.values()):
        return "checks_not_all_true"
    if report.get("failure") is not None:
        return "passed_report_has_failure"
    secret_leaks = report.get("secret_leaks")
    if not isinstance(secret_leaks, list):
        return "secret_leaks_missing"
    if secret_leaks:
        return "secret_leaks_detected"
    reported_elapsed = report.get("elapsed_seconds")
    requested_duration = float(state["duration_seconds"])
    if (
        isinstance(reported_elapsed, bool)
        or not isinstance(reported_elapsed, (int, float))
        or not isfinite(float(reported_elapsed))
        or float(reported_elapsed)
        < requested_duration - REPORT_DURATION_TOLERANCE_SECONDS
    ):
        return "elapsed_duration_too_short"
    planned_end = state.get("planned_end_epoch")
    if (
        isinstance(planned_end, bool)
        or not isinstance(planned_end, (int, float))
        or not isfinite(float(planned_end))
    ):
        return "planned_end_missing"
    if time.time() < float(planned_end) - REPORT_DURATION_TOLERANCE_SECONDS:
        return "wall_clock_duration_too_short"
    return _verified_report_transcript(report, state, output)


def background_soak_status(state_path: Path) -> dict[str, Any]:
    state_path = Path(state_path).resolve()
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("schema_version") != (
        "loveengine.pilot-soak-run/1"
    ):
        raise LoveEngineError("invalid_soak_state", str(state_path))
    output = Path(str(state["output"])).resolve()
    report_path = output / REPORT_FILENAME
    report: Any = None
    report_error: str | None = None
    if report_path.is_file():
        try:
            report = read_json(report_path)
        except LoveEngineError as error:
            report_error = error.code
        if report_error is None and not isinstance(report, dict):
            report_error = "report_not_object"
    pid = int(state.get("pid") or 0)
    process_alive = _process_alive(pid)
    failure_reason: str | None = None
    if isinstance(report, dict):
        if report.get("passed") is True:
            if process_alive:
                # A child can write its report just before its process exits.
                # Do not accept it until the recorded process has actually ended.
                status = "running"
            else:
                report_error = _passed_report_issue(report, state, output)
                status = "failed" if report_error is not None else "passed"
        else:
            status = "failed"
        if status == "failed":
            failure_reason = "soak_report_failed"
    elif report_error is not None:
        status = "failed"
        failure_reason = "soak_report_failed"
    elif process_alive:
        status = "running"
    else:
        status = "failed"
        failure_reason = (
            "launch_failed"
            if state.get("launch_error")
            else "process_exited_without_report"
        )
    duration = float(state["duration_seconds"])
    elapsed = max(0.0, time.time() - float(state["started_at_epoch"]))
    progress = (
        100.0
        if status == "passed"
        else min(99.9, elapsed / duration * 100)
    )
    result = {
        **state,
        "state_path": str(state_path),
        "status": status,
        "process_alive": process_alive,
        "checked_at": _utc_now(),
        "elapsed_seconds": round(elapsed, 3),
        "remaining_seconds": round(max(0.0, duration - elapsed), 3),
        "progress_percent": round(progress, 2),
        "report": report,
    }
    if failure_reason is not None:
        result["failure_reason"] = failure_reason
    if report_error is not None:
        result["report_validation_error"] = report_error
    return result


def start_background_soak(
    output: Path,
    *,
    duration_seconds: float,
    event_count: int,
    observers: int,
    stage: str = "core",
) -> dict[str, Any]:
    validate_pilot_soak_args(
        duration_seconds, event_count, observers, stage
    )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / STATE_FILENAME
    report_path = output / REPORT_FILENAME
    if state_path.is_file():
        existing = background_soak_status(state_path)
        if existing["status"] == "running":
            raise LoveEngineError(
                "pilot_soak_already_running", str(state_path), 4
            )
        raise LoveEngineError("pilot_soak_state_exists", str(state_path), 3)
    if report_path.exists():
        raise LoveEngineError("pilot_soak_report_exists", str(report_path), 3)

    stdout_path = output / STDOUT_FILENAME
    stderr_path = output / STDERR_FILENAME
    started_at_epoch = time.time()
    run_id = uuid.uuid4().hex
    command = [
        sys.executable,
        "-m",
        "loveengine_witness.cli",
        "pilot",
        "soak",
        "--duration-seconds",
        str(duration_seconds),
        "--events",
        str(event_count),
        "--observers",
        str(observers),
        "--stage",
        stage,
        "--output",
        str(output),
        "--worker-run-id",
        run_id,
    ]
    state: dict[str, Any] = {
        "schema_version": "loveengine.pilot-soak-run/1",
        "status": "starting",
        "pid": None,
        "started_at": _utc_now(),
        "started_at_epoch": started_at_epoch,
        "planned_end_epoch": started_at_epoch + duration_seconds,
        "duration_seconds": duration_seconds,
        "event_count": event_count,
        "observer_count": observers,
        "stage": stage,
        "run_id": run_id,
        "output": str(output),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "report_path": str(report_path),
        "package_root": str(Path(__file__).resolve().parents[2]),
    }
    write_json(state_path, state)
    creationflags = 0
    process_kwargs: dict[str, Any] = {
        "cwd": str(Path(__file__).resolve().parents[2]),
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NO_WINDOW
        )
        process_kwargs["creationflags"] = creationflags
    else:
        process_kwargs["start_new_session"] = True
    try:
        with (
            stdout_path.open("ab", buffering=0) as stdout,
            stderr_path.open("ab", buffering=0) as stderr,
        ):
            process = subprocess.Popen(
                command,
                stdout=stdout,
                stderr=stderr,
                **process_kwargs,
            )
    except OSError as exc:
        state["status"] = "failed"
        state["launch_error"] = exc.__class__.__name__
        write_json(state_path, state)
        raise LoveEngineError(
            "pilot_soak_launch_failed", exc.__class__.__name__, 4
        ) from exc
    state["pid"] = process.pid
    state["status"] = "running"
    write_json(state_path, state)
    return background_soak_status(state_path)
