#!/usr/bin/env python3
"""Read-only capacity and toolchain gate for a shared remote experiment host."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


GIB = 1024**3
SCHEMA_VERSION = "loveengine.remote-host-preflight/1"
REQUIRED_TOOLS = (
    "python3",
    "uv",
    "git",
    "bash",
    "tar",
    "ps",
    "forge",
    "anvil",
)
RELEVANT_PROCESSES = {
    "anvil",
    "forge",
    "loveengine",
    "loveengine_witness",
    "run_core_experiments.py",
    "start_shared_quickstart.py",
    "start_shared_core.py",
}


@dataclass(frozen=True)
class CapacityThresholds:
    max_load_per_cpu: float = 0.5
    min_available_memory_bytes: int = 3 * GIB
    min_free_disk_bytes: int = 5 * GIB
    min_cpu_count: int = 2


def _existing_path(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _linux_memory() -> tuple[int | None, int | None]:
    path = Path("/proc/meminfo")
    if not path.is_file():
        return None, None
    values: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, raw = line.split(":", 1)
        match = re.search(r"\d+", raw)
        if match:
            values[key] = int(match.group()) * 1024
    return values.get("MemTotal"), values.get("MemAvailable")


def _tool_info(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    if path is None:
        return {"available": False, "path": None, "version": None}
    version_args = {
        "python3": ["--version"],
        "uv": ["--version"],
        "git": ["--version"],
        "bash": ["--version"],
        "tar": ["--version"],
        "ps": ["--version"],
        "forge": ["--version"],
        "anvil": ["--version"],
    }[name]
    try:
        result = subprocess.run(
            [path, *version_args],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
        version = next(
            (line.strip() for line in combined.splitlines() if line.strip()),
            None,
        )
    except (OSError, subprocess.TimeoutExpired):
        version = None
    return {"available": True, "path": path, "version": version}


def _process_snapshot(
    ignored_pids: set[int] | None = None,
) -> tuple[
    bool,
    list[dict[str, Any]],
    list[dict[str, Any]],
    str | None,
]:
    if os.name == "nt":
        return False, [], [], "process inspection requires Linux ps"
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,%cpu=,%mem=,args=", "--sort=-%cpu"],
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, [], [], type(exc).__name__
    return _parse_process_snapshot(
        result.stdout,
        ignored_pids=ignored_pids,
    )


def _parse_process_snapshot(
    output: str,
    *,
    ignored_pids: set[int] | None = None,
) -> tuple[bool, list[dict[str, Any]], list[dict[str, Any]], str | None]:
    ignored = ignored_pids or set()

    def process_percent(raw: str) -> float:
        if raw == "-":
            return 0.0
        value = float(raw)
        if not math.isfinite(value) or value < 0:
            raise ValueError
        return value

    top: list[dict[str, Any]] = []
    relevant: list[dict[str, Any]] = []
    column_errors = 0
    pid_errors = 0
    cpu_errors = 0
    memory_errors = 0
    for raw in output.splitlines():
        parts = raw.split(None, 3)
        if len(parts) not in {3, 4}:
            if raw.strip():
                column_errors += 1
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            pid_errors += 1
            continue
        try:
            cpu_percent = process_percent(parts[1])
        except ValueError:
            cpu_errors += 1
            continue
        try:
            memory_percent = process_percent(parts[2])
        except ValueError:
            memory_errors += 1
            continue
        arguments = parts[3] if len(parts) == 4 else ""
        first_argument = arguments.split(None, 1)[0] if arguments else ""
        command = (
            first_argument.rsplit("/", 1)[-1]
            if first_argument.startswith("/")
            else first_argument
        )
        item = {
            "pid": pid,
            "command": command,
            "cpu_percent": cpu_percent,
            "memory_percent": memory_percent,
        }
        if int(item["pid"]) in ignored:
            continue
        if len(top) < 8:
            top.append(item)
        searchable = arguments.lower()
        if any(name in searchable for name in RELEVANT_PROCESSES):
            relevant.append(item)
    if column_errors or pid_errors or cpu_errors or memory_errors:
        return (
            False,
            top,
            relevant,
            "unparsed process rows: "
            f"columns={column_errors}, pid={pid_errors}, "
            f"cpu={cpu_errors}, memory={memory_errors}",
        )
    return True, top, relevant, None


def collect_host_snapshot(workspace: Path) -> dict[str, Any]:
    host_cpu_count = os.cpu_count() or 1
    if hasattr(os, "sched_getaffinity"):
        cpu_count = max(1, len(os.sched_getaffinity(0)))
    else:
        cpu_count = host_cpu_count
    try:
        load = os.getloadavg()
    except (AttributeError, OSError):
        load = (0.0, 0.0, 0.0)
    memory_total, memory_available = _linux_memory()
    disk = shutil.disk_usage(_existing_path(workspace))
    (
        process_snapshot_ok,
        top_processes,
        relevant_processes,
        process_snapshot_error,
    ) = _process_snapshot({os.getpid()})
    tools = {name: _tool_info(name) for name in REQUIRED_TOOLS}
    return {
        "hostname": platform.node(),
        "platform": platform.system().lower(),
        "platform_release": platform.release(),
        "architecture": platform.machine(),
        "host_cpu_count": host_cpu_count,
        "cpu_count": cpu_count,
        "load_1m": load[0],
        "load_5m": load[1],
        "load_15m": load[2],
        "load_per_cpu_1m": load[0] / cpu_count,
        "memory_total_bytes": memory_total,
        "memory_available_bytes": memory_available,
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
        "tools": tools,
        "process_snapshot_ok": process_snapshot_ok,
        "process_snapshot_error": process_snapshot_error,
        "top_processes": top_processes,
        "relevant_processes": relevant_processes,
    }


def evaluate_capacity(
    snapshot: dict[str, Any],
    thresholds: CapacityThresholds,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not 0 < thresholds.max_load_per_cpu <= CapacityThresholds.max_load_per_cpu:
        reasons.append("max load per CPU weakens or invalidates the shared-host limit")
    if (
        thresholds.min_available_memory_bytes
        < CapacityThresholds.min_available_memory_bytes
    ):
        reasons.append("minimum available memory weakens the shared-host limit")
    if thresholds.min_free_disk_bytes < CapacityThresholds.min_free_disk_bytes:
        reasons.append("minimum free disk weakens the shared-host limit")
    if thresholds.min_cpu_count < CapacityThresholds.min_cpu_count:
        reasons.append("minimum CPU count weakens the shared-host limit")
    if snapshot.get("platform") != "linux":
        reasons.append("remote lab requires Linux")
    if snapshot.get("process_snapshot_ok") is not True:
        reasons.append("could not verify existing shared-host processes")
    if int(snapshot.get("cpu_count") or 0) < thresholds.min_cpu_count:
        reasons.append("insufficient CPU count")
    if float(snapshot.get("load_per_cpu_1m") or 0.0) > thresholds.max_load_per_cpu:
        reasons.append("one-minute load per CPU exceeds the shared-host limit")
    available = snapshot.get("memory_available_bytes")
    if available is None or int(available) < thresholds.min_available_memory_bytes:
        reasons.append("insufficient available memory")
    if int(snapshot.get("disk_free_bytes") or 0) < thresholds.min_free_disk_bytes:
        reasons.append("insufficient free disk")
    tools = snapshot.get("tools", {})
    for name in REQUIRED_TOOLS:
        if not tools.get(name, {}).get("available"):
            reasons.append(f"required tool is missing: {name}")
    forge_version = str(tools.get("forge", {}).get("version") or "").strip()
    anvil_version = str(tools.get("anvil", {}).get("version") or "").strip()
    if re.fullmatch(r"forge Version: 1\.7\.1", forge_version) is None:
        reasons.append("forge is not pinned Foundry 1.7.1")
    if re.fullmatch(r"anvil Version: 1\.7\.1", anvil_version) is None:
        reasons.append("anvil is not pinned Foundry 1.7.1")
    if snapshot.get("relevant_processes"):
        reasons.append("existing LoveEngine/Anvil/Forge process detected")
    return not reasons, reasons


def run_preflight(
    workspace: Path,
    thresholds: CapacityThresholds,
) -> dict[str, Any]:
    snapshot = collect_host_snapshot(workspace)
    safe, reasons = evaluate_capacity(snapshot, thresholds)
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(workspace.resolve()),
        "safe_to_run": safe,
        "reasons": reasons,
        "thresholds": asdict(thresholds),
        "host": snapshot,
        "mutated_host": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--max-load-per-cpu", type=float, default=0.5)
    parser.add_argument("--min-memory-gib", type=float, default=3.0)
    parser.add_argument("--min-disk-gib", type=float, default=5.0)
    parser.add_argument("--min-cpus", type=int, default=2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    thresholds = CapacityThresholds(
        max_load_per_cpu=args.max_load_per_cpu,
        min_available_memory_bytes=int(args.min_memory_gib * GIB),
        min_free_disk_bytes=int(args.min_disk_gib * GIB),
        min_cpu_count=args.min_cpus,
    )
    report = run_preflight(args.workspace, thresholds)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["safe_to_run"] else 4


if __name__ == "__main__":
    raise SystemExit(main())
