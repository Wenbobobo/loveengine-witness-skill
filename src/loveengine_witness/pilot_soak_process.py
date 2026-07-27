"""Detached pilot soak lifecycle and observable status."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import LoveEngineError
from .jsonio import read_json, write_json
from .pilot_soak import validate_pilot_soak_args


STATE_FILENAME = "pilot-soak-run.json"
STDOUT_FILENAME = "pilot-soak.stdout.jsonl"
STDERR_FILENAME = "pilot-soak.stderr.log"
REPORT_FILENAME = "pilot-soak-report.json"


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


def background_soak_status(state_path: Path) -> dict[str, Any]:
    state_path = Path(state_path).resolve()
    state = read_json(state_path)
    if not isinstance(state, dict) or state.get("schema_version") != (
        "loveengine.pilot-soak-run/1"
    ):
        raise LoveEngineError("invalid_soak_state", str(state_path))
    output = Path(str(state["output"])).resolve()
    report_path = output / REPORT_FILENAME
    report = read_json(report_path) if report_path.is_file() else None
    pid = int(state.get("pid") or 0)
    if isinstance(report, dict):
        status = "passed" if report.get("passed") is True else "failed"
    elif _process_alive(pid):
        status = "running"
    else:
        status = "failed"
    duration = float(state["duration_seconds"])
    elapsed = max(0.0, time.time() - float(state["started_at_epoch"]))
    progress = 100.0 if report is not None else min(99.9, elapsed / duration * 100)
    return {
        **state,
        "state_path": str(state_path),
        "status": status,
        "checked_at": _utc_now(),
        "elapsed_seconds": round(elapsed, 3),
        "remaining_seconds": round(max(0.0, duration - elapsed), 3),
        "progress_percent": round(progress, 2),
        "report": report,
    }


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
    if state_path.is_file():
        existing = background_soak_status(state_path)
        if existing["status"] == "running":
            raise LoveEngineError(
                "pilot_soak_already_running", str(state_path), 4
            )
        raise LoveEngineError("pilot_soak_state_exists", str(state_path), 3)

    stdout_path = output / STDOUT_FILENAME
    stderr_path = output / STDERR_FILENAME
    started_at_epoch = time.time()
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
        "output": str(output),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "report_path": str(output / REPORT_FILENAME),
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
