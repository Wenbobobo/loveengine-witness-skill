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
