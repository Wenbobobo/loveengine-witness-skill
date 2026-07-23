from __future__ import annotations

from pathlib import Path

import pytest

from loveengine_witness.pilot_demo import run_pilot_demo
from loveengine_witness.core_transcript import verify_core_transcript
from loveengine_witness.pilot_transcript import verify_pilot_transcript


@pytest.mark.integration
def test_witness_core_pilot_stops_at_gate(tmp_path: Path) -> None:
    result = run_pilot_demo(
        tmp_path, event_count=3, observer_count=3, simulate_faults=False
    )
    transcript = result["transcript"]

    assert result["stage"] == "core"
    assert result["package_installed"] is True
    assert result["observation_receipts"] == 3
    assert result["review_receipts"] == 3
    assert result["gate_ready"] is True
    assert result["environment"] == "local_anvil"
    assert result["actors_simulated"] is True
    assert result["chain_verification"] == "chain_consistency"
    assert "proposal" not in transcript
    assert "vote_approvals" not in transcript
    assert "final_state" not in transcript
    assert verify_core_transcript(transcript)["valid"] is True


@pytest.mark.integration
def test_package_to_public_sink_governance_pilot_e2e(tmp_path: Path) -> None:
    result = run_pilot_demo(tmp_path, event_count=12, stage="governance")
    transcript = result["transcript"]

    assert result["stage"] == "governance"
    assert result["package_installed"] is True
    assert result["observation_receipts"] == 3
    assert result["read_only_observers"] == 10
    assert result["vote_approvals"] == 5
    assert result["proposal_executed"] is True
    assert int(result["total_uto"]) > 0
    assert result["offline_verification"] == "offline_integrity"
    assert result["chain_verification"] == "chain_consistency"
    assert verify_pilot_transcript(transcript)["valid"] is True
