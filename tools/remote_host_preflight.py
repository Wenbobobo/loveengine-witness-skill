#!/usr/bin/env python3
"""Read-only capacity and toolchain gate for a shared remote experiment host."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import re
import secrets
import shutil
import stat
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


GIB = 1024**3
SCHEMA_VERSION = "loveengine.remote-host-preflight/1"
SHARED_HOST_LEASE_SCHEMA_VERSION = "loveengine.shared-host-preflight-lease/1"
SHARED_HOST_LEASE_MAX_BYTES = 64 * 1024
SHARED_HOST_LEASE_TTL_NS = 15_000_000_000
SHARED_HOST_LOCK_FILENAME = ".shared-host-core.lock"
MIN_RESERVED_SHARED_HOST_CPUS = 1
MAX_LAB_CPU_ASSIGNMENT = 2
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


def _threshold_record(thresholds: CapacityThresholds) -> dict[str, int | float]:
    """Return the exact threshold representation used in a one-shot lease."""

    return asdict(thresholds)


def _has_exact_thresholds(
    value: object,
    expected: CapacityThresholds,
) -> bool:
    if not isinstance(value, dict):
        return False
    record = _threshold_record(expected)
    if set(value) != set(record):
        return False
    return all(
        type(value[key]) is type(expected_value) and value[key] == expected_value
        for key, expected_value in record.items()
    )


def is_passing_preflight(
    preflight: object,
    workspace: Path,
    thresholds: CapacityThresholds,
) -> bool:
    """Check a report before it is used as an in-memory launch authorization."""

    if not isinstance(preflight, dict):
        return False
    host = preflight.get("host")
    return (
        preflight.get("schema_version") == SCHEMA_VERSION
        and preflight.get("workspace") == str(workspace.resolve())
        and preflight.get("safe_to_run") is True
        and preflight.get("mutated_host") is False
        and preflight.get("reasons") == []
        and isinstance(preflight.get("checked_at_monotonic_ns"), int)
        and not isinstance(preflight.get("checked_at_monotonic_ns"), bool)
        and preflight["checked_at_monotonic_ns"] > 0
        and _has_exact_thresholds(preflight.get("thresholds"), thresholds)
        and isinstance(host, dict)
        and host.get("process_snapshot_ok") is True
        and host.get("relevant_processes") == []
    )


def shared_host_lock_path() -> Path:
    """Return the per-user advisory lock for compliant shared-host labs."""

    return (
        Path.home()
        / ".local"
        / "share"
        / "loveengine-witness-lab"
        / SHARED_HOST_LOCK_FILENAME
    )


def acquire_shared_host_lock() -> int | None:
    """Acquire the one shared-host lab lock without waiting for other work."""

    if os.name == "nt":
        raise RuntimeError("shared-host locking requires POSIX")
    import fcntl

    path = shared_host_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        # A non-interactive caller can arrive with one of its standard streams
        # closed, in which case ``open`` is allowed to return 0, 1, or 2. The
        # supervisor/guardian protocol deliberately refuses those descriptors,
        # so normalize the launcher-owned lock before it crosses that boundary.
        if descriptor < 3:
            duplicate_command = getattr(fcntl, "F_DUPFD_CLOEXEC", fcntl.F_DUPFD)
            replacement = fcntl.fcntl(descriptor, duplicate_command, 3)
            try:
                os.close(descriptor)
            except OSError:
                os.close(replacement)
                raise
            descriptor = replacement
        if descriptor < 3:
            raise RuntimeError("shared-host lab lock descriptor is not non-standard")
        mode = os.fstat(descriptor).st_mode
        if not stat.S_ISREG(mode):
            raise RuntimeError("shared-host lab lock is not a regular file")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(descriptor)
            return None
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def close_shared_host_lock(descriptor: int | None) -> None:
    if descriptor is not None:
        os.close(descriptor)


def validate_shared_host_lock_descriptor(descriptor: int) -> None:
    """Require a non-standard descriptor for the canonical held lab lock.

    The launcher passes the advisory lock through the supervisor to the
    guardian.  A descriptor that merely happens to be open cannot establish
    that lifecycle boundary: standard streams and an unrelated file would let
    the launcher release the real lock while a claimed guardian still runs.
    ``flock`` on an inherited open-file description keeps the lock held by the
    guardian after the supervisor exits.
    """

    if os.name == "nt":
        raise RuntimeError("shared-host locking requires POSIX")
    if type(descriptor) is not int or descriptor < 3:
        raise ValueError("shared-host lock descriptor must be at least 3")
    path = shared_host_lock_path()
    try:
        path_lstat = path.lstat()
        expected = path.stat()
        actual = os.fstat(descriptor)
    except OSError as exc:
        raise ValueError("shared-host lock descriptor is unavailable") from exc
    if (
        stat.S_ISLNK(path_lstat.st_mode)
        or not stat.S_ISREG(expected.st_mode)
        or not stat.S_ISREG(actual.st_mode)
        or (path_lstat.st_dev, path_lstat.st_ino)
        != (expected.st_dev, expected.st_ino)
        or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)
    ):
        raise ValueError("shared-host lock descriptor is not the canonical lock")
    import fcntl

    try:
        # This is a no-op for the inherited lock's open-file description. If
        # the descriptor was separately opened, it becomes the guardian's own
        # held lock before any workload lifetime is accepted.
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise ValueError("shared-host lock descriptor is not the held lock") from exc
    try:
        current_path_lstat = path.lstat()
    except OSError as exc:
        raise ValueError("shared-host lock path changed during validation") from exc
    if (
        stat.S_ISLNK(current_path_lstat.st_mode)
        or (current_path_lstat.st_dev, current_path_lstat.st_ino)
        != (expected.st_dev, expected.st_ino)
    ):
        raise ValueError("shared-host lock path changed during validation")


def _read_lease_bytes(descriptor: int) -> bytes:
    if descriptor < 0:
        raise ValueError("shared-host preflight lease descriptor is invalid")
    chunks: list[bytes] = []
    total = 0
    try:
        while True:
            chunk = os.read(descriptor, min(4096, SHARED_HOST_LEASE_MAX_BYTES + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > SHARED_HOST_LEASE_MAX_BYTES:
                raise ValueError("shared-host preflight lease is too large")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    if not chunks:
        raise ValueError("shared-host preflight lease is empty")
    return b"".join(chunks)


def _write_lease_bytes(descriptor: int, value: bytes) -> None:
    if descriptor < 0 or len(value) > SHARED_HOST_LEASE_MAX_BYTES:
        raise ValueError("shared-host preflight lease is invalid")
    offset = 0
    try:
        while offset < len(value):
            written = os.write(descriptor, value[offset:])
            if written <= 0:
                raise OSError("could not write shared-host preflight lease")
            offset += written
    finally:
        os.close(descriptor)


def create_shared_host_preflight_lease(
    descriptor: int,
    workspace: Path,
    thresholds: CapacityThresholds,
    preflight: dict[str, Any],
    *,
    launcher_pid: int,
    launcher_start_ticks: int,
) -> str:
    """Write one short-lived, EOF-delimited lease to an inherited POSIX pipe."""

    write_started = False
    try:
        resolved_workspace = workspace.resolve()
        if launcher_pid <= 1 or launcher_start_ticks <= 0:
            raise ValueError("shared-host preflight lease launcher identity is invalid")
        if not is_passing_preflight(preflight, resolved_workspace, thresholds):
            raise ValueError("cannot lease a non-passing shared-host preflight")
        issued_at = time.monotonic_ns()
        lease_id = secrets.token_hex(16)
        value = {
            "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
            "lease_id": lease_id,
            "workspace": str(resolved_workspace),
            "thresholds": _threshold_record(thresholds),
            "preflight": preflight,
            "launcher": {
                "pid": launcher_pid,
                "process_start_ticks": launcher_start_ticks,
            },
            "issued_at_monotonic_ns": issued_at,
            "expires_at_monotonic_ns": issued_at + SHARED_HOST_LEASE_TTL_NS,
        }
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        write_started = True
        _write_lease_bytes(descriptor, encoded)
        return lease_id
    except Exception:
        if not write_started:
            os.close(descriptor)
        raise


def consume_shared_host_preflight_lease(
    descriptor: int,
    workspace: Path,
    thresholds: CapacityThresholds,
    *,
    launcher_pid: int,
    launcher_start_ticks: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Consume an inherited preflight lease exactly once and fail closed."""

    resolved_workspace = workspace.resolve()
    try:
        value = json.loads(_read_lease_bytes(descriptor).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("shared-host preflight lease is unreadable") from exc
    if not isinstance(value, dict):
        raise ValueError("shared-host preflight lease is not an object")
    if value.get("schema_version") != SHARED_HOST_LEASE_SCHEMA_VERSION:
        raise ValueError("shared-host preflight lease schema is invalid")
    lease_id = value.get("lease_id")
    if not isinstance(lease_id, str) or re.fullmatch(r"[0-9a-f]{32}", lease_id) is None:
        raise ValueError("shared-host preflight lease identifier is invalid")
    if value.get("workspace") != str(resolved_workspace):
        raise ValueError("shared-host preflight lease workspace is invalid")
    if not _has_exact_thresholds(value.get("thresholds"), thresholds):
        raise ValueError("shared-host preflight lease thresholds are invalid")
    launcher = value.get("launcher")
    if (
        not isinstance(launcher, dict)
        or launcher.get("pid") != launcher_pid
        or launcher.get("process_start_ticks") != launcher_start_ticks
    ):
        raise ValueError("shared-host preflight lease launcher identity is invalid")
    issued_at = value.get("issued_at_monotonic_ns")
    expires_at = value.get("expires_at_monotonic_ns")
    now = time.monotonic_ns()
    if (
        isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at != issued_at + SHARED_HOST_LEASE_TTL_NS
        or now < issued_at
        or now > expires_at
    ):
        raise ValueError("shared-host preflight lease is expired")
    preflight = value.get("preflight")
    if not is_passing_preflight(preflight, resolved_workspace, thresholds):
        raise ValueError("shared-host preflight lease is not a passing resource gate")
    return preflight, {
        "schema_version": SHARED_HOST_LEASE_SCHEMA_VERSION,
        "lease_id": lease_id,
        "issued_at_monotonic_ns": issued_at,
        "expires_at_monotonic_ns": expires_at,
        "launcher": launcher,
    }


def max_lab_cpu_assignment(
    available_cpu_count: int,
    requested_max_cpus: int = MAX_LAB_CPU_ASSIGNMENT,
) -> int:
    """Return the bounded lab affinity while retaining one shared-host CPU."""

    if (
        isinstance(available_cpu_count, bool)
        or not isinstance(available_cpu_count, int)
        or available_cpu_count < 0
    ):
        raise ValueError("available CPU count must be a non-negative integer")
    if (
        isinstance(requested_max_cpus, bool)
        or not isinstance(requested_max_cpus, int)
        or not 1 <= requested_max_cpus <= MAX_LAB_CPU_ASSIGNMENT
    ):
        raise ValueError("requested lab CPUs must be between 1 and 2")
    return min(requested_max_cpus, max(0, available_cpu_count - MIN_RESERVED_SHARED_HOST_CPUS))


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
        "reserved_cpu_count": MIN_RESERVED_SHARED_HOST_CPUS,
        "max_lab_cpu_assignment": max_lab_cpu_assignment(cpu_count),
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
    if max_lab_cpu_assignment(int(snapshot.get("cpu_count") or 0)) < 1:
        reasons.append("CPU reserve leaves no CPU for the lab")
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
        "checked_at_monotonic_ns": time.monotonic_ns(),
    }


def shared_host_lock_rejection(
    workspace: Path,
    thresholds: CapacityThresholds,
) -> dict[str, Any]:
    """Return a schema-complete rejection when the lab lease lock is held.

    A lock holder intentionally prevents a second host snapshot or process
    launch.  The caller still needs a rejection record that the remote runner
    can bind to its requested workspace and resource thresholds.
    """

    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": str(workspace.resolve()),
        "safe_to_run": False,
        "reasons": ["another compliant LoveEngine shared-host lab is running"],
        "thresholds": _threshold_record(thresholds),
        "host": {
            "process_snapshot_ok": False,
            "relevant_processes": [],
            "lock_held": True,
        },
        "mutated_host": False,
        "checked_at_monotonic_ns": time.monotonic_ns(),
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
