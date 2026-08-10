#!/usr/bin/env python3
"""Key-only, resource-gated deployment of the Witness core lab over SSH."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

import psutil

from loveengine_witness.contract_dependency_bundle import (
    BUNDLE_RESULT_SCHEMA_VERSION,
    build_contract_dependency_bundle,
)
from remote_host_preflight import (
    SCHEMA_VERSION as REMOTE_PREFLIGHT_SCHEMA_VERSION,
    SHARED_HOST_LEASE_TTL_NS,
    max_lab_cpu_assignment,
)


ROOT = Path(__file__).resolve().parents[1]
HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
REMOTE_ROOT_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
HEX_32_PATTERN = re.compile(r"^[0-9a-f]{32}$")
TUNNEL_NODE_COUNT = 3
CORE_RECEIPT_COUNT = 3
CORE_EVENT_COUNT = 12
REMOTE_QUICKSTART_WATCHDOG_SECONDS = 600
ADDRESS_PATTERN = re.compile(r"^0x[0-9a-fA-F]{40}$")
QUICKSTART_WATCHDOG_MODE = "--watchdog"
CORE_WATCHDOG_MODE = "--core-watchdog"
CORE_WATCHDOG_RESULT_SCHEMA_VERSION = "loveengine.core-watchdog-result/1"
QUICKSTART_WATCHDOG_RESULT_SCHEMA_VERSION = "loveengine.quickstart-watchdog-result/1"
SHARED_HOST_LEASE_SCHEMA_VERSION = "loveengine.shared-host-preflight-lease/1"
GIB = 1024**3
CORE_DIAGNOSTIC_DEPENDENCIES = frozenset({"forge-std", "openzeppelin-contracts"})
CORE_DIAGNOSTIC_STAGES = frozenset(
    {
        "git_init",
        "git_remote_add",
        "git_fetch",
        "git_checkout",
        "git_rev_parse",
        "git_submodule_update",
    }
)


class ResourceGuardBlocked(RuntimeError):
    """A deliberate execution-time capacity rejection, not a lab failure."""

    def __init__(self, phase: str, preflight: dict[str, Any]) -> None:
        super().__init__(f"{phase} blocked by the shared-host resource guard")
        self.phase = phase
        self.preflight = preflight


def _remote_core_failure_message(remote_report: object) -> str:
    """Render only the bounded diagnostic produced by the core runner."""

    if (
        not isinstance(remote_report, dict)
        or remote_report.get("status") != "failed"
    ):
        return "remote core experiment did not pass"
    steps = remote_report.get("steps")
    if not isinstance(steps, list):
        return "remote core experiment did not pass"
    for step in steps:
        if (
            not isinstance(step, dict)
            or step.get("name") != "contracts-prepare"
            or isinstance(step.get("exit_code"), bool)
            or step.get("exit_code") != 4
        ):
            continue
        diagnostic = step.get("diagnostic")
        if not isinstance(diagnostic, dict) or set(diagnostic) != {
            "kind",
            "error_code",
            "dependency",
            "stage",
            "command_exit_code",
        }:
            continue
        dependency = diagnostic.get("dependency")
        stage = diagnostic.get("stage")
        command_exit_code = diagnostic.get("command_exit_code")
        if (
            diagnostic.get("kind") != "contract_prepare"
            or diagnostic.get("error_code") != "contract_dependency_install_failed"
            or not isinstance(dependency, str)
            or not isinstance(stage, str)
            or dependency not in CORE_DIAGNOSTIC_DEPENDENCIES
            or stage not in CORE_DIAGNOSTIC_STAGES
            or isinstance(command_exit_code, bool)
            or not isinstance(command_exit_code, int)
            or not 1 <= command_exit_code <= 255
        ):
            continue
        return (
            "remote core contracts-prepare failed "
            f"[dependency={dependency}, stage={stage}, "
            f"command_exit_code={command_exit_code}]"
        )
    return "remote core experiment did not pass"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_target(host: str, user: str, port: int) -> None:
    if not HOST_PATTERN.fullmatch(host) or ".." in host:
        raise ValueError("invalid SSH host")
    if not USER_PATTERN.fullmatch(user):
        raise ValueError("invalid SSH user")
    if not 1 <= port <= 65535:
        raise ValueError("invalid SSH port")


def _validate_remote_root(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not REMOTE_ROOT_PATTERN.fullmatch(value)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("remote root must be a safe relative path below $HOME")
    return path.as_posix()


REMOTE_DEPLOYMENT_CREATE_SOURCE = r'''import json
import os
import sys
from pathlib import Path, PurePosixPath


remote_root = PurePosixPath(sys.argv[1])
deployment_name = sys.argv[2]
if (
    remote_root.is_absolute()
    or any(part in {"", ".", ".."} for part in remote_root.parts)
    or not deployment_name
    or deployment_name in {".", ".."}
    or "/" in deployment_name
):
    raise SystemExit("unsafe deployment path")

home = Path.home().resolve(strict=True)
current = home
for part in remote_root.parts:
    candidate = current / part
    try:
        candidate.mkdir(mode=0o700)
    except FileExistsError:
        pass
    if candidate.is_symlink() or not candidate.is_dir():
        raise SystemExit("remote root contains a symlink or non-directory")
    current = candidate.resolve(strict=True)
    try:
        current.relative_to(home)
    except ValueError as exc:
        raise SystemExit("remote root escaped home") from exc

deployment = current / deployment_name
deployment.mkdir(mode=0o700)
if deployment.is_symlink():
    raise SystemExit("deployment is a symlink")
resolved = deployment.resolve(strict=True)
try:
    relative = resolved.relative_to(home)
except ValueError as exc:
    raise SystemExit("deployment escaped home") from exc
if resolved.parent != current:
    raise SystemExit("deployment escaped its canonical root")
os.chmod(resolved, 0o700)
print(json.dumps({"created": True, "relative": relative.as_posix()}, sort_keys=True))
'''


def _validate_remote_limits(
    *,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
    timeout_seconds: int,
) -> None:
    if (
        not math.isfinite(max_load_per_cpu)
        or not 0 < max_load_per_cpu <= 0.5
    ):
        raise ValueError("max load per CPU must be between 0 and 0.5")
    if not math.isfinite(min_memory_gib) or min_memory_gib < 3:
        raise ValueError("minimum available memory cannot be below 3 GiB")
    if not math.isfinite(min_disk_gib) or min_disk_gib < 5:
        raise ValueError("minimum free disk cannot be below 5 GiB")
    if not 60 <= timeout_seconds <= 3_600:
        raise ValueError("remote timeout must be between 60 and 3600 seconds")


def _requested_thresholds(
    *,
    max_load_per_cpu: float,
    min_memory_gib: float,
    min_disk_gib: float,
) -> dict[str, int | float]:
    """Return the exact execution-time resource policy requested by this lab."""

    return {
        "max_load_per_cpu": max_load_per_cpu,
        "min_available_memory_bytes": int(min_memory_gib * GIB),
        "min_free_disk_bytes": int(min_disk_gib * GIB),
        "min_cpu_count": 2,
    }


def _has_exact_thresholds(
    actual: object,
    expected: dict[str, int | float],
) -> bool:
    return isinstance(actual, dict) and set(actual) == set(expected) and all(
        type(actual[key]) is type(value) and actual[key] == value
        for key, value in expected.items()
    )


def _validated_execution_resource_block(
    start_info: dict[str, Any],
    *,
    label: str,
    expected_thresholds: dict[str, int | float],
    expected_workspace_relative: str,
) -> dict[str, Any]:
    """Accept only a complete, request-bound launcher resource rejection."""

    preflight = start_info.get("resource_preflight")
    expected_suffix = _validate_owned_remote_relative(expected_workspace_relative)
    if (
        start_info.get("status") != "blocked_by_resource_guard"
        or not isinstance(preflight, dict)
        or preflight.get("schema_version") != REMOTE_PREFLIGHT_SCHEMA_VERSION
        or preflight.get("safe_to_run") is not False
        or preflight.get("mutated_host") is not False
        or not isinstance(preflight.get("workspace"), str)
        or not preflight["workspace"].rstrip("/").endswith(
            "/" + expected_suffix
        )
        or not _has_exact_thresholds(preflight.get("thresholds"), expected_thresholds)
        or not isinstance(preflight.get("host"), dict)
        or isinstance(preflight.get("checked_at_monotonic_ns"), bool)
        or not isinstance(preflight.get("checked_at_monotonic_ns"), int)
        or preflight["checked_at_monotonic_ns"] <= 0
    ):
        raise RuntimeError(f"remote {label} returned an invalid resource rejection")
    reasons = preflight.get("reasons")
    if (
        not isinstance(reasons, list)
        or not reasons
        or any(not isinstance(reason, str) or not reason.strip() for reason in reasons)
    ):
        raise RuntimeError(f"remote {label} returned an invalid resource rejection")
    return preflight


def _last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
    raise RuntimeError("remote command did not emit a JSON object")


def _validated_dependency_bundle_binding(
    built: dict[str, Any],
    installed: dict[str, Any],
) -> dict[str, Any]:
    """Bind the remote installation to the exact locally verified archive."""

    shared_fields = (
        "archive",
        "archive_sha256",
        "manifest_sha256",
        "file_count",
        "total_size",
        "dependencies",
    )
    if (
        built.get("schema_version") != BUNDLE_RESULT_SCHEMA_VERSION
        or built.get("operation") != "build"
        or installed.get("schema_version") != BUNDLE_RESULT_SCHEMA_VERSION
        or installed.get("operation") != "install"
        or any(built.get(field) != installed.get(field) for field in shared_fields)
    ):
        raise RuntimeError("remote contract dependency bundle is not locally bound")
    return {
        "verified": True,
        **{field: built[field] for field in shared_fields},
    }


def _resolve_executable(value: str | None, name: str) -> str:
    if value:
        path = Path(value).resolve()
        if not path.is_file():
            raise ValueError(f"{name} executable not found: {path}")
        return str(path)
    path = shutil.which(name)
    if path is None:
        raise ValueError(f"{name} executable is required")
    return path


def _ssh_options(args: argparse.Namespace) -> list[str]:
    identity = args.identity_file.resolve()
    known_hosts = args.known_hosts.resolve()
    if not identity.is_file():
        raise ValueError(f"SSH identity file not found: {identity}")
    if not known_hosts.is_file() or known_hosts.stat().st_size == 0:
        raise ValueError(f"known_hosts file is missing or empty: {known_hosts}")
    return [
        "-F",
        "NUL" if os.name == "nt" else "/dev/null",
        "-p",
        str(args.port),
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PreferredAuthentications=publickey",
        "-o",
        "PubkeyAuthentication=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        "-o",
        "GSSAPIAuthentication=no",
        "-o",
        "HostbasedAuthentication=no",
        "-o",
        "ControlMaster=no",
        "-o",
        "ControlPath=none",
        "-o",
        "ControlPersist=no",
        "-o",
        "ForwardAgent=no",
        "-o",
        "RequestTTY=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=none",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def _scp_options(args: argparse.Namespace) -> list[str]:
    options = _ssh_options(args)
    port_index = options.index("-p")
    options[port_index] = "-P"
    return options


def _no_forwarding_options() -> list[str]:
    return ["-o", "ClearAllForwardings=yes"]


def _remote_bash(script: str) -> str:
    # All required tool locations are supplied explicitly. A login shell could
    # execute user profile code on a shared host, so never load one here.
    return _remote_nonlogin_bash(script)


def _remote_nonlogin_bash(script: str) -> str:
    return "bash -c " + shlex.quote(script)


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_http_json(
    url: str,
    *,
    timeout: float,
    process: subprocess.Popen[str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            detail = process.stderr.read().strip() if process.stderr else ""
            raise RuntimeError(
                f"SSH tunnel exited before readiness: {detail or process.returncode}"
            )
        try:
            with urlopen(url, timeout=1) as response:
                value = json.loads(response.read().decode("utf-8"))
            if isinstance(value, dict):
                return value
        except (OSError, URLError, ValueError, json.JSONDecodeError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise RuntimeError(f"timed out waiting for {url}: {last_error}")


def _wait_http_json_or_none(
    url: str,
    *,
    timeout: float,
    process: subprocess.Popen[str] | None = None,
) -> dict[str, Any] | None:
    try:
        return _wait_http_json(url, timeout=timeout, process=process)
    except RuntimeError:
        if process is not None and process.poll() is not None:
            raise
        return None


def _reap_local_process(
    process: Any,
    *,
    label: str,
    timeout_seconds: float = 5,
) -> dict[str, Any]:
    """Best-effort reap for a process handle created by this runner.

    A local SSH tunnel or node client failing during teardown must not prevent
    the independently-owned remote process group from being stopped.  Keep the
    failure machine-readable for the final report, but never raise here.
    """

    outcome: dict[str, Any] = {
        "label": label,
        "verified": False,
        "action": "reap_failed",
        "returncode": None,
        "process_tree_inspection_available": False,
        "observed_descendant_count": 0,
        "alive_descendant_pids": [],
        "complete_process_tree_cleanup_verified": False,
    }
    errors: list[str] = []

    def record_error(exc: Exception) -> None:
        errors.append(type(exc).__name__)

    descendants: list[psutil.Process] = []
    process_pid = getattr(process, "pid", None)
    tree_applicable = isinstance(process_pid, int) and process_pid > 0
    tree_available = not tree_applicable
    if tree_applicable:
        try:
            root = psutil.Process(process_pid)
            descendants = root.children(recursive=True)
            tree_available = True
        except psutil.NoSuchProcess:
            tree_available = False
            errors.append("ProcessTreeUnavailable")
        except psutil.Error as exc:
            record_error(exc)
        outcome["process_tree_inspection_available"] = tree_available
        outcome["observed_descendant_count"] = len(descendants)

    try:
        initial_returncode = process.poll()
    except (AttributeError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        record_error(exc)
        initial_returncode = None
    if initial_returncode is not None:
        outcome.update({"action": "already_exited", "returncode": initial_returncode})
        outcome["complete_process_tree_cleanup_verified"] = tree_available
        outcome["verified"] = tree_available
        if errors:
            outcome["error_types"] = errors
        return outcome

    for child in reversed(descendants):
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            record_error(exc)

    terminated = False
    try:
        process.terminate()
        terminated = True
    except (AttributeError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        record_error(exc)

    if terminated:
        try:
            returncode = process.wait(timeout=timeout_seconds)
            outcome.update({"action": "terminated", "returncode": returncode})
        except subprocess.TimeoutExpired as exc:
            record_error(exc)
            try:
                process.kill()
            except (AttributeError, OSError, TypeError, ValueError, subprocess.SubprocessError) as kill_exc:
                record_error(kill_exc)
            else:
                try:
                    returncode = process.wait(timeout=timeout_seconds)
                    outcome.update(
                        {
                            "action": "killed_after_timeout",
                            "returncode": returncode,
                        }
                    )
                except (OSError, TypeError, ValueError, subprocess.SubprocessError) as wait_exc:
                    record_error(wait_exc)
        except (OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            record_error(exc)

    try:
        final_returncode = process.poll()
    except (AttributeError, OSError, TypeError, ValueError, subprocess.SubprocessError) as exc:
        record_error(exc)
        final_returncode = None
    if final_returncode is not None:
        outcome["returncode"] = final_returncode
        if outcome["action"] == "reap_failed":
            outcome["action"] = "exited_during_reap"

    alive_descendants: list[psutil.Process] = []
    if tree_available and descendants:
        try:
            _, alive_descendants = psutil.wait_procs(
                descendants, timeout=timeout_seconds
            )
        except psutil.Error as exc:
            record_error(exc)
            alive_descendants = list(descendants)
        for child in alive_descendants:
            try:
                child.kill()
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as exc:
                record_error(exc)
        try:
            _, alive_descendants = psutil.wait_procs(
                alive_descendants, timeout=timeout_seconds
            )
        except psutil.Error as exc:
            record_error(exc)
    alive_pids: list[int] = []
    for child in alive_descendants:
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                alive_pids.append(child.pid)
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            record_error(exc)
            alive_pids.append(child.pid)
    tree_clean = tree_available and not alive_pids
    outcome["alive_descendant_pids"] = sorted(alive_pids)
    outcome["complete_process_tree_cleanup_verified"] = tree_clean
    outcome["verified"] = final_returncode is not None and tree_clean
    if errors:
        outcome["error_types"] = errors
    return outcome


def _validated_review_receipt(
    node_result: dict[str, Any],
    *,
    task_id: str,
    dispute_id: str,
    expected_node: str,
) -> dict[str, Any]:
    receipts = node_result.get("receipts")
    receipt = (
        receipts[0]
        if isinstance(receipts, list) and len(receipts) == 1
        else {}
    )
    result = receipt.get("result") if isinstance(receipt, dict) else None
    if (
        node_result.get("rejected") != 0
        or not _same_address(node_result.get("node"), expected_node)
        or not isinstance(receipt, dict)
        or receipt.get("task_id") != task_id
        or not _same_address(receipt.get("node"), expected_node)
        or receipt.get("status") != "completed"
        or not isinstance(result, dict)
        or result.get("evidence_verified") is not True
        or result.get("dispute_id") != dispute_id
    ):
        raise RuntimeError(
            "tunneled node did not return an evidence-verified bound receipt"
        )
    return receipt


def _same_address(value: object, expected: str) -> bool:
    return (
        isinstance(value, str)
        and bool(ADDRESS_PATTERN.fullmatch(value))
        and bool(ADDRESS_PATTERN.fullmatch(expected))
        and value.lower() == expected.lower()
    )


def _validated_queued_submission(
    submission: dict[str, Any],
    *,
    task_id: str,
    recipient: str,
) -> dict[str, Any]:
    task = submission.get("task")
    if (
        submission.get("queued") is not True
        or submission.get("task_id") != task_id
        or not _same_address(submission.get("recipient"), recipient)
        or not isinstance(task, dict)
        or task.get("task_id") != task_id
        or task.get("task_type") != "review_dispute"
        or not _same_address(task.get("recipient"), recipient)
    ):
        raise RuntimeError(
            "tunneled task submission did not queue for its expected recipient"
        )
    return {
        "queued": True,
        "task_id": task_id,
        "recipient": recipient,
        "task_type": "review_dispute",
    }


def _validated_quickstart_watchdog(start_info: dict[str, Any]) -> dict[str, int | str]:
    watchdog = _validated_owned_watchdog(
        start_info,
        label="Quickstart",
        process_kind="quickstart_supervisor",
        scope="owned_quickstart_process_group",
        expected_timeout_seconds=REMOTE_QUICKSTART_WATCHDOG_SECONDS,
    )
    reported_path = start_info.get("watchdog", {}).get("result_path")
    if not isinstance(reported_path, str) or not reported_path.endswith(
        "/.quickstart-watchdog-result.json"
    ):
        raise RuntimeError("remote Quickstart watchdog lacks a terminal result path")
    return {**watchdog, "result_path": reported_path}


def _validated_core_watchdog(
    start_info: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, int | str]:
    watchdog = _validated_owned_watchdog(
        start_info,
        label="core",
        process_kind="core_supervisor",
        scope="owned_core_process_group",
        expected_timeout_seconds=timeout_seconds,
    )
    reported_path = start_info.get("watchdog", {}).get("result_path")
    if not isinstance(reported_path, str) or not reported_path.endswith(
        "/.core-watchdog-result.json"
    ):
        raise RuntimeError("remote core watchdog lacks a terminal result path")
    return {**watchdog, "result_path": reported_path}


def _validated_core_watchdog_result(
    result: dict[str, Any],
    *,
    watchdog: dict[str, int | str],
    target_pid: int,
    target_process_start_ticks: int,
    require_normal_completion: bool,
) -> dict[str, Any]:
    """Validate a core guardian's persisted terminal result, not its absence."""

    return _validated_watchdog_terminal_result(
        result,
        schema_version=CORE_WATCHDOG_RESULT_SCHEMA_VERSION,
        watchdog=watchdog,
        target_pid=target_pid,
        target_process_start_ticks=target_process_start_ticks,
        require_normal_completion=require_normal_completion,
        label="core",
    )


def _validated_quickstart_watchdog_result(
    result: dict[str, Any],
    *,
    watchdog: dict[str, int | str],
    target_pid: int,
    target_process_start_ticks: int,
    require_normal_completion: bool,
) -> dict[str, Any]:
    return _validated_watchdog_terminal_result(
        result,
        schema_version=QUICKSTART_WATCHDOG_RESULT_SCHEMA_VERSION,
        watchdog=watchdog,
        target_pid=target_pid,
        target_process_start_ticks=target_process_start_ticks,
        require_normal_completion=require_normal_completion,
        label="Quickstart",
    )


def _validated_watchdog_terminal_result(
    result: dict[str, Any],
    *,
    schema_version: str,
    watchdog: dict[str, int | str],
    target_pid: int,
    target_process_start_ticks: int,
    require_normal_completion: bool,
    label: str,
) -> dict[str, Any]:
    """Validate the guardian's persisted terminal result, not only its absence."""

    guardian = result.get("guardian")
    target = result.get("target")
    if (
        result.get("schema_version") != schema_version
        or not isinstance(guardian, dict)
        or guardian.get("pid") != watchdog.get("pid")
        or guardian.get("process_start_ticks")
        != watchdog.get("process_start_ticks")
        or not isinstance(target, dict)
        or target.get("pid") != target_pid
        or target.get("process_start_ticks") != target_process_start_ticks
        or result.get("timeout_seconds") != watchdog.get("timeout_seconds")
        or not isinstance(result.get("status"), str)
        or not isinstance(result.get("terminated"), bool)
        or isinstance(result.get("recorded_at_monotonic_ns"), bool)
        or not isinstance(result.get("recorded_at_monotonic_ns"), int)
        or result["recorded_at_monotonic_ns"] <= 0
    ):
        raise RuntimeError(f"remote {label} watchdog terminal result is invalid")
    if require_normal_completion:
        if (
            result["status"] != "target_exited_before_deadline"
            or result["terminated"] is not False
        ):
            raise RuntimeError(
                f"remote {label} watchdog did not observe normal completion"
            )
    elif result["status"] not in {
        "target_exited_before_deadline",
        "terminated",
        "killed",
        "target_absent",
    }:
        raise RuntimeError(f"remote {label} watchdog ended in an unsafe state")
    return result


def _validated_remote_launch_lease(
    start_info: dict[str, Any],
    *,
    workspace_key: str,
    supervisor_pid: int,
    supervisor_start_ticks: int,
    label: str,
    expected_thresholds: dict[str, int | float],
    expected_max_cpus: int,
    expected_workspace_relative: str | None = None,
) -> dict[str, Any]:
    """Bind an accepted FD lease to the observed supervisor and workspace."""

    workspace = start_info.get(workspace_key)
    preflight = start_info.get("resource_preflight")
    binding = start_info.get("resource_preflight_binding")
    if (
        start_info.get("resource_preflight_source") != "launcher_fd_lease"
        or not isinstance(workspace, str)
        or not isinstance(preflight, dict)
        or preflight.get("schema_version") != REMOTE_PREFLIGHT_SCHEMA_VERSION
        or preflight.get("workspace") != workspace
        or preflight.get("safe_to_run") is not True
        or preflight.get("mutated_host") is not False
        or preflight.get("reasons") != []
        or not isinstance(binding, dict)
    ):
        raise RuntimeError(f"remote {label} launch lacks a bound passing preflight")
    thresholds = preflight.get("thresholds")
    host = preflight.get("host")
    if (
        not _has_exact_thresholds(thresholds, expected_thresholds)
        or not isinstance(host, dict)
        or host.get("process_snapshot_ok") is not True
        or host.get("relevant_processes") != []
    ):
        raise RuntimeError(f"remote {label} launch has weakened resource thresholds")
    host_cpu_count = host.get("cpu_count")
    if (
        isinstance(host_cpu_count, bool)
        or not isinstance(host_cpu_count, int)
        or max_lab_cpu_assignment(host_cpu_count) not in {1, 2}
        or start_info.get("nice_increment") != 15
        or start_info.get("process_nice") != 15
        or isinstance(start_info.get("max_cpus"), bool)
        or not isinstance(start_info.get("max_cpus"), int)
        or start_info["max_cpus"] != expected_max_cpus
        or not 1 <= expected_max_cpus <= max_lab_cpu_assignment(host_cpu_count)
        or not isinstance(start_info.get("cpu_affinity"), list)
        or not start_info["cpu_affinity"]
        or len(start_info["cpu_affinity"]) > max_lab_cpu_assignment(host_cpu_count)
        or any(
            isinstance(cpu, bool) or not isinstance(cpu, int)
            for cpu in start_info["cpu_affinity"]
        )
        or len(set(start_info["cpu_affinity"])) != len(start_info["cpu_affinity"])
    ):
        raise RuntimeError(f"remote {label} launch lacks enforced shared-host limits")
    if expected_workspace_relative is not None:
        suffix = _validate_owned_remote_relative(expected_workspace_relative)
        if not workspace.rstrip("/").endswith("/" + suffix):
            raise RuntimeError(f"remote {label} launch has an unexpected workspace")
    launcher = binding.get("launcher")
    supervisor = binding.get("supervisor")
    issued_at = binding.get("issued_at_monotonic_ns")
    expires_at = binding.get("expires_at_monotonic_ns")
    checked_at = preflight.get("checked_at_monotonic_ns")
    if (
        binding.get("schema_version") != SHARED_HOST_LEASE_SCHEMA_VERSION
        or not isinstance(binding.get("lease_id"), str)
        or HEX_32_PATTERN.fullmatch(binding["lease_id"]) is None
        or not isinstance(launcher, dict)
        or not isinstance(supervisor, dict)
        or supervisor.get("pid") != supervisor_pid
        or supervisor.get("process_start_ticks") != supervisor_start_ticks
        or any(
            isinstance(identity.get(key), bool)
            or not isinstance(identity.get(key), int)
            or identity[key] <= 1
            for identity in (launcher, supervisor)
            for key in ("pid", "process_start_ticks")
        )
        or isinstance(checked_at, bool)
        or not isinstance(checked_at, int)
        or checked_at <= 0
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at != issued_at + SHARED_HOST_LEASE_TTL_NS
        or checked_at > issued_at
    ):
        raise RuntimeError(f"remote {label} launch has an invalid FD lease binding")
    return binding


def _validated_owned_watchdog(
    start_info: dict[str, Any],
    *,
    label: str,
    process_kind: str,
    scope: str,
    expected_timeout_seconds: int,
) -> dict[str, int | str]:
    watchdog = start_info.get("watchdog")
    if not isinstance(watchdog, dict):
        raise RuntimeError(f"remote {label} did not start an owned watchdog")
    try:
        pid = int(watchdog["pid"])
        process_start_ticks = int(watchdog["process_start_ticks"])
        reported_timeout_seconds = int(watchdog["timeout_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"remote {label} watchdog identity is invalid") from exc
    if (
        pid <= 1
        or process_start_ticks <= 0
        or reported_timeout_seconds != expected_timeout_seconds
        or start_info.get("process_kind") != process_kind
        or watchdog.get("scope") != scope
    ):
        raise RuntimeError(f"remote {label} watchdog is not bounded to its group")
    return {
        "pid": pid,
        "process_start_ticks": process_start_ticks,
        "timeout_seconds": reported_timeout_seconds,
        "scope": scope,
    }


def _validate_remote_core_acceptance(
    remote_report: dict[str, Any],
    offline_transcript: dict[str, Any],
    *,
    expected_thresholds: dict[str, int | float],
    expected_max_cpus: int,
) -> dict[str, Any]:
    """Fail closed unless the downloaded core report and transcript agree."""

    if (
        isinstance(expected_max_cpus, bool)
        or not isinstance(expected_max_cpus, int)
        or expected_max_cpus < 1
    ):
        raise ValueError("expected core CPU assignment must be positive")

    required_report = {
        "schema_version": "loveengine.core-experiment-report/2",
        "status": "passed",
        "environment": "local_anvil",
        "actors_simulated": True,
        "resource_profile": "shared_host",
        "gate_ready": True,
        "recovery_tests": True,
        "observation_receipts": CORE_RECEIPT_COUNT,
        "review_receipts": CORE_RECEIPT_COUNT,
    }
    for field, expected in required_report.items():
        if remote_report.get(field) != expected:
            raise RuntimeError(
                f"remote core report has unexpected {field}: "
                f"{remote_report.get(field)!r}"
            )
    verification = remote_report.get("verification")
    if not isinstance(verification, dict) or verification != {
        "offline": "offline_integrity",
        "rpc": "chain_consistency",
        "rpc_with_policy": "chain_verified",
    }:
        raise RuntimeError("remote core report has incomplete verification levels")
    preparation = remote_report.get("contract_preparation")
    if (
        not isinstance(preparation, dict)
        or preparation.get("schema_version")
        != "loveengine.contract-preparation/1"
        or preparation.get("prepared") is not True
        or not isinstance(preparation.get("artifacts"), dict)
        or not preparation["artifacts"]
    ):
        raise RuntimeError("remote core report lacks verified contract preparation")
    resource_preflight = remote_report.get("resource_preflight")
    if (
        not isinstance(resource_preflight, dict)
        or resource_preflight.get("schema_version")
        != REMOTE_PREFLIGHT_SCHEMA_VERSION
        or resource_preflight.get("safe_to_run") is not True
        or resource_preflight.get("mutated_host") is not False
        or resource_preflight.get("reasons") != []
        or isinstance(resource_preflight.get("checked_at_monotonic_ns"), bool)
        or not isinstance(resource_preflight.get("checked_at_monotonic_ns"), int)
        or resource_preflight["checked_at_monotonic_ns"] <= 0
    ):
        raise RuntimeError("remote core report lacks a passing resource preflight")
    resource_host = resource_preflight.get("host")
    resource_limits = remote_report.get("resource_limits")
    if (
        remote_report.get("resource_preflight_source") != "launcher_fd_lease"
        or not isinstance(remote_report.get("resource_preflight_binding"), dict)
    ):
        raise RuntimeError("remote core report lacks a one-shot launcher lease")
    lease_binding = remote_report["resource_preflight_binding"]
    launcher = lease_binding.get("launcher")
    supervisor = lease_binding.get("supervisor")
    issued_at = lease_binding.get("issued_at_monotonic_ns")
    expires_at = lease_binding.get("expires_at_monotonic_ns")
    if (
        lease_binding.get("schema_version") != SHARED_HOST_LEASE_SCHEMA_VERSION
        or not isinstance(lease_binding.get("lease_id"), str)
        or HEX_32_PATTERN.fullmatch(lease_binding["lease_id"]) is None
        or not isinstance(launcher, dict)
        or not isinstance(supervisor, dict)
        or any(
            isinstance(identity.get(key), bool)
            or not isinstance(identity.get(key), int)
            or identity[key] <= 1
            for identity in (launcher, supervisor)
            for key in ("pid", "process_start_ticks")
        )
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
        or expires_at != issued_at + SHARED_HOST_LEASE_TTL_NS
        or resource_preflight["checked_at_monotonic_ns"] > issued_at
    ):
        raise RuntimeError("remote core report has an invalid launcher lease binding")
    host_cpu_count = (
        resource_host.get("cpu_count") if isinstance(resource_host, dict) else None
    )
    expected_cpu_cap: int | None = None
    if (
        isinstance(host_cpu_count, int)
        and not isinstance(host_cpu_count, bool)
        and host_cpu_count >= 0
    ):
        expected_cpu_cap = max_lab_cpu_assignment(host_cpu_count)
    if (
        not isinstance(resource_host, dict)
        or resource_host.get("process_snapshot_ok") is not True
        or resource_host.get("relevant_processes") != []
        or isinstance(host_cpu_count, bool)
        or not isinstance(host_cpu_count, int)
        or resource_host.get("reserved_cpu_count") != 1
        or isinstance(resource_host.get("max_lab_cpu_assignment"), bool)
        or resource_host.get("max_lab_cpu_assignment") != expected_cpu_cap
        or expected_cpu_cap not in {1, 2}
        or not isinstance(resource_limits, dict)
        or isinstance(resource_limits.get("nice_increment"), bool)
        or resource_limits.get("nice_increment") != 15
        or isinstance(resource_limits.get("process_nice"), bool)
        or resource_limits.get("process_nice") != 15
        or not isinstance(resource_limits.get("cpu_affinity"), list)
        or not resource_limits["cpu_affinity"]
        or len(resource_limits["cpu_affinity"]) != expected_max_cpus
        or expected_max_cpus > expected_cpu_cap
        or any(
            isinstance(cpu, bool) or not isinstance(cpu, int)
            for cpu in resource_limits["cpu_affinity"]
        )
        or len(set(resource_limits["cpu_affinity"]))
        != len(resource_limits["cpu_affinity"])
    ):
        raise RuntimeError("remote core report lacks enforced shared-host limits")
    thresholds = resource_preflight.get("thresholds")
    if not _has_exact_thresholds(thresholds, expected_thresholds):
        raise RuntimeError("remote core report has unexpected resource thresholds")

    required_offline = {
        "valid": True,
        "verification_level": "offline_integrity",
        "chain_verified": False,
        "trust_bound": False,
        "environment": "local_anvil",
        "actors_simulated": True,
        "event_count": CORE_EVENT_COUNT,
        "observation_receipts": CORE_RECEIPT_COUNT,
        "review_receipts": CORE_RECEIPT_COUNT,
        "gate_ready": True,
    }
    for field, expected in required_offline.items():
        if offline_transcript.get(field) != expected:
            raise RuntimeError(
                f"offline transcript has unexpected {field}: "
                f"{offline_transcript.get(field)!r}"
            )
    if (
        not isinstance(remote_report.get("run_id"), str)
        or offline_transcript.get("run_id") != remote_report["run_id"]
    ):
        raise RuntimeError("remote core report and offline transcript run IDs differ")
    return {
        "accepted": True,
        "run_id": remote_report["run_id"],
        "event_count": CORE_EVENT_COUNT,
        "observation_receipts": CORE_RECEIPT_COUNT,
        "review_receipts": CORE_RECEIPT_COUNT,
        "verification_level": offline_transcript["verification_level"],
    }


def _validate_owned_remote_relative(value: str) -> str:
    if not isinstance(value, str):
        raise RuntimeError("remote artifact escaped the unique deployment directory")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)
    ):
        raise RuntimeError("remote artifact escaped the unique deployment directory")
    return path.as_posix()


def _owned_remote_file(deployment_rel: str, relative: str) -> str:
    return f"~/{_validate_remote_root(deployment_rel)}/{_validate_owned_remote_relative(relative)}"


REMOTE_LISTENER_INSPECTION_SOURCE = r'''import argparse
import ipaddress
import json
import os
from pathlib import Path


def process_stat(pid):
    stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    closing = stat.rfind(")")
    if closing < 0:
        raise ValueError("invalid process stat")
    fields = stat[closing + 1:].split()
    if len(fields) <= 19:
        raise ValueError("incomplete process stat")
    return fields[0], int(fields[2]), int(fields[3]), int(fields[19])


def group_members(leader):
    members = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            state, group, session, _ = process_stat(int(entry.name))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as exc:
            raise RuntimeError("process group inspection failed") from exc
        if group == leader and session == leader and state != "Z":
            members.append(int(entry.name))
    return sorted(members)


def socket_inodes(pids):
    found = set()
    for pid in pids:
        fd_root = Path(f"/proc/{pid}/fd")
        try:
            fds = list(fd_root.iterdir())
        except OSError as exc:
            raise RuntimeError("process fd inspection failed") from exc
        for fd in fds:
            try:
                target = os.readlink(fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise RuntimeError("process fd target inspection failed") from exc
            if target.startswith("socket:[") and target.endswith("]"):
                found.add(target[8:-1])
    return found


def decode_address(raw, family):
    address_hex, port_hex = raw.split(":", 1)
    if family == "ipv4":
        address = str(ipaddress.IPv4Address(bytes.fromhex(address_hex)[::-1]))
    else:
        packed = bytes.fromhex(address_hex)
        packed = b"".join(
            packed[index:index + 4][::-1] for index in range(0, 16, 4)
        )
        address = str(ipaddress.IPv6Address(packed))
    return address, int(port_hex, 16)


def listeners(path, family, inodes):
    result = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[1:]
    except OSError as exc:
        raise RuntimeError("kernel listener table inspection failed") from exc
    for line in lines:
        columns = line.split()
        if len(columns) < 10 or columns[3] != "0A":
            continue
        if columns[9] not in inodes:
            continue
        try:
            address, port = decode_address(columns[1], family)
        except (ValueError, ipaddress.AddressValueError):
            continue
        result.append({"address": address, "family": family, "port": port})
    return result


parser = argparse.ArgumentParser()
parser.add_argument("--pid", type=int, required=True)
parser.add_argument("--start-ticks", type=int, required=True)
parser.add_argument("--expected-port", action="append", type=int, required=True)
parser.add_argument("--expected-script", type=Path, required=True)
args = parser.parse_args()
expected_ports = sorted(set(args.expected_port))
result = {
    "schema_version": "loveengine.remote-listener-inspection/1",
    "pid": args.pid,
    "process_start_ticks": args.start_ticks,
    "expected_ports": expected_ports,
    "available": False,
    "loopback_only": False,
    "expected_ports_listening": False,
    "owned_group_process_count": 0,
    "listener_count": 0,
    "non_loopback_listener_count": 0,
    "unexpected_listener_count": 0,
    "listeners": [],
}
try:
    state, group, session, start_ticks = process_stat(args.pid)
    arguments = Path(f"/proc/{args.pid}/cmdline").read_bytes().split(b"\x00")
    expected_script = os.fsencode(str(args.expected_script.resolve()))
except (OSError, ValueError):
    result["reason"] = "process_unavailable"
else:
    if (
        state == "Z"
        or group != args.pid
        or session != args.pid
        or start_ticks != args.start_ticks
        or len(arguments) < 3
        or arguments[1] != expected_script
        or arguments[2] != b"--supervisor"
        or arguments.count(expected_script) != 1
        or arguments.count(b"--supervisor") != 1
    ):
        result["reason"] = "process_identity_mismatch"
    else:
        try:
            members = group_members(args.pid)
            if args.pid not in members:
                raise RuntimeError("process group leader disappeared")
            inodes = socket_inodes(members)
            found = listeners(Path("/proc/net/tcp"), "ipv4", inodes)
            found += listeners(Path("/proc/net/tcp6"), "ipv6", inodes)
        except (OSError, RuntimeError, ValueError):
            result["reason"] = "listener_inspection_unavailable"
        else:
            found.sort(key=lambda item: (item["port"], item["family"], item["address"]))
            observed_ports = {item["port"] for item in found}
            non_loopback = [
                item for item in found
                if not ipaddress.ip_address(item["address"]).is_loopback
            ]
            unexpected = [item for item in found if item["port"] not in expected_ports]
            result.update(
                {
                    "available": True,
                    "owned_group_process_count": len(members),
                    "listener_count": len(found),
                    "non_loopback_listener_count": len(non_loopback),
                    "unexpected_listener_count": len(unexpected),
                    "listeners": found,
                    "loopback_only": not non_loopback,
                    "expected_ports_listening": set(expected_ports).issubset(observed_ports),
                }
            )
print(json.dumps(result, sort_keys=True))
'''


def _remote_listener_inspection_command(
    pid: int,
    process_start_ticks: int,
    expected_ports: tuple[int, int],
    *,
    expected_script_relative: str,
) -> str:
    if pid <= 1 or process_start_ticks <= 0:
        raise ValueError("owned process identity must be positive")
    if (
        len(expected_ports) != 2
        or len(set(expected_ports)) != 2
        or any(not 1 <= port <= 65535 for port in expected_ports)
    ):
        raise ValueError("listener inspection requires two distinct valid ports")
    expected_script_relative = _validate_watchdog_script_relative(
        expected_script_relative
    )
    ports = " ".join(f"--expected-port {port}" for port in expected_ports)
    return _remote_nonlogin_bash(
        "python3 - "
        f"--pid {pid} --start-ticks {process_start_ticks} {ports} "
        f"--expected-script \"$HOME/{expected_script_relative}/tools/"
        "start_shared_quickstart.py\""
    )


def _validated_remote_listener_inspection(
    inspection: dict[str, Any],
    *,
    pid: int,
    process_start_ticks: int,
    expected_ports: tuple[int, int],
) -> dict[str, Any]:
    expected = sorted(expected_ports)
    if (
        inspection.get("schema_version")
        != "loveengine.remote-listener-inspection/1"
        or inspection.get("pid") != pid
        or inspection.get("process_start_ticks") != process_start_ticks
        or inspection.get("expected_ports") != expected
        or inspection.get("available") is not True
        or inspection.get("loopback_only") is not True
        or inspection.get("expected_ports_listening") is not True
        or inspection.get("non_loopback_listener_count") != 0
        or inspection.get("unexpected_listener_count") != 0
        or not isinstance(inspection.get("listeners"), list)
        or int(inspection.get("owned_group_process_count") or 0) < 1
        or int(inspection.get("listener_count") or 0) < len(expected)
    ):
        raise RuntimeError(
            "remote Quickstart listener inspection did not prove loopback-only "
            "expected listeners"
        )
    observed_ports = {
        item.get("port")
        for item in inspection["listeners"]
        if isinstance(item, dict)
    }
    if not set(expected).issubset(observed_ports):
        raise RuntimeError("remote listener inspection missed an expected port")
    return inspection


def _validate_supervisor_script_name(value: str) -> str:
    if value not in {"start_shared_core.py", "start_shared_quickstart.py"}:
        raise ValueError("unsupported shared-host supervisor script")
    return value


def _owned_supervisor_argument_check(
    *,
    expected_script_relative: str,
    expected_script_name: str,
) -> str:
    """Return Bash that binds a process-group leader to this deployment."""

    expected_script_relative = _validate_watchdog_script_relative(
        expected_script_relative
    )
    expected_script_name = _validate_supervisor_script_name(expected_script_name)
    return (
        "if [ ! -r \"/proc/$pid/cmdline\" ]; then exit 9; fi; "
        "argv=(); "
        "while IFS= read -r -d '' argument; do argv+=(\"$argument\"); done "
        "< \"/proc/$pid/cmdline\"; "
        "if [ \"${#argv[@]}\" -eq 0 ]; then exit 9; fi; "
        f"expected_script_relative={shlex.quote(expected_script_relative)}; "
        "if ! expected_script_dir=$(cd \"$HOME/$expected_script_relative/tools\" "
        "&& pwd -P); then exit 9; fi; "
        f"expected_script=\"$expected_script_dir/{expected_script_name}\"; "
        "if [ \"${#argv[@]}\" -lt 3 ] || "
        "[ \"${argv[1]}\" != \"$expected_script\" ] || "
        "[ \"${argv[2]}\" != \"--supervisor\" ]; then exit 9; fi; "
        "script_count=0; supervisor_count=0; "
        "for argument in \"${argv[@]}\"; do "
        "if [ \"$argument\" = \"$expected_script\" ]; then "
        "script_count=$((script_count + 1)); fi; "
        "if [ \"$argument\" = \"--supervisor\" ]; then "
        "supervisor_count=$((supervisor_count + 1)); fi; "
        "done; "
        "if [ \"$script_count\" != 1 ] || [ \"$supervisor_count\" != 1 ]; "
        "then exit 9; fi; "
    )


def _owned_group_stop_command(
    pid: int,
    process_start_ticks: int,
    *,
    expected_script_relative: str,
    expected_script_name: str,
) -> str:
    if pid <= 1 or process_start_ticks <= 0:
        raise ValueError("owned process identity must be positive")
    return _remote_nonlogin_bash(
        "set -eu; "
        f"pid={pid}; "
        f"expected_start={process_start_ticks}; "
        "group_has_live_members() { "
        "snapshot=$(ps -eo pgid=,sid=,stat=) || return 2; "
        "while read -r pgid sid state; do "
        "if [ \"$pgid\" = \"$pid\" ] && [ \"$sid\" = \"$pid\" ]; then "
        "case \"$state\" in Z*) ;; *) return 0 ;; esac; "
        "fi; "
        "done <<< \"$snapshot\"; "
        "return 1; "
        "}; "
        "stat=$(cat \"/proc/$pid/stat\" 2>/dev/null || true); "
        "if [ -z \"$stat\" ]; then "
        "if [ -e \"/proc/$pid/stat\" ]; then exit 9; fi; "
        "if group_has_live_members; then exit 9; else group_status=$?; fi; "
        "if [ \"$group_status\" -eq 1 ]; then exit 0; fi; "
        "exit 9; "
        "else "
        "tail=${stat##*) }; "
        "set -- $tail; "
        "state=$1; "
        "eval \"current_pgrp=\\${3}\"; "
        "eval \"current_session=\\${4}\"; "
        "eval \"current_start=\\${20}\"; "
        "if [ \"$current_pgrp\" != \"$pid\" ] || [ \"$current_session\" != \"$pid\" ]; then exit 9; fi; "
        "if [ \"$current_start\" != \"$expected_start\" ]; then exit 9; fi; "
        "if [ \"$state\" = \"Z\" ]; then "
        "if group_has_live_members; then exit 9; else group_status=$?; fi; "
        "if [ \"$group_status\" -eq 1 ]; then exit 0; fi; "
        "exit 9; "
        "else "
        + _owned_supervisor_argument_check(
            expected_script_relative=expected_script_relative,
            expected_script_name=expected_script_name,
        )
        + "fi; "
        "fi; "
        "if group_has_live_members; then :; else "
        "group_status=$?; "
        "if [ \"$group_status\" -eq 1 ]; then exit 0; fi; "
        "exit 9; "
        "fi; "
        "kill -TERM -- \"-$pid\" 2>/dev/null || true; "
        "for _ in {1..20}; do "
        "if group_has_live_members; then :; else "
        "group_status=$?; "
        "if [ \"$group_status\" -eq 1 ]; then exit 0; fi; "
        "exit 9; "
        "fi; "
        "sleep 0.25; "
        "done; "
        "kill -KILL -- \"-$pid\" 2>/dev/null || true; "
        "for _ in {1..8}; do "
        "if group_has_live_members; then :; else "
        "group_status=$?; "
        "if [ \"$group_status\" -eq 1 ]; then exit 0; fi; "
        "exit 9; "
        "fi; "
        "sleep 0.25; "
        "done; "
        "exit 10"
    )


def _owned_group_absence_command(
    pid: int,
    process_start_ticks: int,
    *,
    expected_script_relative: str,
    expected_script_name: str,
    attempts: int = 20,
) -> str:
    if pid <= 1 or process_start_ticks <= 0:
        raise ValueError("owned process identity must be positive")
    if not 1 <= attempts <= 100_000:
        raise ValueError("owned process wait attempts must be bounded")
    return _remote_nonlogin_bash(
        "set -eu; "
        f"pid={pid}; "
        f"expected_start={process_start_ticks}; "
        "group_has_live_members() { "
        "snapshot=$(ps -eo pgid=,sid=,stat=) || return 2; "
        "while read -r pgid sid state; do "
        "if [ \"$pgid\" = \"$pid\" ] && [ \"$sid\" = \"$pid\" ]; then "
        "case \"$state\" in Z*) ;; *) return 0 ;; esac; "
        "fi; "
        "done <<< \"$snapshot\"; "
        "return 1; "
        "}; "
        f"for _ in {{1..{attempts}}}; do "
        "stat=$(cat \"/proc/$pid/stat\" 2>/dev/null || true); "
        "if [ -z \"$stat\" ]; then "
        "if [ -e \"/proc/$pid/stat\" ]; then exit 9; fi; "
        "else "
        "tail=${stat##*) }; "
        "set -- $tail; "
        "state=$1; "
        "eval \"current_pgrp=\\${3}\"; "
        "eval \"current_session=\\${4}\"; "
        "eval \"current_start=\\${20}\"; "
        "if [ \"$current_pgrp\" != \"$pid\" ] || [ \"$current_session\" != \"$pid\" ]; then exit 9; fi; "
        "if [ \"$current_start\" != \"$expected_start\" ]; then exit 9; fi; "
        "if [ \"$state\" != \"Z\" ]; then "
        + _owned_supervisor_argument_check(
            expected_script_relative=expected_script_relative,
            expected_script_name=expected_script_name,
        )
        + "fi; "
        "fi; "
        "if group_has_live_members; then :; else "
        "group_status=$?; "
        "if [ \"$group_status\" -eq 1 ]; then exit 0; fi; "
        "exit 9; "
        "fi; "
        "sleep 0.25; "
        "done; "
        "exit 10"
    )


def _validate_watchdog_mode(mode: str) -> str:
    if mode not in {QUICKSTART_WATCHDOG_MODE, CORE_WATCHDOG_MODE}:
        raise ValueError("unsupported owned watchdog mode")
    return mode


def _validate_watchdog_script_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not REMOTE_ROOT_PATTERN.fullmatch(value)
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("expected watchdog script must be below remote $HOME")
    return path.as_posix()


def _owned_watchdog_argument_check(
    *,
    target_pid: int,
    target_process_start_ticks: int,
    timeout_seconds: int,
    expected_script_relative: str,
    mode: str,
) -> str:
    """Return Bash which checks the watchdog's exact NUL-delimited argv.

    Looking for text in a flattened command line allows an extra, malicious
    option to imitate the expected target.  The watchdog is a trust boundary,
    so require exactly one deployment-local script, mode, PID, start-tick, and
    timeout option.
    """

    expected_mode = _validate_watchdog_mode(mode)
    expected_script_relative = _validate_watchdog_script_relative(
        expected_script_relative
    )
    return (
        "argv=(); "
        "while IFS= read -r -d '' argument; do argv+=(\"$argument\"); done "
        "< \"/proc/$pid/cmdline\"; "
        f"expected_script_relative={shlex.quote(expected_script_relative)}; "
        "if ! expected_script_dir=$(cd \"$HOME/$expected_script_relative/tools\" "
        "&& pwd -P); then exit 9; fi; "
        "expected_script=\"$expected_script_dir/start_shared_quickstart.py\"; "
        f"expected_mode={shlex.quote(expected_mode)}; "
        f"expected_target_pid={target_pid}; "
        f"expected_target_start={target_process_start_ticks}; "
        f"expected_timeout={timeout_seconds}; "
        "if [ \"${#argv[@]}\" -ne 13 ] || "
        "[ \"${argv[1]}\" != \"$expected_script\" ] || "
        "[ \"${argv[2]}\" != \"$expected_mode\" ] || "
        "[ \"${argv[3]}\" != \"--watchdog-pid\" ] || "
        "[ \"${argv[4]}\" != \"$expected_target_pid\" ] || "
        "[ \"${argv[5]}\" != \"--watchdog-start-ticks\" ] || "
        "[ \"${argv[6]}\" != \"$expected_target_start\" ] || "
        "[ \"${argv[7]}\" != \"--watchdog-seconds\" ] || "
        "[ \"${argv[8]}\" != \"$expected_timeout\" ] || "
        "[ \"${argv[9]}\" != \"--watchdog-result-file\" ] || "
        "[ -z \"${argv[10]}\" ] || "
        "[ \"${argv[11]}\" != \"--shared-host-lock-fd\" ] || "
        "[ -z \"${argv[12]}\" ]; then exit 9; fi; "
        "case \"${argv[12]}\" in *[!0-9]*|'') exit 9 ;; esac; "
        "if [ \"${argv[12]}\" -lt 3 ]; then exit 9; fi; "
        "expected_lock_path=\"$HOME/.local/share/loveengine-witness-lab/"
        ".shared-host-core.lock\"; "
        "if ! python3 - \"$pid\" \"${argv[12]}\" \"$expected_lock_path\" <<'PY'\n"
        "import fcntl\n"
        "import os\n"
        "import stat\n"
        "import sys\n"
        "pid = int(sys.argv[1])\n"
        "descriptor = int(sys.argv[2])\n"
        "path = sys.argv[3]\n"
        "try:\n"
        "    path_lstat = os.lstat(path)\n"
        "    expected = os.stat(path)\n"
        "    actual = os.stat(f'/proc/{pid}/fd/{descriptor}')\n"
        "except OSError:\n"
        "    raise SystemExit(1)\n"
        "if (\n"
        "    stat.S_ISLNK(path_lstat.st_mode)\n"
        "    or not stat.S_ISREG(expected.st_mode)\n"
        "    or not stat.S_ISREG(actual.st_mode)\n"
        "    or (path_lstat.st_dev, path_lstat.st_ino) != (expected.st_dev, expected.st_ino)\n"
        "    or (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino)\n"
        "):\n"
        "    raise SystemExit(1)\n"
        "try:\n"
        "    probe = os.open(path, os.O_RDWR | getattr(os, 'O_CLOEXEC', 0))\n"
        "    probe_stat = os.fstat(probe)\n"
        "except OSError:\n"
        "    raise SystemExit(1)\n"
        "if (probe_stat.st_dev, probe_stat.st_ino) != (expected.st_dev, expected.st_ino):\n"
        "    os.close(probe)\n"
        "    raise SystemExit(1)\n"
        "try:\n"
        "    try:\n"
        "        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    except BlockingIOError:\n"
        "        held = True\n"
        "    else:\n"
        "        held = False\n"
        "finally:\n"
        "    os.close(probe)\n"
        "if not held:\n"
        "    raise SystemExit(1)\n"
        "try:\n"
        "    current_lstat = os.lstat(path)\n"
        "except OSError:\n"
        "    raise SystemExit(1)\n"
        "if (\n"
        "    stat.S_ISLNK(current_lstat.st_mode)\n"
        "    or (current_lstat.st_dev, current_lstat.st_ino) != (expected.st_dev, expected.st_ino)\n"
        "):\n"
        "    raise SystemExit(1)\n"
        "raise SystemExit(0)\n"
        "PY\n"
        "then exit 9; fi; "
    )


def _owned_watchdog_liveness_command(
    pid: int,
    process_start_ticks: int,
    *,
    target_pid: int,
    target_process_start_ticks: int,
    timeout_seconds: int,
    expected_script_relative: str,
    mode: str = QUICKSTART_WATCHDOG_MODE,
) -> str:
    if (
        pid <= 1
        or process_start_ticks <= 0
        or target_pid <= 1
        or target_process_start_ticks <= 0
        or not 60 <= timeout_seconds <= 3_600
    ):
        raise ValueError("owned watchdog identity must be positive")
    expected_mode = _validate_watchdog_mode(mode)
    return _remote_nonlogin_bash(
        "set -eu; "
        f"pid={pid}; "
        f"expected_start={process_start_ticks}; "
        "stat=$(cat \"/proc/$pid/stat\" 2>/dev/null || true); "
        "if [ -z \"$stat\" ]; then exit 10; fi; "
        "tail=${stat##*) }; "
        "set -- $tail; "
        "state=$1; "
        "eval \"current_pgrp=\\${3}\"; "
        "eval \"current_session=\\${4}\"; "
        "eval \"current_start=\\${20}\"; "
        "if [ \"$current_pgrp\" != \"$pid\" ] || "
        "[ \"$current_session\" != \"$pid\" ]; then exit 9; fi; "
        "if [ \"$current_start\" != \"$expected_start\" ]; then exit 9; fi; "
        "if [ \"$state\" = \"Z\" ]; then exit 10; fi; "
        "if [ ! -r \"/proc/$pid/cmdline\" ]; then exit 10; fi; "
        + _owned_watchdog_argument_check(
            target_pid=target_pid,
            target_process_start_ticks=target_process_start_ticks,
            timeout_seconds=timeout_seconds,
            expected_script_relative=expected_script_relative,
            mode=expected_mode,
        )
        + "exit 0"
    )


def _owned_watchdog_absence_command(
    pid: int,
    process_start_ticks: int,
    *,
    attempts: int = 40,
    target_pid: int,
    target_process_start_ticks: int,
    timeout_seconds: int,
    expected_script_relative: str,
    mode: str = QUICKSTART_WATCHDOG_MODE,
) -> str:
    if (
        pid <= 1
        or process_start_ticks <= 0
        or target_pid <= 1
        or target_process_start_ticks <= 0
        or not 60 <= timeout_seconds <= 3_600
    ):
        raise ValueError("owned watchdog identity must be positive")
    if not 1 <= attempts <= 100_000:
        raise ValueError("owned watchdog wait attempts must be bounded")
    expected_mode = _validate_watchdog_mode(mode)
    return _remote_nonlogin_bash(
        "set -eu; "
        f"pid={pid}; "
        f"expected_start={process_start_ticks}; "
        f"for _ in {{1..{attempts}}}; do "
        "stat=$(cat \"/proc/$pid/stat\" 2>/dev/null || true); "
        "if [ -z \"$stat\" ]; then "
        "if [ ! -e \"/proc/$pid/stat\" ]; then exit 0; fi; "
        "exit 9; "
        "fi; "
        "tail=${stat##*) }; "
        "set -- $tail; "
        "state=$1; "
        "eval \"current_pgrp=\\${3}\"; "
        "eval \"current_session=\\${4}\"; "
        "eval \"current_start=\\${20}\"; "
        "if [ \"$current_pgrp\" != \"$pid\" ] || "
        "[ \"$current_session\" != \"$pid\" ]; then exit 9; fi; "
        "if [ \"$current_start\" != \"$expected_start\" ]; then exit 9; fi; "
        "if [ \"$state\" = \"Z\" ]; then exit 0; fi; "
        "if [ ! -r \"/proc/$pid/cmdline\" ]; then "
        "if [ ! -e \"/proc/$pid/stat\" ]; then exit 0; fi; "
        "exit 9; "
        "fi; "
        + _owned_watchdog_argument_check(
            target_pid=target_pid,
            target_process_start_ticks=target_process_start_ticks,
            timeout_seconds=timeout_seconds,
            expected_script_relative=expected_script_relative,
            mode=expected_mode,
        )
        + "sleep 0.25; "
        "done; "
        "exit 10"
    )


class RemoteLab:
    def __init__(self, args: argparse.Namespace) -> None:
        _validate_target(args.host, args.user, args.port)
        self.remote_root = _validate_remote_root(args.remote_root)
        _validate_remote_limits(
            max_load_per_cpu=args.max_load_per_cpu,
            min_memory_gib=args.min_memory_gib,
            min_disk_gib=args.min_disk_gib,
            timeout_seconds=args.timeout_seconds,
        )
        self.args = args
        self.ssh = _resolve_executable(args.ssh, "ssh")
        self.scp = _resolve_executable(args.scp, "scp")
        self.target = f"{args.user}@{args.host}"
        self.ssh_options = _ssh_options(args)
        self.scp_options = _scp_options(args)
        self.current_phase = "not_started"
        self.current_output: Path | None = None
        self.current_output_owned = False
        self.current_commit: str | None = None
        self.last_preflight: dict[str, Any] | None = None
        self.current_deployment_rel: str | None = None
        self.current_remote_deployment_created = False
        self.downloaded_diagnostics: list[str] = []
        self.start_recoveries: list[dict[str, Any]] = []
        self.execution_resource_blocks: list[dict[str, Any]] = []

    def ssh_run(
        self,
        command: str,
        *,
        input_text: str | None = None,
        timeout: int,
        allow_capacity_rejection: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [
                self.ssh,
                *self.ssh_options,
                *_no_forwarding_options(),
                self.target,
                command,
            ],
            input=input_text,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        allowed = {0, 4} if allow_capacity_rejection else {0}
        if result.returncode not in allowed:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"remote command failed with exit code {result.returncode}: {detail}"
            )
        return result

    def preflight(self) -> dict[str, Any]:
        source = (ROOT / "tools" / "remote_host_preflight.py").read_text(
            encoding="utf-8"
        )
        command = _remote_bash(
            "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
            "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
            "python3 - --workspace . "
            f"--max-load-per-cpu {self.args.max_load_per_cpu} "
            f"--min-memory-gib {self.args.min_memory_gib} "
            f"--min-disk-gib {self.args.min_disk_gib} "
            "--min-cpus 2"
        )
        result = self.ssh_run(
            command,
            input_text=source,
            timeout=30,
            allow_capacity_rejection=True,
        )
        return _last_json_object(result.stdout)

    @staticmethod
    def _lab_cpu_cap(preflight: dict[str, Any]) -> int:
        host = preflight.get("host")
        if not isinstance(host, dict):
            raise RuntimeError("resource preflight omitted host CPU capacity")
        try:
            cpu_count = int(host["cpu_count"])
            cap = max_lab_cpu_assignment(cpu_count)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("resource preflight has invalid host CPU capacity") from exc
        if cap < 1:
            raise RuntimeError("shared-host CPU reserve leaves no CPU for the lab")
        reported_cap = host.get("max_lab_cpu_assignment")
        if reported_cap is not None and reported_cap != cap:
            raise RuntimeError("resource preflight CPU reserve report is inconsistent")
        return cap

    def _git_value(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return result.stdout.strip()

    def _require_clean_source(self) -> str:
        if self._git_value("status", "--porcelain"):
            raise RuntimeError("remote deployment requires a clean Git worktree")
        return self._git_value("rev-parse", "HEAD")

    def _require_source_unchanged(self, expected_commit: str) -> None:
        current = self._require_clean_source()
        if current != expected_commit:
            raise RuntimeError("remote deployment source commit changed during the run")

    @staticmethod
    def _reserve_local_output(path: Path) -> Path:
        try:
            path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise FileExistsError(
                f"local output already exists; refusing to overwrite it: {path}"
            ) from exc
        return path

    def _new_failure_output(self) -> Path:
        root = (ROOT / "tmp" / "remote-experiments" / self.args.host).resolve()
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        for index in range(1, 1000):
            candidate = root / f"failed-{suffix}-{index:03d}"
            try:
                return self._reserve_local_output(candidate)
            except FileExistsError:
                continue
        raise RuntimeError("could not reserve a unique remote-lab failure output")

    def _copy_to_remote(self, local: Path, remote: str, *, recursive: bool = False) -> None:
        command = [self.scp, *self.scp_options]
        if recursive:
            command.append("-r")
        command.extend([str(local), f"{self.target}:{remote}"])
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"scp upload failed: {result.stderr.strip()}")

    def _copy_from_remote(self, remote: str, local: Path) -> None:
        local.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                self.scp,
                *self.scp_options,
                f"{self.target}:{remote}",
                str(local),
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError(f"scp download failed: {result.stderr.strip()}")

    def _remote_free_ports(self) -> tuple[int, int]:
        source = """\
import json
import socket

sockets = []
ports = []
try:
    for _ in range(2):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        sockets.append(listener)
        ports.append(listener.getsockname()[1])
    print(json.dumps({"pilot_port": ports[0], "rpc_port": ports[1]}))
finally:
    for listener in sockets:
        listener.close()
"""
        result = self.ssh_run("python3 -", input_text=source, timeout=15)
        value = _last_json_object(result.stdout)
        pilot_port = int(value["pilot_port"])
        rpc_port = int(value["rpc_port"])
        if (
            not 1 <= pilot_port <= 65535
            or not 1 <= rpc_port <= 65535
            or pilot_port == rpc_port
        ):
            raise RuntimeError("remote port probe returned invalid ports")
        return pilot_port, rpc_port

    def _remote_package_archive(
        self,
        remote_deployment: str,
        deployment_rel: str,
    ) -> str:
        source = """\
import json
import sys
from pathlib import Path

deployment = Path.cwd().resolve(strict=True)
config_path = Path(sys.argv[1]).resolve(strict=True)
try:
    config_path.relative_to(deployment)
except ValueError as exc:
    raise SystemExit("config escaped deployment") from exc
with config_path.open(encoding="utf-8") as stream:
    config = json.load(stream)
raw = Path(config["package_archive"])
if not raw.is_absolute():
    raw = deployment / raw
candidate = raw.resolve(strict=True)
try:
    relative = candidate.relative_to(deployment)
except ValueError as exc:
    raise SystemExit("package escaped deployment") from exc
if not candidate.is_file():
    raise SystemExit("package is not a regular file")
current = deployment
for part in relative.parts:
    current = current / part
    if current.is_symlink():
        raise SystemExit("package path uses a symlink")
print(json.dumps({"package_archive_relative": relative.as_posix()}))
"""
        command = _remote_bash(
            f"cd \"{remote_deployment}\"; "
            "python3 - tmp/tunnel-pilot/pilot-config.json"
        )
        result = self.ssh_run(
            command,
            input_text=source,
            timeout=15,
        )
        value = _last_json_object(result.stdout)
        return _owned_remote_file(
            deployment_rel,
            _validate_owned_remote_relative(str(value["package_archive_relative"])),
        )

    def _stop_owned_remote_group(
        self,
        pid: int,
        process_start_ticks: int,
        *,
        expected_script_relative: str,
        expected_script_name: str,
    ) -> dict[str, int | bool]:
        try:
            stop_command = _owned_group_stop_command(
                pid,
                process_start_ticks,
                expected_script_relative=expected_script_relative,
                expected_script_name=expected_script_name,
            )
            absence_command = _owned_group_absence_command(
                pid,
                process_start_ticks,
                expected_script_relative=expected_script_relative,
                expected_script_name=expected_script_name,
            )
        except ValueError:
            return {
                "verified": False,
                "stop_returncode": -1,
                "absence_returncode": -1,
            }
        stop_result = subprocess.run(
            [
                self.ssh,
                *self.ssh_options,
                *_no_forwarding_options(),
                self.target,
                stop_command,
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        absence_result = subprocess.run(
            [
                self.ssh,
                *self.ssh_options,
                *_no_forwarding_options(),
                self.target,
                absence_command,
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return {
            "verified": absence_result.returncode == 0,
            "stop_returncode": stop_result.returncode,
            "absence_returncode": absence_result.returncode,
        }

    def _stop_owned_remote_group_safely(
        self,
        pid: int,
        process_start_ticks: int,
        *,
        expected_script_relative: str,
        expected_script_name: str,
    ) -> dict[str, Any]:
        """Keep a transport failure from skipping later owned cleanup."""

        try:
            return self._stop_owned_remote_group(
                pid,
                process_start_ticks,
                expected_script_relative=expected_script_relative,
                expected_script_name=expected_script_name,
            )
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            return {
                "verified": False,
                "stop_returncode": -1,
                "absence_returncode": -1,
                "error_type": type(exc).__name__,
            }

    def _verify_owned_remote_watchdog_live(
        self,
        pid: int,
        process_start_ticks: int,
        *,
        target_pid: int,
        target_process_start_ticks: int,
        timeout_seconds: int,
        expected_script_relative: str,
        mode: str = QUICKSTART_WATCHDOG_MODE,
    ) -> dict[str, int | bool | str]:
        command = _owned_watchdog_liveness_command(
            pid,
            process_start_ticks,
            target_pid=target_pid,
            target_process_start_ticks=target_process_start_ticks,
            timeout_seconds=timeout_seconds,
            expected_script_relative=expected_script_relative,
            mode=mode,
        )
        self.ssh_run(command, timeout=15)
        return {
            "verified": True,
            "pid": pid,
            "process_start_ticks": process_start_ticks,
            "target_pid": target_pid,
            "target_process_start_ticks": target_process_start_ticks,
            "timeout_seconds": timeout_seconds,
            "expected_script_relative": expected_script_relative,
            "mode": mode,
        }

    def _wait_owned_remote_watchdog_exit(
        self,
        pid: int,
        process_start_ticks: int,
        *,
        target_pid: int,
        target_process_start_ticks: int,
        timeout_seconds: int,
        expected_script_relative: str,
        mode: str = QUICKSTART_WATCHDOG_MODE,
    ) -> dict[str, int | bool]:
        try:
            command = _owned_watchdog_absence_command(
                pid,
                process_start_ticks,
                target_pid=target_pid,
                target_process_start_ticks=target_process_start_ticks,
                timeout_seconds=timeout_seconds,
                expected_script_relative=expected_script_relative,
                mode=mode,
            )
        except ValueError:
            return {"verified": False, "absence_returncode": -1}
        result = subprocess.run(
            [
                self.ssh,
                *self.ssh_options,
                *_no_forwarding_options(),
                self.target,
                command,
            ],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return {
            "verified": result.returncode == 0,
            "absence_returncode": result.returncode,
        }

    def _wait_owned_remote_watchdog_exit_safely(
        self,
        pid: int,
        process_start_ticks: int,
        *,
        target_pid: int,
        target_process_start_ticks: int,
        timeout_seconds: int,
        expected_script_relative: str,
        mode: str = QUICKSTART_WATCHDOG_MODE,
    ) -> dict[str, Any]:
        """Always report a failed watchdog check instead of aborting cleanup."""

        try:
            return self._wait_owned_remote_watchdog_exit(
                pid,
                process_start_ticks,
                target_pid=target_pid,
                target_process_start_ticks=target_process_start_ticks,
                timeout_seconds=timeout_seconds,
                expected_script_relative=expected_script_relative,
                mode=mode,
            )
        except (OSError, RuntimeError, TypeError, ValueError, subprocess.SubprocessError) as exc:
            return {
                "verified": False,
                "absence_returncode": -1,
                "error_type": type(exc).__name__,
            }

    def _inspect_remote_loopback_listeners(
        self,
        *,
        pid: int,
        process_start_ticks: int,
        expected_ports: tuple[int, int],
        expected_script_relative: str,
    ) -> dict[str, Any]:
        command = _remote_listener_inspection_command(
            pid,
            process_start_ticks,
            expected_ports,
            expected_script_relative=expected_script_relative,
        )
        result = self.ssh_run(
            command,
            input_text=REMOTE_LISTENER_INSPECTION_SOURCE,
            timeout=15,
        )
        return _validated_remote_listener_inspection(
            _last_json_object(result.stdout),
            pid=pid,
            process_start_ticks=process_start_ticks,
            expected_ports=expected_ports,
        )

    def _wait_for_remote_loopback_listeners(
        self,
        *,
        pid: int,
        process_start_ticks: int,
        expected_ports: tuple[int, int],
        expected_script_relative: str,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self._inspect_remote_loopback_listeners(
                    pid=pid,
                    process_start_ticks=process_start_ticks,
                    expected_ports=expected_ports,
                    expected_script_relative=expected_script_relative,
                )
            except RuntimeError as exc:
                last_error = exc
                time.sleep(0.5)
        raise RuntimeError(
            "timed out waiting for remote loopback listener inspection: "
            f"{last_error}"
        )

    def _run_owned_remote_core(
        self,
        *,
        deployment_rel: str,
        remote_deployment: str,
        local_output: Path,
        max_cpus: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        requested_thresholds = _requested_thresholds(
            max_load_per_cpu=self.args.max_load_per_cpu,
            min_memory_gib=self.args.min_memory_gib,
            min_disk_gib=self.args.min_disk_gib,
        )
        start_command = _remote_bash(
            "set -eu; "
            "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
            "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
            f"cd \"{remote_deployment}\"; "
            "exec python3 tools/start_shared_core.py "
            "--output tmp/remote-core --log tmp/remote-core.log "
            f"--max-load-per-cpu {self.args.max_load_per_cpu} "
            f"--min-memory-gib {self.args.min_memory_gib} "
            f"--min-disk-gib {self.args.min_disk_gib} "
            f"--max-cpus {max_cpus} --nice-increment 15 "
            f"--watchdog-seconds {self.args.timeout_seconds}"
        )
        def recover_start_failure(exc: Exception) -> None:
            recovery = self._recover_owned_remote_core_start(
                deployment_rel=deployment_rel,
                local_output=local_output,
                max_cpus=max_cpus,
            )
            self._record_start_recovery("core", recovery)
            report = self._download_remote_core_diagnostics(
                deployment_rel=deployment_rel,
                local_output=local_output,
            )
            if report is not None:
                raise RuntimeError(
                    self._remote_core_report_failure(report)
                ) from exc
            if recovery.get("identity_recovered") is True:
                group_cleanup = recovery.get("group_cleanup")
                watchdog_cleanup = recovery.get("watchdog_cleanup")
                if (
                    isinstance(group_cleanup, dict)
                    and group_cleanup.get("verified") is True
                    and isinstance(watchdog_cleanup, dict)
                    and watchdog_cleanup.get("verified") is True
                ):
                    raise RuntimeError(
                        "remote core startup failed after verified owned cleanup"
                    ) from exc
                raise RuntimeError(
                    "remote core startup failed with recovered identity but unverified cleanup"
                ) from exc
            raise RuntimeError("remote core startup failed before owned identity was available") from exc

        try:
            started = self.ssh_run(
                start_command,
                timeout=30,
                allow_capacity_rejection=True,
            )
            start_info = _last_json_object(started.stdout)
        except (
            OSError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            recover_start_failure(exc)

        if started.returncode == 4:
            execution_preflight = _validated_execution_resource_block(
                start_info,
                label="core",
                expected_thresholds=requested_thresholds,
                expected_workspace_relative=f"{deployment_rel}/tmp/remote-core",
            )
            self._record_execution_resource_block("core", execution_preflight)
            raise ResourceGuardBlocked("core", execution_preflight)

        try:
            remote_pid = int(start_info["pid"])
            process_start_ticks = int(start_info["process_start_ticks"])
            if remote_pid <= 1 or process_start_ticks <= 0:
                raise RuntimeError("remote core returned an invalid process identity")
        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            recover_start_failure(exc)
        cleanup: dict[str, Any] = {
            "verified": False,
            "stop_returncode": -1,
            "absence_returncode": -1,
        }
        core_watchdog: dict[str, int | str] | None = None
        watchdog_liveness: dict[str, int | bool | str] | None = None
        watchdog_cleanup: dict[str, int | bool] = {
            "verified": False,
            "absence_returncode": -1,
        }
        watchdog_terminal: dict[str, Any] | None = None
        try:
            _validated_remote_launch_lease(
                start_info,
                workspace_key="output",
                supervisor_pid=remote_pid,
                supervisor_start_ticks=process_start_ticks,
                label="core",
                expected_thresholds=requested_thresholds,
                expected_max_cpus=max_cpus,
                expected_workspace_relative=f"{deployment_rel}/tmp/remote-core",
            )
            core_watchdog = _validated_core_watchdog(
                start_info,
                timeout_seconds=self.args.timeout_seconds,
            )
            watchdog_liveness = self._verify_owned_remote_watchdog_live(
                int(core_watchdog["pid"]),
                int(core_watchdog["process_start_ticks"]),
                target_pid=remote_pid,
                target_process_start_ticks=process_start_ticks,
                timeout_seconds=int(core_watchdog["timeout_seconds"]),
                expected_script_relative=deployment_rel,
                mode=CORE_WATCHDOG_MODE,
            )
            wait_command = _owned_group_absence_command(
                remote_pid,
                process_start_ticks,
                expected_script_relative=deployment_rel,
                expected_script_name="start_shared_core.py",
                attempts=max(20, self.args.timeout_seconds * 4),
            )
            self.ssh_run(
                wait_command,
                timeout=self.args.timeout_seconds + 30,
            )
        finally:
            try:
                cleanup = self._stop_owned_remote_group_safely(
                    remote_pid,
                    process_start_ticks,
                    expected_script_relative=deployment_rel,
                    expected_script_name="start_shared_core.py",
                )
            finally:
                try:
                    if core_watchdog is not None:
                        watchdog_cleanup = (
                            self._wait_owned_remote_watchdog_exit_safely(
                                int(core_watchdog["pid"]),
                                int(core_watchdog["process_start_ticks"]),
                                target_pid=remote_pid,
                                target_process_start_ticks=process_start_ticks,
                                timeout_seconds=int(core_watchdog["timeout_seconds"]),
                                expected_script_relative=deployment_rel,
                                mode=CORE_WATCHDOG_MODE,
                            )
                        )
                finally:
                    try:
                        self._copy_from_remote(
                            _owned_remote_file(
                                deployment_rel,
                                "tmp/remote-core.log",
                            ),
                            local_output / "remote-core.log",
                        )
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        pass
                    try:
                        self._copy_from_remote(
                            _owned_remote_file(
                                deployment_rel,
                                "tmp/remote-core/.core-watchdog-result.json",
                            ),
                            local_output / "core-watchdog-result.json",
                        )
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        pass
        if not cleanup["verified"]:
            raise RuntimeError("could not verify cleanup of the remote core process group")
        if core_watchdog is None or watchdog_liveness is None:
            raise RuntimeError("remote core did not provide a valid watchdog")
        if not watchdog_cleanup["verified"]:
            raise RuntimeError("could not verify shutdown of the remote core watchdog")
        watchdog_result_path = local_output / "core-watchdog-result.json"
        if not watchdog_result_path.is_file():
            raise RuntimeError("remote core watchdog did not persist a terminal result")
        try:
            watchdog_terminal = _validated_core_watchdog_result(
                json.loads(watchdog_result_path.read_text(encoding="utf-8")),
                watchdog=core_watchdog,
                target_pid=remote_pid,
                target_process_start_ticks=process_start_ticks,
                require_normal_completion=True,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("remote core watchdog terminal result is unreadable") from exc
        cleanup["watchdog"] = core_watchdog
        cleanup["watchdog_liveness"] = watchdog_liveness
        cleanup["watchdog_cleanup"] = watchdog_cleanup
        cleanup["watchdog_terminal"] = watchdog_terminal
        self._copy_from_remote(
            _owned_remote_file(
                deployment_rel,
                "tmp/remote-core/core-experiment-report.json",
            ),
            local_output / "core-experiment-report.json",
        )
        remote_report = json.loads(
            (local_output / "core-experiment-report.json").read_text(encoding="utf-8")
        )
        if remote_report.get("status") != "passed":
            raise RuntimeError(_remote_core_failure_message(remote_report))
        if (
            start_info.get("resource_preflight")
            != remote_report.get("resource_preflight")
            or start_info.get("resource_preflight_binding")
            != remote_report.get("resource_preflight_binding")
        ):
            raise RuntimeError("remote core preflight evidence is not continuous")
        return remote_report, cleanup

    def _record_start_recovery(self, phase: str, recovery: dict[str, Any]) -> None:
        entries = getattr(self, "start_recoveries", None)
        if entries is None:
            entries = []
            self.start_recoveries = entries
        entries.append({"phase": phase, "result": recovery})

    def _record_execution_resource_block(
        self,
        phase: str,
        preflight: dict[str, Any],
    ) -> None:
        entries = getattr(self, "execution_resource_blocks", None)
        if entries is None:
            entries = []
            self.execution_resource_blocks = entries
        entries.append({"phase": phase, "preflight": preflight})

    def _recover_owned_remote_core_start(
        self,
        *,
        deployment_rel: str,
        local_output: Path,
        max_cpus: int,
    ) -> dict[str, Any]:
        """Attempt cleanup only after recovering the immutable owned start record."""

        result: dict[str, Any] = {"identity_recovered": False}
        launch_path = local_output / "core-launch.json"
        try:
            self._copy_from_remote(
                _owned_remote_file(
                    deployment_rel,
                    "tmp/remote-core/.core-launch.json",
                ),
                launch_path,
            )
            diagnostics = getattr(self, "downloaded_diagnostics", None)
            if diagnostics is None:
                diagnostics = []
                self.downloaded_diagnostics = diagnostics
            diagnostics.append("core-launch.json")
            start_info = json.loads(launch_path.read_text(encoding="utf-8"))
            remote_pid = int(start_info["pid"])
            process_start_ticks = int(start_info["process_start_ticks"])
            if remote_pid <= 1 or process_start_ticks <= 0:
                raise ValueError("invalid recovered process identity")
            _validated_remote_launch_lease(
                start_info,
                workspace_key="output",
                supervisor_pid=remote_pid,
                supervisor_start_ticks=process_start_ticks,
                label="core",
                expected_thresholds=_requested_thresholds(
                    max_load_per_cpu=self.args.max_load_per_cpu,
                    min_memory_gib=self.args.min_memory_gib,
                    min_disk_gib=self.args.min_disk_gib,
                ),
                expected_max_cpus=max_cpus,
                expected_workspace_relative=f"{deployment_rel}/tmp/remote-core",
            )
            watchdog = _validated_core_watchdog(
                start_info,
                timeout_seconds=self.args.timeout_seconds,
            )
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            result["recovery_error_type"] = type(exc).__name__
            return result
        result["identity_recovered"] = True
        result["group_cleanup"] = self._stop_owned_remote_group_safely(
            remote_pid,
            process_start_ticks,
            expected_script_relative=deployment_rel,
            expected_script_name="start_shared_core.py",
        )
        result["watchdog_cleanup"] = self._wait_owned_remote_watchdog_exit_safely(
            int(watchdog["pid"]),
            int(watchdog["process_start_ticks"]),
            target_pid=remote_pid,
            target_process_start_ticks=process_start_ticks,
            timeout_seconds=int(watchdog["timeout_seconds"]),
            expected_script_relative=deployment_rel,
            mode=CORE_WATCHDOG_MODE,
        )
        return result

    def _download_remote_core_diagnostics(
        self,
        *,
        deployment_rel: str,
        local_output: Path,
    ) -> dict[str, Any] | None:
        """Copy only owned core diagnostics when startup fails before a PID exists."""

        for remote_name, local_name in (
            ("tmp/remote-core.log", "remote-core.log"),
            ("tmp/remote-core/.core-launch.json", "core-launch.json"),
            (
                "tmp/remote-core/.core-watchdog-result.json",
                "core-watchdog-result.json",
            ),
            ("tmp/remote-core/core-experiment-report.json", "core-experiment-report.json"),
        ):
            local_path = local_output / local_name
            if local_path.is_file():
                continue
            try:
                self._copy_from_remote(
                    _owned_remote_file(deployment_rel, remote_name),
                    local_path,
                )
                diagnostics = getattr(self, "downloaded_diagnostics", None)
                if diagnostics is None:
                    diagnostics = []
                    self.downloaded_diagnostics = diagnostics
                if local_name not in diagnostics:
                    diagnostics.append(local_name)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue
        report_path = local_output / "core-experiment-report.json"
        if not report_path.is_file():
            return None
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return report if isinstance(report, dict) else None

    @staticmethod
    def _remote_core_report_failure(report: dict[str, Any]) -> str:
        """Return a bounded error category without incorporating remote logs."""

        status = report.get("status")
        error = report.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return f"remote core {status}: {error['code']}"
        return f"remote core ended with status {status}"

    def _recover_owned_remote_quickstart_start(
        self,
        *,
        deployment_rel: str,
        local_output: Path,
        max_cpus: int,
    ) -> dict[str, Any]:
        """Recover only a deterministic Quickstart record before cleanup."""

        result: dict[str, Any] = {"identity_recovered": False}
        launch_path = local_output / "quickstart-launch.json"
        try:
            self._copy_from_remote(
                _owned_remote_file(
                    deployment_rel,
                    "tmp/tunnel-pilot/.quickstart-launch.json",
                ),
                launch_path,
            )
            diagnostics = getattr(self, "downloaded_diagnostics", None)
            if diagnostics is None:
                diagnostics = []
                self.downloaded_diagnostics = diagnostics
            if "quickstart-launch.json" not in diagnostics:
                diagnostics.append("quickstart-launch.json")
            start_info = json.loads(launch_path.read_text(encoding="utf-8"))
            remote_pid = int(start_info["pid"])
            process_start_ticks = int(start_info["process_start_ticks"])
            if remote_pid <= 1 or process_start_ticks <= 0:
                raise ValueError("invalid recovered Quickstart process identity")
            _validated_remote_launch_lease(
                start_info,
                workspace_key="root",
                supervisor_pid=remote_pid,
                supervisor_start_ticks=process_start_ticks,
                label="Quickstart",
                expected_thresholds=_requested_thresholds(
                    max_load_per_cpu=self.args.max_load_per_cpu,
                    min_memory_gib=self.args.min_memory_gib,
                    min_disk_gib=self.args.min_disk_gib,
                ),
                expected_max_cpus=max_cpus,
                expected_workspace_relative=f"{deployment_rel}/tmp/tunnel-pilot",
            )
            watchdog = _validated_quickstart_watchdog(start_info)
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            result["recovery_error_type"] = type(exc).__name__
            return result
        result["identity_recovered"] = True
        result["group_cleanup"] = self._stop_owned_remote_group_safely(
            remote_pid,
            process_start_ticks,
            expected_script_relative=deployment_rel,
            expected_script_name="start_shared_quickstart.py",
        )
        result["watchdog_cleanup"] = self._wait_owned_remote_watchdog_exit_safely(
            int(watchdog["pid"]),
            int(watchdog["process_start_ticks"]),
            target_pid=remote_pid,
            target_process_start_ticks=process_start_ticks,
            timeout_seconds=int(watchdog["timeout_seconds"]),
            expected_script_relative=deployment_rel,
            mode=QUICKSTART_WATCHDOG_MODE,
        )
        return result

    def _download_remote_quickstart_diagnostics(
        self,
        *,
        deployment_rel: str,
        local_output: Path,
    ) -> dict[str, Any] | None:
        """Copy only deterministic Quickstart diagnostics after a start failure."""

        for remote_name, local_name in (
            ("tmp/tunnel-pilot.log", "quickstart.log"),
            ("tmp/tunnel-pilot/.quickstart-launch.json", "quickstart-launch.json"),
            (
                "tmp/tunnel-pilot/.quickstart-watchdog-result.json",
                "quickstart-watchdog-result.json",
            ),
        ):
            local_path = local_output / local_name
            if local_path.is_file():
                continue
            try:
                self._copy_from_remote(
                    _owned_remote_file(deployment_rel, remote_name),
                    local_path,
                )
                diagnostics = getattr(self, "downloaded_diagnostics", None)
                if diagnostics is None:
                    diagnostics = []
                    self.downloaded_diagnostics = diagnostics
                if local_name not in diagnostics:
                    diagnostics.append(local_name)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue
        launch_path = local_output / "quickstart-launch.json"
        if not launch_path.is_file():
            return None
        try:
            launch = json.loads(launch_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return launch if isinstance(launch, dict) else None

    @staticmethod
    def _remote_quickstart_report_failure(launch: dict[str, Any]) -> str:
        status = launch.get("status")
        if isinstance(status, str):
            return f"remote Quickstart startup ended with status {status}"
        return "remote Quickstart startup produced an unusable launch record"

    def _tunnel_smoke(
        self,
        *,
        deployment_rel: str,
        remote_deployment: str,
        local_output: Path,
    ) -> dict[str, Any]:
        preflight = self.preflight()
        if not preflight.get("safe_to_run"):
            self._record_execution_resource_block("tunnel_preflight", preflight)
            raise ResourceGuardBlocked("tunnel_preflight", preflight)
        max_cpus = self._lab_cpu_cap(preflight)
        requested_thresholds = _requested_thresholds(
            max_load_per_cpu=self.args.max_load_per_cpu,
            min_memory_gib=self.args.min_memory_gib,
            min_disk_gib=self.args.min_disk_gib,
        )
        remote_pilot_port, remote_rpc_port = self._remote_free_ports()
        local_pilot_port = _free_local_port()
        local_rpc_port = _free_local_port()
        while local_rpc_port == local_pilot_port:
            local_rpc_port = _free_local_port()
        tunnel_dir = local_output / "tunnel"
        tunnel_dir.mkdir(parents=True, exist_ok=False)
        start_command = _remote_bash(
            "set -eu; "
            "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
            "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
            f"cd \"{remote_deployment}\"; "
            "exec python3 tools/start_shared_quickstart.py "
            "--root tmp/tunnel-pilot --host 127.0.0.1 "
            f"--port {remote_pilot_port} --rpc-port {remote_rpc_port} "
            "--log tmp/tunnel-pilot.log "
            f"--max-cpus {max_cpus} --nice-increment 15 "
            f"--max-load-per-cpu {self.args.max_load_per_cpu} "
            f"--min-memory-gib {self.args.min_memory_gib} "
            f"--min-disk-gib {self.args.min_disk_gib} "
            f"--watchdog-seconds {REMOTE_QUICKSTART_WATCHDOG_SECONDS}"
        )
        def recover_start_failure(exc: Exception) -> None:
            recovery = self._recover_owned_remote_quickstart_start(
                deployment_rel=deployment_rel,
                local_output=local_output,
                max_cpus=max_cpus,
            )
            self._record_start_recovery("quickstart", recovery)
            report = self._download_remote_quickstart_diagnostics(
                deployment_rel=deployment_rel,
                local_output=local_output,
            )
            if report is not None:
                raise RuntimeError(
                    self._remote_quickstart_report_failure(report)
                ) from exc
            if recovery.get("identity_recovered") is True:
                group_cleanup = recovery.get("group_cleanup")
                watchdog_cleanup = recovery.get("watchdog_cleanup")
                if (
                    isinstance(group_cleanup, dict)
                    and group_cleanup.get("verified") is True
                    and isinstance(watchdog_cleanup, dict)
                    and watchdog_cleanup.get("verified") is True
                ):
                    raise RuntimeError(
                        "remote Quickstart startup failed after verified owned cleanup"
                    ) from exc
                raise RuntimeError(
                    "remote Quickstart startup failed with recovered identity but unverified cleanup"
                ) from exc
            raise RuntimeError(
                "remote Quickstart startup failed before owned identity was available"
            ) from exc

        try:
            started = self.ssh_run(
                start_command,
                timeout=30,
                allow_capacity_rejection=True,
            )
            start_info = _last_json_object(started.stdout)
        except (
            OSError,
            RuntimeError,
            ValueError,
            json.JSONDecodeError,
            subprocess.SubprocessError,
        ) as exc:
            recover_start_failure(exc)

        if started.returncode == 4:
            execution_preflight = _validated_execution_resource_block(
                start_info,
                label="Quickstart",
                expected_thresholds=requested_thresholds,
                expected_workspace_relative=(
                    f"{deployment_rel}/tmp/tunnel-pilot"
                ),
            )
            self._record_execution_resource_block("quickstart", execution_preflight)
            raise ResourceGuardBlocked("quickstart", execution_preflight)

        try:
            remote_pid = int(start_info["pid"])
            process_start_ticks = int(start_info["process_start_ticks"])
            if remote_pid <= 1 or process_start_ticks <= 0:
                raise RuntimeError("remote Quickstart returned an invalid process identity")
        except (RuntimeError, ValueError, TypeError, KeyError) as exc:
            recover_start_failure(exc)

        watchdog: dict[str, int | str] | None = None
        tunnel_process: subprocess.Popen[str] | None = None
        node_processes: list[subprocess.Popen[str]] = []
        local_process_cleanup: list[dict[str, Any]] = []
        report: dict[str, Any] | None = None
        owned_cleanup = False
        cleanup_verification: dict[str, int | bool] = {
            "verified": False,
            "stop_returncode": -1,
            "absence_returncode": -1,
        }
        watchdog_cleanup: dict[str, int | bool] = {
            "verified": False,
            "absence_returncode": -1,
        }
        watchdog_liveness: dict[str, dict[str, int | bool | str]] = {}
        watchdog_terminal: dict[str, Any] | None = None
        execution_launch: dict[str, Any] | None = None
        try:
            _validated_remote_launch_lease(
                start_info,
                workspace_key="root",
                supervisor_pid=remote_pid,
                supervisor_start_ticks=process_start_ticks,
                label="Quickstart",
                expected_thresholds=requested_thresholds,
                expected_max_cpus=max_cpus,
                expected_workspace_relative=f"{deployment_rel}/tmp/tunnel-pilot",
            )
            watchdog = _validated_quickstart_watchdog(start_info)
            execution_launch = {
                "supervisor": {
                    "pid": remote_pid,
                    "process_start_ticks": process_start_ticks,
                },
                "workspace": start_info["root"],
                "resource_preflight": start_info["resource_preflight"],
                "resource_preflight_binding": start_info[
                    "resource_preflight_binding"
                ],
                "limits": {
                    "max_cpus": start_info["max_cpus"],
                    "nice_increment": start_info["nice_increment"],
                    "process_nice": start_info["process_nice"],
                    "cpu_affinity": start_info["cpu_affinity"],
                },
            }
            listener_inspection = self._wait_for_remote_loopback_listeners(
                pid=remote_pid,
                process_start_ticks=process_start_ticks,
                expected_ports=(remote_pilot_port, remote_rpc_port),
                expected_script_relative=deployment_rel,
            )
            watchdog_liveness["before_tunnel_readiness"] = (
                self._verify_owned_remote_watchdog_live(
                    int(watchdog["pid"]),
                    int(watchdog["process_start_ticks"]),
                    target_pid=remote_pid,
                    target_process_start_ticks=process_start_ticks,
                    timeout_seconds=int(watchdog["timeout_seconds"]),
                    expected_script_relative=deployment_rel,
                )
            )
            tunnel_process = subprocess.Popen(
                [
                    self.ssh,
                    *self.ssh_options,
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-T",
                    "-N",
                    "-L",
                    f"127.0.0.1:{local_pilot_port}:127.0.0.1:{remote_pilot_port}",
                    "-L",
                    f"127.0.0.1:{local_rpc_port}:127.0.0.1:{remote_rpc_port}",
                    self.target,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            base_url = f"http://127.0.0.1:{local_pilot_port}"
            _wait_http_json(
                base_url + "/readyz",
                timeout=120,
                process=tunnel_process,
            )
            invite_path = tunnel_dir / "pilot-invite.json"
            trust_policy_path = tunnel_dir / "pilot-trust-policy.json"
            package_path = tunnel_dir / "loveengine-witness.zip"
            self._copy_from_remote(
                _owned_remote_file(deployment_rel, "tmp/tunnel-pilot/pilot-invite.json"),
                invite_path,
            )
            self._copy_from_remote(
                _owned_remote_file(
                    deployment_rel,
                    "tmp/tunnel-pilot/pilot-trust-policy.json",
                ),
                trust_policy_path,
            )
            remote_package = self._remote_package_archive(
                remote_deployment,
                deployment_rel,
            )
            self._copy_from_remote(remote_package, package_path)

            invite = json.loads(invite_path.read_text(encoding="utf-8"))
            invite.update(
                {
                    "server_url": base_url,
                    "operator_url": base_url + "/operator/",
                    "dashboard_url": base_url + "/demo/",
                    "relay_url": base_url + "/v1/ws",
                }
            )
            invite_path.write_text(
                json.dumps(invite, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
            node_runs: list[dict[str, Any]] = []
            for index in range(1, TUNNEL_NODE_COUNT + 1):
                profile_path = tunnel_dir / f"node-{index}.json"
                self._copy_from_remote(
                    _owned_remote_file(
                        deployment_rel,
                        f"tmp/tunnel-pilot/profiles/node-{index}.json",
                    ),
                    profile_path,
                )
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                node = str(profile["profile"]["node"])
                dispute_id = f"remote-tunnel-dispute-{index}"
                task_id = f"remote-tunnel-late-task-{index}"
                verdicts_path = tunnel_dir / f"node-{index}-verdicts.json"
                verdicts_path.write_text(
                    json.dumps({dispute_id: "dismiss"}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                node_result_path = tunnel_dir / f"node-{index}-result.json"
                node_process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "loveengine_witness.cli",
                        "node",
                        "connect",
                        "--invite",
                        str(invite_path),
                        "--trust-policy",
                        str(trust_policy_path),
                        "--package",
                        str(package_path),
                        "--profile",
                        str(profile_path),
                        "--rpc-url",
                        f"http://127.0.0.1:{local_rpc_port}",
                        "--address",
                        node,
                        "--cursor-db",
                        str(tunnel_dir / f"node-{index}.cursor.sqlite"),
                        "--verdicts",
                        str(verdicts_path),
                        "--expected-tasks",
                        "1",
                        "--output",
                        str(node_result_path),
                    ],
                    cwd=ROOT,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                node_processes.append(node_process)
                node_runs.append(
                    {
                        "index": index,
                        "node": node,
                        "task_id": task_id,
                        "dispute_id": dispute_id,
                        "result_path": node_result_path,
                        "process": node_process,
                    }
                )
            connected_deadline = time.monotonic() + 60
            metrics: dict[str, Any] | None = None
            while time.monotonic() < connected_deadline:
                for node_run in node_runs:
                    node_process = node_run["process"]
                    if node_process.poll() is not None:
                        _, detail = node_process.communicate()
                        raise RuntimeError(
                            "tunneled node "
                            f"{node_run['index']} exited before connecting: "
                            f"{detail.strip()}"
                        )
                metrics = _wait_http_json_or_none(
                    base_url + "/v1/metrics",
                    timeout=2,
                    process=tunnel_process,
                )
                if metrics is None:
                    continue
                if (
                    int(metrics["connections"]["agents"])
                    >= TUNNEL_NODE_COUNT
                ):
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(
                    f"{TUNNEL_NODE_COUNT} tunneled nodes did not connect "
                    "within 60 seconds"
                )

            watchdog_liveness["before_task_submission"] = (
                self._verify_owned_remote_watchdog_live(
                    int(watchdog["pid"]),
                    int(watchdog["process_start_ticks"]),
                    target_pid=remote_pid,
                    target_process_start_ticks=process_start_ticks,
                    timeout_seconds=int(watchdog["timeout_seconds"]),
                    expected_script_relative=deployment_rel,
                )
            )
            submissions: list[dict[str, Any]] = []
            for node_run in node_runs:
                enqueue_command = _remote_bash(
                    "set -eu; "
                    "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
                    "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
                    f"cd \"{remote_deployment}\"; "
                    "uv run python tools/enqueue_pilot_task.py "
                    "--root tmp/tunnel-pilot "
                    f"--profile-index {node_run['index']} "
                    f"--task-id {node_run['task_id']} "
                    f"--dispute-id {node_run['dispute_id']} "
                    f"--evidence-base-url http://127.0.0.1:{local_pilot_port}"
                )
                submitted = self.ssh_run(enqueue_command, timeout=30)
                submissions.append(
                    _validated_queued_submission(
                        _last_json_object(submitted.stdout),
                        task_id=node_run["task_id"],
                        recipient=node_run["node"],
                    )
                )

            receipt_summaries: list[dict[str, Any]] = []
            for node_run in node_runs:
                node_process = node_run["process"]
                stdout, stderr = node_process.communicate(timeout=90)
                if node_process.returncode != 0:
                    raise RuntimeError(
                        f"tunneled node {node_run['index']} failed: "
                        + (
                            stderr.strip()
                            or stdout.strip()
                            or str(node_process.returncode)
                        )
                    )
                node_result = json.loads(
                    node_run["result_path"].read_text(encoding="utf-8")
                )
                receipt = _validated_review_receipt(
                    node_result,
                    task_id=node_run["task_id"],
                    dispute_id=node_run["dispute_id"],
                    expected_node=node_run["node"],
                )
                receipt_summaries.append(
                    {
                        "node": node_run["node"],
                        "task_id": receipt["task_id"],
                        "dispute_id": receipt["result"]["dispute_id"],
                        "status": receipt["status"],
                        "evidence_verified": True,
                    }
                )
            metrics_deadline = time.monotonic() + 15
            while time.monotonic() < metrics_deadline:
                metrics = _wait_http_json_or_none(
                    base_url + "/v1/metrics",
                    timeout=2,
                    process=tunnel_process,
                )
                if metrics is None:
                    continue
                if (
                    int(metrics["relay"]["acked"]) == TUNNEL_NODE_COUNT
                    and int(metrics["relay"]["receipt_confirmed"])
                    == TUNNEL_NODE_COUNT
                ):
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("Relay did not persist the tunneled receipt")
            report = {
                "status": "passed",
                "resource_preflight": preflight,
                "remote_bind": "loopback_only",
                "remote_services_exposed": False,
                "remote_pilot_port": remote_pilot_port,
                "remote_rpc_port": remote_rpc_port,
                "local_pilot_port": local_pilot_port,
                "local_rpc_port": local_rpc_port,
                "listener_inspection": listener_inspection,
                "watchdog": watchdog,
                "watchdog_liveness": watchdog_liveness,
                "execution_launch": execution_launch,
                "resource_profile": {
                    "max_cpus": max_cpus,
                    "reserved_cpu_count": 1,
                },
                "node_count": TUNNEL_NODE_COUNT,
                "nodes": [node_run["node"] for node_run in node_runs],
                "expected_recipients": [node_run["node"] for node_run in node_runs],
                "task_submissions": submissions,
                "receipt_count": len(receipt_summaries),
                "receipts": receipt_summaries,
                "evidence_verified": True,
                "relay_acked": int(metrics["relay"]["acked"]),
                "relay_receipt_confirmed": int(
                    metrics["relay"]["receipt_confirmed"]
                ),
            }
        finally:
            try:
                for index, node_process in enumerate(node_processes, start=1):
                    local_process_cleanup.append(
                        _reap_local_process(
                            node_process,
                            label=f"node-{index}",
                        )
                    )
                if tunnel_process is not None:
                    local_process_cleanup.append(
                        _reap_local_process(tunnel_process, label="ssh_tunnel")
                    )
            finally:
                try:
                    cleanup_verification = self._stop_owned_remote_group_safely(
                        remote_pid,
                        process_start_ticks,
                        expected_script_relative=deployment_rel,
                        expected_script_name="start_shared_quickstart.py",
                    )
                    owned_cleanup = bool(cleanup_verification["verified"])
                finally:
                    try:
                        if watchdog is not None:
                            watchdog_cleanup = (
                                self._wait_owned_remote_watchdog_exit_safely(
                                    int(watchdog["pid"]),
                                    int(watchdog["process_start_ticks"]),
                                    target_pid=remote_pid,
                                    target_process_start_ticks=process_start_ticks,
                                    timeout_seconds=int(watchdog["timeout_seconds"]),
                                    expected_script_relative=deployment_rel,
                                )
                            )
                    finally:
                        try:
                            self._copy_from_remote(
                                _owned_remote_file(
                                    deployment_rel,
                                    "tmp/tunnel-pilot.log",
                                ),
                                tunnel_dir / "quickstart.log",
                            )
                        except (OSError, RuntimeError, subprocess.SubprocessError):
                            pass
                        try:
                            self._copy_from_remote(
                                _owned_remote_file(
                                    deployment_rel,
                                    "tmp/tunnel-pilot/.quickstart-launch.json",
                                ),
                                tunnel_dir / "quickstart-launch.json",
                            )
                        except (OSError, RuntimeError, subprocess.SubprocessError):
                            pass
                        try:
                            self._copy_from_remote(
                                _owned_remote_file(
                                    deployment_rel,
                                    "tmp/tunnel-pilot/.quickstart-watchdog-result.json",
                                ),
                                tunnel_dir / "quickstart-watchdog-result.json",
                            )
                        except (OSError, RuntimeError, subprocess.SubprocessError):
                            pass
        if report is None:
            raise RuntimeError("tunnel smoke ended without a report")
        report["local_process_cleanup"] = local_process_cleanup
        report["owned_process_cleanup"] = owned_cleanup
        report["cleanup_verification"] = cleanup_verification
        report["watchdog_cleanup"] = watchdog_cleanup
        if not all(item.get("verified") is True for item in local_process_cleanup):
            raise RuntimeError("could not verify cleanup of all local tunnel processes")
        if not owned_cleanup:
            raise RuntimeError(
                "could not verify cleanup of the remote process group "
                f"(stop={cleanup_verification['stop_returncode']}, "
                f"absence={cleanup_verification['absence_returncode']})"
            )
        if not watchdog_cleanup["verified"]:
            raise RuntimeError("could not verify shutdown of the remote watchdog")
        if watchdog is None:
            raise RuntimeError("remote Quickstart did not provide a valid watchdog")
        watchdog_result_path = tunnel_dir / "quickstart-watchdog-result.json"
        if not watchdog_result_path.is_file():
            raise RuntimeError("remote Quickstart watchdog did not persist a terminal result")
        try:
            watchdog_terminal = _validated_quickstart_watchdog_result(
                json.loads(watchdog_result_path.read_text(encoding="utf-8")),
                watchdog=watchdog,
                target_pid=remote_pid,
                target_process_start_ticks=process_start_ticks,
                # Quickstart is intentionally persistent. The runner requested
                # the verified group teardown above, so its guardian's exit is
                # a safe teardown terminal state, never natural completion.
                require_normal_completion=False,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "remote Quickstart watchdog terminal result is unreadable"
            ) from exc
        report["watchdog_terminal"] = watchdog_terminal
        report["requested_teardown"] = {
            "requested_by": "remote_lab_runner",
            "group_cleanup_verified": owned_cleanup,
            "watchdog_safe_terminal": True,
        }
        return report

    def _run_once(self) -> dict[str, Any]:
        self.current_phase = "preflight"
        preflight = self.preflight()
        self.last_preflight = preflight
        if not preflight.get("safe_to_run"):
            return {
                "schema_version": "loveengine.remote-lab-report/1",
                "status": "blocked_by_resource_guard",
                "target": self.args.host,
                "authentication": "publickey",
                "preflight": preflight,
            }
        core_max_cpus = self._lab_cpu_cap(preflight)
        self.current_phase = "source_validation"
        commit = self._require_clean_source()
        self.current_commit = commit
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        deployment_rel = f"{self.remote_root}/{commit[:12]}-{suffix}"
        remote_deployment = f"$HOME/{deployment_rel}"
        self.current_deployment_rel = deployment_rel
        local_output = (
            self.args.output.resolve()
            if self.args.output
            else (
                ROOT
                / "tmp"
                / "remote-experiments"
                / self.args.host
                / f"{commit[:12]}-{suffix}"
            ).resolve()
        )
        self._reserve_local_output(local_output)
        self.current_output = local_output
        self.current_output_owned = True
        started_at = _utc_now()

        self.current_phase = "remote_deployment_create"
        deployment_name = PurePosixPath(deployment_rel).name
        create_command = _remote_bash(
            "python3 - "
            f"{shlex.quote(self.remote_root)} {shlex.quote(deployment_name)}"
        )
        creation = self.ssh_run(
            create_command,
            input_text=REMOTE_DEPLOYMENT_CREATE_SOURCE,
            timeout=30,
        )
        creation_result = _last_json_object(creation.stdout)
        if creation_result != {"created": True, "relative": deployment_rel}:
            raise RuntimeError("remote deployment path was not canonically created")
        self.current_remote_deployment_created = True
        self.current_phase = "dependency_bundle_build"
        self._require_source_unchanged(commit)
        with tempfile.TemporaryDirectory(prefix="loveengine-remote-lab-") as raw_temp:
            archive = Path(raw_temp) / "source.tar"
            dependency_archive = Path(raw_temp) / "contract-dependencies.zip"
            dependency_bundle_build = build_contract_dependency_bundle(
                dependency_archive,
                contracts_root=ROOT / "contracts",
            )
            self.current_dependency_bundle = {
                "verified": False,
                "local": dependency_bundle_build,
            }
            subprocess.run(
                ["git", "archive", "--format=tar", "-o", str(archive), commit],
                cwd=ROOT,
                check=True,
                timeout=120,
            )
            self.current_phase = "source_archive_upload"
            self._copy_to_remote(
                archive,
                _owned_remote_file(deployment_rel, "source.tar"),
            )
            self._copy_to_remote(
                dependency_archive,
                _owned_remote_file(deployment_rel, "contract-dependencies.zip"),
            )

        self.current_phase = "remote_source_extract"
        extract_command = _remote_bash(
            "set -eu; "
            f"cd \"{remote_deployment}\"; "
            "tar --no-same-owner -xf source.tar; "
            "rm -f source.tar"
        )
        self.ssh_run(extract_command, timeout=120)
        self.current_phase = "remote_dependency_bundle_install"
        install_command = _remote_bash(
            "set -eu; "
            f"cd \"{remote_deployment}\"; "
            "PYTHONPATH=src python3 -m "
            "loveengine_witness.contract_dependency_bundle install "
            "contract-dependencies.zip --contracts-root contracts "
            "--expected-sha256 "
            f"{dependency_bundle_build['archive_sha256']}; "
            "rm -f contract-dependencies.zip"
        )
        installed_output = self.ssh_run(install_command, timeout=300)
        dependency_bundle_install = _last_json_object(installed_output.stdout)
        dependency_bundle_binding = _validated_dependency_bundle_binding(
            dependency_bundle_build,
            dependency_bundle_install,
        )
        self.current_dependency_bundle = dependency_bundle_binding
        self.current_phase = "remote_core"
        remote_report, core_cleanup = self._run_owned_remote_core(
            deployment_rel=deployment_rel,
            remote_deployment=remote_deployment,
            local_output=local_output,
            max_cpus=core_max_cpus,
        )
        self.current_phase = "transcript_download"
        self._copy_from_remote(
            _owned_remote_file(
                deployment_rel,
                "tmp/remote-core/witness-core-transcript.json",
            ),
            local_output / "witness-core-transcript.json",
        )
        self.current_phase = "local_transcript_verification"
        uv = _resolve_executable(None, "uv")
        verification = subprocess.run(
            [
                uv,
                "run",
                "loveengine",
                "pilot",
                "transcript",
                "verify",
                str(local_output / "witness-core-transcript.json"),
            ],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        offline = _last_json_object(verification.stdout)
        core_acceptance = _validate_remote_core_acceptance(
            remote_report,
            offline,
            expected_thresholds=_requested_thresholds(
                max_load_per_cpu=self.args.max_load_per_cpu,
                min_memory_gib=self.args.min_memory_gib,
                min_disk_gib=self.args.min_disk_gib,
            ),
            expected_max_cpus=core_max_cpus,
        )
        self.current_phase = "tunnel_smoke"
        self._require_source_unchanged(commit)
        tunnel = self._tunnel_smoke(
            deployment_rel=deployment_rel,
            remote_deployment=remote_deployment,
            local_output=local_output,
        )
        self._require_source_unchanged(commit)
        self.current_phase = "reporting"
        report = {
            "schema_version": "loveengine.remote-lab-report/1",
            "status": "passed",
            "target": self.args.host,
            "authentication": "publickey",
            "source_commit": commit,
            "remote_deployment": deployment_rel,
            "remote_deployment_preserved": True,
            "remote_services_exposed": False,
            "remote_bind": "loopback_only",
            "resource_profile": {
                "nice_increment": 15,
                "max_cpus": core_max_cpus,
                "reserved_cpu_count": 1,
                "available_cpus": int(preflight["host"]["cpu_count"]),
                "max_load_per_cpu": self.args.max_load_per_cpu,
            },
            "started_at": started_at,
            "completed_at": _utc_now(),
            "preflight": preflight,
            "contract_dependency_bundle": dependency_bundle_binding,
            "core": remote_report,
            "core_acceptance": core_acceptance,
            "tunnel_smoke": tunnel,
            "downloaded_transcript_verification": offline,
            "local_output": str(local_output),
            "owned_process_cleanup": tunnel["owned_process_cleanup"],
            "core_cleanup_verification": core_cleanup,
            "remote_file_cleanup_performed": False,
        }
        return report

    def _write_report(self, report: dict[str, Any]) -> None:
        output = self.current_output
        if output is None:
            requested = self.args.output.resolve() if self.args.output else None
            if requested is not None:
                try:
                    output = self._reserve_local_output(requested)
                except FileExistsError:
                    report["requested_output_collision"] = {
                        "path": str(requested),
                        "preserved": True,
                    }
                    output = self._new_failure_output()
            else:
                output = self._new_failure_output()
            self.current_output = output
            self.current_output_owned = True
        (output / "remote-lab-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )

    def run(self) -> dict[str, Any]:
        try:
            report = self._run_once()
        except Exception as exc:
            try:
                postflight = self.preflight()
            except Exception as postflight_exc:
                postflight = {
                    "available": False,
                    "error_type": type(postflight_exc).__name__,
                }
            blocked = isinstance(exc, ResourceGuardBlocked)
            report = {
                "schema_version": "loveengine.remote-lab-report/1",
                "status": "blocked_by_resource_guard" if blocked else "failed",
                "target": self.args.host,
                "authentication": "publickey",
                "source_commit": self.current_commit,
                "phase": self.current_phase,
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                },
                "preflight": self.last_preflight,
                "remote_deployment": getattr(self, "current_deployment_rel", None),
                "remote_deployment_created": getattr(
                    self,
                    "current_remote_deployment_created",
                    False,
                ),
                "remote_deployment_preserved": bool(
                    getattr(self, "current_remote_deployment_created", False)
                ),
                "downloaded_diagnostics": list(
                    getattr(self, "downloaded_diagnostics", [])
                ),
                "start_recoveries": list(getattr(self, "start_recoveries", [])),
                "execution_resource_blocks": list(
                    getattr(self, "execution_resource_blocks", [])
                ),
                "contract_dependency_bundle": getattr(
                    self,
                    "current_dependency_bundle",
                    None,
                ),
                "postflight": postflight,
                "postflight_cleanup_verified": bool(
                    postflight.get("host", {}).get("process_snapshot_ok")
                    and not postflight.get("host", {}).get(
                        "relevant_processes"
                    )
                ),
                "completed_at": _utc_now(),
                "remote_file_cleanup_performed": False,
            }
            if blocked:
                report["resource_guard"] = {
                    "phase": exc.phase,
                    "preflight": exc.preflight,
                }
            self._write_report(report)
            return report
        if report.get("status") != "passed":
            report.setdefault("phase", self.current_phase)
            report.setdefault("completed_at", _utc_now())
            self._write_report(report)
            return report
        self.current_phase = "postflight"
        try:
            postflight = self.preflight()
        except Exception as exc:
            postflight = {
                "available": False,
                "error_type": type(exc).__name__,
            }
        cleanup_verified = bool(
            postflight.get("host", {}).get("process_snapshot_ok")
            and not postflight.get("host", {}).get("relevant_processes")
        )
        report["postflight"] = postflight
        report["postflight_cleanup_verified"] = cleanup_verified
        if not cleanup_verified:
            report["status"] = "failed"
            report["phase"] = "postflight"
            report["error"] = {
                "type": "PostflightCleanupError",
                "message": "could not verify absence of lab processes",
            }
        report["completed_at"] = _utc_now()
        if self.current_commit is not None:
            try:
                self._require_source_unchanged(self.current_commit)
            except Exception as exc:
                report["status"] = "failed"
                report["phase"] = "source_identity"
                report["error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc)[:2000],
                }
        self._write_report(report)
        return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "run"))
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--port", type=int, default=22)
    parser.add_argument("--identity-file", type=Path, required=True)
    parser.add_argument("--known-hosts", type=Path, required=True)
    parser.add_argument("--remote-root", default=".local/share/loveengine-witness-lab")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ssh")
    parser.add_argument("--scp")
    parser.add_argument("--max-load-per-cpu", type=float, default=0.5)
    parser.add_argument("--min-memory-gib", type=float, default=3.0)
    parser.add_argument("--min-disk-gib", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    lab = RemoteLab(args)
    report = lab.preflight() if args.mode == "preflight" else lab.run()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if args.mode == "preflight":
        return 0 if report.get("safe_to_run") else 4
    return 0 if report.get("status") == "passed" else 4


if __name__ == "__main__":
    raise SystemExit(main())
