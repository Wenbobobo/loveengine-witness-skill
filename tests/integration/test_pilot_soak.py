from __future__ import annotations

from pathlib import Path

import pytest

from loveengine_witness.pilot_soak import run_pilot_soak


@pytest.mark.integration
def test_accelerated_pilot_soak_with_faults_and_ten_observers(
    tmp_path: Path,
) -> None:
    report = run_pilot_soak(
        tmp_path,
        duration_seconds=0.12,
        event_count=12,
        observers=10,
    )
    assert report["mode"] == "accelerated"
    assert report["passed"] is True
    assert all(report["checks"].values())
