#!/usr/bin/env python3
"""Start one owned, resource-limited core experiment process group."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from start_shared_quickstart import (
    _limit_process,
    _linux_process_start_ticks,
    _stop_failed_start,
    validate_shared_host_limits,
)


ROOT = Path(__file__).resolve().parents[1]


def start_core(
    output: Path,
    *,
    log: Path,
    max_cpus: int = 2,
    nice_increment: int = 15,
    max_load_per_cpu: float = 0.5,
    min_memory_gib: float = 3.0,
    min_disk_gib: float = 5.0,
) -> dict[str, object]:
    if os.name == "nt":
        raise RuntimeError("shared-host core launcher requires POSIX")
    validate_shared_host_limits(max_cpus, nice_increment)
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required")
    resolved_output = output.resolve()
    resolved_log = log.resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "UV_CONCURRENT_DOWNLOADS": "2",
            "UV_CONCURRENT_BUILDS": "1",
            "CARGO_BUILD_JOBS": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    payload = [
        sys.executable,
        str(ROOT / "tools" / "run_core_experiments.py"),
        "--shared-host",
        "--prepare-contracts",
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
        str(resolved_output),
    ]
    bash = shutil.which("bash")
    if bash is None:
        raise RuntimeError("bash is required")
    # Let this short-lived launcher exit before the child performs its own
    # process preflight, then preserve the process-group leader with exec.
    command = [
        bash,
        "-c",
        'sleep 1; exec "$@"',
        "loveengine-shared-core",
        *payload,
    ]
    with resolved_log.open("ab") as stream:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
            preexec_fn=lambda: _limit_process(max_cpus, nice_increment),
        )
    try:
        process_start_ticks = _linux_process_start_ticks(process.pid)
    except (OSError, ValueError):
        _stop_failed_start(process)
        raise
    return {
        "pid": process.pid,
        "process_start_ticks": process_start_ticks,
        "output": str(resolved_output),
        "log": str(resolved_log),
        "nice_increment": nice_increment,
        "max_cpus": max_cpus,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--max-cpus", type=int, default=2)
    parser.add_argument("--nice-increment", type=int, default=15)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.5)
    parser.add_argument("--min-memory-gib", type=float, default=3.0)
    parser.add_argument("--min-disk-gib", type=float, default=5.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = start_core(
        args.output,
        log=args.log,
        max_cpus=args.max_cpus,
        nice_increment=args.nice_increment,
        max_load_per_cpu=args.max_load_per_cpu,
        min_memory_gib=args.min_memory_gib,
        min_disk_gib=args.min_disk_gib,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
