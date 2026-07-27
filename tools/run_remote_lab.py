#!/usr/bin/env python3
"""Key-only, resource-gated deployment of the Witness core lab over SSH."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
HOST_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_PATTERN = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
REMOTE_ROOT_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
REMOTE_FILE_PATTERN = re.compile(r"^/[A-Za-z0-9._/-]+$")


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


def _last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
    raise RuntimeError("remote command did not emit a JSON object")


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
        "-p",
        str(args.port),
        "-i",
        str(identity),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
    ]


def _scp_options(args: argparse.Namespace) -> list[str]:
    options = _ssh_options(args)
    options[0] = "-P"
    return options


def _remote_bash(script: str) -> str:
    return "bash -lc " + shlex.quote(script)


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


def _validate_remote_artifact(value: str, deployment_rel: str) -> str:
    if (
        not REMOTE_FILE_PATTERN.fullmatch(value)
        or ".." in PurePosixPath(value).parts
        or f"/{deployment_rel}/" not in value
    ):
        raise RuntimeError("remote artifact escaped the unique deployment directory")
    return value


def _owned_group_stop_command(pid: int, process_start_ticks: int) -> str:
    if pid <= 1 or process_start_ticks <= 0:
        raise ValueError("owned process identity must be positive")
    return _remote_bash(
        "set -eu; "
        f"pid={pid}; "
        f"expected_start={process_start_ticks}; "
        "group_has_live_members() { "
        "while read -r pgid state; do "
        "if [ \"$pgid\" = \"$pid\" ]; then "
        "case \"$state\" in Z*) ;; *) return 0 ;; esac; "
        "fi; "
        "done < <(ps -eo pgid=,stat=); "
        "return 1; "
        "}; "
        "if [ ! -r \"/proc/$pid/stat\" ]; then "
        "if group_has_live_members; then exit 10; fi; "
        "exit 0; "
        "fi; "
        "stat=$(cat \"/proc/$pid/stat\"); "
        "tail=${stat##*) }; "
        "set -- $tail; "
        "state=$1; "
        "eval \"current_start=\\${20}\"; "
        "if [ \"$current_start\" != \"$expected_start\" ]; then exit 9; fi; "
        "if [ \"$state\" != \"Z\" ]; then "
        "command=$(tr '\\000' ' ' < \"/proc/$pid/cmdline\"); "
        "case \"$command\" in "
        "*\"loveengine pilot quickstart\"*) ;; "
        "*) exit 9 ;; "
        "esac; "
        "fi; "
        "if ! group_has_live_members; then exit 0; fi; "
        "kill -TERM -- \"-$pid\"; "
        "for _ in {1..20}; do "
        "if ! group_has_live_members; then exit 0; fi; "
        "sleep 0.25; "
        "done; "
        "kill -KILL -- \"-$pid\"; "
        "for _ in {1..8}; do "
        "if ! group_has_live_members; then exit 0; fi; "
        "sleep 0.25; "
        "done; "
        "exit 10"
    )


def _owned_group_absence_command(pid: int, process_start_ticks: int) -> str:
    if pid <= 1 or process_start_ticks <= 0:
        raise ValueError("owned process identity must be positive")
    return _remote_bash(
        "set -eu; "
        f"pid={pid}; "
        f"expected_start={process_start_ticks}; "
        "group_has_live_members() { "
        "while read -r pgid state; do "
        "if [ \"$pgid\" = \"$pid\" ]; then "
        "case \"$state\" in Z*) ;; *) return 0 ;; esac; "
        "fi; "
        "done < <(ps -eo pgid=,stat=); "
        "return 1; "
        "}; "
        "for _ in {1..20}; do "
        "if [ -r \"/proc/$pid/stat\" ]; then "
        "stat=$(cat \"/proc/$pid/stat\"); "
        "tail=${stat##*) }; "
        "set -- $tail; "
        "eval \"current_start=\\${20}\"; "
        "if [ \"$current_start\" != \"$expected_start\" ]; then exit 9; fi; "
        "fi; "
        "if ! group_has_live_members; then exit 0; fi; "
        "sleep 0.25; "
        "done; "
        "exit 10"
    )


class RemoteLab:
    def __init__(self, args: argparse.Namespace) -> None:
        _validate_target(args.host, args.user, args.port)
        self.remote_root = _validate_remote_root(args.remote_root)
        self.args = args
        self.ssh = _resolve_executable(args.ssh, "ssh")
        self.scp = _resolve_executable(args.scp, "scp")
        self.target = f"{args.user}@{args.host}"
        self.ssh_options = _ssh_options(args)
        self.scp_options = _scp_options(args)

    def ssh_run(
        self,
        command: str,
        *,
        input_text: str | None = None,
        timeout: int,
        allow_capacity_rejection: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [self.ssh, *self.ssh_options, self.target, command],
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

with open(sys.argv[1], encoding="utf-8") as stream:
    config = json.load(stream)
print(json.dumps({"package_archive": config["package_archive"]}))
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
        return _validate_remote_artifact(
            str(value["package_archive"]),
            deployment_rel,
        )

    def _stop_owned_remote_group(
        self, pid: int, process_start_ticks: int
    ) -> dict[str, int | bool]:
        try:
            stop_command = _owned_group_stop_command(pid, process_start_ticks)
            absence_command = _owned_group_absence_command(
                pid, process_start_ticks
            )
        except ValueError:
            return {
                "verified": False,
                "stop_returncode": -1,
                "absence_returncode": -1,
            }
        stop_result = subprocess.run(
            [self.ssh, *self.ssh_options, self.target, stop_command],
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        absence_result = subprocess.run(
            [self.ssh, *self.ssh_options, self.target, absence_command],
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

    def _tunnel_smoke(
        self,
        *,
        deployment_rel: str,
        remote_deployment: str,
        local_output: Path,
    ) -> dict[str, Any]:
        preflight = self.preflight()
        if not preflight.get("safe_to_run"):
            raise RuntimeError("tunnel smoke blocked by the second resource guard")
        remote_pilot_port, remote_rpc_port = self._remote_free_ports()
        local_pilot_port = _free_local_port()
        local_rpc_port = _free_local_port()
        while local_rpc_port == local_pilot_port:
            local_rpc_port = _free_local_port()
        start_command = _remote_bash(
            "set -eu; "
            "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
            "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
            f"cd \"{remote_deployment}\"; "
            "uv run python tools/start_shared_quickstart.py "
            "--root tmp/tunnel-pilot --host 127.0.0.1 "
            f"--port {remote_pilot_port} --rpc-port {remote_rpc_port} "
            "--log tmp/tunnel-pilot.log --max-cpus 2 --nice-increment 15"
        )
        started = self.ssh_run(start_command, timeout=30)
        start_info = _last_json_object(started.stdout)
        remote_pid = int(start_info["pid"])
        process_start_ticks = int(start_info["process_start_ticks"])
        if remote_pid <= 1 or process_start_ticks <= 0:
            raise RuntimeError("remote Quickstart returned an invalid process identity")

        tunnel_dir = local_output / "tunnel"
        tunnel_dir.mkdir(parents=True, exist_ok=False)
        tunnel_process: subprocess.Popen[str] | None = None
        node_process: subprocess.Popen[str] | None = None
        report: dict[str, Any] | None = None
        owned_cleanup = False
        cleanup_verification: dict[str, int | bool] = {
            "verified": False,
            "stop_returncode": -1,
            "absence_returncode": -1,
        }
        try:
            tunnel_process = subprocess.Popen(
                [
                    self.ssh,
                    *self.ssh_options,
                    "-o",
                    "ExitOnForwardFailure=yes",
                    "-T",
                    "-N",
                    "-L",
                    f"{local_pilot_port}:127.0.0.1:{remote_pilot_port}",
                    "-L",
                    f"{local_rpc_port}:127.0.0.1:{remote_rpc_port}",
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
            remote_pilot = f"~/{deployment_rel}/tmp/tunnel-pilot"
            invite_path = tunnel_dir / "pilot-invite.json"
            trust_policy_path = tunnel_dir / "pilot-trust-policy.json"
            profile_path = tunnel_dir / "node-1.json"
            package_path = tunnel_dir / "loveengine-witness.zip"
            self._copy_from_remote(
                f"{remote_pilot}/pilot-invite.json",
                invite_path,
            )
            self._copy_from_remote(
                f"{remote_pilot}/pilot-trust-policy.json",
                trust_policy_path,
            )
            self._copy_from_remote(
                f"{remote_pilot}/profiles/node-1.json",
                profile_path,
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
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            node = str(profile["profile"]["node"])
            dispute_id = "remote-tunnel-dispute"
            task_id = "remote-tunnel-late-task"
            verdicts_path = tunnel_dir / "verdicts.json"
            verdicts_path.write_text(
                json.dumps({dispute_id: "dismiss"}, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            node_result_path = tunnel_dir / "node-result.json"
            uv = _resolve_executable(None, "uv")
            node_process = subprocess.Popen(
                [
                    uv,
                    "run",
                    "loveengine",
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
                    str(tunnel_dir / "node.cursor.sqlite"),
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
            connected_deadline = time.monotonic() + 60
            metrics: dict[str, Any] | None = None
            while time.monotonic() < connected_deadline:
                if node_process.poll() is not None:
                    _, detail = node_process.communicate()
                    raise RuntimeError(
                        f"tunneled node exited before connecting: {detail.strip()}"
                    )
                metrics = _wait_http_json_or_none(
                    base_url + "/v1/metrics",
                    timeout=2,
                    process=tunnel_process,
                )
                if metrics is None:
                    continue
                if int(metrics["connections"]["agents"]) >= 1:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError("tunneled node did not connect within 60 seconds")

            enqueue_command = _remote_bash(
                "set -eu; "
                "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
                "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
                f"cd \"{remote_deployment}\"; "
                "uv run python tools/enqueue_pilot_task.py "
                "--root tmp/tunnel-pilot --profile-index 1 "
                f"--task-id {task_id} --dispute-id {dispute_id}"
            )
            submitted = self.ssh_run(enqueue_command, timeout=30)
            submission = _last_json_object(submitted.stdout)
            stdout, stderr = node_process.communicate(timeout=90)
            if node_process.returncode != 0:
                raise RuntimeError(
                    "tunneled node failed: "
                    + (stderr.strip() or stdout.strip() or str(node_process.returncode))
                )
            node_result = json.loads(node_result_path.read_text(encoding="utf-8"))
            if (
                node_result.get("rejected") != 0
                or len(node_result.get("receipts", [])) != 1
                or node_result["receipts"][0].get("task_id") != task_id
            ):
                raise RuntimeError("tunneled node did not return the bound receipt")
            metrics_deadline = time.monotonic() + 15
            while time.monotonic() < metrics_deadline:
                metrics = _wait_http_json_or_none(
                    base_url + "/v1/metrics",
                    timeout=2,
                    process=tunnel_process,
                )
                if metrics is None:
                    continue
                if int(metrics["relay"]["acked"]) == 1:
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
                "node": node,
                "task_submission": submission,
                "receipt_count": 1,
                "relay_acked": int(metrics["relay"]["acked"]),
            }
        finally:
            if node_process is not None and node_process.poll() is None:
                node_process.terminate()
                try:
                    node_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    node_process.kill()
                    node_process.wait(timeout=5)
            if tunnel_process is not None and tunnel_process.poll() is None:
                tunnel_process.terminate()
                try:
                    tunnel_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    tunnel_process.kill()
                    tunnel_process.wait(timeout=5)
            cleanup_verification = self._stop_owned_remote_group(
                remote_pid, process_start_ticks
            )
            owned_cleanup = bool(cleanup_verification["verified"])
            try:
                self._copy_from_remote(
                    f"~/{deployment_rel}/tmp/tunnel-pilot.log",
                    tunnel_dir / "quickstart.log",
                )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                pass
        if report is None:
            raise RuntimeError("tunnel smoke ended without a report")
        report["owned_process_cleanup"] = owned_cleanup
        report["cleanup_verification"] = cleanup_verification
        if not owned_cleanup:
            raise RuntimeError(
                "could not verify cleanup of the remote process group "
                f"(stop={cleanup_verification['stop_returncode']}, "
                f"absence={cleanup_verification['absence_returncode']})"
            )
        return report

    def run(self) -> dict[str, Any]:
        preflight = self.preflight()
        if not preflight.get("safe_to_run"):
            return {
                "schema_version": "loveengine.remote-lab-report/1",
                "status": "blocked_by_resource_guard",
                "target": self.args.host,
                "authentication": "publickey",
                "preflight": preflight,
            }
        commit = self._require_clean_source()
        suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        deployment_rel = f"{self.remote_root}/{commit[:12]}-{suffix}"
        remote_deployment = f"$HOME/{deployment_rel}"
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
        local_output.mkdir(parents=True, exist_ok=False)
        started_at = _utc_now()

        create_command = _remote_bash(
            "set -eu; umask 077; "
            f"test ! -e \"{remote_deployment}\"; "
            f"mkdir -p \"{remote_deployment}\""
        )
        self.ssh_run(create_command, timeout=30)
        with tempfile.TemporaryDirectory(prefix="loveengine-remote-lab-") as raw_temp:
            archive = Path(raw_temp) / "source.tar"
            subprocess.run(
                ["git", "archive", "--format=tar", "-o", str(archive), commit],
                cwd=ROOT,
                check=True,
                timeout=120,
            )
            self._copy_to_remote(
                archive,
                f"~/{deployment_rel}/source.tar",
            )

        extract_command = _remote_bash(
            "set -eu; "
            f"cd \"{remote_deployment}\"; "
            "tar --no-same-owner -xf source.tar; "
            "rm -f source.tar"
        )
        self.ssh_run(extract_command, timeout=120)
        run_command = _remote_bash(
            "set -eu; "
            "export PATH=\"$HOME/.local/bin:$HOME/.codex/tools/"
            "foundry-v1.7.1:$HOME/.foundry/bin:$HOME/.cargo/bin:$PATH\"; "
            f"cd \"{remote_deployment}\"; "
            "python3 tools/run_core_experiments.py "
            "--shared-host --prepare-contracts --include-recovery-tests "
            f"--max-load-per-cpu {self.args.max_load_per_cpu} "
            f"--min-memory-gib {self.args.min_memory_gib} "
            f"--min-disk-gib {self.args.min_disk_gib} "
            "--max-cpus 2 --nice-increment 15 --events 12 --observers 3 "
            "--output tmp/remote-core"
        )
        result = self.ssh_run(run_command, timeout=self.args.timeout_seconds)
        remote_report = _last_json_object(result.stdout)
        if remote_report.get("status") != "passed":
            raise RuntimeError("remote core experiment did not pass")

        self._copy_from_remote(
            f"~/{deployment_rel}/tmp/remote-core/core-experiment-report.json",
            local_output / "core-experiment-report.json",
        )
        self._copy_from_remote(
            f"~/{deployment_rel}/tmp/remote-core/witness-core-transcript.json",
            local_output / "witness-core-transcript.json",
        )
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
        if (
            offline.get("verification_level") != "offline_integrity"
            or offline.get("trust_bound") is not False
        ):
            raise RuntimeError("downloaded transcript failed offline boundary checks")
        tunnel = self._tunnel_smoke(
            deployment_rel=deployment_rel,
            remote_deployment=remote_deployment,
            local_output=local_output,
        )
        report = {
            "schema_version": "loveengine.remote-lab-report/1",
            "status": "passed",
            "target": self.args.host,
            "authentication": "publickey",
            "source_commit": commit,
            "remote_deployment": deployment_rel,
            "remote_services_exposed": False,
            "remote_bind": "loopback_only",
            "resource_profile": {
                "nice_increment": 15,
                "max_cpus": 2,
                "max_load_per_cpu": self.args.max_load_per_cpu,
            },
            "started_at": started_at,
            "completed_at": _utc_now(),
            "preflight": preflight,
            "core": remote_report,
            "tunnel_smoke": tunnel,
            "downloaded_transcript_verification": offline,
            "local_output": str(local_output),
            "owned_process_cleanup": tunnel["owned_process_cleanup"],
            "remote_file_cleanup_performed": False,
        }
        (local_output / "remote-lab-report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
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
