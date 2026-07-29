"""Formal and accelerated LAN pilot soak runner."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import psutil

from .core_transcript import verify_core_transcript
from .errors import LoveEngineError
from .jsonio import write_json
from .pilot_demo import run_pilot_demo
from .pilot_transcript import verify_pilot_transcript


MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
RUNTIME_TREE_SAMPLE_INTERVAL_SECONDS = 0.5
MIN_RUNTIME_TREE_PROCESS_COUNT = 4
SOAK_SUCCESS_CHECK_KEYS = frozenset(
    {
        "zero_event_loss",
        "three_agents",
        "ten_observers",
        "server_restart",
        "anvil_restart",
        "three_agent_disconnects",
        "recovery_under_30s",
        "ack_p95_under_2s",
        "ack_max_under_5s",
        "disk_under_250mb",
        "memory_under_512mb",
        "root_process_memory_under_512mb",
        "runtime_tree_sampling_observed",
        "runtime_tree_memory_under_512mb",
        "secret_scan_zero",
    }
)


def validate_pilot_soak_args(
    duration_seconds: float,
    event_count: int,
    observers: int,
    stage: str = "core",
) -> None:
    if stage not in {"core", "governance"}:
        raise LoveEngineError("invalid_pilot_stage", stage)
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


def _root_process_peak_rss_bytes() -> int | None:
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


class _RuntimeTreeSampler:
    """Sample the CLI process and its current descendants as one runtime tree."""

    def __init__(
        self,
        root_pid: int,
        *,
        interval_seconds: float = RUNTIME_TREE_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        self._root_pid = root_pid
        self._interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._peak_rss_bytes: int | None = None
        self._sample_count = 0
        self._max_process_count = 0
        self._sample_error_count = 0

    def start(self) -> None:
        self._sample()
        self._thread = threading.Thread(
            target=self._run,
            name="loveengine-runtime-tree-sampler",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval_seconds + 1)
        self._sample()
        return self.metrics()

    def metrics(self) -> dict[str, Any]:
        with self._lock:
            available = (
                self._sample_count > 0 and self._peak_rss_bytes is not None
            )
            return {
                "available": available,
                "sampled_peak_rss_bytes": self._peak_rss_bytes,
                "sample_count": self._sample_count,
                "max_process_count": self._max_process_count,
                "sample_interval_seconds": self._interval_seconds,
                "sample_error_count": self._sample_error_count,
            }

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            self._sample()

    def _sample(self) -> None:
        try:
            root = psutil.Process(self._root_pid)
            processes = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            return
        except (OSError, psutil.Error):
            with self._lock:
                self._sample_error_count += 1
            return

        seen_pids: set[int] = set()
        total_rss = 0
        process_count = 0
        errors = 0
        for process in processes:
            if process.pid in seen_pids:
                continue
            seen_pids.add(process.pid)
            try:
                total_rss += int(process.memory_info().rss)
                process_count += 1
            except psutil.NoSuchProcess:
                # A process that disappeared before this sample has no current
                # RSS to include. It is not an incomplete measurement.
                continue
            except (OSError, psutil.Error):
                errors += 1
        with self._lock:
            self._sample_error_count += errors
            if process_count == 0:
                self._sample_error_count += 1
                return
            self._sample_count += 1
            self._max_process_count = max(
                self._max_process_count, process_count
            )
            if self._peak_rss_bytes is None:
                self._peak_rss_bytes = total_rss
            else:
                self._peak_rss_bytes = max(self._peak_rss_bytes, total_rss)


def _runtime_tree_metrics(root_pid: int) -> _RuntimeTreeSampler:
    """Construct the sampler through one patchable seam for tests."""

    return _RuntimeTreeSampler(root_pid)


def _scan_token_leak(root: Path, token_file: Path) -> list[str]:
    if not token_file.is_file():
        return []
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


def _memory_report(
    runtime_tree: dict[str, Any], root_peak_rss_bytes: int | None
) -> dict[str, Any]:
    return {
        "root_process_peak_rss_bytes": root_peak_rss_bytes,
        "root_process_metric": (
            "peak_working_set" if os.name == "nt" else "vm_hwm"
        ),
        "runtime_tree_sampled_peak_rss_bytes": runtime_tree[
            "sampled_peak_rss_bytes"
        ],
        "runtime_tree_sample_count": runtime_tree["sample_count"],
        "runtime_tree_max_process_count": runtime_tree["max_process_count"],
        "runtime_tree_required_min_process_count": (
            MIN_RUNTIME_TREE_PROCESS_COUNT
        ),
        "runtime_tree_sample_interval_seconds": runtime_tree[
            "sample_interval_seconds"
        ],
        "runtime_tree_sample_error_count": runtime_tree[
            "sample_error_count"
        ],
        "runtime_tree_available": runtime_tree["available"],
    }


def _memory_checks(memory: dict[str, Any]) -> dict[str, bool]:
    root_peak = memory["root_process_peak_rss_bytes"]
    tree_peak = memory["runtime_tree_sampled_peak_rss_bytes"]
    tree_available = memory["runtime_tree_available"]
    tree_sampling_observed = (
        tree_available
        and memory["runtime_tree_sample_error_count"] == 0
        and memory["runtime_tree_max_process_count"]
        >= MIN_RUNTIME_TREE_PROCESS_COUNT
    )
    root_under_limit = (
        isinstance(root_peak, int) and root_peak < MEMORY_LIMIT_BYTES
    )
    runtime_tree_under_limit = (
        tree_sampling_observed
        and isinstance(tree_peak, int)
        and tree_peak < MEMORY_LIMIT_BYTES
    )
    return {
        # Historical compatibility alias. It continues to describe only the
        # root process; the runtime-tree sampled sum-RSS check is the complete
        # owned-lab resource gate and is included in every passed report.
        "memory_under_512mb": root_under_limit,
        "root_process_memory_under_512mb": root_under_limit,
        "runtime_tree_sampling_observed": tree_sampling_observed,
        "runtime_tree_memory_under_512mb": runtime_tree_under_limit,
    }


def _failure_details(error: BaseException) -> dict[str, str]:
    if isinstance(error, LoveEngineError):
        return {
            "code": error.code,
            "exception_type": error.__class__.__name__,
        }
    return {
        "code": "unexpected_exception",
        "exception_type": error.__class__.__name__,
    }


def _soak_mode(duration_seconds: float, formal: bool) -> str:
    if formal:
        return "formal-4h"
    if duration_seconds >= 3600:
        return "wall-clock-preflight"
    return "accelerated"


def _unavailable_runtime_tree_metrics() -> dict[str, Any]:
    return {
        "available": False,
        "sampled_peak_rss_bytes": None,
        "sample_count": 0,
        "max_process_count": 0,
        "sample_interval_seconds": RUNTIME_TREE_SAMPLE_INTERVAL_SECONDS,
        "sample_error_count": 1,
    }


def _failure_diagnostics(
    sampler: _RuntimeTreeSampler, output: Path
) -> tuple[dict[str, Any], int | None, list[str], bool]:
    try:
        runtime_tree = sampler.stop()
    except Exception:
        runtime_tree = _unavailable_runtime_tree_metrics()
    try:
        root_peak_rss_bytes = _root_process_peak_rss_bytes()
    except Exception:
        root_peak_rss_bytes = None
    try:
        disk = _disk_bytes(output)
    except Exception:
        disk = None
    try:
        leaks = _scan_token_leak(output, output / "operator.token")
    except Exception:
        leaks = []
        secret_scan_completed = False
    else:
        secret_scan_completed = True
    return (
        _memory_report(runtime_tree, root_peak_rss_bytes),
        disk,
        leaks,
        secret_scan_completed,
    )


def _write_report_best_effort(output: Path, report: dict[str, Any]) -> None:
    try:
        write_json(output / "pilot-soak-report.json", report)
    except Exception:
        pass


def _minimal_failure_report(
    *,
    formal: bool,
    stage: str,
    duration_seconds: float,
    elapsed_seconds: float,
    event_count: int,
    observers: int,
    error: BaseException,
) -> dict[str, Any]:
    return {
        "schema_version": "loveengine.pilot-soak-report/1",
        "mode": _soak_mode(duration_seconds, formal),
        "stage": stage,
        "requested_duration_seconds": duration_seconds,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "event_count": event_count,
        "observer_count": observers,
        "disk_bytes": None,
        "peak_rss_bytes": None,
        "peak_rss_bytes_scope": "root_process_os_peak",
        "memory": None,
        "faults": None,
        "ack_latency_ms": None,
        "completion_latency_ms": None,
        "secret_leaks": None,
        "checks": {"run_completed": False},
        "failure": _failure_details(error),
        "passed": False,
        "transcript_path": None,
    }


def run_pilot_soak(
    output: Path,
    *,
    duration_seconds: float,
    event_count: int,
    observers: int = 10,
    stage: str = "core",
) -> dict[str, Any]:
    validate_pilot_soak_args(
        duration_seconds, event_count, observers, stage
    )
    formal = duration_seconds >= 4 * 3600
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    sampler = _runtime_tree_metrics(os.getpid())
    try:
        sampler.start()
        result = run_pilot_demo(
            output,
            stage=stage,
            event_count=event_count,
            observer_count=observers,
            event_interval=duration_seconds / event_count,
            simulate_faults=True,
        )
        transcript = result["transcript"]
        if stage == "core":
            verify_core_transcript(transcript)
        else:
            verify_pilot_transcript(transcript)
    except BaseException as error:
        elapsed = time.monotonic() - started
        report = _minimal_failure_report(
            formal=formal,
            stage=stage,
            duration_seconds=duration_seconds,
            elapsed_seconds=elapsed,
            event_count=event_count,
            observers=observers,
            error=error,
        )
        _write_report_best_effort(output, report)
        memory, disk, leaks, secret_scan_completed = _failure_diagnostics(
            sampler, output
        )
        report.update(
            {
                "disk_bytes": disk,
                "peak_rss_bytes": memory["root_process_peak_rss_bytes"],
                "memory": memory,
                "secret_leaks": leaks,
                "checks": {
                    "run_completed": False,
                    **_memory_checks(memory),
                    "secret_scan_zero": (
                        secret_scan_completed and not leaks
                    ),
                },
            }
        )
        _write_report_best_effort(output, report)
        raise

    elapsed = time.monotonic() - started
    memory = _memory_report(sampler.stop(), _root_process_peak_rss_bytes())
    relay = transcript["metrics"]
    latency = relay["latency_ms"]
    disk = _disk_bytes(output)
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
        **_memory_checks(memory),
        "secret_scan_zero": not leaks,
    }
    passed = all(checks.values())
    report = {
        "schema_version": "loveengine.pilot-soak-report/1",
        "mode": _soak_mode(duration_seconds, formal),
        "stage": stage,
        "requested_duration_seconds": duration_seconds,
        "elapsed_seconds": round(elapsed, 3),
        "event_count": event_count,
        "observer_count": observers,
        "disk_bytes": disk,
        "peak_rss_bytes": memory["root_process_peak_rss_bytes"],
        "peak_rss_bytes_scope": "root_process_os_peak",
        "memory": memory,
        "faults": result["faults"],
        "ack_latency_ms": latency,
        "completion_latency_ms": relay["completion_latency_ms"],
        "secret_leaks": leaks,
        "checks": checks,
        "failure": (
            None
            if passed
            else {
                "code": "pilot_soak_failed",
                "exception_type": "LoveEngineError",
            }
        ),
        "passed": passed,
        "transcript_path": result["transcript_path"],
    }
    write_json(output / "pilot-soak-report.json", report)
    if not passed:
        failed = sorted(key for key, passed in checks.items() if not passed)
        raise LoveEngineError("pilot_soak_failed", ",".join(failed), 4)
    return report
