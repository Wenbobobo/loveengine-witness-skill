from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_local_loop_demo_runs_real_anvil_flow_and_writes_transcript(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loveengine_witness.cli",
            "demo",
            "local-loop",
            "--output",
            str(tmp_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    transcript_path = Path(summary["transcript_path"])
    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    assert transcript["final_state"]["registered_witness_count"] == "5"
    assert transcript["final_state"]["proposal_executed"] is True
    assert int(transcript["final_state"]["total_uto"]) >= 0

    verify = subprocess.run(
        [
            sys.executable,
            "-m",
            "loveengine_witness.cli",
            "transcript",
            "verify",
            str(transcript_path),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
