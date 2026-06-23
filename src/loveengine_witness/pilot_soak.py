"""Formal and accelerated LAN pilot soak runner."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import time
from pathlib import Path
from typing import Any

from .errors import LoveEngineError
from .jsonio import write_json
from .pilot_demo import run_pilot_demo
from .pilot_transcript import verify_pilot_transcript


def validate_pilot_soak_args(
    duration_seconds: float, event_count: int, observers: int
) -> None:
    if duration_seconds <= 0 or duration_seconds > 4 * 3600:
        raise LoveEngineError("invalid_soak_duration", str(duration_seconds))
    if event_count <= 0:
        raise LoveEngineError("invalid_event_count", str(event_count))
    if observers != 10:
        raise LoveEngineError("invalid_observer_count", "M5 requires 10 observers")
    if duration_seconds >= 4 * 3600 and event_count < 240:
        raise LoveEngineError(
            "formal_soak_event_count", "at least 240 events required"
        )


def _disk_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _rss_bytes() -> int | None:
    if os.name == "nt":
        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(Counters),
            wintypes.DWORD,
        ]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        handle = kernel32.GetCurrentProcess()
        if psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return int(counters.PeakWorkingSetSize)
        return None
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    return None


def _scan_token_leak(root: Path, token_file: Path) -> list[str]:
    token = token_file.read_bytes()
    leaks = []
    for path in root.rglob("*"):
        if not path.is_file() or path.resolve() == token_file.resolve():
            continue
        try:
            if token and token in path.read_bytes():
                leaks.append(path.relative_to(root).as_posix())
        except OSError:
            continue
    return leaks


def run_pilot_soak(
    output: Path,
    *,
    duration_seconds: float,
    event_count: int,
    observers: int = 10,
) -> dict[str, Any]:
    validate_pilot_soak_args(duration_seconds, event_count, observers)
    formal = duration_seconds >= 4 * 3600
    output = Path(output).resolve()
    started = time.monotonic()
    result = run_pilot_demo(
        output,
        event_count=event_count,
        observer_count=observers,
        event_interval=duration_seconds / event_count,
        simulate_faults=True,
    )
    elapsed = time.monotonic() - started
    transcript = result["transcript"]
    verify_pilot_transcript(transcript)
    relay = transcript["metrics"]
    latency = relay["latency_ms"]
    disk = _disk_bytes(output)
    memory = _rss_bytes()
    leaks = _scan_token_leak(output, output / "operator.token")
    checks = {
        "zero_event_loss": len(transcript["events"]) == event_count,
        "three_agents": result["observation_receipts"] == 3,
        "ten_observers": result["read_only_observers"] == observers,
        "server_restart": result["faults"]["server_restarts"] == 1,
        "anvil_restart": result["faults"]["anvil_restarts"] == 1,
        "three_agent_disconnects": result["faults"]["agent_disconnects"] == 3,
        "recovery_under_30s": result["faults"]["recovery_seconds"] < 30,
        "ack_p95_under_2s": latency["p95"] < 2000,
        "ack_max_under_5s": latency["max"] < 5000,
        "disk_under_250mb": disk < 250 * 1024 * 1024,
        "memory_under_512mb": memory is not None and memory < 512 * 1024 * 1024,
        "secret_scan_zero": not leaks,
    }
    report = {
        "schema_version": "loveengine.pilot-soak-report/1",
        "mode": (
            "formal-4h"
            if formal
            else "wall-clock-preflight"
            if duration_seconds >= 3600
            else "accelerated"
        ),
        "requested_duration_seconds": duration_seconds,
        "elapsed_seconds": round(elapsed, 3),
        "event_count": event_count,
        "observer_count": observers,
        "disk_bytes": disk,
        "peak_rss_bytes": memory,
        "faults": result["faults"],
        "ack_latency_ms": latency,
        "completion_latency_ms": relay["completion_latency_ms"],
        "secret_leaks": leaks,
        "checks": checks,
        "passed": all(checks.values()),
        "transcript_path": result["transcript_path"],
    }
    write_json(output / "pilot-soak-report.json", report)
    if not report["passed"]:
        failed = sorted(key for key, passed in checks.items() if not passed)
        raise LoveEngineError("pilot_soak_failed", ",".join(failed), 4)
    return report
