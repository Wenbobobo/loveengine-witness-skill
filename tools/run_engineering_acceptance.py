#!/usr/bin/env python3
"""Run the exact-commit 15-minute engineering acceptance gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psutil

from loveengine_witness.pilot_soak import SOAK_SUCCESS_CHECK_KEYS


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "loveengine.engineering-acceptance-report/1"
DURATION_SECONDS = 900
EVENT_COUNT = 30
OBSERVER_COUNT = 10
POLL_SECONDS = 5
COMPLETION_GRACE_SECONDS = 300
REPORT_FILENAME = "engineering-acceptance-report.json"
V2_REQUIRED_SOAK_CHECKS = SOAK_SUCCESS_CHECK_KEYS | {
    "v2_wall_clock_duration_met",
    "v2_participant_claims",
    "v2_standalone_evidence_honest",
}


class AcceptanceError(RuntimeError):
    """One stable acceptance invariant failed."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _run(
    command: list[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    if check and result.returncode != 0:
        raise AcceptanceError(
            f"command_failed:{stdout_path.stem}:exit_{result.returncode}"
        )
    return result


def _parse_json_output(result: subprocess.CompletedProcess[str], label: str) -> dict[str, Any]:
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise AcceptanceError(f"{label}_missing_json")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"{label}_invalid_json") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label}_not_object")
    return value


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AcceptanceError(f"git_{args[0]}_failed")
    return result.stdout.strip()


def _require_clean_commit() -> dict[str, str]:
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise AcceptanceError("worktree_not_clean")
    commit = _git("rev-parse", "HEAD")
    if len(commit) != 40:
        raise AcceptanceError("invalid_git_commit")
    branch = _git("branch", "--show-current") or "detached"
    return {"commit": commit, "branch": branch, "clean": "true"}


def _powershell_command() -> list[str]:
    candidates = ["powershell", "pwsh"] if os.name == "nt" else ["pwsh"]
    executable = next((item for item in candidates if shutil.which(item)), None)
    if executable is None:
        raise AcceptanceError("powershell_not_available")
    command = [executable, "-NoProfile"]
    if os.name == "nt" and Path(executable).name.lower().startswith("powershell"):
        command.extend(["-ExecutionPolicy", "Bypass"])
    return command + ["-File", str(ROOT / "tools" / "run_release_checks.ps1")]


def _argument_value(command: list[str], name: str) -> str | None:
    try:
        index = command.index(name)
    except ValueError:
        return None
    if index + 1 >= len(command):
        return None
    return command[index + 1]


def _cleanup_owned_soak(
    launch: dict[str, Any], state_path: Path, soak_output: Path
) -> dict[str, Any]:
    cleanup: dict[str, Any] = {
        "attempted": True,
        "identity_verified": False,
        "stopped": False,
        "error": None,
    }
    try:
        expected_state_path = soak_output / "pilot-soak-run.json"
        if state_path != expected_state_path.resolve():
            raise AcceptanceError("cleanup_state_path_mismatch")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(launch.get("pid") or 0)
        run_id = str(launch.get("run_id") or "")
        if pid <= 0 or state.get("pid") != pid or state.get("run_id") != run_id:
            raise AcceptanceError("cleanup_state_identity_mismatch")
        if Path(str(state.get("output"))).resolve() != soak_output:
            raise AcceptanceError("cleanup_output_mismatch")
        if Path(str(state.get("package_root"))).resolve() != ROOT:
            raise AcceptanceError("cleanup_package_root_mismatch")
        try:
            process = psutil.Process(pid)
        except psutil.NoSuchProcess:
            cleanup["identity_verified"] = True
            cleanup["stopped"] = True
            return cleanup
        command = process.cmdline()
        if (
            "loveengine_witness.cli" not in command
            or _argument_value(command, "--worker-run-id") != run_id
            or Path(str(_argument_value(command, "--output"))).resolve()
            != soak_output
        ):
            raise AcceptanceError("cleanup_process_command_mismatch")
        started_at = float(state["started_at_epoch"])
        if abs(process.create_time() - started_at) > 60:
            raise AcceptanceError("cleanup_process_start_mismatch")
        cleanup["identity_verified"] = True
        owned = process.children(recursive=True)
        for child in reversed(owned):
            child.terminate()
        process.terminate()
        _, alive = psutil.wait_procs([*owned, process], timeout=10)
        for item in alive:
            item.kill()
        _, alive = psutil.wait_procs(alive, timeout=5)
        cleanup["stopped"] = not alive
        if alive:
            cleanup["error"] = "owned_processes_still_alive"
    except (AcceptanceError, OSError, TypeError, ValueError, psutil.Error) as exc:
        cleanup["error"] = str(exc)
    return cleanup


def _sample_owned_process_tree(
    root_pid: int, observed: dict[str, dict[str, Any]]
) -> None:
    try:
        root = psutil.Process(root_pid)
        processes = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return
    except psutil.Error as exc:
        raise AcceptanceError(
            f"process_tree_sampling_failed:{exc.__class__.__name__}"
        ) from exc
    for process in processes:
        try:
            observed[str(process.pid)] = {
                "pid": process.pid,
                "create_time": process.create_time(),
                "name": process.name(),
            }
        except psutil.NoSuchProcess:
            continue
        except psutil.Error as exc:
            raise AcceptanceError(
                f"process_tree_sampling_failed:{exc.__class__.__name__}"
            ) from exc


def _matching_live_processes(
    observed: dict[str, dict[str, Any]],
) -> list[psutil.Process]:
    alive: list[psutil.Process] = []
    for item in observed.values():
        try:
            process = psutil.Process(int(item["pid"]))
            if abs(process.create_time() - float(item["create_time"])) > 0.01:
                continue
            if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                alive.append(process)
        except psutil.NoSuchProcess:
            continue
        except (KeyError, TypeError, ValueError, psutil.Error) as exc:
            raise AcceptanceError(
                f"process_tree_verification_failed:{exc.__class__.__name__}"
            ) from exc
    return alive


def _cleanup_observed_processes(
    observed: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result = {"attempted": True, "stopped": False, "error": None}
    try:
        alive = _matching_live_processes(observed)
        for process in reversed(alive):
            process.terminate()
        _, alive = psutil.wait_procs(alive, timeout=10)
        for process in alive:
            process.kill()
        _, alive = psutil.wait_procs(alive, timeout=5)
        result["stopped"] = not alive
        if alive:
            result["error"] = "owned_processes_still_alive"
    except (AcceptanceError, psutil.Error) as exc:
        result["error"] = str(exc)
    return result


def _validate_terminal_evidence(
    status: dict[str, Any],
    report: dict[str, Any],
    verification: dict[str, Any],
    *,
    diagnostic_sizes: dict[str, int],
) -> None:
    if status.get("status") != "passed" or status.get("process_alive") is not False:
        raise AcceptanceError("soak_not_terminal_passed")
    if report.get("run_id") != status.get("run_id"):
        raise AcceptanceError("soak_run_id_mismatch")
    if report.get("passed") is not True or report.get("failure") is not None:
        raise AcceptanceError("soak_report_failed")
    if report.get("requested_duration_seconds") != DURATION_SECONDS:
        raise AcceptanceError("soak_duration_mismatch")
    try:
        elapsed_seconds = float(report["elapsed_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AcceptanceError("soak_elapsed_invalid") from exc
    if elapsed_seconds < DURATION_SECONDS:
        raise AcceptanceError("soak_wall_clock_duration_not_met")
    if report.get("event_count") != EVENT_COUNT:
        raise AcceptanceError("soak_event_count_mismatch")
    if report.get("observer_count") != OBSERVER_COUNT:
        raise AcceptanceError("soak_observer_count_mismatch")
    if report.get("core_transcript_version") != 2:
        raise AcceptanceError("core_transcript_version_mismatch")
    checks = report.get("checks")
    if not isinstance(checks, dict) or not V2_REQUIRED_SOAK_CHECKS.issubset(checks):
        raise AcceptanceError("soak_check_inventory_incomplete")
    if any(value is not True for value in checks.values()):
        raise AcceptanceError("soak_checks_failed")
    if report.get("secret_leaks") != []:
        raise AcceptanceError("soak_secret_findings")
    nonempty_diagnostics = sorted(
        name for name, size in diagnostic_sizes.items() if size != 0
    )
    if nonempty_diagnostics:
        raise AcceptanceError(
            "unexpected_stderr:" + ",".join(nonempty_diagnostics)
        )
    expected_verification = {
        "valid": True,
        "verification_level": "offline_integrity",
        "chain_verified": False,
        "trust_bound": False,
        "event_count": EVENT_COUNT,
        "observation_receipts": 3,
        "review_receipts": 3,
        "gate_ready": True,
        "participant_claims_verified": True,
        "nodes": 3,
        "declared_operator_groups": 2,
        "declared_network_groups": 2,
    }
    for key, expected in expected_verification.items():
        if verification.get(key) != expected:
            raise AcceptanceError(f"transcript_{key}_mismatch")
    if verification.get("run_id") != report.get("run_id"):
        raise AcceptanceError("transcript_run_id_mismatch")


def _soak_transcript_path(report: dict[str, Any], soak_output: Path) -> Path:
    if report.get("passed") is not True:
        failure = report.get("failure")
        code = failure.get("code") if isinstance(failure, dict) else None
        suffix = f":{code}" if isinstance(code, str) and code else ""
        raise AcceptanceError("soak_report_failed" + suffix)
    raw_path = report.get("transcript_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AcceptanceError("soak_transcript_path_missing")
    transcript_path = Path(raw_path).resolve()
    try:
        transcript_path.relative_to(soak_output.resolve())
    except ValueError as exc:
        raise AcceptanceError("soak_transcript_path_outside_output") from exc
    if not transcript_path.is_file():
        raise AcceptanceError("soak_transcript_missing")
    return transcript_path


def _initial_report(
    git_context: dict[str, str], manifest_package_hash: str
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "running",
        "evidence_class": "engineering_acceptance",
        "started_at": _utc_now(),
        "source_commit": git_context["commit"],
        "source_branch": git_context["branch"],
        "worktree_clean": git_context["clean"] == "true",
        "manifest_package_hash": manifest_package_hash,
        "stage": "core",
        "duration_seconds": DURATION_SECONDS,
        "event_count": EVENT_COUNT,
        "observer_count": OBSERVER_COUNT,
        "core_transcript_version": 2,
        "environment": "local_anvil",
        "actors_simulated": True,
        "long_term_availability_verified": False,
        "production_ready": False,
        "failure": None,
        "checks": {},
        "artifacts": {},
    }


def run(output: Path) -> dict[str, Any]:
    git_context = _require_clean_commit()
    acceptance_env = {
        **os.environ,
        "LOVEENGINE_EXPECTED_SOURCE_COMMIT": git_context["commit"],
    }
    manifest = json.loads(
        (ROOT / "skills" / "loveengine-witness" / "skill-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    manifest_package_hash = manifest.get("package_hash")
    if not isinstance(manifest_package_hash, str) or not manifest_package_hash.startswith(
        "sha256:"
    ):
        raise AcceptanceError("manifest_package_hash_invalid")

    output = output.resolve()
    if output.exists():
        raise AcceptanceError("output_already_exists")
    output.mkdir(parents=True)
    report_path = output / REPORT_FILENAME
    result = _initial_report(git_context, manifest_package_hash)
    _write_json(report_path, result)
    owned_soak: tuple[dict[str, Any], Path, Path] | None = None
    observed_processes: dict[str, dict[str, Any]] = {}

    try:
        release_stdout = output / "release-gate.stdout.log"
        release_stderr = output / "release-gate.stderr.log"
        _run(
            _powershell_command(),
            stdout_path=release_stdout,
            stderr_path=release_stderr,
            env=acceptance_env,
        )
        result["checks"]["complete_release_gate"] = True

        soak_output = output / "soak"
        launch = _run(
            [
                sys.executable,
                "-m",
                "loveengine_witness.cli",
                "pilot",
                "soak",
                "--stage",
                "core",
                "--duration-seconds",
                str(DURATION_SECONDS),
                "--events",
                str(EVENT_COUNT),
                "--observers",
                str(OBSERVER_COUNT),
                "--core-transcript-version",
                "2",
                "--output",
                str(soak_output),
                "--background",
            ],
            stdout_path=output / "soak-launch.stdout.json",
            stderr_path=output / "soak-launch.stderr.log",
            env=acceptance_env,
        )
        launch_value = _parse_json_output(launch, "soak_launch")
        state_path = Path(str(launch_value["state_path"])).resolve()
        owned_soak = (launch_value, state_path, soak_output.resolve())
        worker_pid = int(launch_value.get("pid") or 0)
        if worker_pid <= 0:
            raise AcceptanceError("soak_worker_pid_missing")
        _sample_owned_process_tree(worker_pid, observed_processes)
        deadline = time.monotonic() + DURATION_SECONDS + COMPLETION_GRACE_SECONDS
        status_log = output / "soak-status.jsonl"
        status: dict[str, Any] = launch_value
        with status_log.open("a", encoding="utf-8") as log:
            while status.get("status") in {"starting", "running"}:
                if time.monotonic() >= deadline:
                    raise AcceptanceError("soak_completion_timeout")
                time.sleep(POLL_SECONDS)
                _sample_owned_process_tree(worker_pid, observed_processes)
                poll = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "loveengine_witness.cli",
                        "pilot",
                        "soak-status",
                        str(state_path),
                    ],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                    env=acceptance_env,
                )
                if poll.returncode != 0:
                    raise AcceptanceError("soak_status_failed")
                status = _parse_json_output(poll, "soak_status")
                log.write(json.dumps(status, ensure_ascii=False, sort_keys=True) + "\n")
                log.flush()

        live_processes = _matching_live_processes(observed_processes)
        process_tree_evidence = {
            "root_pid": worker_pid,
            "sampled_processes": sorted(
                observed_processes.values(), key=lambda item: int(item["pid"])
            ),
            "sample_count": len(observed_processes),
            "alive_after_worker_exit": [process.pid for process in live_processes],
            "complete_process_tree_cleanup_verified": not live_processes,
        }
        process_tree_path = output / "process-tree-evidence.json"
        _write_json(process_tree_path, process_tree_evidence)
        if live_processes:
            raise AcceptanceError("owned_descendant_process_still_alive")
        result["checks"]["complete_process_tree_cleanup"] = True

        soak_report_path = soak_output / "pilot-soak-report.json"
        soak_report = json.loads(soak_report_path.read_text(encoding="utf-8"))
        transcript_path = _soak_transcript_path(soak_report, soak_output)
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        if transcript.get("source_commit") != git_context["commit"]:
            raise AcceptanceError("transcript_source_commit_mismatch")
        verification_result = _run(
            [
                sys.executable,
                "-m",
                "loveengine_witness.cli",
                "pilot",
                "transcript",
                "verify",
                str(transcript_path),
            ],
            stdout_path=output / "transcript-verify.stdout.json",
            stderr_path=output / "transcript-verify.stderr.log",
            env=acceptance_env,
        )
        verification = _parse_json_output(verification_result, "transcript_verify")
        launch_stderr = output / "soak-launch.stderr.log"
        verification_stderr = output / "transcript-verify.stderr.log"
        soak_stderr = soak_output / "pilot-soak.stderr.log"
        _validate_terminal_evidence(
            status,
            soak_report,
            verification,
            diagnostic_sizes={
                "soak_launch": launch_stderr.stat().st_size,
                "soak_process": soak_stderr.stat().st_size,
                "transcript_verify": verification_stderr.stat().st_size,
            },
        )
        result["checks"]["terminal_soak_evidence"] = True
        result["checks"]["offline_transcript_verification"] = True
        result["checks"]["runtime_stderr_empty"] = True

        with tempfile.TemporaryDirectory(prefix="loveengine-transcript-tamper-") as temp:
            tampered_path = Path(temp) / "witness-core.tampered.json"
            tampered = json.loads(transcript_path.read_text(encoding="utf-8"))
            tampered["run_id"] = str(tampered["run_id"]) + "-tampered"
            tampered_path.write_text(
                json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tamper = _run(
                [
                    sys.executable,
                    "-m",
                    "loveengine_witness.cli",
                    "pilot",
                    "transcript",
                    "verify",
                    str(tampered_path),
                ],
                stdout_path=output / "tamper-check.stdout.log",
                stderr_path=output / "tamper-check.stderr.log",
                check=False,
                env=acceptance_env,
            )
            if tamper.returncode == 0:
                raise AcceptanceError("tampered_transcript_accepted")
        result["checks"]["tampered_transcript_rejected"] = True

        _run(
            [sys.executable, str(ROOT / "tools" / "scan_secrets.py")],
            stdout_path=output / "final-secret-scan.stdout.log",
            stderr_path=output / "final-secret-scan.stderr.log",
            env=acceptance_env,
        )
        _run(
            ["git", "diff", "--check"],
            stdout_path=output / "final-diff-check.stdout.log",
            stderr_path=output / "final-diff-check.stderr.log",
            env=acceptance_env,
        )
        result["checks"]["final_secret_scan"] = True
        result["checks"]["final_diff_check"] = True
        if _git("status", "--porcelain=v1", "--untracked-files=all"):
            raise AcceptanceError("worktree_changed_during_acceptance")
        if _git("rev-parse", "HEAD") != git_context["commit"]:
            raise AcceptanceError("source_commit_changed_during_acceptance")
        result["checks"]["worktree_remained_clean"] = True

        result["run_id"] = soak_report["run_id"]
        result["verification"] = verification
        for label, path in {
            "soak_state": state_path,
            "soak_report": soak_report_path,
            "transcript": transcript_path,
            "soak_stderr": soak_stderr,
            "soak_launch_stderr": launch_stderr,
            "transcript_verify_stderr": verification_stderr,
            "release_gate_stdout": release_stdout,
            "release_gate_stderr": release_stderr,
            "process_tree_evidence": process_tree_path,
        }.items():
            result["artifacts"][label] = {
                "path": str(path),
                "sha256": _sha256(path),
                "size": path.stat().st_size,
            }
        result["status"] = "passed"
    except Exception as exc:
        result["status"] = "failed"
        result["failure"] = {
            "type": exc.__class__.__name__,
            "message": str(exc),
        }
    if result["status"] != "passed" and owned_soak is not None:
        result["cleanup"] = _cleanup_owned_soak(*owned_soak)
        result["descendant_cleanup"] = _cleanup_observed_processes(
            observed_processes
        )
    result["completed_at"] = _utc_now()
    result["passed"] = result["status"] == "passed"
    _write_json(report_path, result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete release gate and one fixed 900-second core "
            "engineering acceptance against a clean exact commit."
        )
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = run(args.output)
    except AcceptanceError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, sort_keys=True))
        return 4
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result.get("status") == "passed" else 4


if __name__ == "__main__":
    raise SystemExit(main())
