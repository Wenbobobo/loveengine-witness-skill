#!/usr/bin/env python3
"""Start one resource-limited, loopback-only Pilot Quickstart process group."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
from pathlib import Path


def _parse_linux_process_start_ticks(stat: str) -> int:
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise ValueError("invalid Linux process stat")
    fields = stat[closing_parenthesis + 1 :].split()
    if len(fields) <= 19:
        raise ValueError("Linux process stat is missing the start time")
    start_ticks = int(fields[19])
    if start_ticks <= 0:
        raise ValueError("Linux process start time must be positive")
    return start_ticks


def _linux_process_start_ticks(pid: int) -> int:
    return _parse_linux_process_start_ticks(
        Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    )


def _limit_process(max_cpus: int, nice_increment: int) -> None:
    validate_shared_host_limits(max_cpus, nice_increment)
    os.nice(nice_increment)
    if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
        available = sorted(os.sched_getaffinity(0))
        selected = available[: max(1, min(max_cpus, len(available)))]
        os.sched_setaffinity(0, selected)


def validate_shared_host_limits(max_cpus: int, nice_increment: int) -> None:
    if not 1 <= max_cpus <= 2:
        raise ValueError("shared-host max CPUs must be between 1 and 2")
    if not 15 <= nice_increment <= 19:
        raise ValueError("shared-host nice increment must be between 15 and 19")


def _stop_failed_start(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)


def start_quickstart(
    root: Path,
    *,
    host: str,
    port: int,
    rpc_port: int,
    log: Path,
    max_cpus: int = 2,
    nice_increment: int = 15,
) -> dict:
    if os.name == "nt":
        raise RuntimeError("shared-host Quickstart launcher requires POSIX")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("shared-host Quickstart must bind loopback")
    validate_shared_host_limits(max_cpus, nice_increment)
    if not 1 <= port <= 65535 or not 1 <= rpc_port <= 65535 or port == rpc_port:
        raise ValueError("invalid or conflicting Quickstart ports")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required")
    resolved_root = root.resolve()
    resolved_log = log.resolve()
    resolved_root.parent.mkdir(parents=True, exist_ok=True)
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
    command = [
        uv,
        "run",
        "loveengine",
        "pilot",
        "quickstart",
        "--root",
        str(resolved_root),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--host",
        host,
        "--port",
        str(port),
        "--rpc-port",
        str(rpc_port),
        "--headless",
    ]
    with resolved_log.open("ab") as stream:
        process = subprocess.Popen(
            command,
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
        "root": str(resolved_root),
        "log": str(resolved_log),
        "host": host,
        "port": port,
        "rpc_port": rpc_port,
        "nice_increment": nice_increment,
        "max_cpus": max_cpus,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--rpc-port", type=int, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--max-cpus", type=int, default=2)
    parser.add_argument("--nice-increment", type=int, default=15)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = start_quickstart(
        args.root,
        host=args.host,
        port=args.port,
        rpc_port=args.rpc_port,
        log=args.log,
        max_cpus=args.max_cpus,
        nice_increment=args.nice_increment,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
