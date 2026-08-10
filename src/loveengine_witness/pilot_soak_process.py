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

import psutil

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
LAUNCH_REGISTRATION_GRACE_SECONDS = 30.0


def _terminate_launched_process(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float = 5.0,
) -> bool:
    """Reap a worker tree that could not be published in lifecycle state."""

    descendants: list[psutil.Process] = []
    tree_available = False
    try:
        root = psutil.Process(process.pid)
        descendants = root.children(recursive=True)
        tree_available = True
    except psutil.NoSuchProcess:
        tree_available = process.poll() is not None
    except psutil.Error:
        tree_available = False

    for child in reversed(descendants):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error:
            tree_available = False
    try:
        process.terminate()
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=timeout_seconds)
        except (OSError, subprocess.SubprocessError):
            pass
    except (OSError, subprocess.SubprocessError):
        pass

    alive: list[psutil.Process] = []
    if descendants:
        try:
            _, alive = psutil.wait_procs(descendants, timeout=timeout_seconds)
        except psutil.Error:
            alive = list(descendants)
            tree_available = False
        for child in alive:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                tree_available = False
        try:
            _, alive = psutil.wait_procs(alive, timeout=timeout_seconds)
        except psutil.Error:
            tree_available = False
    return process.poll() is not None and tree_available and not alive


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_soak_state(state_path: Path, state: dict[str, Any]) -> None:
    """Atomically replace one local lifecycle state record."""

    temporary_path = state_path.with_name(
        f".{state_path.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        write_json(temporary_path, state)
        os.replace(temporary_path, state_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _reserve_initial_soak_state(state_path: Path, state: dict[str, Any]) -> bool:
    """Create the initial lifecycle state exactly once without partial JSON.

    A same-directory hard link gives us an exclusive, atomic publication of a
    fully written state file on both supported local filesystems. Later state
    updates use ``os.replace`` and are not part of the startup race.
    """

    temporary_path = state_path.with_name(
        f".{state_path.name}.{uuid.uuid4().hex}.reserve"
    )
    try:
        write_json(temporary_path, state)
        try:
            os.link(temporary_path, state_path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def _launch_registration_deadline_epoch(state: dict[str, Any]) -> float:
    """Return the bounded lease for the pre-PID launcher window.

    Older state files do not carry the explicit deadline, so they retain the
    same bounded behavior relative to their recorded start time.
    """

    raw_deadline = state.get("launch_registration_deadline_epoch")
    if (
        isinstance(raw_deadline, (int, float))
        and not isinstance(raw_deadline, bool)
        and isfinite(float(raw_deadline))
    ):
        return float(raw_deadline)
    return float(state["started_at_epoch"]) + LAUNCH_REGISTRATION_GRACE_SECONDS


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
    if report.get("core_transcript_version", 1) != state.get(
        "core_transcript_version", 1
    ):
        return "core_transcript_version_mismatch"
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


def _persist_terminal_state(
    state_path: Path,
    state: dict[str, Any],
    *,
    status: str,
    checked_at: str,
    failure_reason: str | None,
    report_error: str | None,
) -> dict[str, Any]:
    """Persist a verified terminal result after the recorded child exits.

    The report and transcript are still revalidated on every status query. The
    persisted fields are a durable lifecycle summary, not a substitute for
    report verification or a trust anchor.
    """

    if status not in {"passed", "failed"}:
        return state
    if (
        state.get("status") == status
        and isinstance(state.get("finished_at"), str)
        and state["finished_at"].strip()
    ):
        return state

    persisted = dict(state)
    persisted["status"] = status
    persisted["finished_at"] = checked_at
    if failure_reason is None:
        persisted.pop("failure_reason", None)
    else:
        persisted["failure_reason"] = failure_reason
    if report_error is None:
        persisted.pop("report_validation_error", None)
    else:
        persisted["report_validation_error"] = report_error

    if persisted == state:
        return state
    _write_soak_state(state_path, persisted)
    return persisted


def background_soak_status(state_path: Path) -> dict[str, Any]:
    state_path = Path(state_path).resolve()
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("schema_version") != (
        "loveengine.pilot-soak-run/1"
    ):
        raise LoveEngineError("invalid_soak_state", str(state_path))
    terminal_failure = (
        state.get("status") == "failed"
        and state.get("failure_reason")
        in {"launch_registration_timeout", "launch_failed"}
        and isinstance(state.get("finished_at"), str)
        and bool(state["finished_at"].strip())
    )
    output = Path(str(state["output"])).resolve()
    report_path = output / REPORT_FILENAME
    pid = int(state.get("pid") or 0)
    process_alive = _process_alive(pid)
    starting_without_pid = (
        state.get("status") == "starting"
        and pid <= 0
        and not state.get("launch_error")
    )
    now_epoch = time.time()
    launch_registration_timed_out = (
        starting_without_pid
        and now_epoch >= _launch_registration_deadline_epoch(state)
    )
    report: Any = None
    report_error: str | None = None
    if not process_alive and not starting_without_pid and report_path.is_file():
        try:
            report = read_json(report_path)
        except LoveEngineError as error:
            report_error = error.code
        if report_error is None and not isinstance(report, dict):
            report_error = "report_not_object"
    failure_reason: str | None = None
    if terminal_failure:
        status = "failed"
        failure_reason = str(state.get("failure_reason") or "soak_report_failed")
        existing_report_error = state.get("report_validation_error")
        report_error = (
            str(existing_report_error)
            if isinstance(existing_report_error, str)
            else None
        )
    elif starting_without_pid and not launch_registration_timed_out:
        status = "starting"
    elif launch_registration_timed_out:
        # The launcher may crash between atomically reserving the output and
        # recording the detached child PID. Do not leave a stale reservation
        # blocking recovery forever, and do not accept an unbound report.
        status = "failed"
        failure_reason = "launch_registration_timeout"
    elif process_alive:
        # A child can expose an incomplete or final report just before it
        # exits. Keep the durable state non-terminal until the PID is gone.
        status = "running"
    elif isinstance(report, dict):
        if report.get("passed") is True:
            report_error = _passed_report_issue(report, state, output)
            status = "failed" if report_error is not None else "passed"
        else:
            status = "failed"
        if status == "failed":
            failure_reason = "soak_report_failed"
    elif report_error is not None:
        status = "failed"
        failure_reason = "soak_report_failed"
    else:
        status = "failed"
        failure_reason = (
            "launch_failed"
            if state.get("launch_error")
            else "process_exited_without_report"
        )
    duration = float(state["duration_seconds"])
    elapsed = max(0.0, now_epoch - float(state["started_at_epoch"]))
    progress = (
        100.0
        if status == "passed"
        else min(99.9, elapsed / duration * 100)
    )
    checked_at = _utc_now()
    if not process_alive:
        state = _persist_terminal_state(
            state_path,
            state,
            status=status,
            checked_at=checked_at,
            failure_reason=failure_reason,
            report_error=report_error,
        )
    result = {
        **state,
        "state_path": str(state_path),
        "status": status,
        "process_alive": process_alive,
        "checked_at": checked_at,
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
    core_transcript_version: int = 1,
) -> dict[str, Any]:
    validate_pilot_soak_args(
        duration_seconds, event_count, observers, stage
    )
    if core_transcript_version not in {1, 2}:
        raise LoveEngineError(
            "unsupported_core_transcript_version", str(core_transcript_version)
        )
    if core_transcript_version == 2 and (
        stage != "core"
        or duration_seconds != 900
        or event_count != 30
        or observers != 10
    ):
        raise LoveEngineError(
            "invalid_v2_acceptance_profile",
            "V2 soak requires core/900 seconds/30 events/10 observers",
        )
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / STATE_FILENAME
    report_path = output / REPORT_FILENAME
    if state_path.is_file():
        existing = background_soak_status(state_path)
        if existing["status"] in {"starting", "running"}:
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
        "--core-transcript-version",
        str(core_transcript_version),
    ]
    state: dict[str, Any] = {
        "schema_version": "loveengine.pilot-soak-run/1",
        "status": "starting",
        "pid": None,
        "started_at": _utc_now(),
        "started_at_epoch": started_at_epoch,
        "launch_registration_deadline_epoch": (
            started_at_epoch + LAUNCH_REGISTRATION_GRACE_SECONDS
        ),
        "planned_end_epoch": started_at_epoch + duration_seconds,
        "duration_seconds": duration_seconds,
        "event_count": event_count,
        "observer_count": observers,
        "stage": stage,
        "core_transcript_version": core_transcript_version,
        "run_id": run_id,
        "output": str(output),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "report_path": str(report_path),
        "package_root": str(Path(__file__).resolve().parents[2]),
    }
    if not _reserve_initial_soak_state(state_path, state):
        existing = background_soak_status(state_path)
        if existing["status"] in {"starting", "running"}:
            raise LoveEngineError(
                "pilot_soak_already_running", str(state_path), 4
            )
        raise LoveEngineError("pilot_soak_state_exists", str(state_path), 3)
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
        _write_soak_state(state_path, state)
        raise LoveEngineError(
            "pilot_soak_launch_failed", exc.__class__.__name__, 4
        ) from exc
    state["pid"] = process.pid
    state["status"] = "running"
    try:
        _write_soak_state(state_path, state)
    except Exception as exc:
        cleanup_verified = _terminate_launched_process(process)
        raise LoveEngineError(
            "pilot_soak_registration_failed",
            f"{type(exc).__name__};cleanup_verified={str(cleanup_verified).lower()}",
            4,
        ) from exc
    return background_soak_status(state_path)
