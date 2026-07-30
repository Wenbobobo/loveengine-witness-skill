from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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


def test_version_reports_runtime_source() -> None:
    result = run_cli("version")

    assert result.returncode == 0, result.stderr
    value = __import__("json").loads(result.stdout)
    assert value["package_version"] == "0.6.1"
    assert value["skill_version"] == "0.6.1-contract-public-pilot"
    assert value["protocol"] == "loveengine-witness-net/0.6"
    assert Path(value["package_root"]).resolve() == ROOT
