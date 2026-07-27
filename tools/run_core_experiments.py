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
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from remote_host_preflight import CapacityThresholds, run_preflight


ROOT = Path(__file__).resolve().parents[1]
GIB = 1024**3
FOUNDRY_VERSION = "1.7.1"
FORGE_STD_COMMIT = "77041d2ce690e692d6e03cc812b57d1ddaa4d505"
OPENZEPPELIN_COMMIT = "e4f70216d759d8e6a64144a9e1f7bbeed78e7079"
MAX_SHARED_HOST_CPUS = 2
MIN_SHARED_HOST_NICE_INCREMENT = 15


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


def _foundry_binary(name: str) -> Path | None:
    executable = name + (".exe" if os.name == "nt" else "")
    configured = os.environ.get("FOUNDRY_BIN")
    candidates = [
        Path(configured) / executable if configured else None,
        Path.home()
        / ".codex"
        / "tools"
        / f"foundry-v{FOUNDRY_VERSION}"
        / executable,
        Path.home() / ".foundry" / "bin" / executable,
    ]
    discovered = shutil.which(name)
    if discovered:
        candidates.append(Path(discovered))
    return next(
        (candidate.resolve() for candidate in candidates if candidate and candidate.is_file()),
        None,
    )


def is_exact_forge_version(output: str) -> bool:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return bool(
        lines
        and re.fullmatch(r"forge Version: 1\.7\.1", lines[0]) is not None
    )


def _apply_shared_host_limits(max_cpus: int, nice_increment: int) -> dict[str, Any]:
    validate_shared_host_limits(max_cpus, nice_increment)
    result: dict[str, Any] = {
        "nice_increment": 0,
        "cpu_affinity": None,
    }
    if os.name != "nt":
        os.nice(nice_increment)
        result["nice_increment"] = nice_increment
        if hasattr(os, "sched_getaffinity") and hasattr(os, "sched_setaffinity"):
            available = sorted(os.sched_getaffinity(0))
            selected = available[: max(1, min(max_cpus, len(available)))]
            os.sched_setaffinity(0, selected)
            result["cpu_affinity"] = selected
    return result


def validate_shared_host_limits(max_cpus: int, nice_increment: int) -> None:
    if not 1 <= max_cpus <= MAX_SHARED_HOST_CPUS:
        raise ValueError("shared-host max CPUs must be between 1 and 2")
    if not MIN_SHARED_HOST_NICE_INCREMENT <= nice_increment <= 19:
        raise ValueError("shared-host nice increment must be between 15 and 19")


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
        self.steps.append(
            {
                "name": label,
                "command": command,
                "exit_code": result.returncode,
                "duration_seconds": duration,
                "log": str(log_path),
            }
        )
        if result.returncode != 0:
            raise RuntimeError(f"{label} failed with exit code {result.returncode}")
        return result.stdout or ""

    def write_report(self, report: dict[str, Any]) -> None:
        self.report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _prepare_contracts(experiment: Experiment) -> None:
    contracts = ROOT / "contracts"
    forge = _foundry_binary("forge")
    if forge is None:
        raise RuntimeError("forge is required")
    version = experiment.run("forge-version", [str(forge), "--version"])
    if not is_exact_forge_version(version):
        raise RuntimeError("Foundry 1.7.1 is required")
    dependencies = (
        (
            contracts / "lib" / "forge-std",
            f"foundry-rs/forge-std@{FORGE_STD_COMMIT}",
        ),
        (
            contracts / "lib" / "openzeppelin-contracts",
            f"OpenZeppelin/openzeppelin-contracts@{OPENZEPPELIN_COMMIT}",
        ),
    )
    for path, package in dependencies:
        if not path.is_dir():
            experiment.run(
                "forge-install-" + path.name,
                [str(forge), "install", package, "--no-git"],
                cwd=contracts,
            )
    experiment.run(
        "forge-build",
        [str(forge), "build", "--threads", "1"],
        cwd=contracts,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--events", type=int, default=12)
    parser.add_argument("--observers", type=int, default=10)
    parser.add_argument("--prepare-contracts", action="store_true")
    parser.add_argument("--include-recovery-tests", action="store_true")
    parser.add_argument("--shared-host", action="store_true")
    parser.add_argument("--max-cpus", type=int, default=2)
    parser.add_argument("--nice-increment", type=int, default=15)
    parser.add_argument("--max-load-per-cpu", type=float, default=0.5)
    parser.add_argument("--min-memory-gib", type=float, default=3.0)
    parser.add_argument("--min-disk-gib", type=float, default=5.0)
    parser.add_argument("--step-timeout-seconds", type=int, default=1800)
    return parser


def main() -> int:
    args = build_parser().parse_args()
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
    forge = _foundry_binary("forge")
    anvil = _foundry_binary("anvil")
    if forge and anvil and forge.parent == anvil.parent:
        environment["FOUNDRY_BIN"] = str(forge.parent)
    if args.shared_host:
        validate_shared_host_limits(args.max_cpus, args.nice_increment)
        thresholds = CapacityThresholds(
            max_load_per_cpu=args.max_load_per_cpu,
            min_available_memory_bytes=int(args.min_memory_gib * GIB),
            min_free_disk_bytes=int(args.min_disk_gib * GIB),
            min_cpu_count=2,
        )
        preflight = run_preflight(output, thresholds)
        if not preflight["safe_to_run"]:
            print(json.dumps(preflight, ensure_ascii=False, sort_keys=True))
            return 4
        limits = _apply_shared_host_limits(args.max_cpus, args.nice_increment)

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
        "resource_limits": limits,
        "started_at": started_at,
        "steps": experiment.steps,
    }
    try:
        uv = shutil.which("uv")
        if uv is None:
            raise RuntimeError("uv is required")
        experiment.run("uv-sync", [uv, "sync", "--frozen"])
        if args.prepare_contracts:
            _prepare_contracts(experiment)
        demo_output = experiment.run(
            "core-e2e",
            [
                uv,
                "run",
                "loveengine",
                "demo",
                "lan-pilot",
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
            offline.get("verification_level") != "offline_integrity"
            or offline.get("trust_bound") is not False
        ):
            raise RuntimeError("offline transcript verification overstated trust")

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


if __name__ == "__main__":
    raise SystemExit(main())
