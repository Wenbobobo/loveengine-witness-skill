#!/usr/bin/env python3
"""Cross-platform Witness core experiment runner with shared-host limits."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from remote_host_preflight import (
    CapacityThresholds,
    is_passing_preflight,
)
from start_shared_quickstart import _limit_process, select_shared_host_cpus


ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
MAX_SHARED_HOST_CPUS = 2
MIN_SHARED_HOST_NICE_INCREMENT = 15
CONTRACT_PREP_FAILURE_PATTERN = re.compile(
    r"^could not install "
    r"(?P<dependency>forge-std|openzeppelin-contracts) "
    r"\[stage=(?P<stage>git_init|git_remote_add|git_fetch|git_checkout|"
    r"git_rev_parse|git_submodule_update), "
    r"exit_code=(?P<command_exit_code>[1-9][0-9]{0,2})\]$"
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _last_json_object(text: str) -> dict[str, Any]:
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{"):
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
    raise RuntimeError("command did not emit a JSON object")


def _safe_contract_prepare_diagnostic(stderr: str) -> dict[str, Any] | None:
    """Extract only the terminal allowlisted CLI error from stderr."""

    for line in reversed(stderr[-16_384:].splitlines()):
        try:
            payload = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if "error" not in payload:
            continue
        error = payload["error"]
        if not isinstance(error, dict):
            return None
        if error.get("code") != "contract_dependency_install_failed":
            return None
        message = error.get("message")
        if not isinstance(message, str):
            return None
        match = CONTRACT_PREP_FAILURE_PATTERN.fullmatch(message)
        if match is None:
            return None
        dependency = match.group("dependency")
        stage = match.group("stage")
        command_exit_code = int(match.group("command_exit_code"))
        if not 1 <= command_exit_code <= 255:
            continue
        return {
            "kind": "contract_prepare",
            "error_code": "contract_dependency_install_failed",
            "dependency": dependency,
            "stage": stage,
            "command_exit_code": command_exit_code,
        }
    return None


def _apply_shared_host_limits(max_cpus: int, nice_increment: int) -> dict[str, Any]:
    affinity, process_nice = _limit_process(max_cpus, nice_increment)
    return {
        "nice_increment": nice_increment,
        "process_nice": process_nice,
        "cpu_affinity": affinity,
    }


def validate_shared_host_limits(max_cpus: int, nice_increment: int) -> None:
    if not 1 <= max_cpus <= MAX_SHARED_HOST_CPUS:
        raise ValueError("shared-host max CPUs must be between 1 and 2")
    if not MIN_SHARED_HOST_NICE_INCREMENT <= nice_increment <= 19:
        raise ValueError("shared-host nice increment must be between 15 and 19")


def _shared_host_thresholds(args: argparse.Namespace) -> CapacityThresholds:
    return CapacityThresholds(
        max_load_per_cpu=args.max_load_per_cpu,
        min_available_memory_bytes=int(args.min_memory_gib * GIB),
        min_free_disk_bytes=int(args.min_disk_gib * GIB),
        min_cpu_count=2,
    )


class Experiment:
    def __init__(
        self,
        output: Path,
        *,
        timeout_seconds: int,
        environment: dict[str, str],
    ) -> None:
        self.output = output
        self.log_root = output / "logs"
        self.report_path = output / "core-experiment-report.json"
        self.timeout_seconds = timeout_seconds
        self.environment = environment
        self.steps: list[dict[str, Any]] = []
        self.log_root.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        label: str,
        command: list[str],
        *,
        cwd: Path = ROOT,
    ) -> str:
        started = time.monotonic()
        result = subprocess.run(
            command,
            cwd=cwd,
            env=self.environment,
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_seconds,
        )
        duration = round(time.monotonic() - started, 3)
        log_path = self.log_root / f"{label}.log"
        log_path.write_text(
            (result.stdout or "") + (result.stderr or ""),
            encoding="utf-8",
        )
        step = {
            "name": label,
            "command": command,
            "exit_code": result.returncode,
            "duration_seconds": duration,
            "log": str(log_path),
        }
        if result.returncode == 4 and label == "contracts-prepare":
            diagnostic = _safe_contract_prepare_diagnostic(result.stderr or "")
            if diagnostic is not None:
                step["diagnostic"] = diagnostic
        self.steps.append(step)
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed with exit code {result.returncode}")
        return result.stdout or ""

    def write_report(self, report: dict[str, Any]) -> None:
        self.report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.set_defaults(shared_host=False)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--events", type=int, default=12)
    parser.add_argument("--observers", type=int, default=10)
    parser.add_argument("--include-recovery-tests", action="store_true")
    parser.add_argument("--max-cpus", type=int, default=2)
    parser.add_argument("--nice-increment", type=int, default=15)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.5)
    parser.add_argument("--min-memory-gib", type=float, default=3.0)
    parser.add_argument("--min-disk-gib", type=float, default=5.0)
    parser.add_argument("--step-timeout-seconds", type=int, default=1800)
    return parser


def run_core_experiment(
    args: argparse.Namespace,
    *,
    shared_host_preflight: dict[str, Any] | None = None,
    shared_host_preflight_source: str | None = None,
    resource_preflight_binding: dict[str, Any] | None = None,
    shared_host_limits: dict[str, Any] | None = None,
) -> int:
    """Run the core experiment after the shared-host launcher binds a lease."""

    run_id = (
        "core-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    output = (
        args.output.resolve()
        if args.output
        else (ROOT / "tmp" / "core-experiments" / run_id).resolve()
    )
    output.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    preflight: dict[str, Any] | None = None
    limits: dict[str, Any] | None = None
    environment = os.environ.copy()
    environment.update(
        {
            "UV_CONCURRENT_DOWNLOADS": "2",
            "UV_CONCURRENT_BUILDS": "1",
            "CARGO_BUILD_JOBS": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    experiment = Experiment(
        output,
        timeout_seconds=args.step_timeout_seconds,
        environment=environment,
    )
    report: dict[str, Any] = {
        "schema_version": "loveengine.core-experiment-report/2",
        "run_id": run_id,
        "status": "running",
        "environment": "local_anvil",
        "execution_host": platform.node(),
        "execution_platform": platform.platform(),
        "actors_simulated": True,
        "resource_profile": "shared_host" if args.shared_host else "default",
        "resource_preflight": preflight,
        "resource_preflight_source": None,
        "resource_preflight_binding": None,
        "resource_limits": limits,
        "started_at": started_at,
        "steps": experiment.steps,
    }
    try:
        if shared_host_preflight is not None and not args.shared_host:
            raise ValueError("shared-host preflight lease requires --shared-host")
        if (
            shared_host_preflight_source is not None
            or resource_preflight_binding is not None
        ) and shared_host_preflight is None:
            raise ValueError("shared-host preflight metadata requires a lease")
        if shared_host_limits is not None and shared_host_preflight is None:
            raise ValueError("shared-host limits require a preflight lease")
        if args.shared_host:
            validate_shared_host_limits(args.max_cpus, args.nice_increment)
            if not hasattr(os, "sched_getaffinity") or not hasattr(
                os, "sched_setaffinity"
            ):
                raise RuntimeError("shared-host CPU affinity enforcement is required")
            thresholds = _shared_host_thresholds(args)
            if shared_host_preflight is None:
                raise ValueError(
                    "shared-host core runs require start_shared_core.py and its one-shot lease"
                )
            if shared_host_preflight_source != "launcher_fd_lease":
                raise ValueError("shared-host preflight lease source is invalid")
            if not is_passing_preflight(
                shared_host_preflight,
                output,
                thresholds,
            ):
                raise ValueError("shared-host preflight lease is not passing")
            if not isinstance(resource_preflight_binding, dict):
                raise ValueError("shared-host preflight lease binding is invalid")
            preflight = shared_host_preflight
            preflight_source = shared_host_preflight_source
            report["resource_preflight"] = preflight
            report["resource_preflight_source"] = preflight_source
            report["resource_preflight_binding"] = resource_preflight_binding
            if not preflight["safe_to_run"]:
                report.update(
                    {
                        "status": "blocked_by_resource_guard",
                        "completed_at": _utc_now(),
                        "error": {
                            "code": "resource_preflight_rejected",
                            "reasons": preflight.get("reasons", []),
                        },
                    }
                )
                experiment.write_report(report)
                print(json.dumps(report, ensure_ascii=False, sort_keys=True))
                return 4
            if shared_host_limits is None:
                limits = _apply_shared_host_limits(args.max_cpus, args.nice_increment)
            else:
                affinity = shared_host_limits.get("cpu_affinity")
                if (
                    set(shared_host_limits)
                    != {"nice_increment", "process_nice", "cpu_affinity"}
                    or shared_host_limits.get("nice_increment") != args.nice_increment
                    or shared_host_limits.get("process_nice") != args.nice_increment
                    or not isinstance(affinity, list)
                    or not affinity
                    or len(affinity) > args.max_cpus
                    or len(set(affinity)) != len(affinity)
                    or any(
                        isinstance(cpu, bool) or not isinstance(cpu, int)
                        for cpu in affinity
                    )
                ):
                    raise ValueError("shared-host launcher limits are invalid")
                limits = {
                    "nice_increment": args.nice_increment,
                    "process_nice": args.nice_increment,
                    "cpu_affinity": affinity,
                }
            report["resource_limits"] = limits
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is required")
        experiment.run("uv-sync", [uv, "sync", "--frozen"])
        preparation_output = experiment.run(
            "contracts-prepare",
            [uv, "run", "loveengine", "pilot", "contracts", "prepare"],
        )
        preparation = _last_json_object(preparation_output)
        if (
            preparation.get("schema_version")
            != "loveengine.contract-preparation/1"
            or preparation.get("prepared") is not True
            or not isinstance(preparation.get("artifacts"), dict)
        ):
            raise RuntimeError("contracts prepare did not emit verified provenance")
        report["contract_preparation"] = preparation
        demo_output = experiment.run(
            "core-e2e",
            [
                uv,
                "run",
                "loveengine",
                "demo",
                "lan-pilot",
                "--run-id",
                run_id,
                "--stage",
                "core",
                "--events",
                str(args.events),
                "--observers",
                str(args.observers),
                "--output",
                str(output / "runtime"),
            ],
        )
        demo = _last_json_object(demo_output)
        expected = {
            "stage": "core",
            "environment": "local_anvil",
            "actors_simulated": True,
            "offline_verification": "offline_integrity",
            "chain_verification": "chain_consistency",
            "trust_verification": "chain_verified",
            "gate_ready": True,
        }
        for key, value in expected.items():
            if demo.get(key) != value:
                raise RuntimeError(f"core E2E returned unexpected {key}")

        offline_output = experiment.run(
            "offline-transcript",
            [
                uv,
                "run",
                "loveengine",
                "pilot",
                "transcript",
                "verify",
                str(demo["transcript_path"]),
            ],
        )
        offline = _last_json_object(offline_output)
        if (
            offline.get("valid") is not True
            or offline.get("verification_level") != "offline_integrity"
            or offline.get("trust_bound") is not False
            or offline.get("chain_verified") is not False
            or offline.get("run_id") != run_id
        ):
            raise RuntimeError("offline transcript verification or run binding failed")

        experiment.run(
            "tamper-and-policy-tests",
            [
                uv,
                "run",
                "pytest",
                "tests/test_core_transcript.py",
                "tests/test_trust_policy.py",
                "-q",
            ],
        )
        if args.include_recovery_tests:
            experiment.run(
                "restart-and-snapshot-tests",
                [
                    uv,
                    "run",
                    "pytest",
                    "tests/integration/test_pilot_chain.py",
                    "tests/integration/test_pilot_quickstart.py",
                    "tests/test_pilot_snapshot.py",
                    "-q",
                ],
            )

        collected_transcript = output / "witness-core-transcript.json"
        shutil.copy2(Path(demo["transcript_path"]), collected_transcript)
        report.update(
            {
                "status": "passed",
                "completed_at": _utc_now(),
                "transcript_path": str(demo["transcript_path"]),
                "collected_transcript": str(collected_transcript),
                "verification": {
                    "offline": demo["offline_verification"],
                    "rpc": demo["chain_verification"],
                    "rpc_with_policy": demo["trust_verification"],
                },
                "observation_receipts": demo["observation_receipts"],
                "review_receipts": demo["review_receipts"],
                "gate_ready": demo["gate_ready"],
                "recovery_tests": args.include_recovery_tests,
                "proves": [
                    "release-to-task trust binding on local Anvil",
                    "Relay delivery and signed observation/review receipts",
                    "artifact and evidence integrity through ProposalGate",
                    "runtime compatibility on the recorded execution host",
                ],
                "does_not_prove": [
                    "source statements are true",
                    "actors are socially or organizationally independent",
                    "public/testnet/production deployment or long-term availability",
                ],
            }
        )
    except Exception as exc:
        report.update(
            {
                "status": "failed",
                "completed_at": _utc_now(),
                "error": str(exc),
            }
        )
        experiment.write_report(report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1
    experiment.write_report(report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    return run_core_experiment(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
