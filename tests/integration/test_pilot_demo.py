from __future__ import annotations

from pathlib import Path

import pytest

from loveengine_witness.pilot_demo import run_pilot_demo
from loveengine_witness.pilot_transcript import verify_pilot_transcript


@pytest.mark.integration
def test_package_to_public_sink_pilot_e2e(tmp_path: Path) -> None:
    result = run_pilot_demo(tmp_path, event_count=12)
    transcript = result["transcript"]

    assert result["package_installed"] is True
    assert result["observation_receipts"] == 3
    assert result["read_only_observers"] == 10
    assert result["vote_approvals"] == 5
    assert result["proposal_executed"] is True
    assert int(result["total_uto"]) > 0
    assert verify_pilot_transcript(transcript)["valid"] is True
