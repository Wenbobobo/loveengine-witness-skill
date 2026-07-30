#!/usr/bin/env python3
"""Start one resource-limited, loopback-only Pilot Quickstart process group."""

from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


MIN_RESERVED_SHARED_HOST_CPUS = 1
DEFAULT_WATCHDOG_SECONDS = 600
MIN_WATCHDOG_SECONDS = 60
MAX_WATCHDOG_SECONDS = 900
MAX_CORE_WATCHDOG_SECONDS = 3_600
QUICKSTART_LAUNCH_READY_SECONDS = 15


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


def select_shared_host_cpus(
    available_cpus: Iterable[int], max_cpus: int
) -> list[int]:
    """Choose a bounded affinity while reserving one CPU for a shared host."""

    validate_shared_host_limits(max_cpus, 15)
    available = sorted(set(available_cpus))
    if len(available) <= MIN_RESERVED_SHARED_HOST_CPUS:
        raise ValueError("shared-host CPU reserve leaves no CPU for the lab")
    return available[: min(max_cpus, len(available) - MIN_RESERVED_SHARED_HOST_CPUS)]


def _shared_host_cpu_affinity(max_cpus: int) -> list[int]:
    if not hasattr(os, "sched_getaffinity") or not hasattr(os, "sched_setaffinity"):
        raise RuntimeError("shared-host CPU affinity enforcement is required")
    return select_shared_host_cpus(os.sched_getaffinity(0), max_cpus)


def _limit_process(max_cpus: int, nice_increment: int) -> None:
    validate_shared_host_limits(max_cpus, nice_increment)
    os.nice(nice_increment)
    os.sched_setaffinity(0, _shared_host_cpu_affinity(max_cpus))


def validate_shared_host_limits(max_cpus: int, nice_increment: int) -> None:
    if not 1 <= max_cpus <= 2:
        raise ValueError("shared-host max CPUs must be between 1 and 2")
    if not 15 <= nice_increment <= 19:
        raise ValueError("shared-host nice increment must be between 15 and 19")


def validate_watchdog_seconds(seconds: int) -> None:
    if not MIN_WATCHDOG_SECONDS <= seconds <= MAX_WATCHDOG_SECONDS:
        raise ValueError(
            "Quickstart watchdog must be between "
            f"{MIN_WATCHDOG_SECONDS} and {MAX_WATCHDOG_SECONDS} seconds"
        )


def validate_core_watchdog_seconds(seconds: int) -> None:
    if not MIN_WATCHDOG_SECONDS <= seconds <= MAX_CORE_WATCHDOG_SECONDS:
        raise ValueError(
            "core watchdog must be between "
            f"{MIN_WATCHDOG_SECONDS} and {MAX_CORE_WATCHDOG_SECONDS} seconds"
        )


def _process_stat(pid: int) -> tuple[str, int, int, int]:
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing_parenthesis = stat.rfind(")")
    if closing_parenthesis < 0:
        raise ValueError("invalid Linux process stat")
    fields = stat[closing_parenthesis + 1 :].split()
    if len(fields) <= 19:
        raise ValueError("Linux process stat is missing process fields")
    process_group = int(fields[2])
    session = int(fields[3])
    return fields[0], process_group, session, int(fields[19])


def _owned_failed_start_state(
    pid: int,
    expected_start_ticks: int | None,
    *,
    expected_script: Path,
    expected_mode: str,
) -> str:
    """Authenticate a just-launched supervisor or watchdog before signaling it."""

    if pid <= 1 or expected_start_ticks is None or expected_start_ticks <= 0:
        return "identity_unavailable"
    try:
        state, process_group, session, start_ticks = _process_stat(pid)
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError):
        return "identity_mismatch"
    if (
        start_ticks != expected_start_ticks
        or process_group != pid
        or session != pid
    ):
        return "identity_mismatch"
    try:
        arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\x00")
    except FileNotFoundError:
        return "absent"
    except OSError:
        return "identity_mismatch"
    if (
        os.fsencode(str(expected_script.resolve())) not in arguments
        or expected_mode.encode("utf-8") not in arguments
    ):
        return "identity_mismatch"
    return "absent" if state == "Z" else "live"


def _stop_failed_start(
    process: subprocess.Popen[bytes],
    *,
    expected_start_ticks: int | None,
    expected_script: Path,
    expected_mode: str,
) -> dict:
    """Best-effort failure cleanup that never signals an unbound process group."""

    state = _owned_failed_start_state(
        process.pid,
        expected_start_ticks,
        expected_script=expected_script,
        expected_mode=expected_mode,
    )
    if state != "live":
        return {"status": f"target_{state}", "terminated": False}
    if not hasattr(os, "killpg"):
        return {"status": "process_group_control_unavailable", "terminated": False}
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "target_absent", "terminated": False}
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        state = _owned_failed_start_state(
            process.pid,
            expected_start_ticks,
            expected_script=expected_script,
            expected_mode=expected_mode,
        )
        if state != "live":
            return {"status": f"target_{state}", "terminated": False}
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return {"status": "target_absent", "terminated": False}
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return {"status": "termination_timeout", "terminated": False}
        return {"status": "killed", "terminated": True}
    return {"status": "terminated", "terminated": True}


def _owned_quickstart_state(pid: int, expected_start_ticks: int) -> str:
    if pid <= 1 or expected_start_ticks <= 0:
        return "identity_mismatch"
    try:
        state, process_group, session, start_ticks = _process_stat(pid)
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(
            b"\x00", b" "
        )
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError):
        return "identity_mismatch"
    if (
        start_ticks != expected_start_ticks
        or process_group != pid
        or session != pid
        or b"start_shared_quickstart.py --supervisor" not in command
    ):
        return "identity_mismatch"
    if state == "Z":
        return "absent"
    return "live"


def _owned_core_state(pid: int, expected_start_ticks: int) -> str:
    if pid <= 1 or expected_start_ticks <= 0:
        return "identity_mismatch"
    try:
        state, process_group, session, start_ticks = _process_stat(pid)
        command = Path(f"/proc/{pid}/cmdline").read_bytes().replace(
            b"\x00", b" "
        )
    except FileNotFoundError:
        return "absent"
    except (OSError, ValueError):
        return "identity_mismatch"
    if (
        start_ticks != expected_start_ticks
        or process_group != pid
        or session != pid
        or b"start_shared_core.py --supervisor" not in command
    ):
        return "identity_mismatch"
    if state == "Z":
        return "absent"
    return "live"


def _owned_quickstart_is_live(pid: int, expected_start_ticks: int) -> bool:
    return _owned_quickstart_state(pid, expected_start_ticks) == "live"


def _owned_session_group_has_live_members(process_group: int) -> bool | None:
    """Return membership only when the original session can be inspected.

    ``None`` is deliberately distinct from an empty process group.  In the
    leader-loss path, treating an unreadable ``/proc`` as live would authorize
    ``killpg`` without proving that the original session still owns that PID.
    """

    try:
        entries = list(Path("/proc").iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            state, member_group, session, _ = _process_stat(int(entry.name))
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            return None
        if (
            member_group == process_group
            and session == process_group
            and state != "Z"
        ):
            return True
    return False


def stop_owned_quickstart_group(pid: int, expected_start_ticks: int) -> dict:
    """Terminate only the originally launched Quickstart process group."""

    state = _owned_quickstart_state(pid, expected_start_ticks)
    if state == "identity_mismatch":
        return {"status": "target_identity_mismatch", "terminated": False}
    has_members = _owned_session_group_has_live_members(pid)
    if has_members is None:
        return {"status": "process_group_inspection_unavailable", "terminated": False}
    if not has_members:
        if state == "absent":
            return {"status": "target_absent", "terminated": False}
        return {"status": "process_group_membership_inconsistent", "terminated": False}
    if not hasattr(os, "killpg"):
        return {"status": "process_group_control_unavailable", "terminated": False}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "target_absent", "terminated": False}
    for _ in range(20):
        has_members = _owned_session_group_has_live_members(pid)
        if has_members is None:
            return {"status": "process_group_inspection_unavailable", "terminated": False}
        if not has_members:
            return {"status": "terminated", "terminated": True}
        time.sleep(0.25)
    if _owned_quickstart_state(pid, expected_start_ticks) == "identity_mismatch":
        return {"status": "target_identity_mismatch", "terminated": False}
    has_members = _owned_session_group_has_live_members(pid)
    if has_members is None:
        return {"status": "process_group_inspection_unavailable", "terminated": False}
    if not has_members:
        return {"status": "terminated", "terminated": True}
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return {"status": "target_absent", "terminated": True}
    for _ in range(8):
        has_members = _owned_session_group_has_live_members(pid)
        if has_members is None:
            return {"status": "process_group_inspection_unavailable", "terminated": False}
        if not has_members:
            return {"status": "killed", "terminated": True}
        time.sleep(0.25)
    return {"status": "termination_timeout", "terminated": False}


def stop_owned_core_group(pid: int, expected_start_ticks: int) -> dict:
    """Terminate only the originally launched core experiment process group."""

    state = _owned_core_state(pid, expected_start_ticks)
    if state == "identity_mismatch":
        return {"status": "target_identity_mismatch", "terminated": False}
    has_members = _owned_session_group_has_live_members(pid)
    if has_members is None:
        return {"status": "process_group_inspection_unavailable", "terminated": False}
    if not has_members:
        if state == "absent":
            return {"status": "target_absent", "terminated": False}
        return {"status": "process_group_membership_inconsistent", "terminated": False}
    if not hasattr(os, "killpg"):
        return {"status": "process_group_control_unavailable", "terminated": False}
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return {"status": "target_absent", "terminated": False}
    for _ in range(20):
        has_members = _owned_session_group_has_live_members(pid)
        if has_members is None:
            return {"status": "process_group_inspection_unavailable", "terminated": False}
        if not has_members:
            return {"status": "terminated", "terminated": True}
        time.sleep(0.25)
    if _owned_core_state(pid, expected_start_ticks) == "identity_mismatch":
        return {"status": "target_identity_mismatch", "terminated": False}
    has_members = _owned_session_group_has_live_members(pid)
    if has_members is None:
        return {"status": "process_group_inspection_unavailable", "terminated": False}
    if not has_members:
        return {"status": "terminated", "terminated": True}
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return {"status": "target_absent", "terminated": True}
    for _ in range(8):
        has_members = _owned_session_group_has_live_members(pid)
        if has_members is None:
            return {"status": "process_group_inspection_unavailable", "terminated": False}
        if not has_members:
            return {"status": "killed", "terminated": True}
        time.sleep(0.25)
    return {"status": "termination_timeout", "terminated": False}


def watch_owned_quickstart(
    pid: int,
    expected_start_ticks: int,
    watchdog_seconds: int,
) -> dict:
    """Bound a Quickstart lifetime even if the SSH orchestrator disappears."""

    validate_watchdog_seconds(watchdog_seconds)
    if hasattr(os, "nice"):
        try:
            os.nice(19)
        except OSError:
            pass
    deadline = time.monotonic() + watchdog_seconds
    while time.monotonic() < deadline:
        state = _owned_quickstart_state(pid, expected_start_ticks)
        if state == "identity_mismatch":
            return {
                "status": "target_identity_mismatch",
                "terminated": False,
            }
        if state == "absent":
            has_members = _owned_session_group_has_live_members(pid)
            if has_members is None:
                return {
                    "status": "process_group_inspection_unavailable",
                    "terminated": False,
                }
            if has_members:
                return stop_owned_quickstart_group(pid, expected_start_ticks)
            return {
                "status": "target_exited_before_deadline",
                "terminated": False,
            }
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return stop_owned_quickstart_group(pid, expected_start_ticks)


def watch_owned_core(
    pid: int,
    expected_start_ticks: int,
    watchdog_seconds: int,
) -> dict:
    """Bound a core experiment group if the SSH orchestrator disappears."""

    validate_core_watchdog_seconds(watchdog_seconds)
    if hasattr(os, "nice"):
        try:
            os.nice(19)
        except OSError:
            pass
    deadline = time.monotonic() + watchdog_seconds
    while time.monotonic() < deadline:
        state = _owned_core_state(pid, expected_start_ticks)
        if state == "identity_mismatch":
            return {
                "status": "target_identity_mismatch",
                "terminated": False,
            }
        if state == "absent":
            has_members = _owned_session_group_has_live_members(pid)
            if has_members is None:
                return {
                    "status": "process_group_inspection_unavailable",
                    "terminated": False,
                }
            if has_members:
                return stop_owned_core_group(pid, expected_start_ticks)
            return {
                "status": "target_exited_before_deadline",
                "terminated": False,
            }
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return stop_owned_core_group(pid, expected_start_ticks)


def _watchdog_command(
    pid: int,
    expected_start_ticks: int,
    watchdog_seconds: int,
) -> list[str]:
    validate_watchdog_seconds(watchdog_seconds)
    if pid <= 1 or expected_start_ticks <= 0:
        raise ValueError("owned Quickstart process identity must be positive")
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--watchdog",
        "--watchdog-pid",
        str(pid),
        "--watchdog-start-ticks",
        str(expected_start_ticks),
        "--watchdog-seconds",
        str(watchdog_seconds),
    ]


def _core_watchdog_command(
    pid: int,
    expected_start_ticks: int,
    watchdog_seconds: int,
) -> list[str]:
    validate_core_watchdog_seconds(watchdog_seconds)
    if pid <= 1 or expected_start_ticks <= 0:
        raise ValueError("owned core process identity must be positive")
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--core-watchdog",
        "--watchdog-pid",
        str(pid),
        "--watchdog-start-ticks",
        str(expected_start_ticks),
        "--watchdog-seconds",
        str(watchdog_seconds),
    ]


def _quickstart_command(
    root: Path,
    *,
    host: str,
    port: int,
    rpc_port: int,
) -> list[str]:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required")
    return [
        uv,
        "run",
        "loveengine",
        "pilot",
        "quickstart",
        "--root",
        str(root),
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


def _write_launch_info(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _supervisor_command(
    root: Path,
    *,
    host: str,
    port: int,
    rpc_port: int,
    log: Path,
    ready_file: Path,
    max_cpus: int,
    nice_increment: int,
    watchdog_seconds: int,
) -> list[str]:
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--supervisor",
        "--root",
        str(root),
        "--host",
        host,
        "--port",
        str(port),
        "--rpc-port",
        str(rpc_port),
        "--log",
        str(log),
        "--ready-file",
        str(ready_file),
        "--max-cpus",
        str(max_cpus),
        "--nice-increment",
        str(nice_increment),
        "--watchdog-seconds",
        str(watchdog_seconds),
    ]


def supervise_quickstart(
    root: Path,
    *,
    host: str,
    port: int,
    rpc_port: int,
    log: Path,
    ready_file: Path,
    max_cpus: int,
    nice_increment: int,
    watchdog_seconds: int,
) -> int:
    """Start the guardian before the Quickstart child, then wait for the child."""

    if os.name == "nt":
        raise RuntimeError("shared-host Quickstart supervisor requires POSIX")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("shared-host Quickstart must bind loopback")
    validate_shared_host_limits(max_cpus, nice_increment)
    validate_watchdog_seconds(watchdog_seconds)
    if not 1 <= port <= 65535 or not 1 <= rpc_port <= 65535 or port == rpc_port:
        raise ValueError("invalid or conflicting Quickstart ports")
    resolved_root = root.resolve()
    resolved_log = log.resolve()
    resolved_ready = ready_file.resolve()
    resolved_root.parent.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    resolved_ready.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "UV_CONCURRENT_DOWNLOADS": "2",
            "UV_CONCURRENT_BUILDS": "1",
            "CARGO_BUILD_JOBS": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    _limit_process(max_cpus, nice_increment)
    supervisor_pid = os.getpid()
    supervisor_start_ticks = _linux_process_start_ticks(supervisor_pid)
    watchdog: subprocess.Popen[bytes] | None = None
    watchdog_start_ticks: int | None = None
    with resolved_log.open("ab") as stream:
        try:
            watchdog = subprocess.Popen(
                _watchdog_command(
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
                _quickstart_command(
                    resolved_root,
                    host=host,
                    port=port,
                    rpc_port=rpc_port,
                ),
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=environment,
            )
        except (OSError, ValueError):
            if watchdog is not None:
                _stop_failed_start(
                    watchdog,
                    expected_start_ticks=watchdog_start_ticks,
                    expected_script=Path(__file__).resolve(),
                    expected_mode="--watchdog",
                )
            raise
        _write_launch_info(
            resolved_ready,
            {
                "pid": supervisor_pid,
                "process_start_ticks": supervisor_start_ticks,
                "root": str(resolved_root),
                "log": str(resolved_log),
                "host": host,
                "port": port,
                "rpc_port": rpc_port,
                "nice_increment": nice_increment,
                "max_cpus": max_cpus,
                "reserved_cpu_count": MIN_RESERVED_SHARED_HOST_CPUS,
                "process_kind": "quickstart_supervisor",
                "watchdog": {
                    "pid": watchdog.pid,
                    "process_start_ticks": watchdog_start_ticks,
                    "timeout_seconds": watchdog_seconds,
                    "scope": "owned_quickstart_process_group",
                },
            },
        )
        result = child.wait()
        try:
            stream.write(
                f"quickstart_child_exit={result}; "
                "supervisor_exits_for_guardian_cleanup\n".encode("utf-8")
            )
            stream.flush()
        except OSError:
            pass
        return result


def start_quickstart(
    root: Path,
    *,
    host: str,
    port: int,
    rpc_port: int,
    log: Path,
    max_cpus: int = 2,
    nice_increment: int = 15,
    watchdog_seconds: int = DEFAULT_WATCHDOG_SECONDS,
) -> dict:
    if os.name == "nt":
        raise RuntimeError("shared-host Quickstart launcher requires POSIX")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("shared-host Quickstart must bind loopback")
    validate_shared_host_limits(max_cpus, nice_increment)
    validate_watchdog_seconds(watchdog_seconds)
    if not 1 <= port <= 65535 or not 1 <= rpc_port <= 65535 or port == rpc_port:
        raise ValueError("invalid or conflicting Quickstart ports")
    if shutil.which("uv") is None:
        raise RuntimeError("uv is required")
    resolved_root = root.resolve()
    resolved_log = log.resolve()
    cpu_affinity = _shared_host_cpu_affinity(max_cpus)
    resolved_root.parent.mkdir(parents=True, exist_ok=True)
    resolved_log.parent.mkdir(parents=True, exist_ok=True)
    ready_file = resolved_log.parent / (
        f".{resolved_log.name}.{time.time_ns()}.quickstart-launch.json"
    )
    with resolved_log.open("ab") as stream:
        process = subprocess.Popen(
            _supervisor_command(
                resolved_root,
                host=host,
                port=port,
                rpc_port=rpc_port,
                log=resolved_log,
                ready_file=ready_file,
                max_cpus=max_cpus,
                nice_increment=nice_increment,
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
        deadline = time.monotonic() + QUICKSTART_LAUNCH_READY_SECONDS
        while not ready_file.is_file() and time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(
                    "quickstart supervisor exited before its guardian was ready"
                )
            time.sleep(0.05)
        if not ready_file.is_file():
            raise RuntimeError("timed out waiting for the quickstart guardian to start")
        start_info = json.loads(ready_file.read_text(encoding="utf-8"))
        if (
            start_info.get("pid") != process.pid
            or start_info.get("process_start_ticks") != process_start_ticks
            or start_info.get("process_kind") != "quickstart_supervisor"
        ):
            raise RuntimeError("quickstart supervisor launch identity is inconsistent")
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
    parser.add_argument("--root", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--rpc-port", type=int)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument("--max-cpus", type=int, default=2)
    parser.add_argument("--nice-increment", type=int, default=15)
    parser.add_argument("--watchdog-seconds", type=int, default=DEFAULT_WATCHDOG_SECONDS)
    parser.add_argument("--watchdog", action="store_true")
    parser.add_argument("--core-watchdog", action="store_true")
    parser.add_argument("--supervisor", action="store_true")
    parser.add_argument("--watchdog-pid", type=int)
    parser.add_argument("--watchdog-start-ticks", type=int)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    mode_count = sum((args.watchdog, args.core_watchdog, args.supervisor))
    if mode_count > 1:
        raise ValueError("watchdog and supervisor modes are mutually exclusive")
    if args.watchdog:
        if args.watchdog_pid is None or args.watchdog_start_ticks is None:
            raise ValueError("watchdog requires --watchdog-pid and --watchdog-start-ticks")
        result = watch_owned_quickstart(
            args.watchdog_pid,
            args.watchdog_start_ticks,
            args.watchdog_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.core_watchdog:
        if args.watchdog_pid is None or args.watchdog_start_ticks is None:
            raise ValueError("core watchdog requires --watchdog-pid and --watchdog-start-ticks")
        result = watch_owned_core(
            args.watchdog_pid,
            args.watchdog_start_ticks,
            args.watchdog_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    if args.root is None or args.port is None or args.rpc_port is None or args.log is None:
        raise ValueError("Quickstart requires --root, --port, --rpc-port, and --log")
    if args.supervisor:
        if args.ready_file is None:
            raise ValueError("Quickstart supervisor requires --ready-file")
        return supervise_quickstart(
            args.root,
            host=args.host,
            port=args.port,
            rpc_port=args.rpc_port,
            log=args.log,
            ready_file=args.ready_file,
            max_cpus=args.max_cpus,
            nice_increment=args.nice_increment,
            watchdog_seconds=args.watchdog_seconds,
        )
    result = start_quickstart(
        args.root,
        host=args.host,
        port=args.port,
        rpc_port=args.rpc_port,
        log=args.log,
        max_cpus=args.max_cpus,
        nice_increment=args.nice_increment,
        watchdog_seconds=args.watchdog_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
