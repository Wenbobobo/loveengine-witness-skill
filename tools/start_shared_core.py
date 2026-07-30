#!/usr/bin/env python3
"""Start one owned, resource-limited core experiment process group."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from start_shared_quickstart import (
    _core_watchdog_command,
    _linux_process_start_ticks,
    _stop_failed_start,
    select_shared_host_cpus,
    validate_core_watchdog_seconds,
    validate_shared_host_limits,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORE_WATCHDOG_SECONDS = 1_800
CORE_LAUNCH_READY_SECONDS = 15


def _core_payload(
    output: Path,
    *,
    max_cpus: int,
    nice_increment: int,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "tools" / "run_core_experiments.py"),
        "--shared-host",
        "--include-recovery-tests",
        "--max-load-per-cpu",
        str(max_load_per_cpu),
        "--min-memory-gib",
        str(min_memory_gib),
        "--min-disk-gib",
        str(min_disk_gib),
        "--max-cpus",
        str(max_cpus),
        "--nice-increment",
        str(nice_increment),
        "--events",
        "12",
        "--observers",
        "3",
        "--output",
        str(output),
    ]


def _core_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "UV_CONCURRENT_DOWNLOADS": "2",
            "UV_CONCURRENT_BUILDS": "1",
            "CARGO_BUILD_JOBS": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    return environment


def _core_supervisor_command(
    output: Path,
    *,
    log: Path,
    ready_file: Path,
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


def supervise_core(
    output: Path,
    *,
    log: Path,
    ready_file: Path,
    max_cpus: int,
    nice_increment: int,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
    watchdog_seconds: int,
) -> int:
    """Start the guardian before the core child, then wait for that child."""

    if os.name == "nt":
        raise RuntimeError("shared-host core supervisor requires POSIX")
    validate_shared_host_limits(max_cpus, nice_increment)
    validate_core_watchdog_seconds(watchdog_seconds)
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required")
    resolved_output = output.resolve()
    resolved_log = log.resolve()
    resolved_ready = ready_file.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    resolved_ready.parent.mkdir(parents=True, exist_ok=True)
    supervisor_pid = os.getpid()
    supervisor_start_ticks = _linux_process_start_ticks(supervisor_pid)
    watchdog: subprocess.Popen[bytes] | None = None
    watchdog_start_ticks: int | None = None
    with resolved_log.open("ab") as stream:
        try:
            watchdog = subprocess.Popen(
                _core_watchdog_command(
                    supervisor_pid,
                    supervisor_start_ticks,
                    watchdog_seconds,
                ),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            watchdog_start_ticks = _linux_process_start_ticks(watchdog.pid)
            child = subprocess.Popen(
                _core_payload(
                    resolved_output,
                    max_cpus=max_cpus,
                    nice_increment=nice_increment,
                    max_load_per_cpu=max_load_per_cpu,
                    min_memory_gib=min_memory_gib,
                    min_disk_gib=min_disk_gib,
                ),
                cwd=ROOT,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=_core_environment(),
            )
        except (OSError, ValueError):
            if watchdog is not None:
                _stop_failed_start(
                    watchdog,
                    expected_start_ticks=watchdog_start_ticks,
                    expected_script=Path(__file__).resolve().parent
                    / "start_shared_quickstart.py",
                    expected_mode="--core-watchdog",
                )
            raise
        _write_launch_info(
            resolved_ready,
            {
                "pid": supervisor_pid,
                "process_start_ticks": supervisor_start_ticks,
                "output": str(resolved_output),
                "log": str(resolved_log),
                "ready_file": str(resolved_ready),
                "nice_increment": nice_increment,
                "max_cpus": max_cpus,
                "reserved_cpu_count": 1,
                "process_kind": "core_supervisor",
                "watchdog": {
                    "pid": watchdog.pid,
                    "process_start_ticks": watchdog_start_ticks,
                    "timeout_seconds": watchdog_seconds,
                    "scope": "owned_core_process_group",
                },
            },
        )
        return child.wait()


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
    cpu_affinity = select_shared_host_cpus(os.sched_getaffinity(0), max_cpus)
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    ready_file = resolved_log.parent / (
        f".{resolved_log.name}.{time.time_ns()}.core-launch.json"
    )
    with resolved_log.open("ab") as stream:
        process = subprocess.Popen(
            _core_supervisor_command(
                resolved_output,
                log=resolved_log,
                ready_file=ready_file,
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
        )
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
        ):
            raise RuntimeError("core supervisor launch identity is inconsistent")
        start_info["cpu_affinity"] = cpu_affinity
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
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.supervisor:
        if args.ready_file is None:
            raise ValueError("core supervisor requires --ready-file")
        return supervise_core(
            args.output,
            log=args.log,
            ready_file=args.ready_file,
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
