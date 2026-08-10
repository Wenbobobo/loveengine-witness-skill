from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from loveengine_witness.cli import build_parser


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loveengine_witness.cli", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_cli_exposes_m1_command_tree() -> None:
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    for command in ("manifest", "node", "fixture", "evidence", "transcript"):
        assert command in result.stdout


def test_cli_exposes_m5_command_tree() -> None:
    result = run_cli("--help")

    assert result.returncode == 0, result.stderr
    for command in ("package", "pilot", "witness"):
        assert command in result.stdout

    pilot = run_cli("pilot", "--help")
    assert pilot.returncode == 0, pilot.stderr
    for command in (
        "quickstart",
        "serve",
        "status",
        "contracts",
        "chain",
        "transcript",
        "snapshot",
        "soak",
    ):
        assert command in pilot.stdout

    soak = run_cli("pilot", "soak", "--help")
    assert soak.returncode == 0, soak.stderr
    assert "--worker-run-id" not in soak.stdout


def test_lan_pilot_exposes_explicit_run_id_binding() -> None:
    result = run_cli("demo", "lan-pilot", "--help")

    assert result.returncode == 0, result.stderr
    assert "--run-id" in result.stdout


def test_pilot_soak_defaults_to_engineering_acceptance_profile() -> None:
    args = build_parser().parse_args(
        ["pilot", "soak", "--output", "candidate-soak"]
    )

    assert args.duration_seconds == 900
    assert args.events == 30
    assert args.observers == 10
    assert args.stage == "core"


def test_version_reports_runtime_source() -> None:
    result = run_cli("version")

    assert result.returncode == 0, result.stderr
    value = __import__("json").loads(result.stdout)
    assert value["package_version"] == "0.6.1"
    assert value["skill_version"] == "0.6.1-contract-public-pilot"
    assert value["protocol"] == "loveengine-witness-net/0.6"
    assert Path(value["package_root"]).resolve() == ROOT
