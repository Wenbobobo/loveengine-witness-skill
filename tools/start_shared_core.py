#!/usr/bin/env python3
"""Start one owned, resource-limited core experiment process group."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from remote_host_preflight import (
    CapacityThresholds,
    acquire_shared_host_lock,
    close_shared_host_lock,
    consume_shared_host_preflight_lease,
    create_shared_host_preflight_lease,
    run_preflight,
    shared_host_lock_rejection,
)
from run_core_experiments import run_core_experiment
from start_shared_quickstart import (
    _core_watchdog_command,
    _limit_process,
    _linux_process_start_ticks,
    _stop_failed_start,
    select_shared_host_cpus,
    validate_core_watchdog_seconds,
    validate_shared_host_limits,
)


DEFAULT_CORE_WATCHDOG_SECONDS = 1_800
CORE_LAUNCH_READY_SECONDS = 15


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _core_arguments(
    output: Path,
    *,
    max_cpus: int,
    nice_increment: int,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
) -> argparse.Namespace:
    return argparse.Namespace(
        output=output,
        events=12,
        observers=3,
        include_recovery_tests=True,
        shared_host=True,
        max_cpus=max_cpus,
        nice_increment=nice_increment,
        max_load_per_cpu=max_load_per_cpu,
        min_memory_gib=min_memory_gib,
        min_disk_gib=min_disk_gib,
        step_timeout_seconds=1_800,
    )


def _core_supervisor_command(
    output: Path,
    *,
    log: Path,
    ready_file: Path,
    preflight_lease_fd: int,
    shared_host_lock_fd: int,
    max_cpus: int,
    nice_increment: int,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
    watchdog_seconds: int,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--supervisor",
        "--output",
        str(output),
        "--log",
        str(log),
        "--ready-file",
        str(ready_file),
        "--shared-host-preflight-fd",
        str(preflight_lease_fd),
        "--shared-host-lock-fd",
        str(shared_host_lock_fd),
        "--max-cpus",
        str(max_cpus),
        "--nice-increment",
        str(nice_increment),
        "--max-load-per-cpu",
        str(max_load_per_cpu),
        "--min-memory-gib",
        str(min_memory_gib),
        "--min-disk-gib",
        str(min_disk_gib),
        "--watchdog-seconds",
        str(watchdog_seconds),
    ]


def _write_launch_info(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _write_resource_block_report(
    output: Path,
    preflight: dict[str, Any],
    *,
    source: str,
    binding: dict[str, Any] | None = None,
) -> Path:
    """Leave a machine-readable resource rejection without starting a group."""

    report_path = output / "core-experiment-report.json"
    _write_launch_info(
        report_path,
        {
            "schema_version": "loveengine.core-experiment-report/2",
            "run_id": "core-preflight-" + secrets.token_hex(8),
            "status": "blocked_by_resource_guard",
            "environment": "local_anvil",
            "actors_simulated": True,
            "resource_profile": "shared_host",
            "resource_preflight": preflight,
            "resource_preflight_source": source,
            "resource_preflight_binding": binding,
            "resource_limits": None,
            "started_at": _utc_now(),
            "completed_at": _utc_now(),
            "steps": [],
            "error": {
                "code": "resource_preflight_rejected",
                "reasons": preflight.get("reasons", []),
            },
        },
    )
    return report_path


def supervise_core(
    output: Path,
    *,
    log: Path,
    ready_file: Path,
    preflight_lease_fd: int,
    shared_host_lock_fd: int,
    max_cpus: int,
    nice_increment: int,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
    watchdog_seconds: int,
) -> int:
    """Consume the one-shot lease, start a guardian, and run core in-process."""

    if os.name == "nt":
        raise RuntimeError("shared-host core supervisor requires POSIX")
    validate_shared_host_limits(max_cpus, nice_increment)
    validate_core_watchdog_seconds(watchdog_seconds)
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required")
    resolved_output = output.resolve()
    resolved_log = log.resolve()
    resolved_ready = ready_file.resolve()
    resolved_output.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    resolved_ready.parent.mkdir(parents=True, exist_ok=True)
    supervisor_pid = os.getpid()
    supervisor_start_ticks = _linux_process_start_ticks(supervisor_pid)
    launcher_pid = os.getppid()
    if launcher_pid <= 1:
        close_shared_host_lock(shared_host_lock_fd)
        raise RuntimeError("core supervisor preflight launcher is unavailable")
    try:
        launcher_start_ticks = _linux_process_start_ticks(launcher_pid)
    except (OSError, ValueError) as exc:
        close_shared_host_lock(shared_host_lock_fd)
        raise RuntimeError("core supervisor preflight launcher is unreadable") from exc
    thresholds = CapacityThresholds(
        max_load_per_cpu=max_load_per_cpu,
        min_available_memory_bytes=int(min_memory_gib * 1024**3),
        min_free_disk_bytes=int(min_disk_gib * 1024**3),
        min_cpu_count=2,
    )
    try:
        preflight, lease_binding = consume_shared_host_preflight_lease(
            preflight_lease_fd,
            resolved_output,
            thresholds,
            launcher_pid=launcher_pid,
            launcher_start_ticks=launcher_start_ticks,
        )
    except Exception:
        close_shared_host_lock(shared_host_lock_fd)
        raise
    binding = {
        **lease_binding,
        "supervisor": {
            "pid": supervisor_pid,
            "process_start_ticks": supervisor_start_ticks,
        },
    }
    try:
        applied_cpu_affinity, process_nice = _limit_process(max_cpus, nice_increment)
    except Exception:
        close_shared_host_lock(shared_host_lock_fd)
        raise
    applied_limits = {
        "nice_increment": nice_increment,
        "process_nice": process_nice,
        "cpu_affinity": applied_cpu_affinity,
    }
    watchdog: subprocess.Popen[bytes] | None = None
    watchdog_start_ticks: int | None = None
    watchdog_result_path = resolved_output / ".core-watchdog-result.json"
    with resolved_log.open("ab") as stream:
        try:
            watchdog = subprocess.Popen(
                _core_watchdog_command(
                    supervisor_pid,
                    supervisor_start_ticks,
                    watchdog_seconds,
                    result_file=watchdog_result_path,
                    shared_host_lock_fd=shared_host_lock_fd,
                ),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                # The guardian retains the advisory lock if the supervisor
                # exits unexpectedly while it is still cleaning this group.
                pass_fds=(shared_host_lock_fd,),
            )
            watchdog_start_ticks = _linux_process_start_ticks(watchdog.pid)
        except (OSError, ValueError):
            if watchdog is not None:
                _stop_failed_start(
                    watchdog,
                    expected_start_ticks=watchdog_start_ticks,
                    expected_script=Path(__file__).resolve().parent
                    / "start_shared_quickstart.py",
                    expected_mode="--core-watchdog",
                )
            close_shared_host_lock(shared_host_lock_fd)
            raise
        try:
            _write_launch_info(
                resolved_ready,
                {
                    "pid": supervisor_pid,
                    "process_start_ticks": supervisor_start_ticks,
                    "output": str(resolved_output),
                    "log": str(resolved_log),
                    "ready_file": str(resolved_ready),
                    "nice_increment": nice_increment,
                    "process_nice": process_nice,
                    "max_cpus": max_cpus,
                    "cpu_affinity": applied_cpu_affinity,
                    "reserved_cpu_count": 1,
                    "process_kind": "core_supervisor",
                    "resource_preflight": preflight,
                    "resource_preflight_source": "launcher_fd_lease",
                    "resource_preflight_binding": binding,
                    "watchdog": {
                        "pid": watchdog.pid,
                        "process_start_ticks": watchdog_start_ticks,
                        "timeout_seconds": watchdog_seconds,
                        "scope": "owned_core_process_group",
                        "result_path": str(watchdog_result_path),
                    },
                },
            )
            return run_core_experiment(
                _core_arguments(
                    resolved_output,
                    max_cpus=max_cpus,
                    nice_increment=nice_increment,
                    max_load_per_cpu=max_load_per_cpu,
                    min_memory_gib=min_memory_gib,
                    min_disk_gib=min_disk_gib,
                ),
                shared_host_preflight=preflight,
                shared_host_preflight_source="launcher_fd_lease",
                resource_preflight_binding=binding,
                shared_host_limits=applied_limits,
            )
        finally:
            close_shared_host_lock(shared_host_lock_fd)


def start_core(
    output: Path,
    *,
    log: Path,
    max_cpus: int = 2,
    nice_increment: int = 15,
    max_load_per_cpu: float = 0.5,
    min_memory_gib: float = 3.0,
    min_disk_gib: float = 5.0,
    watchdog_seconds: int = DEFAULT_CORE_WATCHDOG_SECONDS,
) -> dict[str, object]:
    if os.name == "nt":
        raise RuntimeError("shared-host core launcher requires POSIX")
    validate_shared_host_limits(max_cpus, nice_increment)
    validate_core_watchdog_seconds(watchdog_seconds)
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required")
    resolved_output = output.resolve()
    resolved_log = log.resolve()
    if not hasattr(os, "sched_getaffinity") or not hasattr(
        os, "sched_setaffinity"
    ):
        raise RuntimeError("shared-host CPU affinity enforcement is required")
    select_shared_host_cpus(os.sched_getaffinity(0), max_cpus)
    resolved_output.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    thresholds = CapacityThresholds(
        max_load_per_cpu=max_load_per_cpu,
        min_available_memory_bytes=int(min_memory_gib * 1024**3),
        min_free_disk_bytes=int(min_disk_gib * 1024**3),
        min_cpu_count=2,
    )
    lock_fd = acquire_shared_host_lock()
    if lock_fd is None:
        preflight = shared_host_lock_rejection(resolved_output, thresholds)
        _write_resource_block_report(
            resolved_output,
            preflight,
            source="launcher_lock",
        )
        return {
            "status": "blocked_by_resource_guard",
            "resource_preflight": preflight,
        }
    try:
        preflight = run_preflight(resolved_output, thresholds)
        if not preflight.get("safe_to_run"):
            _write_resource_block_report(
                resolved_output,
                preflight,
                source="launcher_initial",
            )
            close_shared_host_lock(lock_fd)
            lock_fd = None
            return {
                "status": "blocked_by_resource_guard",
                "resource_preflight": preflight,
            }
        launcher_pid = os.getpid()
        launcher_start_ticks = _linux_process_start_ticks(launcher_pid)
        if hasattr(os, "pipe2") and hasattr(os, "O_CLOEXEC"):
            lease_read_fd, lease_write_fd = os.pipe2(os.O_CLOEXEC)
        else:
            lease_read_fd, lease_write_fd = os.pipe()
        ready_file = resolved_output / ".core-launch.json"
        if ready_file.exists():
            raise FileExistsError("core supervisor ready record already exists")
        with resolved_log.open("ab") as stream:
            process = subprocess.Popen(
                _core_supervisor_command(
                    resolved_output,
                    log=resolved_log,
                    ready_file=ready_file,
                    preflight_lease_fd=lease_read_fd,
                    shared_host_lock_fd=lock_fd,
                    max_cpus=max_cpus,
                    nice_increment=nice_increment,
                    max_load_per_cpu=max_load_per_cpu,
                    min_memory_gib=min_memory_gib,
                    min_disk_gib=min_disk_gib,
                    watchdog_seconds=watchdog_seconds,
                ),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(lease_read_fd, lock_fd),
            )
        try:
            create_shared_host_preflight_lease(
                lease_write_fd,
                resolved_output,
                thresholds,
                preflight,
                launcher_pid=launcher_pid,
                launcher_start_ticks=launcher_start_ticks,
            )
        finally:
            lease_write_fd = -1
        os.close(lease_read_fd)
        lease_read_fd = -1
        close_shared_host_lock(lock_fd)
        lock_fd = None
    except Exception:
        if "lease_read_fd" in locals() and lease_read_fd >= 0:
            os.close(lease_read_fd)
        if "lease_write_fd" in locals() and lease_write_fd >= 0:
            os.close(lease_write_fd)
        close_shared_host_lock(lock_fd)
        raise
    process_start_ticks: int | None = None
    try:
        process_start_ticks = _linux_process_start_ticks(process.pid)
        deadline = time.monotonic() + CORE_LAUNCH_READY_SECONDS
        while not ready_file.is_file() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("core supervisor exited before its guardian was ready")
            time.sleep(0.05)
        if not ready_file.is_file():
            raise RuntimeError("timed out waiting for the core guardian to start")
        start_info = json.loads(ready_file.read_text(encoding="utf-8"))
        if (
            start_info.get("pid") != process.pid
            or start_info.get("process_start_ticks") != process_start_ticks
            or start_info.get("process_kind") != "core_supervisor"
            or start_info.get("resource_preflight_source") != "launcher_fd_lease"
            or start_info.get("process_nice") != nice_increment
            or start_info.get("cpu_affinity")
            != select_shared_host_cpus(os.sched_getaffinity(0), max_cpus)
        ):
            raise RuntimeError("core supervisor launch identity is inconsistent")
        return start_info
    except Exception:
        _stop_failed_start(
            process,
            expected_start_ticks=process_start_ticks,
            expected_script=Path(__file__).resolve(),
            expected_mode="--supervisor",
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--max-cpus", type=int, default=2)
    parser.add_argument("--nice-increment", type=int, default=15)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.5)
    parser.add_argument("--min-memory-gib", type=float, default=3.0)
    parser.add_argument("--min-disk-gib", type=float, default=5.0)
    parser.add_argument(
        "--watchdog-seconds",
        type=int,
        default=DEFAULT_CORE_WATCHDOG_SECONDS,
    )
    parser.add_argument("--supervisor", action="store_true")
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--shared-host-preflight-fd", type=int)
    parser.add_argument("--shared-host-lock-fd", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.supervisor:
        if args.ready_file is None:
            raise ValueError("core supervisor requires --ready-file")
        if (
            args.shared_host_preflight_fd is None
            or args.shared_host_lock_fd is None
        ):
            raise ValueError("core supervisor requires a preflight lease and lock")
        return supervise_core(
            args.output,
            log=args.log,
            ready_file=args.ready_file,
            preflight_lease_fd=args.shared_host_preflight_fd,
            shared_host_lock_fd=args.shared_host_lock_fd,
            max_cpus=args.max_cpus,
            nice_increment=args.nice_increment,
            max_load_per_cpu=args.max_load_per_cpu,
            min_memory_gib=args.min_memory_gib,
            min_disk_gib=args.min_disk_gib,
            watchdog_seconds=args.watchdog_seconds,
        )
    result = start_core(
        args.output,
        log=args.log,
        max_cpus=args.max_cpus,
        nice_increment=args.nice_increment,
        max_load_per_cpu=args.max_load_per_cpu,
        min_memory_gib=args.min_memory_gib,
        min_disk_gib=args.min_disk_gib,
        watchdog_seconds=args.watchdog_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 4 if result.get("status") == "blocked_by_resource_guard" else 0


if __name__ == "__main__":
    raise SystemExit(main())
